from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .catalog import WorldCatalog


@dataclass(slots=True)
class ValidationResult:
    valid: bool
    issues: list[str]


class IRSpec:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.semantic_guidance = self._load_semantic_guidance()
        self.catalog = self._load_catalog()

    def _load_semantic_guidance(self) -> str:
        """Load optional human-readable semantics declared by the IR spec.

        The JSON spec remains the machine-checkable structural contract.  A
        version may additionally point at a Markdown sidecar that explains how
        to choose between multiple structurally legal encodings.
        """
        filename = self.data.get("semantic_guidance_file")
        if filename is None:
            return "No additional semantic guidance is defined for this IR version."
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("semantic_guidance_file must be a non-empty string")

        guidance_path = self.path.parent / filename
        if not guidance_path.is_file():
            raise ValueError(
                f"IR semantic guidance file does not exist: {guidance_path}"
            )
        text = guidance_path.read_text(encoding="utf-8").strip()
        if not text:
            raise ValueError(
                f"IR semantic guidance file is empty: {guidance_path}"
            )
        return text

    def _load_catalog(self) -> WorldCatalog | None:
        filename = self.data.get("world_catalog_file")
        if filename is None:
            return None
        if not isinstance(filename, str) or not filename.strip():
            raise ValueError("world_catalog_file must be a non-empty string")
        catalog_path = self.path.parent / filename
        if not catalog_path.is_file():
            raise ValueError(f"World Catalog file does not exist: {catalog_path}")
        return WorldCatalog(catalog_path)

    def pretty(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)

    def catalog_pretty(self) -> str:
        if self.catalog is None:
            return "No controlled World Catalog is defined for this IR version."
        return self.catalog.pretty()

    @property
    def anchors(self) -> set[str]:
        return set(self.data["anchors"])

    def resolve_rule(self, rule: dict[str, Any]) -> dict[str, Any]:
        """Resolve a named nested value schema while keeping the config declarative."""
        if rule.get("kind") != "schema_ref":
            return rule
        schema_name = rule.get("schema")
        schemas = self.data.get("value_schemas", {})
        if not isinstance(schema_name, str) or schema_name not in schemas:
            raise ValueError(f"Unknown value schema reference: {schema_name!r}")
        return schemas[schema_name]


class IRValidator:
    """Deterministic validator driven by the selected World IR spec.

    It checks structural legality, nested typed-value legality, reference
    integrity, and relation source/target compatibility. It intentionally does
    not judge fuzzy fidelity to the user's natural-language request.
    """

    def __init__(self, spec: IRSpec):
        self.spec = spec

    def validate(self, ir: dict[str, Any]) -> ValidationResult:
        issues: list[str] = []
        roots = self.spec.data["root_collections"]
        primitives = self.spec.data["primitives"]

        if not isinstance(ir, dict):
            return ValidationResult(valid=False, issues=["World IR root must be an object"])

        expected_roots = set(roots)
        actual_roots = set(ir)
        missing_roots = expected_roots - actual_roots
        extra_roots = actual_roots - expected_roots
        if missing_roots:
            issues.append(f"Missing root collections: {sorted(missing_roots)}")
        if extra_roots:
            issues.append(f"Unknown root collections: {sorted(extra_roots)}")

        ids: set[str] = set()
        id_primitives: dict[str, str] = {}
        id_semantic_types: dict[str, str] = {}
        objects: list[tuple[str, str, dict[str, Any]]] = []

        # First pass: primitive structure, nested value structure, and ids.
        for collection, primitive_name in roots.items():
            arr = ir.get(collection, [])
            if not isinstance(arr, list):
                issues.append(f"{collection} must be an array")
                continue
            p = primitives[primitive_name]
            allowed = set(p["required"]) | set(p["optional"])
            for i, obj in enumerate(arr):
                loc = f"{collection}[{i}]"
                if not isinstance(obj, dict):
                    issues.append(f"{loc} must be an object")
                    continue
                objects.append((loc, primitive_name, obj))
                missing = set(p["required"]) - set(obj)
                extra = set(obj) - allowed
                if missing:
                    issues.append(f"{loc} missing required fields: {sorted(missing)}")
                if extra:
                    issues.append(f"{loc} has unknown fields: {sorted(extra)}")

                obj_id = obj.get("id")
                if isinstance(obj_id, str) and obj_id.strip():
                    if obj_id in ids:
                        issues.append(f"Duplicate id: {obj_id}")
                    else:
                        ids.add(obj_id)
                        id_primitives[obj_id] = primitive_name
                        semantic_type = obj.get("type")
                        if isinstance(semantic_type, str):
                            id_semantic_types[obj_id] = semantic_type
                elif isinstance(obj_id, str):
                    # V2's declarative string_nonempty rule reports the field
                    # error below. Do not register an unusable id for reference
                    # or duplicate-id validation.
                    pass
                elif "id" in obj:
                    issues.append(f"{loc}.id must be a string")

                object_type = obj.get("type")
                if isinstance(object_type, str) and self.spec.catalog is not None:
                    allowed_types = self.spec.catalog.allowed_types(primitive_name)
                    if object_type not in allowed_types:
                        issues.append(
                            f"{loc}.type is not in {self.spec.catalog.version} "
                            f"for {primitive_name}: {object_type!r}; "
                            f"allowed: {sorted(allowed_types)}"
                        )

                for field, value in obj.items():
                    rule = p["fields"].get(field)
                    if rule:
                        self._check_value_shape(
                            f"{loc}.{field}", value, rule, primitive_name, issues
                        )

        # Second pass: references and nested relation semantics now that all ids
        # and primitive types are known.
        for loc, primitive_name, obj in objects:
            p = primitives[primitive_name]
            for field, value in obj.items():
                rule = p["fields"].get(field)
                if rule:
                    self._check_value_semantics(
                        f"{loc}.{field}",
                        value,
                        rule,
                        primitive_name,
                        id_primitives,
                        issues,
                    )

        self._check_catalog_invariants(
            objects,
            id_primitives,
            id_semantic_types,
            issues,
        )

        return ValidationResult(valid=not issues, issues=issues)

    # ------------------------------------------------------------------
    # Structural validation
    # ------------------------------------------------------------------

    def _check_value_shape(
        self,
        name: str,
        value: Any,
        rule: dict[str, Any],
        source_primitive: str,
        issues: list[str],
    ) -> None:
        rule = self.spec.resolve_rule(rule)
        kind = rule["kind"]

        if kind in {"string", "ref", "anchor_or_ref"}:
            if not isinstance(value, str):
                issues.append(f"{name} must be a string")
        elif kind == "string_nonempty":
            if not isinstance(value, str) or not value.strip():
                issues.append(f"{name} must be a non-empty string")
        elif kind == "anchor":
            if not isinstance(value, str) or value not in self.spec.anchors:
                issues.append(f"{name} must be one of {sorted(self.spec.anchors)}")
        elif kind == "enum":
            if value not in rule["values"]:
                issues.append(f"{name} must be one of {rule['values']}")
        elif kind == "const":
            if value != rule["value"]:
                issues.append(f"{name} must equal {rule['value']!r}")
        elif kind == "ref_list":
            if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
                issues.append(f"{name} must be an array of strings")
        elif kind == "relation_list":
            if not isinstance(value, list):
                issues.append(f"{name} must be an array of relation objects")
            else:
                for i, relation in enumerate(value):
                    if not isinstance(relation, dict):
                        issues.append(f"{name}[{i}] must be a relation object")
        elif kind == "integer_nonnegative":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                issues.append(f"{name} must be a non-negative integer")
        elif kind == "scalar":
            if not isinstance(value, (str, int, float)) or isinstance(value, bool):
                issues.append(f"{name} must be a scalar")
        elif kind == "object":
            self._check_object_shape(name, value, rule, source_primitive, issues)
        elif kind == "tagged_union":
            self._check_tagged_union_shape(name, value, rule, source_primitive, issues)
        elif kind == "schema_ref":
            # resolve_rule() should have consumed this already.
            raise AssertionError("unresolved schema_ref")
        else:
            issues.append(f"{name} uses unsupported validator kind: {kind}")

    def _check_object_shape(
        self,
        name: str,
        value: Any,
        rule: dict[str, Any],
        source_primitive: str,
        issues: list[str],
    ) -> None:
        if not isinstance(value, dict):
            issues.append(f"{name} must be an object")
            return

        required = set(rule.get("required", []))
        optional = set(rule.get("optional", []))
        allowed = required | optional
        missing = required - set(value)
        extra = set(value) - allowed
        if missing:
            issues.append(f"{name} missing required fields: {sorted(missing)}")
        if extra:
            issues.append(f"{name} has unknown fields: {sorted(extra)}")

        fields = rule.get("fields", {})
        for field, nested_value in value.items():
            nested_rule = fields.get(field)
            if nested_rule:
                self._check_value_shape(
                    f"{name}.{field}", nested_value, nested_rule, source_primitive, issues
                )

        self._check_object_constraints(name, value, rule, issues)


    def _check_object_constraints(
        self,
        name: str,
        value: dict[str, Any],
        rule: dict[str, Any],
        issues: list[str],
    ) -> None:
        """Check declarative cross-field constraints on nested objects.

        V2 uses this for semantics that are structural enough to enforce
        deterministically, such as choosing exactly one qualitative density
        specification.  Keeping the condition in the JSON spec avoids hiding
        a World-IR rule in Python-only logic.
        """
        for constraint in rule.get("constraints", []):
            kind = constraint.get("kind")
            if kind != "forbid_when":
                issues.append(f"{name} uses unsupported object constraint kind: {kind!r}")
                continue

            condition = constraint.get("if", {})
            path = condition.get("path")
            equals = condition.get("equals")
            field_present = constraint.get("field_present")
            if not isinstance(path, str) or not isinstance(field_present, str):
                issues.append(f"{name} has malformed forbid_when constraint")
                continue

            current: Any = value
            found = True
            for part in path.split("."):
                if not isinstance(current, dict) or part not in current:
                    found = False
                    break
                current = current[part]

            if found and current == equals and field_present in value:
                message = constraint.get("message")
                if not isinstance(message, str) or not message.strip():
                    message = (
                        f"{path}={equals!r} cannot be combined with {field_present}"
                    )
                issues.append(f"{name}: {message}")

    def _check_tagged_union_shape(
        self,
        name: str,
        value: Any,
        rule: dict[str, Any],
        source_primitive: str,
        issues: list[str],
    ) -> None:
        if not isinstance(value, dict):
            issues.append(f"{name} must be an object")
            return

        discriminator = rule["discriminator"]
        tag = value.get(discriminator)
        if not isinstance(tag, str):
            issues.append(f"{name}.{discriminator} must be a string")
            return

        variants = rule.get("variants", {})
        variant = variants.get(tag)
        if variant is None:
            issues.append(
                f"{name}.{discriminator} must be one of {sorted(variants)}"
            )
            return

        # A variant is itself an object schema but does not repeat kind=object.
        object_rule = {
            "kind": "object",
            "required": variant.get("required", []),
            "optional": variant.get("optional", []),
            "fields": variant.get("fields", {}),
        }
        self._check_object_shape(name, value, object_rule, source_primitive, issues)

    # ------------------------------------------------------------------
    # Semantic/reference validation
    # ------------------------------------------------------------------

    def _check_value_semantics(
        self,
        name: str,
        value: Any,
        rule: dict[str, Any],
        source_primitive: str,
        id_primitives: dict[str, str],
        issues: list[str],
    ) -> None:
        rule = self.spec.resolve_rule(rule)
        kind = rule["kind"]
        anchors = self.spec.anchors

        if kind == "ref" and isinstance(value, str):
            if value not in id_primitives:
                issues.append(f"{name} references unknown id: {value}")
        elif kind == "ref_list" and isinstance(value, list):
            for ref in value:
                if isinstance(ref, str) and ref not in id_primitives:
                    issues.append(f"{name} references unknown id: {ref}")
        elif kind == "anchor_or_ref" and isinstance(value, str):
            if value not in anchors and value not in id_primitives:
                issues.append(f"{name} must be an anchor or existing id: {value}")
        elif kind == "relation_list" and isinstance(value, list):
            self._check_relations(
                name, source_primitive, value, id_primitives, issues
            )
        elif kind == "object" and isinstance(value, dict):
            fields = rule.get("fields", {})
            for field, nested_value in value.items():
                nested_rule = fields.get(field)
                if nested_rule:
                    self._check_value_semantics(
                        f"{name}.{field}",
                        nested_value,
                        nested_rule,
                        source_primitive,
                        id_primitives,
                        issues,
                    )
        elif kind == "tagged_union" and isinstance(value, dict):
            discriminator = rule["discriminator"]
            tag = value.get(discriminator)
            variant = rule.get("variants", {}).get(tag)
            if variant:
                fields = variant.get("fields", {})
                for field, nested_value in value.items():
                    nested_rule = fields.get(field)
                    if nested_rule:
                        self._check_value_semantics(
                            f"{name}.{field}",
                            nested_value,
                            nested_rule,
                            source_primitive,
                            id_primitives,
                            issues,
                        )

    def _check_relations(
        self,
        name: str,
        source_primitive: str,
        relations: list[Any],
        id_primitives: dict[str, str],
        issues: list[str],
    ) -> None:
        relation_types = self.spec.data.get("relation_types", {})
        if not relation_types:
            issues.append(f"{name} cannot be validated: spec has no relation_types")
            return

        for i, relation in enumerate(relations):
            relation_name = f"{name}[{i}]"
            if not isinstance(relation, dict):
                continue

            relation_type = relation.get("type")
            if not isinstance(relation_type, str):
                issues.append(f"{relation_name}.type must be a string")
                continue
            if relation_type not in relation_types:
                issues.append(
                    f"{relation_name}.type must be one of {sorted(relation_types)}"
                )
                continue

            rule = relation_types[relation_type]
            required = set(rule.get("required", []))
            optional = set(rule.get("optional", []))
            allowed = required | optional
            missing = required - set(relation)
            extra = set(relation) - allowed
            if missing:
                issues.append(f"{relation_name} missing required fields: {sorted(missing)}")
            if extra:
                issues.append(f"{relation_name} has unknown fields: {sorted(extra)}")

            allowed_sources = set(rule.get("allowed_sources", []))
            if allowed_sources and source_primitive not in allowed_sources:
                issues.append(
                    f"{relation_name} relation {relation_type!r} does not allow source primitive "
                    f"{source_primitive}; allowed: {sorted(allowed_sources)}"
                )

            target = relation.get("target")
            if "target" in relation and not isinstance(target, str):
                issues.append(f"{relation_name}.target must be a string")
            elif isinstance(target, str):
                target_primitive = id_primitives.get(target)
                if target_primitive is None:
                    issues.append(f"{relation_name}.target references unknown id: {target}")
                else:
                    allowed_targets = set(rule.get("allowed_targets", []))
                    if allowed_targets and target_primitive not in allowed_targets:
                        issues.append(
                            f"{relation_name} relation {relation_type!r} does not allow target primitive "
                            f"{target_primitive}; allowed: {sorted(allowed_targets)}"
                        )

            if "direction" in relation:
                direction = relation["direction"]
                direction_values = rule.get("direction_values")
                if direction_values is None:
                    issues.append(
                        f"{relation_name}.direction is not valid for relation {relation_type!r}"
                    )
                elif direction not in direction_values:
                    issues.append(
                        f"{relation_name}.direction must be one of {direction_values}"
                    )

    def _check_catalog_invariants(
        self,
        objects: list[tuple[str, str, dict[str, Any]]],
        id_primitives: dict[str, str],
        id_semantic_types: dict[str, str],
        issues: list[str],
    ) -> None:
        """Enforce closed-world ownership and Region compatibility for V2.

        These rules require both primitive identity and semantic type identity,
        so they run after the reference index has been built. They deliberately
        validate rather than synthesize content: archetype realization remains
        an activation-time compiler policy sourced from the Catalog.
        """
        if self.spec.data.get("version") != "World IR V2":
            return

        catalog = self.spec.catalog
        if catalog is None:
            issues.append("World IR V2 closed-world invariants require a World Catalog")
            return

        for loc, primitive_name, obj in objects:
            placement = obj.get("placement")
            relations = (
                placement.get("relations", [])
                if isinstance(placement, dict)
                else []
            )
            inside_relations = [
                relation
                for relation in relations
                if isinstance(relation, dict) and relation.get("type") == "inside"
            ] if isinstance(relations, list) else []

            if primitive_name == "Region":
                if inside_relations:
                    issues.append(
                        f"{loc} Region cannot have an inside relation; "
                        "Region nesting is unsupported"
                    )
                continue

            if primitive_name not in {"Entity", "Distribution"}:
                continue

            if len(inside_relations) != 1:
                issues.append(
                    f"{loc} must have exactly one inside relation targeting a Region; "
                    f"found {len(inside_relations)}"
                )
                continue

            owner_id = inside_relations[0].get("target")
            if not isinstance(owner_id, str):
                continue
            if id_primitives.get(owner_id) != "Region":
                # The relation validator reports unknown/wrong-primitive targets.
                continue

            source_type = obj.get("type")
            owner_region_type = id_semantic_types.get(owner_id)
            if (
                not isinstance(source_type, str)
                or source_type not in catalog.allowed_types(primitive_name)
                or owner_region_type not in catalog.allowed_types("Region")
            ):
                # Vocabulary errors are already reported in the first pass.
                continue

            allowed_regions = catalog.allowed_regions(primitive_name, source_type)
            if owner_region_type not in allowed_regions:
                issues.append(
                    f"{loc} type {source_type!r} is not allowed inside "
                    f"Region type {owner_region_type!r}; allowed_regions: "
                    f"{sorted(allowed_regions)}"
                )
