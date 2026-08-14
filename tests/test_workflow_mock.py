from pathlib import Path
import json
import unittest

from worldir_agent.config import WorkflowConfig
from worldir_agent.prompts import PromptStore
from worldir_agent.schema import IRSpec
from worldir_agent.workflow import WorldIRWorkflow

ROOT = Path(__file__).resolve().parents[1]


class FakeLLM:
    def __init__(self, responses):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.calls = []
        self.prompts = []

    def complete(self, node, prompt):
        self.calls.append(node)
        self.prompts.append(prompt)
        return self.responses[node].pop(0)


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


class WorkflowTests(unittest.TestCase):
    def test_explicit_edit_bypass(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        state1 = json.loads(json.dumps(state0))
        state1["entities"][0]["location"] = "northwest"
        state1["distributions"][0]["count"] = 20

        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({"expressible": True, "reason": "supported", "unsupported": []})],
            "editor": [json.dumps(state1)],
            "semantic_judge": [judgment()],
        })
        wf = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(),
        )
        result = wf.run("把教堂移到西北，房子改为20栋。", state0)
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.ir["entities"][0]["location"], "northwest")
        self.assertEqual(result.ir["distributions"][0]["count"], 20)


    def test_deliberate_skips_planner_checker_by_default(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        plan = {
            "goal": "make the forest feel closer to the settlement",
            "preserve": ["coast", "forest", "main_road", "church", "houses", "trees"],
            "changes": ["add sparse trees near main_road"],
            "possible_ir_gaps": [],
        }
        candidate = json.loads(json.dumps(state0))
        candidate["distributions"].append({
            "id": "trees_near_road",
            "type": "tree",
            "near": "main_road",
            "density": "low",
        })

        # Intentionally no planner_checker response. If the checker is called,
        # FakeLLM will fail the test.
        llm = FakeLLM({
            "router": [json.dumps({"route": "deliberate", "reason": "abstract"})],
            "planner": [json.dumps(plan)],
            "expressibility": [json.dumps({"expressible": True, "reason": "supported", "unsupported": []})],
            "editor": [json.dumps(candidate)],
            "semantic_judge": [judgment()],
        })
        wf = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(),
        )
        result = wf.run("让森林更有侵入小镇的感觉。", state0)
        self.assertEqual(result.status, "ok")
        self.assertFalse(any(e.node == "planner_checker" for e in result.trace.events))

    def test_deliberate_can_enable_planner_checker_loop(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        plan1 = {
            "goal": "abstract goal",
            "preserve": [],
            "changes": ["overly broad change"],
            "possible_ir_gaps": [],
        }
        plan2 = {
            "goal": "abstract goal",
            "preserve": ["coast", "forest"],
            "changes": ["add sparse trees near main_road"],
            "possible_ir_gaps": [],
        }
        candidate = json.loads(json.dumps(state0))
        candidate["distributions"].append({
            "id": "trees_near_road",
            "type": "tree",
            "near": "main_road",
            "density": "low",
        })
        llm = FakeLLM({
            "router": [json.dumps({"route": "deliberate", "reason": "abstract"})],
            "planner": [json.dumps(plan1), json.dumps(plan2)],
            "planner_checker": [
                json.dumps({"status": "retry", "critique": "too broad"}),
                json.dumps({"status": "pass", "critique": ""}),
            ],
            "expressibility": [json.dumps({"expressible": True, "reason": "supported", "unsupported": []})],
            "editor": [json.dumps(candidate)],
            "semantic_judge": [judgment()],
        })
        wf = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(use_planner_checker=True, planner_max_attempts=3),
        )
        result = wf.run("让整个世界更有从文明走向荒野的感觉。", state0)
        self.assertEqual(result.status, "ok")
        checker_events = [e for e in result.trace.events if e.node == "planner_checker"]
        self.assertEqual(len(checker_events), 2)
        self.assertEqual(result.detail["semantic_intent"], plan2)
        judge_prompt = next(
            event.prompt for event in result.trace.events
            if event.node == "semantic_judge"
        )
        self.assertNotIn(plan2["goal"], judge_prompt)
        self.assertNotIn("# Semantic intent", judge_prompt)

    def test_ir_gap_stops_before_editor(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({
                "expressible": False,
                "reason": "Region has no relative south_of relation",
                "unsupported": ["south_of(village, forest)"]
            })],
        })
        wf = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(),
        )
        result = wf.run("在森林南边增加一个村庄。", state0)
        self.assertEqual(result.status, "ir_gap")
        self.assertIn("south_of(village, forest)", result.detail["expressibility"]["unsupported"])


    def test_v2_loads_semantic_guidance(self):
        spec = IRSpec(ROOT / "config/world_ir_v2.json")
        self.assertIn("placement.anchor", spec.semantic_guidance)
        self.assertIn("direction_of", spec.semantic_guidance)
        self.assertIn("北边靠近道路有一个教堂", spec.semantic_guidance)

    def test_v2_initial_prompt_injects_semantic_guidance(self):
        state0 = json.loads((ROOT / "examples/state0_v2.json").read_text(encoding="utf-8"))
        llm = FakeLLM({
            "initial_translator": [json.dumps(state0)],
            "semantic_judge": [judgment()],
        })
        wf = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(),
        )
        result = wf.run("北边靠近道路有一个教堂。")
        self.assertEqual(result.status, "ok")
        prompt = result.trace.events[0].prompt
        self.assertIn("# Active World IR semantic guidance", prompt)
        self.assertIn("anchor=north", prompt)
        self.assertIn("not `direction_of road north`", prompt)
        self.assertIn("if the Region label were hidden", prompt)
        self.assertIn("minimal strongly implied, observable constituents", prompt)
        self.assertIn('"tree": {', prompt)
        self.assertIn("Never invent a new `type`", prompt)

    def test_concept_realization_guidance_reaches_edit_passes(self):
        prompts = PromptStore(ROOT / "prompts")
        for name in ("planner", "editor", "expressibility", "semantic_judge"):
            text = prompts.read(name)
            self.assertIn("strongly implied", text, name)

    def test_v2_guidance_uses_a_finite_vocabulary_without_composition_pairs(self):
        spec = IRSpec(ROOT / "config/world_ir_v2.json")
        guidance = spec.semantic_guidance
        self.assertIn("## Controlled world vocabulary", guidance)
        self.assertIsNotNone(spec.catalog)
        self.assertEqual(spec.catalog.version, "World Catalog V1")
        self.assertEqual(
            spec.catalog.allowed_types("Distribution"),
            {"house", "tree", "tombstone", "lamp"},
        )
        self.assertIn("does not itself define fixed composition pairs", guidance)
        self.assertNotIn("forest -> tree", guidance.lower())
        self.assertNotIn("town -> house", guidance.lower())

    def test_initial_semantic_judge_retries_with_independent_feedback(self):
        complete = json.loads(
            (ROOT / "examples/state0_v2.json").read_text(encoding="utf-8")
        )
        incomplete = json.loads(json.dumps(complete))
        incomplete["distributions"] = [
            item for item in incomplete["distributions"] if item["type"] != "tree"
        ]
        llm = FakeLLM({
            "initial_translator": [json.dumps(incomplete), json.dumps(complete)],
            "semantic_judge": [
                judgment(
                    "retry",
                    critique="Add observable vegetation from the catalog.",
                    missing=["The requested forest has no observable realization."],
                ),
                judgment(),
            ],
        })
        workflow = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(),
        )

        result = workflow.run("西边有一片森林。")

        self.assertEqual(result.status, "ok")
        self.assertEqual(llm.calls.count("initial_translator"), 2)
        second_prompt = [
            prompt for node, prompt in zip(llm.calls, llm.prompts)
            if node == "initial_translator"
        ][1]
        self.assertIn("Independent Semantic Judge feedback", second_prompt)
        self.assertIn("observable vegetation", second_prompt)

    def test_semantic_judge_uses_a_separate_client_and_blind_context(self):
        state0 = json.loads(
            (ROOT / "examples/state0_v2.json").read_text(encoding="utf-8")
        )
        generator = FakeLLM({"initial_translator": [json.dumps(state0)]})
        judge = FakeLLM({"semantic_judge": [judgment()]})
        workflow = WorldIRWorkflow(
            llm=generator,
            judge_llm=judge,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(),
        )

        result = workflow.run("西边有森林，东边有海岸。")

        self.assertEqual(result.status, "ok")
        self.assertEqual(generator.calls, ["initial_translator"])
        self.assertEqual(judge.calls, ["semantic_judge"])
        self.assertIn("# Original User Request", judge.prompts[0])
        self.assertNotIn("# Semantic intent", judge.prompts[0])
        self.assertNotIn("# Planner output", judge.prompts[0])

    def test_initial_semantic_judge_can_report_ir_gap(self):
        state0 = json.loads(
            (ROOT / "examples/state0_v2.json").read_text(encoding="utf-8")
        )
        llm = FakeLLM({
            "initial_translator": [json.dumps(state0)],
            "semantic_judge": [judgment(
                "ir_gap",
                critique="The requested exact distance has no IR representation.",
                unsupported=["exact_distance(forest, coast, 50m)"],
            )],
        })
        workflow = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(),
        )

        result = workflow.run("森林必须距离海岸精确五十米。")

        self.assertEqual(result.status, "ir_gap")
        self.assertEqual(result.detail["mode"], "initial")
        self.assertEqual(
            result.detail["expressibility"]["unsupported"],
            ["exact_distance(forest, coast, 50m)"],
        )

    def test_edit_semantic_judge_retries_editor(self):
        state0 = json.loads(
            (ROOT / "examples/state0_v2.json").read_text(encoding="utf-8")
        )
        wrong = json.loads(json.dumps(state0))
        wrong["regions"][1]["placement"] = {"anchor": "east"}
        corrected = json.loads(json.dumps(state0))
        corrected["regions"][1]["placement"] = {"anchor": "west"}
        draft_wrong = {
            "world_ir": wrong,
            "runtime_bindings": [],
            "runtime_fact_ops": [],
        }
        draft_corrected = {
            "world_ir": corrected,
            "runtime_bindings": [],
            "runtime_fact_ops": [],
        }
        llm = FakeLLM({
            "router": [json.dumps({"route": "bypass", "reason": "explicit"})],
            "expressibility": [json.dumps({
                "expressible": True,
                "reason": "supported",
                "unsupported": [],
            })],
            "editor": [json.dumps(draft_wrong), json.dumps(draft_corrected)],
            "semantic_judge": [
                judgment(
                    "retry",
                    critique="The forest must use the world-west anchor.",
                ),
                judgment(),
            ],
        })
        workflow = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(),
        )

        result = workflow.run("把森林放在世界西边。", state0)

        self.assertEqual(result.status, "ok")
        self.assertEqual(llm.calls.count("editor"), 2)
        second_editor_prompt = [
            prompt for node, prompt in zip(llm.calls, llm.prompts)
            if node == "editor"
        ][1]
        self.assertIn("Independent Semantic Judge feedback", second_editor_prompt)
        self.assertIn("world-west anchor", second_editor_prompt)


if __name__ == "__main__":
    unittest.main()
