from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ..runtime.models import StrictContractModel


class FuturePolicyV0(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["environment_override"]
    target_profile: Literal["research_base"]
    scope: Literal["future_unresolved_chunks"]
    created_at_frontier: Annotated[list[int], Field(min_length=2, max_length=2)]
    sequence: Annotated[int, Field(ge=1)]
    revision: Annotated[int, Field(ge=1)]


class PersistentClimateHistoryV0(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    kind: Literal["persistent_climate"]
    effect: Literal["snow"]
    scope: Literal["global"]
    since_years_ago: Annotated[int, Field(ge=0)]
    sequence: Annotated[int, Field(ge=1)]


class WorldEvolutionStateV0(StrictContractModel):
    """Demo/experimental contract; deliberately not part of World IR V2."""

    version: Literal["0"]
    contract_status: Literal["demo_experimental_not_world_ir_v2"] = (
        "demo_experimental_not_world_ir_v2"
    )
    future_policy: FuturePolicyV0 | None = None
    history: list[PersistentClimateHistoryV0]

    @model_validator(mode="after")
    def require_unique_monotonic_sequences(self) -> WorldEvolutionStateV0:
        sequences = [event.sequence for event in self.history]
        if self.future_policy is not None:
            sequences.append(self.future_policy.sequence)
        if len(sequences) != len(set(sequences)):
            raise ValueError("evolution sequences must be unique")
        return self

    @property
    def revision(self) -> int:
        values = [event.sequence for event in self.history]
        if self.future_policy is not None:
            values.append(self.future_policy.sequence)
        return max(values, default=0)
