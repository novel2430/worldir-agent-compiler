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

    def complete(self, node, prompt):
        return self.responses[node].pop(0)


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
            "ir_validator": [json.dumps({"valid": True, "issues": [], "critique": ""})],
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
            "ir_validator": [json.dumps({"valid": True, "issues": [], "critique": ""})],
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
            "ir_validator": [json.dumps({"valid": True, "issues": [], "critique": ""})],
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
        llm = FakeLLM({"initial_translator": [json.dumps(state0)]})
        wf = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v2.json"),
            config=WorkflowConfig(),
        )
        result = wf.run("北边靠近道路有一个教堂。")
        self.assertEqual(result.status, "ok")
        prompt = result.trace.events[0].prompt
        self.assertIn("# 当前 World IR 语义约定", prompt)
        self.assertIn("anchor=north", prompt)
        self.assertIn("not `direction_of road north`", prompt)


if __name__ == "__main__":
    unittest.main()
