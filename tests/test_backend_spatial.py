from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from worldir_agent.backend.models import SpatialPlanRequest
from worldir_agent.backend.spatial_lowerer import SpatialLowerer, SpatialLoweringInvalidIR
from worldir_agent.demo.mock_compiler import MOCK_WORLD_IR, MockWorldCompiler
from worldir_agent.demo.models import (
    FuturePolicyV0,
    PersistentClimateHistoryV0,
    WorldEvolutionStateV0,
)
from worldir_agent.schema import IRSpec
from worldir_agent.server.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def request_for(ir=None, evolution=None):
    return SpatialPlanRequest(
        world_ir=copy.deepcopy(ir or MOCK_WORLD_IR),
        world_seed=12345,
        backend_target="godot_artlab_v2",
        evolution_state=evolution,
    )


class SpatialLowererTests(unittest.TestCase):
    def setUp(self):
        self.lowerer = SpatialLowerer(IRSpec(ROOT / "config/world_ir_v2.json"))

    def test_valid_ir_lowers_and_is_deterministic(self):
        first = self.lowerer.lower(request_for()).model_dump(mode="json")
        second = self.lowerer.lower(request_for()).model_dump(mode="json")
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "ok")

    def test_west_forest_is_west_and_coastal(self):
        result = self.lowerer.lower(request_for())
        forest = next(
            field for field in result.spatial_plan.regions
            if field.source_ir_ref == "forest_west"
        )
        self.assertLess(forest.center_m[0], 0.0)
        self.assertEqual(forest.profile_id, "coastal_forest")

    def test_unsupported_legal_ir_is_backend_gap(self):
        ir = copy.deepcopy(MOCK_WORLD_IR)
        ir["regions"] = [{"id": "field", "type": "field", "placement": {"anchor": "center"}}]
        ir["networks"] = []
        ir["distributions"] = []
        result = self.lowerer.lower(request_for(ir))
        self.assertEqual(result.status, "backend_gap")
        self.assertIn("regions[0].type=field", result.gap.paths)

    def test_invalid_ir_does_not_enter_lowering(self):
        ir = copy.deepcopy(MOCK_WORLD_IR)
        ir["regions"][0]["type"] = "research_base"
        with self.assertRaises(SpatialLoweringInvalidIR):
            self.lowerer.lower(request_for(ir))

    def test_output_has_no_godot_resource_or_absolute_path(self):
        text = json.dumps(
            self.lowerer.lower(request_for()).model_dump(mode="json"),
            ensure_ascii=False,
        )
        self.assertNotIn("res://", text)
        self.assertNotIn("C:\\\\", text)
        self.assertNotIn("Node", text)

    def test_world_ir_is_not_mutated(self):
        ir = copy.deepcopy(MOCK_WORLD_IR)
        original = copy.deepcopy(ir)
        self.lowerer.lower(request_for(ir))
        self.assertEqual(ir, original)

    def test_population_semantics_are_preserved(self):
        result = self.lowerer.lower(request_for())
        population = result.spatial_plan.distributions[0]
        self.assertEqual(population.amount_value, "high")
        self.assertEqual(population.arrangement, "clustered")
        self.assertEqual(population.acceptance_probability_scale, 1.0)

    def test_evolution_is_separate_and_revisioned(self):
        evolution = WorldEvolutionStateV0(
            version="0",
            future_policy=FuturePolicyV0(
                id="future_policy_001", kind="environment_override",
                target_profile="research_base", scope="future_unresolved_chunks",
                created_at_frontier=[5, 0], sequence=1, revision=1,
            ),
            history=[PersistentClimateHistoryV0(
                id="history_002", kind="persistent_climate", effect="snow",
                scope="global", since_years_ago=10, sequence=2,
            )],
        )
        result = self.lowerer.lower(request_for(evolution=evolution))
        self.assertEqual(result.spatial_plan.plan_revision, 2)
        self.assertEqual(result.spatial_plan.future_policy.target_profile, "research_base")
        self.assertEqual(result.spatial_plan.history_influences[0].target_profile, "snow_forest")
        self.assertNotIn("future_policy", MOCK_WORLD_IR)
        self.assertNotIn("history", MOCK_WORLD_IR)


class BackendAPITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app(MockWorldCompiler(), enable_demo_intent=True))

    def test_backend_plan_success_envelope(self):
        response = self.client.post("/v1/backend/plan", json={
            "world_ir": MOCK_WORLD_IR,
            "world_seed": 12345,
            "backend_target": "godot_artlab_v2",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["spatial_plan"]["version"], "0")

    def test_backend_plan_failure_envelope(self):
        ir = copy.deepcopy(MOCK_WORLD_IR)
        ir["regions"] = [{"id": "field", "type": "field"}]
        ir["networks"] = []
        ir["distributions"] = []
        response = self.client.post("/v1/backend/plan", json={
            "world_ir": ir, "world_seed": 1, "backend_target": "godot_artlab_v2",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "backend_gap")

    def test_invalid_world_ir_is_422(self):
        response = self.client.post("/v1/backend/plan", json={
            "world_ir": {}, "world_seed": 1, "backend_target": "godot_artlab_v2",
        })
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["detail"]["kind"], "invalid_world_ir")

    def test_mock_compile_uses_formal_compile_result(self):
        response = self.client.post("/v1/compile", json={
            "prompt": "create", "current_ir": None,
            "runtime_context": {"version": "1", "facts": []},
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["world_ir"], MOCK_WORLD_IR)
        self.assertIn("meta", body)

    def test_demo_future_and_history_intents_are_distinct(self):
        base = {
            "current_ir": MOCK_WORLD_IR,
            "evolution_state": {"version": "0", "history": []},
            "frontier_coord": [5, 0],
        }
        future = self.client.post("/v1/demo/interpret", json={
            **base, "prompt": "从前方开始，让接下来生成的区域变成一片废弃研究基地。",
        }).json()
        history = self.client.post("/v1/demo/interpret", json={
            **base, "prompt": "十年前这里开始持续下雪，而且一直没有停。",
        }).json()
        self.assertEqual(future["action"], "set_future_policy")
        self.assertEqual(history["action"], "add_history")
        self.assertNotIn("history_event", future)
        self.assertNotIn("future_policy", history)


if __name__ == "__main__":
    unittest.main()
