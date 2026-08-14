from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WorldCatalog:
    """Machine-readable boundary for World IR object types.

    Roles describe generic semantic capabilities. They intentionally do not
    encode fixed composition mappings between Region and constituent types.
    """

    PRIMITIVES = ("Region", "Network", "Entity", "Distribution")

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self._validate()

    def _validate(self) -> None:
        if not isinstance(self.data, dict):
            raise ValueError("World Catalog root must be an object")
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

        for primitive, entries in types.items():
            if not isinstance(entries, dict) or not entries:
                raise ValueError(
                    f"World Catalog {primitive} types must be a non-empty object"
                )
            for type_name, metadata in entries.items():
                if not isinstance(type_name, str) or not type_name.strip():
                    raise ValueError(
                        f"World Catalog {primitive} type names must be non-empty strings"
                    )
                if not isinstance(metadata, dict) or set(metadata) != {"roles"}:
                    raise ValueError(
                        f"World Catalog {primitive}.{type_name} must contain only roles"
                    )
                roles = metadata["roles"]
                if (
                    not isinstance(roles, list)
                    or not roles
                    or not all(isinstance(role, str) and role.strip() for role in roles)
                ):
                    raise ValueError(
                        f"World Catalog {primitive}.{type_name}.roles must be a non-empty string array"
                    )

    @property
    def version(self) -> str:
        return self.data["version"]

    def allowed_types(self, primitive: str) -> set[str]:
        entries: Any = self.data["types"].get(primitive, {})
        return set(entries)

    def pretty(self) -> str:
        return json.dumps(self.data, ensure_ascii=False, indent=2)
