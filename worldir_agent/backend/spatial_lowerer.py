from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from ..demo.models import WorldEvolutionStateV0
from ..schema import IRSpec, IRValidator
from .models import (
    BackendGap,
    GlobalSpatialPlanV0,
    HistoryInfluenceV0,
    PopulationRuleV0,
    RegionFieldV0,
    SpatialPlanGap,
    SpatialPlanOk,
    SpatialPlanRequest,
    SpatialPlanResult,
    SpatialRuntimeConfig,
    TrailNetworkV0,
)


POLICY_VERSION = "godot_artlab_spatial_v0.1"
ANCHOR_CENTERS: dict[str, tuple[float, float]] = {
    "west": (-96.0, 0.0), "east": (96.0, 0.0),
    "center": (0.0, 0.0), "whole": (0.0, 0.0),
    "north": (0.0, -96.0), "south": (0.0, 96.0),
    "northwest": (-96.0, -96.0), "northeast": (96.0, -96.0),
    "southwest": (-96.0, 96.0), "southeast": (96.0, 96.0),
}
DENSITY_RUNTIME_POLICY: dict[str, tuple[float, float]] = {
    "low": (1.45, 0.55), "medium": (1.0, 0.80), "high": (0.72, 1.0),
}


class SpatialLoweringInvalidIR(ValueError):
    def __init__(self, issues: list[str]):
        super().__init__("World IR failed the official V2 validator")
        self.issues = issues


class SpatialLowerer:
    """Pure deterministic World IR V2 -> Global Spatial Plan V0 lowering."""

    def __init__(self, spec: IRSpec):
        self.spec = spec
        self.validator = IRValidator(spec)

    @classmethod
    def default(cls) -> SpatialLowerer:
        root = Path(__file__).resolve().parents[2]
        return cls(IRSpec(root / "config/world_ir_v2.json"))

    def lower(self, request: SpatialPlanRequest) -> SpatialPlanResult:
        original = copy.deepcopy(request.world_ir)
        validation = self.validator.validate(request.world_ir)
        if not validation.valid:
            raise SpatialLoweringInvalidIR(validation.issues)

        gaps = self._backend_gaps(request.world_ir)
        if gaps:
            return SpatialPlanGap(
                status="backend_gap",
                gap=BackendGap(
                    kind="backend_gap",
                    paths=gaps,
                    reason="The legal World IR uses semantics not implemented by godot_artlab_v2.",
                ),
            )

        evolution = request.evolution_state or WorldEvolutionStateV0(
            version="0", history=[]
        )
        plan = GlobalSpatialPlanV0(
            version="0",
            backend_target=request.backend_target,
            backend_policy_version=POLICY_VERSION,
            plan_revision=evolution.revision,
            world_seed=request.world_seed,
            runtime_config=SpatialRuntimeConfig(
                default_logical_chunk_size_m=48.0,
                default_transition_band_m=16.0,
                environment_compile_distance_to_boundary_m=24.0,
                one_chunk_lookahead=True,
                neutral_profile="neutral_plain",
            ),
            regions=self._lower_regions(request.world_ir),
            networks=self._lower_networks(request.world_ir),
            distributions=self._lower_distributions(request.world_ir),
            future_policy=evolution.future_policy,
            history_influences=[
                HistoryInfluenceV0(
                    history_event_id=event.id,
                    kind="environment_overlay",
                    effect="snow",
                    target_profile="snow_forest",
                    scope="materialized_and_future_chunks",
                )
                for event in evolution.history
                if event.effect == "snow"
            ],
        )
        if request.world_ir != original:
            raise AssertionError("Spatial lowering mutated its World IR input")
        return SpatialPlanOk(status="ok", spatial_plan=plan)

    @staticmethod
    def _backend_gaps(ir: dict[str, Any]) -> list[str]:
        gaps: list[str] = []
        for index, region in enumerate(ir["regions"]):
            if region["type"] not in {"forest", "coast"}:
                gaps.append(f"regions[{index}].type={region['type']}")
            for relation in region.get("placement", {}).get("relations", []):
                if relation["type"] not in {"near", "direction_of"}:
                    gaps.append(f"regions[{index}].placement.relations.{relation['type']}")
        for index, network in enumerate(ir["networks"]):
            if network["type"] != "path":
                gaps.append(f"networks[{index}].type={network['type']}")
            for relation in network.get("placement", {}).get("relations", []):
                if relation["type"] != "inside":
                    gaps.append(f"networks[{index}].placement.relations.{relation['type']}")
        if ir["entities"]:
            gaps.append("entities")
        for index, distribution in enumerate(ir["distributions"]):
            if distribution["type"] != "tree":
                gaps.append(f"distributions[{index}].type={distribution['type']}")
            relations = distribution.get("placement", {}).get("relations", [])
            if not any(relation["type"] == "inside" for relation in relations):
                gaps.append(f"distributions[{index}].placement.inside")
            if any(relation["type"] != "inside" for relation in relations):
                gaps.append(f"distributions[{index}].placement.relations")
            population = distribution.get("population", {})
            if "density_profile" in population:
                gaps.append(f"distributions[{index}].population.density_profile")
        return sorted(set(gaps))

    @staticmethod
    def _lower_regions(ir: dict[str, Any]) -> list[RegionFieldV0]:
        coast_ids = {r["id"] for r in ir["regions"] if r["type"] == "coast"}
        fields: list[RegionFieldV0] = []
        for region in ir["regions"]:
            placement = region.get("placement", {})
            anchor = placement.get("anchor", "center")
            center = ANCHOR_CENTERS[anchor]
            relations = placement.get("relations", [])
            coastal = region["type"] == "forest" and any(
                rel["type"] == "near" and rel["target"] in coast_ids
                for rel in relations
            )
            fields.append(RegionFieldV0(
                id=f"field_{region['id']}", semantic_type=region["type"],
                source_ir_ref=region["id"], center_m=center,
                extent_m=(112.0, 96.0) if region["type"] == "forest" else (128.0, 112.0),
                falloff_m=32.0,
                profile_id="coastal_forest" if coastal or region["type"] == "forest" else "neutral_plain",
                placement_semantics=copy.deepcopy(placement),
            ))
        return fields

    @staticmethod
    def _lower_networks(ir: dict[str, Any]) -> list[TrailNetworkV0]:
        return [TrailNetworkV0(
            id=f"trail_{item['id']}", source_ir_ref=item["id"],
            kind="canonical_x_corridor", topology_from=item["topology"]["from"],
            topology_to=item["topology"]["to"], via=list(item["topology"].get("via", [])),
            z_center_m=0.0,
        ) for item in ir["networks"]]

    @staticmethod
    def _lower_distributions(ir: dict[str, Any]) -> list[PopulationRuleV0]:
        result: list[PopulationRuleV0] = []
        for item in ir["distributions"]:
            population = item.get("population", {})
            amount = population.get("amount", {"mode": "density", "value": "medium"})
            amount_value = amount["value"]
            density_key = str(amount_value) if amount["mode"] == "density" else "medium"
            area_scale, probability_scale = DENSITY_RUNTIME_POLICY[density_key]
            inside = next(
                rel["target"] for rel in item.get("placement", {}).get("relations", [])
                if rel["type"] == "inside"
            )
            result.append(PopulationRuleV0(
                id=f"population_{item['id']}", source_ir_ref=item["id"],
                semantic_type="tree", inside_region_ref=inside,
                amount_mode=amount["mode"], amount_value=amount_value,
                arrangement=population.get("arrangement", {}).get("type", "unspecified"),
                candidate_area_scale=area_scale,
                acceptance_probability_scale=probability_scale,
            ))
        return result
