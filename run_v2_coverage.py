from __future__ import annotations

import argparse
import copy
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from worldir_agent.config import load_config
from worldir_agent.json_utils import pretty_json
from worldir_agent.llm import HTTPJSONLLM
from worldir_agent.prompts import PromptStore
from worldir_agent.schema import IRSpec, IRValidator
from worldir_agent.workflow import WorldIRWorkflow, WorkflowError


Json = dict[str, Any]
CheckFn = Callable[[Json, Json | None, Json], tuple[list[str], list[str]]]


@dataclass(slots=True)
class CoverageCase:
    case_id: str
    title: str
    prompt: str
    expected_status: str
    expected_route: str | None
    check: CheckFn


def find_objects(ir: Json, collection: str, *, obj_type: str | None = None) -> list[Json]:
    items = ir.get(collection, [])
    if not isinstance(items, list):
        return []
    result = [x for x in items if isinstance(x, dict)]
    if obj_type is not None:
        result = [x for x in result if x.get("type") == obj_type]
    return result


def first_object(ir: Json, collection: str, obj_type: str) -> Json | None:
    items = find_objects(ir, collection, obj_type=obj_type)
    return items[0] if items else None


def relation_exists(
    obj: Json | None,
    relation_type: str,
    *,
    target: str | None = None,
    direction: str | None = None,
) -> bool:
    if not isinstance(obj, dict):
        return False
    placement = obj.get("placement")
    if not isinstance(placement, dict):
        return False
    relations = placement.get("relations", [])
    if not isinstance(relations, list):
        return False
    for rel in relations:
        if not isinstance(rel, dict) or rel.get("type") != relation_type:
            continue
        if target is not None and rel.get("target") != target:
            continue
        if direction is not None and rel.get("direction") != direction:
            continue
        return True
    return False


def placement_anchor(obj: Json | None) -> str | None:
    if not isinstance(obj, dict):
        return None
    placement = obj.get("placement")
    if not isinstance(placement, dict):
        return None
    value = placement.get("anchor")
    return value if isinstance(value, str) else None


def amount(obj: Json | None) -> Json | None:
    if not isinstance(obj, dict):
        return None
    population = obj.get("population")
    if not isinstance(population, dict):
        return None
    value = population.get("amount")
    return value if isinstance(value, dict) else None


def arrangement_type(obj: Json | None) -> str | None:
    if not isinstance(obj, dict):
        return None
    population = obj.get("population")
    if not isinstance(population, dict):
        return None
    arrangement = population.get("arrangement")
    if not isinstance(arrangement, dict):
        return None
    value = arrangement.get("type")
    return value if isinstance(value, str) else None


def density_profile(obj: Json | None) -> Json | None:
    if not isinstance(obj, dict):
        return None
    population = obj.get("population")
    if not isinstance(population, dict):
        return None
    profile = population.get("density_profile")
    return profile if isinstance(profile, dict) else None


def baseline_ids(ir: Json) -> Json:
    forest = first_object(ir, "regions", "forest")
    coast = first_object(ir, "regions", "coast")
    road = first_object(ir, "networks", "road")
    church = first_object(ir, "entities", "church")
    houses = first_object(ir, "distributions", "house")
    trees = first_object(ir, "distributions", "tree")
    return {
        "forest": forest.get("id") if forest else None,
        "coast": coast.get("id") if coast else None,
        "road": road.get("id") if road else None,
        "church": church.get("id") if church else None,
        "houses": houses.get("id") if houses else None,
        "trees": trees.get("id") if trees else None,
    }


def baseline_checks(ir: Json) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    ids = baseline_ids(ir)
    for key, value in ids.items():
        if not value:
            failures.append(f"baseline missing expected semantic object: {key}")

    forest = first_object(ir, "regions", "forest")
    coast = first_object(ir, "regions", "coast")
    road = first_object(ir, "networks", "road")
    church = first_object(ir, "entities", "church")
    houses = first_object(ir, "distributions", "house")
    trees = first_object(ir, "distributions", "tree")

    if forest and placement_anchor(forest) != "west":
        failures.append("forest should be anchored west")
    if coast and placement_anchor(coast) != "east":
        failures.append("coast should be anchored east")
    if road:
        topology = road.get("topology")
        if not isinstance(topology, dict) or topology.get("from") != "south" or topology.get("to") != "north":
            failures.append("road topology should be south -> north")
    if church and ids["road"]:
        if placement_anchor(church) != "north":
            failures.append("church should be anchored north")
        if not relation_exists(church, "near", target=ids["road"]):
            failures.append("church should be near road")
    if houses and ids["road"] and not relation_exists(houses, "along", target=ids["road"]):
        failures.append("houses should be along road")
    if trees and ids["forest"]:
        if not relation_exists(trees, "inside", target=ids["forest"]):
            failures.append("trees should be inside forest")
        tree_amount = amount(trees)
        if tree_amount != {"mode": "density", "value": "high"}:
            warnings.append(f"baseline trees amount is not density=high: {tree_amount!r}")

    return failures, warnings



def check_simple_edit(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    church = first_object(output, "entities", "church")
    houses = first_object(output, "distributions", "house")
    if placement_anchor(church) != "northwest":
        failures.append(f"church anchor should be northwest, got {placement_anchor(church)!r}")
    if not relation_exists(church, "near", target=ids["road"]):
        failures.append("church lost near(road) during simple anchor edit")
    if amount(houses) != {"mode": "count", "value": 20}:
        failures.append(f"houses amount should be count=20, got {amount(houses)!r}")
    return failures, []


def check_density_edit(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    if output is None:
        return ["no final IR"], []
    trees = first_object(output, "distributions", "tree")
    if amount(trees) != {"mode": "density", "value": "low"}:
        return [f"trees amount should be density=low, got {amount(trees)!r}"], []
    return [], []

def check_village_south(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    villages = find_objects(output, "regions", obj_type="village")
    if not villages:
        failures.append("no village Region was added")
    elif not relation_exists(villages[0], "direction_of", target=ids["forest"], direction="south"):
        failures.append("village lacks direction_of(forest, south)")
    return failures, []


def check_graveyard(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    """Check the requested semantic shape without hard-coding English nouns."""
    failures: list[str] = []
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []

    baseline_region_ids = {x.get("id") for x in find_objects(baseline, "regions")}
    added_regions = [
        x for x in find_objects(output, "regions")
        if x.get("id") not in baseline_region_ids
        and relation_exists(x, "near", target=ids["church"])
    ]
    if not added_regions:
        failures.append("no newly added Region near(church) was found")
        return failures, []

    region_ids = {x.get("id") for x in added_regions if isinstance(x.get("id"), str)}
    baseline_dist_ids = {x.get("id") for x in find_objects(baseline, "distributions")}
    nested = [
        x for x in find_objects(output, "distributions")
        if x.get("id") not in baseline_dist_ids
        and any(relation_exists(x, "inside", target=region_id) for region_id in region_ids)
    ]
    if not nested:
        failures.append("no newly added Distribution inside the new near-church Region was found")
        return failures, []

    if not any(amount(x) == {"mode": "density", "value": "low"} for x in nested):
        failures.append("the nested Distribution should encode sparse/low density")
    return failures, []


def check_houses_far(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    houses = first_object(output, "distributions", "house")
    church = first_object(output, "entities", "church")
    if not relation_exists(houses, "along", target=ids["road"]):
        failures.append("houses lost along(road)")
    if not relation_exists(houses, "far_from", target=ids["church"]):
        failures.append("houses lack far_from(church)")
    if placement_anchor(church) != "north":
        failures.append("church lost north anchor")
    if not relation_exists(church, "near", target=ids["road"]):
        failures.append("church lost near(road)")
    return failures, []


def check_composition(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    trees = find_objects(output, "distributions", obj_type="tree")
    primary = next((x for x in trees if x.get("id") == ids["trees"]), None)
    if primary is None:
        failures.append("original forest tree Distribution was removed")
    elif not relation_exists(primary, "inside", target=ids["forest"]):
        failures.append("original forest trees lost inside(forest)")

    extras = [x for x in trees if x.get("id") != ids["trees"] and relation_exists(x, "near", target=ids["road"])]
    if not extras:
        failures.append("no second tree Distribution near road was added")
    else:
        extra_amount = amount(extras[0])
        low_or_small = (
            extra_amount == {"mode": "density", "value": "low"}
            or (
                isinstance(extra_amount, dict)
                and extra_amount.get("mode") == "count"
                and isinstance(extra_amount.get("value"), int)
                and 0 <= extra_amount["value"] <= 20
            )
        )
        if not low_or_small:
            warnings.append(f"near-road trees do not obviously encode a small amount: {extra_amount!r}")
    return failures, warnings


def check_clustered(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    if output is None:
        return ["no final IR"], []
    trees = first_object(output, "distributions", "tree")
    if arrangement_type(trees) != "clustered":
        return [f"trees arrangement should be clustered, got {arrangement_type(trees)!r}"], []
    return [], []


def gradient_shape(profile: Json | None, road_id: str | None) -> tuple[bool, str]:
    if not isinstance(profile, dict) or profile.get("type") != "gradient":
        return False, f"density_profile is not gradient: {profile!r}"
    endpoints = [profile.get("from"), profile.get("to")]
    near_road_low = False
    west_high = False
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        selector = endpoint.get("selector")
        density = endpoint.get("density")
        if not isinstance(selector, dict):
            continue
        if selector.get("type") == "near" and selector.get("target") == road_id and density == "low":
            near_road_low = True
        if selector.get("type") == "anchor" and selector.get("value") == "west" and density == "high":
            west_high = True
    if not near_road_low:
        return False, "gradient lacks low-density endpoint near road"
    if not west_high:
        return False, "gradient lacks high-density endpoint at west anchor"
    return True, ""


def check_gradient(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    trees = first_object(output, "distributions", "tree")
    ok, reason = gradient_shape(density_profile(trees), ids["road"])
    if not ok:
        failures.append(reason)
    tree_amount = amount(trees)
    if isinstance(tree_amount, dict) and tree_amount.get("mode") == "density":
        failures.append(
            "trees must not keep amount.mode=density together with density_profile; the profile replaces the prior qualitative density specification"
        )
    return failures, warnings


def check_natural(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    if output is None:
        return ["no final IR"], []
    trees = first_object(output, "distributions", "tree")
    arrangement = arrangement_type(trees)
    if arrangement not in {"random", "clustered"}:
        return [f"abstract natural forest should lower to random or clustered arrangement, got {arrangement!r}"], []
    return [], []


def check_topology_via(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    road = first_object(output, "networks", "road")
    topology = road.get("topology") if isinstance(road, dict) else None
    via = topology.get("via", []) if isinstance(topology, dict) else []
    if ids["church"] not in via:
        return [f"road topology.via should contain church id {ids['church']!r}, got {via!r}"], []
    return [], []


def check_delete_dependency(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    ids = baseline_ids(baseline)
    if output is None:
        return ["no final IR"], []
    failures: list[str] = []
    if any(x.get("id") == ids["forest"] for x in find_objects(output, "regions")):
        failures.append("forest was not deleted")
    for dist in find_objects(output, "distributions"):
        if relation_exists(dist, "inside", target=ids["forest"]):
            failures.append(f"distribution {dist.get('id')!r} still depends on deleted forest")
    return failures, []


def check_gap(_baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    if output is not None:
        return ["expected IR GAP but got a final IR"], []
    express = detail.get("expressibility") if isinstance(detail, dict) else None
    if not isinstance(express, dict) or express.get("expressible") is not False:
        return ["IR GAP result lacks expressibility.expressible=false"], []
    unsupported = express.get("unsupported")
    if not isinstance(unsupported, list) or not unsupported:
        return ["IR GAP should explain at least one unsupported semantic"], []
    return [], []


def check_clustered_gradient(baseline: Json, output: Json | None, detail: Json) -> tuple[list[str], list[str]]:
    failures, warnings = check_gradient(baseline, output, detail)
    if output is None:
        return failures, warnings
    trees = first_object(output, "distributions", "tree")
    if arrangement_type(trees) != "clustered":
        failures.append(f"trees arrangement should also be clustered, got {arrangement_type(trees)!r}")
    return failures, warnings


def build_cases() -> list[CoverageCase]:
    return [
        CoverageCase(
            "B01",
            "simple placement + amount edit",
            "把教堂从北边移到西北边，房屋增加到 20 栋，其他内容保持不变。",
            "ok",
            "bypass",
            check_simple_edit,
        ),
        CoverageCase(
            "B02",
            "simple qualitative density edit",
            "把森林里的树木从高密度改成低密度，其他内容保持不变。",
            "ok",
            "bypass",
            check_density_edit,
        ),
        CoverageCase(
            "R01",
            "relative Region placement",
            "在森林南边增加一个小村庄，其他内容保持不变。",
            "ok",
            "bypass",
            check_village_south,
        ),
        CoverageCase(
            "R02",
            "Region near Entity + nested Distribution",
            "在教堂附近增加一个墓地，墓地作为一个区域，里面稀疏分布墓碑。其他内容保持不变。",
            "ok",
            "bypass",
            check_graveyard,
        ),
        CoverageCase(
            "R03",
            "negative spatial relation / preservation",
            "让教堂成为一个孤立的地标。它仍然位于北边并靠近主路，但住宅不要太靠近教堂，其他主要结构保持不变。",
            "ok",
            "bypass",
            check_houses_far,
        ),
        CoverageCase(
            "P01",
            "multi-Distribution composition",
            "保留森林内部高密度树木，同时在主路附近零散出现少量树木，其他内容保持不变。",
            "ok",
            "bypass",
            check_composition,
        ),
        CoverageCase(
            "P02",
            "clustered arrangement",
            "让森林里的树木明显聚成几团，每团之间留出较大的空隙，而不是均匀散布。其他内容保持不变。",
            "ok",
            "bypass",
            check_clustered,
        ),
        CoverageCase(
            "P03",
            "continuous density gradient",
            "森林里的树木在靠近主路的一侧比较稀疏，越往森林西侧越密，形成连续的密度梯度。其他内容保持不变。",
            "ok",
            "bypass",
            check_gradient,
        ),
        CoverageCase(
            "P04",
            "abstract natural arrangement",
            "让森林看起来不像人工均匀种植的，而像自然生长出来的。其他主要结构保持不变。",
            "ok",
            "deliberate",
            check_natural,
        ),
        CoverageCase(
            "X01",
            "orthogonality: clustered + gradient",
            "让森林里的树木明显成团，同时靠近主路的一侧比较稀疏，越往森林西侧越密，形成连续梯度。其他结构保持不变。",
            "ok",
            "bypass",
            check_clustered_gradient,
        ),
        CoverageCase(
            "N01",
            "Network topology via",
            "让主路先经过教堂，再继续通向北边，其他内容保持不变。",
            "ok",
            "bypass",
            check_topology_via,
        ),
        CoverageCase(
            "S01",
            "state deletion + dependency cleanup",
            "删除森林，同时删除因为森林不存在而无法继续成立的树木分布。其他内容保持不变。",
            "ok",
            "bypass",
            check_delete_dependency,
        ),
        CoverageCase(
            "G01",
            "intentional IR GAP: numeric distance",
            "让所有住宅距离教堂至少 50 米，并且必须保留这个精确的最小距离约束。其他内容保持不变。",
            "ir_gap",
            "bypass",
            check_gap,
        ),
        CoverageCase(
            "G02",
            "intentional IR GAP: between relation",
            "在森林和海岸之间增加一个观景台，并且必须保留‘位于森林与海岸两者之间’这一空间关系。其他内容保持不变。",
            "ir_gap",
            "bypass",
            check_gap,
        ),
    ]


def compact_trace(trace_dict: Json) -> list[Json]:
    result: list[Json] = []
    for event in trace_dict.get("events", []):
        if not isinstance(event, dict):
            continue
        node = str(event.get("node", ""))
        # Keep only outputs that help diagnose routing / planning / lowering.
        if node.startswith(("router", "planner", "expressibility", "editor", "semantic_judge")):
            result.append(
                {
                    "node": node,
                    "attempt": event.get("attempt"),
                    "parsed_response": event.get("parsed_response"),
                }
            )
    return result


def run_validator_probes(spec: IRSpec, fixture_path: Path) -> list[Json]:
    validator = IRValidator(spec)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    probes: list[tuple[str, bool, Json]] = []

    probes.append(("valid_v2_fixture", True, copy.deepcopy(fixture)))

    legacy_location = copy.deepcopy(fixture)
    legacy_location["regions"][0]["location"] = "east"
    probes.append(("reject_legacy_flat_location", False, legacy_location))

    bad_arrangement = copy.deepcopy(fixture)
    tree = first_object(bad_arrangement, "distributions", "tree")
    tree.setdefault("population", {})["arrangement"] = {"type": "natural"}
    probes.append(("reject_unknown_arrangement", False, bad_arrangement))

    bad_amount = copy.deepcopy(fixture)
    house = first_object(bad_amount, "distributions", "house")
    house.setdefault("population", {})["amount"] = {
        "mode": "count",
        "value": 12,
        "density": "low",
    }
    probes.append(("reject_mixed_amount_fields", False, bad_amount))

    bad_relation = copy.deepcopy(fixture)
    church = first_object(bad_relation, "entities", "church")
    church.setdefault("placement", {}).setdefault("relations", []).append(
        {"type": "direction_of", "target": baseline_ids(bad_relation)["forest"]}
    )
    probes.append(("reject_direction_without_direction", False, bad_relation))

    bad_gradient = copy.deepcopy(fixture)
    tree = first_object(bad_gradient, "distributions", "tree")
    tree.setdefault("population", {})["density_profile"] = {
        "type": "gradient",
        "from": {"density": "low"},
        "to": {
            "selector": {"type": "anchor", "value": "west"},
            "density": "high",
        },
    }
    probes.append(("reject_gradient_endpoint_without_selector", False, bad_gradient))

    conflicting_density = copy.deepcopy(fixture)
    tree = first_object(conflicting_density, "distributions", "tree")
    tree.setdefault("population", {})["density_profile"] = {
        "type": "gradient",
        "from": {
            "selector": {"type": "near", "target": baseline_ids(conflicting_density)["road"]},
            "density": "low",
        },
        "to": {
            "selector": {"type": "anchor", "value": "west"},
            "density": "high",
        },
    }
    probes.append(("reject_global_density_plus_density_profile", False, conflicting_density))

    bad_relation_source = copy.deepcopy(fixture)
    forest = first_object(bad_relation_source, "regions", "forest")
    forest.setdefault("placement", {}).setdefault("relations", []).append(
        {"type": "along", "target": baseline_ids(bad_relation_source)["road"]}
    )
    probes.append(("reject_relation_source_type_mismatch", False, bad_relation_source))

    unknown_ref = copy.deepcopy(fixture)
    church = first_object(unknown_ref, "entities", "church")
    church.setdefault("placement", {}).setdefault("relations", []).append(
        {"type": "near", "target": "missing_object"}
    )
    probes.append(("reject_unknown_relation_target", False, unknown_ref))

    results: list[Json] = []
    for name, expected_valid, candidate in probes:
        result = validator.validate(candidate)
        results.append(
            {
                "name": name,
                "expected_valid": expected_valid,
                "actual_valid": result.valid,
                "passed": result.valid == expected_valid,
                "issues": result.issues,
            }
        )
    return results


def extract_route(detail: Json, trace_dict: Json) -> str | None:
    route = detail.get("route") if isinstance(detail, dict) else None
    if isinstance(route, str):
        return route
    for event in trace_dict.get("events", []):
        if not isinstance(event, dict) or event.get("node") != "router":
            continue
        parsed = event.get("parsed_response")
        if isinstance(parsed, dict) and isinstance(parsed.get("route"), str):
            return parsed["route"]
    return None


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(pretty_json(payload) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Run one broad end-to-end World IR V2 coverage suite against the configured LLM."
    )
    p.add_argument("--config", default="config/config.toml")
    p.add_argument("--schema", default="config/world_ir_v2.json")
    p.add_argument("--prompts-dir", default="prompts")
    p.add_argument("--initial-prompt-file", default="examples/prompt_initial.txt")
    p.add_argument(
        "--state",
        help="Use an existing V2 State0 instead of generating State0. Edit cases still all branch from this same state.",
    )
    p.add_argument(
        "--fixture",
        default="examples/state0_v2.json",
        help="Known-good V2 fixture used for deterministic probes and, by default, as the shared edit baseline.",
    )
    p.add_argument(
        "--out-dir",
        help="Directory for per-case result/trace files. Default: runs/v2_coverage_<timestamp>",
    )
    p.add_argument(
        "--only",
        help="Comma-separated case ids to run, e.g. P03,X01,G01. Omit to run all LLM cases.",
    )
    p.add_argument("--skip-validator-probes", action="store_true")
    return p


def main() -> int:
    args = build_parser().parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"runs/v2_coverage_{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)

    config = load_config(args.config)
    spec = IRSpec(args.schema)
    workflow = WorldIRWorkflow(
        llm=HTTPJSONLLM(config.llm),
        prompts=PromptStore(args.prompts_dir),
        spec=spec,
        config=config.workflow,
        route_override="auto",
    )

    report: Json = {
        "suite": "World IR V2 broad coverage",
        "schema": args.schema,
        "model": {
            "provider": config.llm.provider,
            "model": config.llm.model,
            "temperature": config.llm.temperature,
            "max_tokens": config.llm.max_tokens,
            "thinking": config.llm.thinking,
        },
        "workflow": {
            "use_planner_checker": config.workflow.use_planner_checker,
            "planner_max_attempts": config.workflow.planner_max_attempts,
            "editor_max_attempts": config.workflow.editor_max_attempts,
            "json_repair_max_attempts": config.workflow.json_repair_max_attempts,
        },
        "out_dir": str(out_dir),
        "validator_probes": [],
        "initial": {},
        "baseline": {},
        "cases": [],
    }

    print("=" * 88)
    print("World IR V2 broad coverage suite")
    print(f"schema: {args.schema}")
    print(f"model : {config.llm.provider} / {config.llm.model}")
    print(f"planner_checker: {config.workflow.use_planner_checker}")
    print(f"artifacts: {out_dir}")
    print("=" * 88)

    if not args.skip_validator_probes:
        print("\n[STATIC] deterministic validator probes")
        probes = run_validator_probes(spec, Path(args.fixture))
        report["validator_probes"] = probes
        for probe in probes:
            mark = "PASS" if probe["passed"] else "FAIL"
            print(
                f"  [{mark}] {probe['name']}: expected_valid={probe['expected_valid']} "
                f"actual_valid={probe['actual_valid']}"
            )
            if not probe["passed"]:
                for issue in probe["issues"]:
                    print(f"         issue: {issue}")

    # Initial generation is a test target, not the source of truth for the edit suite.
    # A semantically imperfect but schema-valid initial translation must not prevent
    # us from exercising the rest of the compiler. By default all edit cases branch
    # from the canonical V2 fixture. --state explicitly overrides that baseline.
    print("\n[INITIAL] testing initial translator")
    if args.state:
        report["initial"] = {
            "status": "skipped",
            "reason": "--state supplied; initial generation skipped",
        }
        print("  [SKIP] --state supplied; initial generation was not called")
    else:
        prompt = Path(args.initial_prompt_file).read_text(encoding="utf-8").strip()
        try:
            initial_result = workflow.run(prompt, None)
            initial_status = initial_result.status
            initial_ir = initial_result.ir
            initial_trace = initial_result.trace.to_dict()
            initial_detail = initial_result.detail
            if isinstance(initial_ir, dict):
                initial_failures, initial_warnings = baseline_checks(initial_ir)
            else:
                initial_failures = ["initial translator returned no usable IR"]
                initial_warnings = []
            report["initial"] = {
                "status": initial_status,
                "passed": initial_status == "ok" and not initial_failures,
                "checks": {
                    "failures": initial_failures,
                    "warnings": initial_warnings,
                },
                "detail": initial_detail,
                "ir": initial_ir,
                "trace_summary": compact_trace(initial_trace),
            }
            if isinstance(initial_ir, dict):
                write_json(out_dir / "generated_state0_v2.json", initial_ir)
            write_json(out_dir / "generated_state0_v2.trace.json", initial_trace)
            mark = "PASS" if report["initial"]["passed"] else "FAIL"
            print(f"  [{mark}] initial translation status={initial_status}")
            for failure in initial_failures:
                print(f"         failure: {failure}")
            for warning in initial_warnings:
                print(f"         warning: {warning}")
            if initial_failures:
                print("         note: initial failure is non-fatal; edit coverage will use the canonical baseline")
        except Exception as exc:
            trace = workflow.last_trace.to_dict() if workflow.last_trace is not None else {"mode": "initial", "events": []}
            report["initial"] = {
                "status": "exception",
                "passed": False,
                "error": f"{type(exc).__name__}: {exc}",
                "trace_summary": compact_trace(trace),
            }
            write_json(out_dir / "generated_state0_v2.trace.json", trace)
            print(f"  [FAIL] initial generation raised {type(exc).__name__}: {exc}")
            print("         note: provider/initial failure is non-fatal; edit coverage will still run")

    print("\n[BASELINE] preparing one shared canonical edit baseline")
    baseline_path = Path(args.state) if args.state else Path(args.fixture)
    try:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    except Exception as exc:
        report["baseline"] = {
            "status": "exception",
            "source": str(baseline_path),
            "error": f"{type(exc).__name__}: {exc}",
        }
        write_json(out_dir / "report.json", report)
        print(f"  [FATAL] could not load edit baseline {baseline_path}: {exc}")
        print("\n===== COPYABLE_JSON_REPORT =====")
        print(pretty_json(report))
        print("===== END_COPYABLE_JSON_REPORT =====")
        return 2

    validation = IRValidator(spec).validate(baseline) if isinstance(baseline, dict) else None
    base_failures: list[str] = []
    base_warnings: list[str] = []
    if not isinstance(baseline, dict):
        base_failures.append("edit baseline is not a JSON object")
    elif validation is not None and not validation.valid:
        base_failures.extend(f"schema: {issue}" for issue in validation.issues)
    else:
        semantic_failures, semantic_warnings = baseline_checks(baseline)
        base_failures.extend(semantic_failures)
        base_warnings.extend(semantic_warnings)

    report["baseline"] = {
        "source": str(baseline_path),
        "passed": not base_failures,
        "checks": {
            "failures": base_failures,
            "warnings": base_warnings,
        },
        "ir": baseline if isinstance(baseline, dict) else None,
    }
    if isinstance(baseline, dict):
        write_json(out_dir / "edit_baseline_v2.json", baseline)

    mark = "PASS" if report["baseline"]["passed"] else "FAIL"
    print(f"  [{mark}] baseline source={baseline_path}")
    for failure in base_failures:
        print(f"         failure: {failure}")
    for warning in base_warnings:
        print(f"         warning: {warning}")

    if base_failures:
        print("  [FATAL] Canonical edit baseline is unusable; edit cases were not run.")
        write_json(out_dir / "report.json", report)
        print("\n===== COPYABLE_JSON_REPORT =====")
        print(pretty_json(report))
        print("===== END_COPYABLE_JSON_REPORT =====")
        return 2

    wanted = None
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}

    print("\n[LLM] independent edit cases (every case starts from the same State0)")
    for case in build_cases():
        if wanted is not None and case.case_id not in wanted:
            continue
        print(f"\n  [{case.case_id}] {case.title}")
        print(f"       prompt: {case.prompt}")
        print(f"       expect: status={case.expected_status}, route={case.expected_route}")

        case_ir = copy.deepcopy(baseline)
        try:
            result = workflow.run(case.prompt, case_ir)
            status = result.status
            output_ir = result.ir
            detail = result.detail
            trace_dict = result.trace.to_dict()
            error_text = None
        except (WorkflowError, RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
            status = "exception"
            output_ir = None
            detail = {}
            trace_dict = workflow.last_trace.to_dict() if workflow.last_trace is not None else {"mode": "edit", "events": []}
            error_text = f"{type(exc).__name__}: {exc}"

        route = extract_route(detail, trace_dict)
        semantic_failures, semantic_warnings = case.check(baseline, output_ir, detail)
        expectation_failures: list[str] = []
        if status != case.expected_status:
            expectation_failures.append(
                f"status expected {case.expected_status!r}, got {status!r}"
            )
        if case.expected_route is not None and route != case.expected_route:
            expectation_failures.append(
                f"route expected {case.expected_route!r}, got {route!r}"
            )

        failures = expectation_failures + semantic_failures
        passed = not failures
        case_report: Json = {
            "id": case.case_id,
            "title": case.title,
            "prompt": case.prompt,
            "expected": {
                "status": case.expected_status,
                "route": case.expected_route,
            },
            "actual": {
                "status": status,
                "route": route,
            },
            "passed": passed,
            "checks": {
                "failures": failures,
                "warnings": semantic_warnings,
            },
            "detail": detail,
            "output": output_ir,
            "trace_summary": compact_trace(trace_dict),
        }
        if error_text:
            case_report["error"] = error_text

        report["cases"].append(case_report)
        write_json(out_dir / f"{case.case_id}.result.json", case_report)
        write_json(out_dir / f"{case.case_id}.trace.json", trace_dict)

        mark = "PASS" if passed else "FAIL"
        print(f"       actual: status={status}, route={route}  [{mark}]")
        for failure in failures:
            print(f"       failure: {failure}")
        for warning in semantic_warnings:
            print(f"       warning: {warning}")

    static_passed = sum(1 for x in report["validator_probes"] if x.get("passed"))
    static_total = len(report["validator_probes"])
    llm_passed = sum(1 for x in report["cases"] if x.get("passed"))
    llm_total = len(report["cases"])
    warnings_total = sum(len(x.get("checks", {}).get("warnings", [])) for x in report["cases"])
    report["summary"] = {
        "validator_probes": {"passed": static_passed, "total": static_total},
        "initial_passed": bool(report["initial"].get("passed")),
        "baseline_passed": bool(report["baseline"].get("passed")),
        "llm_cases": {"passed": llm_passed, "total": llm_total},
        "warnings": warnings_total,
        "overall_passed": (
            (static_total == 0 or static_passed == static_total)
            and (report["initial"].get("status") == "skipped" or bool(report["initial"].get("passed")))
            and bool(report["baseline"].get("passed"))
            and llm_passed == llm_total
        ),
    }
    write_json(out_dir / "report.json", report)

    print("\n" + "=" * 88)
    print(
        f"SUMMARY: validator={static_passed}/{static_total or 0}, "
        f"initial={'PASS' if report['initial'].get('passed') else ('SKIP' if report['initial'].get('status') == 'skipped' else 'FAIL')}, "
        f"baseline={'PASS' if report['baseline'].get('passed') else 'FAIL'}, "
        f"LLM cases={llm_passed}/{llm_total}, warnings={warnings_total}"
    )
    print(f"full artifacts: {out_dir}")
    print("=" * 88)

    # This is intentionally the last stdout block so the whole experiment can
    # be pasted back into a chat without copying giant prompt/raw-response traces.
    print("\n===== COPYABLE_JSON_REPORT =====")
    print(pretty_json(report))
    print("===== END_COPYABLE_JSON_REPORT =====")

    return 0 if report["summary"]["overall_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
