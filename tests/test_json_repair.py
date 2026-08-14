import json
import unittest
from pathlib import Path

from worldir_agent.config import WorkflowConfig
from worldir_agent.prompts import PromptStore
from worldir_agent.schema import IRSpec
from worldir_agent.workflow import WorldIRWorkflow

ROOT = Path(__file__).resolve().parents[1]


class RepairMockLLM:
    def __init__(self):
        self.calls = []

    def complete(self, node, prompt):
        self.calls.append(node)
        if node == "router":
            return '{"route":"deliberate","reason":"abstract"}'
        if node == "planner":
            return "not json at all"
        if node == "planner_json_repair":
            return json.dumps({
                "goal": "test",
                "preserve": [],
                "changes": ["add sparse trees near main_road"],
                "possible_ir_gaps": [],
            })
        if node == "planner_checker":
            return '{"status":"pass","critique":""}'
        if node == "expressibility":
            return '{"expressible":false,"reason":"stop here","unsupported":["test"]}'
        raise AssertionError(node)


class JsonRepairTests(unittest.TestCase):
    def test_planner_invalid_json_is_repaired(self):
        state0 = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        llm = RepairMockLLM()
        workflow = WorldIRWorkflow(
            llm=llm,
            prompts=PromptStore(ROOT / "prompts"),
            spec=IRSpec(ROOT / "config/world_ir_v0.json"),
            config=WorkflowConfig(json_repair_max_attempts=1),
        )
        result = workflow.run("abstract request", state0)
        self.assertEqual(result.status, "ir_gap")
        self.assertIn("planner_json_repair", llm.calls)
        self.assertTrue(any(
            e.node == "planner" and isinstance(e.parsed_response, dict) and "parse_error" in e.parsed_response
            for e in result.trace.events
        ))


if __name__ == "__main__":
    unittest.main()
