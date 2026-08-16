from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
from pathlib import Path
from typing import Any

from worldir_agent.config import load_config
from worldir_agent.json_utils import pretty_json
from worldir_agent.llm import HTTPJSONLLM
from worldir_agent.prompts import PromptStore
from worldir_agent.schema import IRSpec, IRValidator
from worldir_agent.workflow import WorldIRWorkflow


Json = dict[str, Any]
IR_GAP_CASES = {
    "initial_desert_is_ir_gap",
    "initial_graveyard_is_ir_gap",
    "initial_medieval_town_is_ir_gap",
    "initial_non_snow_pine_forest_gap",
    "add_rowboat_to_snow_forest_is_ir_gap",
    "explicit_highway_is_ir_gap",
    "explicit_ship_is_ir_gap",
}


def load_case_state(case: Json, suite_path: Path) -> Json | None:
    if "current_ir_file" in case:
        path = (suite_path.parent / case["current_ir_file"]).resolve()
        return json.loads(path.read_text(encoding="utf-8"))
    value = case.get("current_ir")
    return copy.deepcopy(value) if isinstance(value, dict) else None


def inside_target(obj: Json) -> str | None:
    relations = obj.get("placement", {}).get("relations", [])
    owners = [
        relation.get("target")
        for relation in relations
        if isinstance(relation, dict) and relation.get("type") == "inside"
    ]
    return owners[0] if len(owners) == 1 and isinstance(owners[0], str) else None


def amount(obj: Json) -> Json | None:
    value = obj.get("population", {}).get("amount")
    return value if isinstance(value, dict) else None


def minimal_world(region_type: str, primitive: str, object_type: str) -> Json:
    collection = "entities" if primitive == "Entity" else "distributions"
    world: Json = {
        "regions": [{"id": "owner", "type": region_type}],
        "networks": [],
        "entities": [],
        "distributions": [],
    }
    world[collection].append({
        "id": "subject",
        "type": object_type,
        "placement": {
            "relations": [{"type": "inside", "target": "owner"}]
        },
    })
    return world


def run_validator_probes(spec: IRSpec, fixture_path: Path) -> list[Json]:
    validator = IRValidator(spec)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    probes: list[tuple[str, bool, Json]] = [
        ("valid_v2_fixture", True, fixture),
        (
            "rowboat_inside_coastal_forest_valid",
            True,
            minimal_world("coastal_forest", "Entity", "rowboat"),
        ),
        (
            "rowboat_inside_snow_forest_invalid",
            False,
            minimal_world("snow_forest", "Entity", "rowboat"),
        ),
        (
            "research_station_inside_research_base_valid",
            True,
            minimal_world("research_base", "Entity", "research_station"),
        ),
        (
            "research_station_inside_coastal_forest_invalid",
            False,
            minimal_world("coastal_forest", "Entity", "research_station"),
        ),
        (
            "grass_inside_snow_forest_invalid",
            False,
            minimal_world("snow_forest", "Distribution", "grass"),
        ),
        (
            "cabin_inside_coastal_forest_valid",
            True,
            minimal_world("coastal_forest", "Entity", "cabin"),
        ),
        (
            "cabin_inside_snow_forest_valid",
            True,
            minimal_world("snow_forest", "Entity", "cabin"),
        ),
    ]

    missing_owner = minimal_world("coastal_forest", "Entity", "tent")
    missing_owner["entities"][0].pop("placement")
    probes.append(("entity_requires_region_owner", False, missing_owner))

    two_owners = minimal_world("coastal_forest", "Distribution", "tree")
    two_owners["regions"].append({"id": "owner_2", "type": "coastal_forest"})
    two_owners["distributions"][0]["placement"]["relations"].append(
        {"type": "inside", "target": "owner_2"}
    )
    probes.append(("distribution_rejects_two_owners", False, two_owners))

    nested = {
        "regions": [
            {"id": "outer", "type": "coastal_forest"},
            {
                "id": "inner",
                "type": "snow_forest",
                "placement": {
                    "relations": [{"type": "inside", "target": "outer"}]
                },
            },
        ],
        "networks": [],
        "entities": [],
        "distributions": [],
    }
    probes.append(("region_nesting_invalid", False, nested))

    road = copy.deepcopy(fixture)
    road["networks"][0]["type"] = "road"
    probes.append(("network_type_is_catalog_controlled", False, road))

    results = []
    for name, expected_valid, candidate in probes:
        validation = validator.validate(candidate)
        results.append({
            "name": name,
            "expected_valid": expected_valid,
            "actual_valid": validation.valid,
            "passed": validation.valid == expected_valid,
            "issues": validation.issues,
        })
    return results


def find_type(ir: Json, collection: str, type_name: str) -> list[Json]:
    return [
        item for item in ir.get(collection, [])
        if isinstance(item, dict) and item.get("type") == type_name
    ]


def check_default_realization(ir: Json, region_type: str, spec: IRSpec) -> list[str]:
    failures: list[str] = []
    regions = find_type(ir, "regions", region_type)
    if len(regions) != 1:
        return [f"expected one {region_type} Region, got {len(regions)}"]
    region_id = regions[0].get("id")
    expected = spec.catalog.default_realization(region_type)
    for item in expected["entities"]:
        matches = find_type(ir, "entities", item["type"])
        if not matches:
            failures.append(f"missing default Entity {item['type']}")
        elif not any(inside_target(match) == region_id for match in matches):
            failures.append(f"default Entity {item['type']} lacks owner {region_id}")
    for item in expected["distributions"]:
        matches = find_type(ir, "distributions", item["type"])
        owned = [match for match in matches if inside_target(match) == region_id]
        if not owned:
            failures.append(f"missing owned default Distribution {item['type']}")
        elif amount(owned[0]) != item["population"]["amount"]:
            failures.append(
                f"{item['type']} amount expected {item['population']['amount']}, "
                f"got {amount(owned[0])}"
            )
    return failures


def check_live_case(case_id: str, state: Json | None, result: Any, spec: IRSpec) -> list[str]:
    expected_status = "ir_gap" if case_id in IR_GAP_CASES else "ok"
    failures = []
    if result.status != expected_status:
        failures.append(f"status expected {expected_status}, got {result.status}")
        return failures
    if result.status != "ok" or not isinstance(result.ir, dict):
        return failures

    validation = IRValidator(spec).validate(result.ir)
    if not validation.valid:
        failures.extend(f"invalid output: {issue}" for issue in validation.issues)
        return failures

    realization_cases = {
        "bare_forest_normalizes_to_coastal_forest": "coastal_forest",
        "snowy_forest_normalizes_to_snow_forest": "snow_forest",
        "research_facility_normalizes_to_research_base": "research_base",
        "coastal_forest_gets_catalog_realization": "coastal_forest",
        "research_base_gets_catalog_realization": "research_base",
        "snow_forest_gets_catalog_realization": "snow_forest",
    }
    if case_id in realization_cases:
        failures.extend(
            check_default_realization(result.ir, realization_cases[case_id], spec)
        )
    elif case_id == "snow_forest_without_cabin_respects_exception":
        if find_type(result.ir, "entities", "cabin"):
            failures.append("explicitly excluded cabin was generated")
    elif case_id == "snow_forest_sparse_trees_overrides_default_density":
        trees = find_type(result.ir, "distributions", "tree")
        if not trees or amount(trees[0]) != {"mode": "density", "value": "low"}:
            failures.append("tree density did not override to low")
    elif case_id == "generic_road_lowers_to_path":
        old_paths = len(find_type(state, "networks", "path"))
        paths = find_type(result.ir, "networks", "path")
        if len(paths) != old_paths + 1:
            failures.append("generic 路 did not add exactly one canonical path")
        elif not any(
            item.get("topology", {}).get("from") == "south"
            and item.get("topology", {}).get("to") == "north"
            for item in paths
        ):
            failures.append("new path did not preserve south-to-north topology")
    elif case_id in {
        "generic_boat_lowers_to_rowboats",
        "literary_boat_lowers_to_rowboat",
    }:
        expected_added = 5 if case_id == "generic_boat_lowers_to_rowboats" else 1
        old_boats = len(find_type(state, "entities", "rowboat"))
        boats = find_type(result.ir, "entities", "rowboat")
        if len(boats) != old_boats + expected_added:
            failures.append(
                f"generic boat wording expected {expected_added} new rowboat "
                f"Entities, got {len(boats) - old_boats}"
            )
        coastal_ids = {
            item.get("id") for item in find_type(result.ir, "regions", "coastal_forest")
        }
        if any(inside_target(item) not in coastal_ids for item in boats):
            failures.append("a lowered rowboat lacks a coastal_forest owner")
    elif case_id == "slight_density_reduction_uses_qualitative_step":
        coastal_ids = {
            item.get("id") for item in find_type(result.ir, "regions", "coastal_forest")
        }
        trees = [
            item for item in find_type(result.ir, "distributions", "tree")
            if inside_target(item) in coastal_ids
        ]
        if not trees or amount(trees[0]) != {"mode": "density", "value": "medium"}:
            failures.append("slight tree reduction did not lower high to medium")
    elif case_id == "replace_coastal_forest_with_snow_forest":
        old = next(item for item in state["regions"] if item["id"] == "coastal_forest")
        new = next(
            (item for item in result.ir["regions"] if item["id"] == "coastal_forest"),
            None,
        )
        if new is None or new.get("type") != "snow_forest":
            failures.append("existing coastal_forest Region was not replaced in place")
        elif new.get("placement") != old.get("placement"):
            failures.append("Region placement changed during replacement")
        if find_type(result.ir, "entities", "rowboat"):
            failures.append("incompatible rowboat survived snow replacement")
        if find_type(result.ir, "distributions", "grass"):
            failures.append("incompatible grass survived snow replacement")
        if not find_type(result.ir, "entities", "ruined_archway"):
            failures.append("new snow default ruined_archway is missing")
    elif case_id == "remove_default_then_unrelated_edit_does_not_restore_it":
        if find_type(result.ir, "entities", "cabin"):
            failures.append("deleted default cabin regrew during unrelated edit")
    elif case_id == "add_memorial_to_snow_forest":
        if not find_type(result.ir, "entities", "maritime_memorial"):
            failures.append("maritime_memorial was not added")
    elif case_id == "add_tent_to_coastal_forest":
        if not find_type(result.ir, "entities", "tent"):
            failures.append("tent was not added")
    elif case_id == "add_radiation_warning_sign_to_research_base":
        if not find_type(result.ir, "entities", "radiation_warning_sign"):
            failures.append("radiation_warning_sign was not added")
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run World IR V2 / World Catalog V2 deterministic and live coverage."
    )
    parser.add_argument("--config", default="config/config.toml")
    parser.add_argument("--schema", default="config/world_ir_v2.json")
    parser.add_argument("--prompts-dir", default="prompts")
    parser.add_argument("--fixture", default="examples/state0_v2.json")
    parser.add_argument("--cases", default="evals/semantic_regression_v2.json")
    parser.add_argument("--out-dir")
    parser.add_argument("--static-only", action="store_true")
    parser.add_argument("--only", help="comma-separated live case IDs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    spec = IRSpec(args.schema)
    probes = run_validator_probes(spec, Path(args.fixture))
    report: Json = {
        "suite": "World IR V2 + World Catalog V2 coverage",
        "catalog_version": spec.catalog.version if spec.catalog else None,
        "validator_probes": probes,
        "live_cases": [],
    }

    for probe in probes:
        mark = "PASS" if probe["passed"] else "FAIL"
        print(f"[{mark}] {probe['name']}")
        if not probe["passed"]:
            for issue in probe["issues"]:
                print(f"  {issue}")

    if not args.static_only:
        config = load_config(args.config)
        workflow = WorldIRWorkflow(
            llm=HTTPJSONLLM(config.llm),
            prompts=PromptStore(args.prompts_dir),
            spec=spec,
            config=config.workflow,
        )
        suite_path = Path(args.cases)
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        wanted = (
            {item.strip() for item in args.only.split(",") if item.strip()}
            if args.only else None
        )
        for case in suite["cases"]:
            if wanted is not None and case["id"] not in wanted:
                continue
            state = load_case_state(case, suite_path)
            result = workflow.run(case["prompt"], state)
            failures = check_live_case(case["id"], state, result, spec)
            entry = {
                "id": case["id"],
                "status": result.status,
                "passed": not failures,
                "failures": failures,
                "detail": result.detail,
                "world_ir": result.ir,
                "trace": result.trace.to_dict(),
            }
            report["live_cases"].append(entry)
            print(f"[{'PASS' if not failures else 'FAIL'}] {case['id']}: {result.status}")
            for failure in failures:
                print(f"  {failure}")

    static_ok = all(item["passed"] for item in probes)
    live_ok = all(item["passed"] for item in report["live_cases"])
    report["passed"] = static_ok and live_ok
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(args.out_dir or f"runs/v2_coverage_{timestamp}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(
        pretty_json(report) + "\n", encoding="utf-8"
    )
    print(f"report: {out_dir / 'report.json'}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
