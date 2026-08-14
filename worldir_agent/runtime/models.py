from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


Anchor = Literal[
    "north",
    "south",
    "east",
    "west",
    "center",
    "northwest",
    "northeast",
    "southwest",
    "southeast",
    "whole",
]


def _normalize_json_integer(value: Any) -> Any:
    """Accept JSON integer-valued floats without enabling general coercion."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


RuntimeCount = Annotated[
    int,
    Field(ge=0),
    BeforeValidator(_normalize_json_integer),
]


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SemanticLocation(StrictContractModel):
    # These fields may be omitted but, per the JSON Schema, explicit null is
    # not a valid value. Pydantic uses the default only for omitted fields.
    anchor: Anchor = None  # type: ignore[assignment]
    inside: Annotated[str, Field(min_length=1)] = None  # type: ignore[assignment]
    near: Annotated[str, Field(min_length=1)] = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def require_one_location_field(self) -> SemanticLocation:
        if self.anchor is None and self.inside is None and self.near is None:
            raise ValueError("semantic location must contain at least one field")
        return self


class AddedObject(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["added_object"]
    object_type: Annotated[str, Field(min_length=1)]
    location: SemanticLocation = None  # type: ignore[assignment]


class RemovedObject(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["removed_object"]
    object_type: Annotated[str, Field(min_length=1)]
    location: SemanticLocation = None  # type: ignore[assignment]


class ObjectState(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["object_state"]
    target: Annotated[str, Field(min_length=1)]
    state: Annotated[str, Field(min_length=1)]


class MarkedArea(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["marked_area"]
    mark: Literal["cleared", "burned"]
    location: SemanticLocation
    affected_type: Annotated[str, Field(min_length=1)] = None  # type: ignore[assignment]
    count: RuntimeCount = None  # type: ignore[assignment]


RuntimeFact = Annotated[
    AddedObject | RemovedObject | ObjectState | MarkedArea,
    Field(discriminator="kind"),
]


class RuntimeContext(StrictContractModel):
    version: Literal["1"]
    facts: list[RuntimeFact]

    @model_validator(mode="after")
    def require_unique_fact_ids(self) -> RuntimeContext:
        seen: set[str] = set()
        duplicates: set[str] = set()
        for fact in self.facts:
            if fact.id in seen:
                duplicates.add(fact.id)
            seen.add(fact.id)
        if duplicates:
            duplicate_list = ", ".join(sorted(duplicates))
            raise ValueError(f"runtime fact ids must be unique: {duplicate_list}")
        return self

    @property
    def fact_ids(self) -> set[str]:
        return {fact.id for fact in self.facts}


class RuntimeBinding(StrictContractModel):
    ir_object_id: Annotated[str, Field(min_length=1)]
    runtime_fact_id: Annotated[str, Field(min_length=1)]
    placement: Literal["at", "inside", "near"]


class RuntimeFactOp(StrictContractModel):
    op: Literal["clear"]
    runtime_fact_id: Annotated[str, Field(min_length=1)]


class CompileDraft(StrictContractModel):
    world_ir: dict[str, Any]
    runtime_bindings: list[RuntimeBinding]
    runtime_fact_ops: list[RuntimeFactOp]
