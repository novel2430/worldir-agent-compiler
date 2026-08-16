from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from worldir_agent.config import WorkflowConfig
from worldir_agent.prompts import PromptStore
from worldir_agent.schema import IRSpec
from worldir_agent.workflow import WorldIRWorkflow


ROOT = Path(__file__).resolve().parents[1]
V2_SPEC = ROOT / "config/world_ir_v2.json"


class FakeLLM:
    def __init__(self, responses):
        self.responses = {key: list(value) for key, value in responses.items()}
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def complete(self, node, prompt):
        self.calls.append(node)
        self.prompts.append(prompt)
        return self.responses[node].pop(0)


def expressible(value=True, unsupported=None, *, lowering=None, semantic_loss=None):
    payload = {
        "expressible": value,
        "reason": "supported" if value else "outside closed-world capabilities",
        "proposed_lowering": lowering or [],
        "semantic_loss": semantic_loss or [],
        "unsupported": unsupported or [],
    }
    return json.dumps(payload)


def judgment(verdict="pass", *, critique="", missing=None, unsupported=None):
    return json.dumps({
        "verdict": verdict,
        "faithful": verdict == "pass",
        "complete": verdict == "pass",
        "restrained": True,
        "preserved": True,
        "unsupported_user_meaning": unsupported or [],
        "missing_observable_evidence": missing or [],
        "invented_content": [],
        "critique": critique,
    })


def workflow(llm, *, judge_llm=None, checker=False):
    return WorldIRWorkflow(
        llm=llm,
        judge_llm=judge_llm,
        prompts=PromptStore(ROOT / "prompts"),
        spec=IRSpec(V2_SPEC),
        config=WorkflowConfig(use_planner_checker=checker),
    )


def archetype_world(
    region_type: str,
    *,
    region_id: str = "environment",
    anchor: str = "west",
    excluded_entities: set[str] | None = None,
    density_overrides: dict[str, str] | None = None,
):
    excluded_entities = excluded_entities or set()
    density_overrides = density_overrides or {}
    realization = IRSpec(V2_SPEC).catalog.default_realization(region_type)
    entities = [
        {
            "id": item["type"],
            "type": item["type"],
            "placement": {
                "relations": [{"type": "inside", "target": region_id}]
            },
        }
        for item in realization["entities"]
        if item["type"] not in excluded_entities
    ]
    distributions = []
    for item in realization["distributions"]:
        population = copy.deepcopy(item["population"])
        if item["type"] in density_overrides:
            population["amount"] = {
                "mode": "density",
                "value": density_overrides[item["type"]],
            }
        distributions.append({
            "id": f"{item['type']}_population",
            "type": item["type"],
            "placement": {
                "relations": [{"type": "inside", "target": region_id}]
            },
            "population": population,
        })
    return {
        "regions": [{
            "id": region_id,
            "type": region_type,
            "placement": {"anchor": anchor},
        }],
        "networks": [],
        "entities": entities,
        "distributions": distributions,
    }


class LegacyWorkflowTests(unittest.TestCase):
    def test_explicit_edit_bypass(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text())
        state1 = copy.deepcopy(state0)
        state1["entities"][0]["location"] = "northwest"
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [expressible()],
            "editor": [json.dumps(state1)],
            "semantic_judge": [judgment()],
        })
        result = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(),
        ).run("把教堂移到西北。", state0)
        self.assertEqual(result.status, "ok")

    def test_planner_checker_remains_optional(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text())
        plan = {
            "goal": "abstract goal",
            "preserve": [],
            "changes": [],
            "possible_ir_gaps": [],
        }
        llm = FakeLLM({
            "router": [json.dumps({"route": "deliberate", "reason": "abstract"})],
            "planner": [json.dumps(plan)],
            "expressibility": [expressible(False, ["abstract goal"])],
        })
        result = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(),
        ).run("抽象修改", state0)
        self.assertEqual(result.status, "ir_gap")
        self.assertNotIn("planner_checker", llm.calls)


class ClosedWorldWorkflowTests(unittest.TestCase):
    def test_v2_loads_closed_world_catalog_and_guidance(self):
        spec = IRSpec(V2_SPEC)
        self.assertEqual(spec.catalog.version, "World Catalog V2")
        self.assertEqual(
            spec.catalog.allowed_types("Region"),
            {"coastal_forest", "research_base", "snow_forest"},
        )
        self.assertEqual(spec.catalog.allowed_types("Network"), {"path"})
        self.assertEqual(
            spec.catalog.allowed_types("Distribution"),
            {"tree", "grass", "shrub", "rock", "fallen_log"},
        )
        self.assertEqual(
            spec.catalog.canonical_type_for_alias("Region", "  SNOWY forest "),
            "snow_forest",
        )
        self.assertEqual(
            spec.catalog.canonical_type_for_alias("Region", "森林"),
            "coastal_forest",
        )
        self.assertEqual(
            spec.catalog.canonical_type_for_alias("Network", "路"),
            "path",
        )
        self.assertEqual(
            spec.catalog.canonical_type_for_alias("Entity", "船"),
            "rowboat",
        )
        self.assertIsNone(
            spec.catalog.canonical_type_for_alias("Region", "graveyard")
        )
        self.assertIn("closed-world capability model", spec.semantic_guidance.lower())
        self.assertIn("catalog-defined default", spec.semantic_guidance.lower())
        self.assertIn("allowed_regions", spec.semantic_guidance)
        self.assertNotIn(
            "use ordinary world knowledge and",
            spec.semantic_guidance.lower(),
        )

    def test_closed_world_policy_reaches_all_semantic_passes(self):
        prompts = PromptStore(ROOT / "prompts")
        for name in (
            "initial_translator",
            "planner",
            "planner_checker",
            "expressibility",
            "editor",
            "semantic_judge",
        ):
            text = prompts.read(name).lower()
            self.assertIn("catalog", text, name)
            self.assertTrue(
                "closed-world" in text
                or "closed world" in text
                or "closed-output" in text,
                name,
            )
        expressibility = prompts.read("expressibility").lower()
        self.assertIn("open-input, closed-output", expressibility)
        self.assertIn("not as the exhaustive set", expressibility)
        self.assertIn("proposed_lowering", expressibility)
        self.assertIn("allowed_regions", prompts.read("editor"))
        self.assertIn(
            "catalog limits output vocabulary, not user phrasing",
            prompts.read("initial_translator").lower(),
        )
        self.assertIn(
            "alias is sufficient but not required",
            prompts.read("semantic_judge").lower(),
        )

    def test_initial_prompt_injects_catalog_defaults(self):
        candidate = archetype_world("coastal_forest")
        llm = FakeLLM({
            "expressibility": [expressible()],
            "initial_translator": [json.dumps(candidate)],
            "semantic_judge": [judgment()],
        })
        result = workflow(llm).run("生成一片森林")
        self.assertEqual(result.status, "ok")
        translator_prompt = next(
            prompt for node, prompt in zip(llm.calls, llm.prompts)
            if node == "initial_translator"
        )
        self.assertIn('"default_realization"', translator_prompt)
        self.assertIn('"coastal_forest"', translator_prompt)
        self.assertIn("catalog-defined default realization", translator_prompt)

    def test_expressibility_lowering_is_shared_with_initial_translator(self):
        candidate = archetype_world("coastal_forest")
        lowering = [
            "Interpret generic woods as the supported coastal_forest archetype."
        ]
        llm = FakeLLM({
            "expressibility": [expressible(lowering=lowering)],
            "initial_translator": [json.dumps(candidate)],
            "semantic_judge": [judgment()],
        })
        result = workflow(llm).run("生成一片林地")
        self.assertEqual(result.status, "ok")
        translator_prompt = next(
            prompt for node, prompt in zip(llm.calls, llm.prompts)
            if node == "initial_translator"
        )
        self.assertIn("Capability-grounded lowering from Expressibility", translator_prompt)
        self.assertIn(lowering[0], translator_prompt)

    def test_expressibility_lowering_is_shared_with_editor(self):
        current = archetype_world("coastal_forest")
        candidate = copy.deepcopy(current)
        candidate["networks"].append({
            "id": "north_south_path",
            "type": "path",
            "topology": {"from": "south", "to": "north"},
        })
        draft = {
            "world_ir": candidate,
            "runtime_bindings": [],
            "runtime_fact_ops": [],
        }
        lowering = [
            "Lower generic 路 to canonical path with south-to-north topology."
        ]
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "concrete"})],
            "expressibility": [expressible(lowering=lowering)],
            "editor": [json.dumps(draft)],
            "semantic_judge": [judgment()],
        })
        result = workflow(llm).run("森林里有一条南北向的路", current)
        self.assertEqual(result.status, "ok")
        editor_prompt = next(
            prompt for node, prompt in zip(llm.calls, llm.prompts)
            if node == "editor"
        )
        self.assertIn("Capability-grounded lowering from Expressibility", editor_prompt)
        self.assertIn(lowering[0], editor_prompt)

    def test_initial_normalization_and_default_realization(self):
        cases = (
            ("生成一片森林", "coastal_forest"),
            ("生成一片 snowy forest", "snow_forest"),
            ("生成一个 research facility", "research_base"),
        )
        for prompt, region_type in cases:
            with self.subTest(region_type=region_type):
                candidate = archetype_world(region_type)
                llm = FakeLLM({
                    "expressibility": [expressible()],
                    "initial_translator": [json.dumps(candidate)],
                    "semantic_judge": [judgment()],
                })
                result = workflow(llm).run(prompt)
                self.assertEqual(result.status, "ok")
                self.assertEqual(result.ir["regions"][0]["type"], region_type)
                expected = IRSpec(V2_SPEC).catalog.default_realization(region_type)
                self.assertEqual(
                    {item["type"] for item in result.ir["entities"]},
                    {item["type"] for item in expected["entities"]},
                )
                actual_amounts = {
                    item["type"]: item["population"]["amount"]
                    for item in result.ir["distributions"]
                }
                self.assertEqual(
                    actual_amounts,
                    {
                        item["type"]: item["population"]["amount"]
                        for item in expected["distributions"]
                    },
                )

    def test_snow_forest_explicit_overrides_are_preserved(self):
        cases = (
            (
                "生成一片雪森林，但是不要木屋",
                archetype_world("snow_forest", excluded_entities={"cabin"}),
                lambda ir: self.assertNotIn(
                    "cabin", {item["type"] for item in ir["entities"]}
                ),
            ),
            (
                "生成一片树很少的雪森林",
                archetype_world(
                    "snow_forest", density_overrides={"tree": "low"}
                ),
                lambda ir: self.assertEqual(
                    next(
                        item for item in ir["distributions"]
                        if item["type"] == "tree"
                    )["population"]["amount"]["value"],
                    "low",
                ),
            ),
        )
        for prompt, candidate, assertion in cases:
            with self.subTest(prompt=prompt):
                llm = FakeLLM({
                    "expressibility": [expressible()],
                    "initial_translator": [json.dumps(candidate)],
                    "semantic_judge": [judgment()],
                })
                result = workflow(llm).run(prompt)
                self.assertEqual(result.status, "ok")
                assertion(result.ir)

    def test_unsupported_initial_concepts_stop_at_capability_gate(self):
        cases = (
            ("生成一片沙漠", "desert"),
            ("生成一个墓地", "graveyard"),
            ("生成一个中世纪小镇", "medieval_town"),
            ("生成一个没有雪的松树林", "non_snow_pine_forest"),
        )
        for prompt, unsupported in cases:
            with self.subTest(unsupported=unsupported):
                llm = FakeLLM({
                    "expressibility": [expressible(False, [unsupported])],
                })
                result = workflow(llm).run(prompt)
                self.assertEqual(result.status, "ir_gap")
                self.assertEqual(llm.calls, ["expressibility"])
                self.assertEqual(
                    result.detail["expressibility"]["unsupported"],
                    [unsupported],
                )

    def test_initial_new_distributions_receive_canonical_amount(self):
        candidate = archetype_world("coastal_forest")
        candidate["distributions"].extend([
            {
                "id": "default_logs",
                "type": "fallen_log",
                "placement": {
                    "relations": [{"type": "inside", "target": "environment"}]
                },
            },
            {
                "id": "gradient_logs",
                "type": "fallen_log",
                "placement": {
                    "relations": [{"type": "inside", "target": "environment"}]
                },
                "population": {
                    "density_profile": {
                        "type": "gradient",
                        "from": {
                            "selector": {"type": "anchor", "value": "west"},
                            "density": "low",
                        },
                        "to": {
                            "selector": {"type": "anchor", "value": "east"},
                            "density": "high",
                        },
                    }
                },
            },
        ])
        llm = FakeLLM({
            "expressibility": [expressible()],
            "initial_translator": [json.dumps(candidate)],
            "semantic_judge": [judgment()],
        })
        result = workflow(llm).run("生成森林并增加倒木")
        by_id = {item["id"]: item for item in result.ir["distributions"]}
        self.assertEqual(
            by_id["default_logs"]["population"]["amount"],
            {"mode": "density", "value": "medium"},
        )
        self.assertNotIn("amount", by_id["gradient_logs"]["population"])

    def test_replace_coastal_forest_with_snow_forest(self):
        current = archetype_world("coastal_forest", region_id="east_region")
        current["entities"].append({
            "id": "existing_cabin",
            "type": "cabin",
            "placement": {
                "relations": [{"type": "inside", "target": "east_region"}]
            },
        })
        candidate = archetype_world("snow_forest", region_id="east_region")
        candidate["entities"] = [
            item for item in candidate["entities"] if item["type"] != "cabin"
        ] + [copy.deepcopy(current["entities"][-1])]
        draft = {"world_ir": candidate, "runtime_bindings": [], "runtime_fact_ops": []}
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [expressible()],
            "editor": [json.dumps(draft)],
            "semantic_judge": [judgment()],
        })
        result = workflow(llm).run("把这片森林变成雪森林", current)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.ir["regions"][0]["id"], "east_region")
        self.assertEqual(result.ir["regions"][0]["placement"], {"anchor": "west"})
        self.assertEqual(result.ir["regions"][0]["type"], "snow_forest")
        entity_types = [item["type"] for item in result.ir["entities"]]
        self.assertEqual(entity_types.count("cabin"), 1)
        self.assertIn("ruined_archway", entity_types)
        self.assertNotIn("rowboat", entity_types)
        self.assertNotIn(
            "grass", {item["type"] for item in result.ir["distributions"]}
        )

    def test_remove_default_then_unrelated_edit_does_not_restore_it(self):
        current = archetype_world("snow_forest")
        without_cabin = copy.deepcopy(current)
        without_cabin["entities"] = [
            item for item in without_cabin["entities"] if item["type"] != "cabin"
        ]
        fewer_rocks = copy.deepcopy(without_cabin)
        rock = next(
            item for item in fewer_rocks["distributions"] if item["type"] == "rock"
        )
        rock["population"]["amount"]["value"] = "medium"
        drafts = [
            {"world_ir": without_cabin, "runtime_bindings": [], "runtime_fact_ops": []},
            {"world_ir": fewer_rocks, "runtime_bindings": [], "runtime_fact_ops": []},
        ]
        llm = FakeLLM({
            "router": [
                json.dumps({"route": "bypass", "reason": "explicit"}),
                json.dumps({"route": "bypass", "reason": "explicit"}),
            ],
            "expressibility": [expressible(), expressible()],
            "editor": [json.dumps(item) for item in drafts],
            "semantic_judge": [judgment(), judgment()],
        })
        wf = workflow(llm)
        first = wf.run("删除木屋", current)
        second = wf.run("把石头减少一点", first.ir)
        self.assertEqual(second.status, "ok")
        self.assertNotIn(
            "cabin", {item["type"] for item in second.ir["entities"]}
        )

    def test_compatible_explicit_entities_can_be_added(self):
        cases = (
            ("snow_forest", "maritime_memorial"),
            ("coastal_forest", "tent"),
            ("research_base", "radiation_warning_sign"),
        )
        for region_type, entity_type in cases:
            with self.subTest(entity_type=entity_type):
                current = archetype_world(region_type)
                candidate = copy.deepcopy(current)
                candidate["entities"].append({
                    "id": f"new_{entity_type}",
                    "type": entity_type,
                    "placement": {
                        "relations": [{"type": "inside", "target": "environment"}]
                    },
                })
                draft = {
                    "world_ir": candidate,
                    "runtime_bindings": [],
                    "runtime_fact_ops": [],
                }
                llm = FakeLLM({
                    "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
                    "expressibility": [expressible()],
                    "editor": [json.dumps(draft)],
                    "semantic_judge": [judgment()],
                })
                result = workflow(llm).run(f"增加 {entity_type}", current)
                self.assertEqual(result.status, "ok")

    def test_incompatible_explicit_request_is_ir_gap_before_editor(self):
        current = archetype_world("snow_forest")
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [expressible(False, ["rowboat inside snow_forest"])],
        })
        result = workflow(llm).run("在雪森林里增加一艘划艇", current)
        self.assertEqual(result.status, "ir_gap")
        self.assertNotIn("editor", llm.calls)

    def test_semantic_judge_uses_separate_blind_context(self):
        candidate = archetype_world("coastal_forest")
        generator = FakeLLM({
            "expressibility": [expressible()],
            "initial_translator": [json.dumps(candidate)],
        })
        judge = FakeLLM({"semantic_judge": [judgment()]})
        result = workflow(generator, judge_llm=judge).run("生成一片森林")
        self.assertEqual(result.status, "ok")
        self.assertEqual(generator.calls, ["expressibility", "initial_translator"])
        self.assertEqual(judge.calls, ["semantic_judge"])
        self.assertNotIn("# Semantic intent", judge.prompts[0])
        self.assertNotIn("# Planner output", judge.prompts[0])


if __name__ == "__main__":
    unittest.main()
