from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
import re
from typing import Any


class WorldCatalog:
    """Machine-readable semantic capability boundary for World IR types."""

    PRIMITIVES = ("Region", "Network", "Entity", "Distribution")
    _TYPE_NAME = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")
    _OPTIONAL_KEYS = {
        "Region": {"aliases", "default_realization"},
        "Network": {"aliases"},
        "Entity": {"aliases", "allowed_regions"},
        "Distribution": {"aliases", "allowed_regions"},
    }

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self._alias_index: dict[str, dict[str, str]] = {}
        self._validate()

    def _validate(self) -> None:
        if not isinstance(self.data, dict):
            raise ValueError("World Catalog root must be an object")
        if set(self.data) != {"version", "types"}:
            raise ValueError("World Catalog root must contain exactly version and types")
        version = self.data.get("version")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("World Catalog version must be a non-empty string")
        types = self.data.get("types")
        if not isinstance(types, dict):
            raise ValueError("World Catalog types must be an object")
        if set(types) != set(self.PRIMITIVES):
            raise ValueError(
                f"World Catalog types must contain exactly {list(self.PRIMITIVES)}"
            )

        for primitive in self.PRIMITIVES:
            entries = types[primitive]
            if not isinstance(entries, dict) or not entries:
                raise ValueError(
                    f"World Catalog {primitive} types must be a non-empty object"
                )
            for type_name, metadata in entries.items():
                self._validate_metadata(primitive, type_name, metadata)

        self._build_alias_index()
        self._validate_cross_references()

    def _validate_metadata(
        self,
        primitive: str,
        type_name: Any,
        metadata: Any,
    ) -> None:
        location = f"World Catalog {primitive}.{type_name}"
        if (
            not isinstance(type_name, str)
            or self._TYPE_NAME.fullmatch(type_name) is None
        ):
            raise ValueError(
                f"World Catalog {primitive} type names must be canonical snake_case strings"
            )
        if not isinstance(metadata, dict):
            raise ValueError(f"{location} metadata must be an object")
        allowed_keys = {"roles"} | self._OPTIONAL_KEYS[primitive]
        missing = {"roles"} - set(metadata)
        extra = set(metadata) - allowed_keys
        if missing or extra:
            raise ValueError(
                f"{location} metadata keys are invalid: "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )

        self._validate_string_array(f"{location}.roles", metadata["roles"], empty=False)
        if "aliases" in metadata:
            self._validate_string_array(
                f"{location}.aliases", metadata["aliases"], empty=True
            )
        if "allowed_regions" in metadata:
            self._validate_string_array(
                f"{location}.allowed_regions",
                metadata["allowed_regions"],
                empty=False,
            )
        if "default_realization" in metadata:
            self._validate_default_realization(
                f"{location}.default_realization",
                metadata["default_realization"],
            )

    @staticmethod
    def _validate_string_array(name: str, value: Any, *, empty: bool) -> None:
        if (
            not isinstance(value, list)
            or (not empty and not value)
            or not all(isinstance(item, str) and item.strip() for item in value)
        ):
            qualifier = "a string array" if empty else "a non-empty string array"
            raise ValueError(f"{name} must be {qualifier}")
        normalized = [WorldCatalog._normalize_phrase(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"{name} must not contain duplicate normalized values")

    def _validate_default_realization(self, name: str, value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {"entities", "distributions"}:
            raise ValueError(
                f"{name} must contain exactly entities and distributions"
            )
        for primitive_key in ("entities", "distributions"):
            entries = value[primitive_key]
            if not isinstance(entries, list):
                raise ValueError(f"{name}.{primitive_key} must be an array")
            seen_types: set[str] = set()
            for index, entry in enumerate(entries):
                entry_name = f"{name}.{primitive_key}[{index}]"
                required_keys = (
                    {"type"} if primitive_key == "entities" else {"type", "population"}
                )
                if not isinstance(entry, dict) or set(entry) != required_keys:
                    raise ValueError(
                        f"{entry_name} must contain exactly {sorted(required_keys)}"
                    )
                type_name = entry.get("type")
                if not isinstance(type_name, str) or not type_name.strip():
                    raise ValueError(f"{entry_name}.type must be a non-empty string")
                if type_name in seen_types:
                    raise ValueError(f"{name}.{primitive_key} repeats type {type_name!r}")
                seen_types.add(type_name)
                if primitive_key == "distributions":
                    self._validate_default_population(
                        f"{entry_name}.population", entry["population"]
                    )

    @staticmethod
    def _validate_default_population(name: str, population: Any) -> None:
        if not isinstance(population, dict) or set(population) != {"amount"}:
            raise ValueError(f"{name} must contain exactly amount")
        amount = population["amount"]
        if not isinstance(amount, dict) or set(amount) != {"mode", "value"}:
            raise ValueError(f"{name}.amount must contain exactly mode and value")
        mode = amount.get("mode")
        value = amount.get("value")
        if mode == "density":
            if value not in {"low", "medium", "high"}:
                raise ValueError(f"{name}.amount density must be low, medium, or high")
        elif mode == "count":
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name}.amount count must be a non-negative integer")
        else:
            raise ValueError(f"{name}.amount.mode must be count or density")

    def _build_alias_index(self) -> None:
        for primitive in self.PRIMITIVES:
            index: dict[str, str] = {}
            for type_name, metadata in self.data["types"][primitive].items():
                phrases = [type_name, *metadata.get("aliases", [])]
                for phrase in phrases:
                    normalized = self._normalize_phrase(phrase)
                    existing = index.get(normalized)
                    if existing is not None and existing != type_name:
                        raise ValueError(
                            f"World Catalog {primitive} phrase {phrase!r} is ambiguous "
                            f"between {existing!r} and {type_name!r}"
                        )
                    index[normalized] = type_name
            self._alias_index[primitive] = index

    def _validate_cross_references(self) -> None:
        region_types = self.allowed_types("Region")
        for primitive in ("Entity", "Distribution"):
            for type_name in self.allowed_types(primitive):
                allowed_regions = self.allowed_regions(primitive, type_name)
                unknown = allowed_regions - region_types
                if unknown:
                    raise ValueError(
                        f"World Catalog {primitive}.{type_name}.allowed_regions "
                        f"references unknown Region types: {sorted(unknown)}"
                    )

        for region_type in region_types:
            realization = self.default_realization(region_type)
            for collection, primitive in (
                ("entities", "Entity"),
                ("distributions", "Distribution"),
            ):
                for item in realization[collection]:
                    type_name = item["type"]
                    if type_name not in self.allowed_types(primitive):
                        raise ValueError(
                            f"World Catalog Region.{region_type}.default_realization "
                            f"references unknown {primitive} type {type_name!r}"
                        )
                    if region_type not in self.allowed_regions(primitive, type_name):
                        raise ValueError(
                            f"World Catalog Region.{region_type}.default_realization "
                            f"uses incompatible {primitive} type {type_name!r}"
                        )

    @staticmethod
    def _normalize_phrase(phrase: str) -> str:
        return " ".join(phrase.casefold().split())

    @property
    def version(self) -> str:
        return self.data["version"]

    def allowed_types(self, primitive: str) -> set[str]:
        entries: Any = self.data["types"].get(primitive, {})
        return set(entries)

    def metadata(self, primitive: str, type_name: str) -> dict[str, Any]:
        try:
            return deepcopy(self.data["types"][primitive][type_name])
        except KeyError as exc:
            raise KeyError(f"Unknown World Catalog type: {primitive}.{type_name}") from exc

    def roles(self, primitive: str, type_name: str) -> tuple[str, ...]:
        return tuple(self.metadata(primitive, type_name)["roles"])

    def aliases(self, primitive: str, type_name: str) -> tuple[str, ...]:
        return tuple(self.metadata(primitive, type_name).get("aliases", []))

    def allowed_regions(self, primitive: str, type_name: str) -> set[str]:
        return set(self.metadata(primitive, type_name).get("allowed_regions", []))

    def default_realization(self, region_type: str) -> dict[str, list[dict[str, Any]]]:
        metadata = self.metadata("Region", region_type)
        return deepcopy(
            metadata.get("default_realization", {"entities": [], "distributions": []})
        )

    def canonical_type_for_alias(self, primitive: str, phrase: str) -> str | None:
        """Return an exact declared normalization; this is not fuzzy NLP matching."""
        if not isinstance(phrase, str):
            return None
        return self._alias_index.get(primitive, {}).get(self._normalize_phrase(phrase))

    def pretty(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)
