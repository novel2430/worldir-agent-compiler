from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field

from ..demo.models import (
    FuturePolicyV0,
    PersistentClimateHistoryV0,
    WorldEvolutionStateV0,
)
from ..runtime.models import StrictContractModel


BackendTarget = Literal["godot_artlab_v2"]


class SpatialPlanRequest(StrictContractModel):
    world_ir: dict[str, Any]
    world_seed: int
    backend_target: BackendTarget
    evolution_state: WorldEvolutionStateV0 | None = None


class SpatialRuntimeConfig(StrictContractModel):
    default_logical_chunk_size_m: float
    default_transition_band_m: float
    environment_compile_distance_to_boundary_m: float
    one_chunk_lookahead: bool
    neutral_profile: Literal["neutral_plain"]


class RegionFieldV0(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    semantic_type: Literal["forest", "coast"]
    source_ir_ref: Annotated[str, Field(min_length=1)]
    center_m: tuple[float, float]
    extent_m: tuple[float, float]
    falloff_m: float
    profile_id: Literal["coastal_forest", "neutral_plain"]
    placement_semantics: dict[str, Any]


class TrailNetworkV0(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    source_ir_ref: Annotated[str, Field(min_length=1)]
    kind: Literal["canonical_x_corridor"]
    topology_from: str
    topology_to: str
    via: list[str]
    z_center_m: float


class PopulationRuleV0(StrictContractModel):
    id: Annotated[str, Field(min_length=1)]
    source_ir_ref: Annotated[str, Field(min_length=1)]
    semantic_type: Literal["tree"]
    inside_region_ref: Annotated[str, Field(min_length=1)]
    amount_mode: Literal["density", "count"]
    amount_value: str | int
    arrangement: Literal["uniform", "random", "clustered", "unspecified"]
    candidate_area_scale: float
    acceptance_probability_scale: float


class HistoryInfluenceV0(StrictContractModel):
    history_event_id: Annotated[str, Field(min_length=1)]
    kind: Literal["environment_overlay"]
    effect: Literal["snow"]
    target_profile: Literal["snow_forest"]
    scope: Literal["materialized_and_future_chunks"]


class GlobalSpatialPlanV0(StrictContractModel):
    version: Literal["0"]
    backend_target: BackendTarget
    backend_policy_version: Literal["godot_artlab_spatial_v0.1"]
    plan_revision: Annotated[int, Field(ge=0)]
    world_seed: int
    runtime_config: SpatialRuntimeConfig
    regions: list[RegionFieldV0]
    networks: list[TrailNetworkV0]
    distributions: list[PopulationRuleV0]
    future_policy: FuturePolicyV0 | None
    history_influences: list[HistoryInfluenceV0]


class BackendGap(StrictContractModel):
    kind: Literal["backend_gap"]
    paths: list[str]
    reason: Annotated[str, Field(min_length=1)]


class SpatialPlanOk(StrictContractModel):
    status: Literal["ok"]
    spatial_plan: GlobalSpatialPlanV0


class SpatialPlanGap(StrictContractModel):
    status: Literal["backend_gap"]
    gap: BackendGap


SpatialPlanResult = Annotated[
    SpatialPlanOk | SpatialPlanGap,
    Field(discriminator="status"),
]
