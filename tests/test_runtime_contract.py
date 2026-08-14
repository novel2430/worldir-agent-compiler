from __future__ import annotations

import unittest

from pydantic import ValidationError

from worldir_agent.runtime.models import CompileDraft, RuntimeContext
from worldir_agent.runtime.validator import RuntimeContractValidator


class RuntimeContextContractTests(unittest.TestCase):
    def test_all_v1_fact_kinds_are_accepted(self):
        context = RuntimeContext.model_validate({
            "version": "1",
            "facts": [
                {
                    "id": "campfire_01",
                    "kind": "added_object",
                    "object_type": "campfire",
                    "location": {"inside": "coast", "anchor": "east"},
                },
                {
                    "id": "removed_tree_17",
                    "kind": "removed_object",
                    "object_type": "tree",
                },
                {
                    "id": "church_door_open",
                    "kind": "object_state",
                    "target": "church_door",
                    "state": "open",
                },
                {
                    "id": "clearing_01",
                    "kind": "marked_area",
                    "mark": "cleared",
                    "location": {"inside": "forest", "anchor": "east"},
                    "affected_type": "tree",
                    "count": 23,
                },
            ],
        })
        self.assertEqual(context.fact_ids, {
            "campfire_01",
            "removed_tree_17",
            "church_door_open",
            "clearing_01",
        })

    def test_runtime_context_rejects_protocol_extensions(self):
        with self.assertRaises(ValidationError):
            RuntimeContext.model_validate({
                "version": "1",
                "facts": [{
                    "id": "area_01",
                    "kind": "marked_area",
                    "mark": "frozen",
                    "location": {"inside": "forest"},
                }],
            })

        with self.assertRaises(ValidationError):
            RuntimeContext.model_validate({
                "version": "1",
                "facts": [{
                    "id": "campfire_01",
                    "kind": "added_object",
                    "object_type": "campfire",
                    "backend_transform": [0, 0, 0],
                }],
            })

    def test_semantic_location_must_not_be_empty(self):
        with self.assertRaises(ValidationError):
            RuntimeContext.model_validate({
                "version": "1",
                "facts": [{
                    "id": "area_01",
                    "kind": "marked_area",
                    "mark": "cleared",
                    "location": {},
                }],
            })

    def test_optional_fields_reject_explicit_null(self):
        with self.assertRaises(ValidationError):
            RuntimeContext.model_validate({
                "version": "1",
                "facts": [{
                    "id": "campfire_01",
                    "kind": "added_object",
                    "object_type": "campfire",
                    "location": None,
                }],
            })

    def test_count_accepts_json_integer_values_and_normalizes_float(self):
        for value in (23, 23.0):
            with self.subTest(value=value):
                context = RuntimeContext.model_validate({
                    "version": "1",
                    "facts": [{
                        "id": "area_01",
                        "kind": "marked_area",
                        "mark": "cleared",
                        "location": {"inside": "forest"},
                        "count": value,
                    }],
                })
                self.assertEqual(context.facts[0].count, 23)
                self.assertIs(type(context.facts[0].count), int)

    def test_count_rejects_non_integer_string_and_negative_values(self):
        for value in (23.5, "23", -1):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    RuntimeContext.model_validate({
                        "version": "1",
                        "facts": [{
                            "id": "area_01",
                            "kind": "marked_area",
                            "mark": "cleared",
                            "location": {"inside": "forest"},
                            "count": value,
                        }],
                    })

    def test_count_json_schema_remains_nonnegative_integer(self):
        schema = RuntimeContext.model_json_schema()
        count = schema["$defs"]["MarkedArea"]["properties"]["count"]
        self.assertEqual(count["type"], "integer")
        self.assertEqual(count["minimum"], 0)


class RuntimeDraftValidationTests(unittest.TestCase):
    def setUp(self):
        self.context = RuntimeContext.model_validate({
            "version": "1",
            "facts": [{
                "id": "clearing_01",
                "kind": "marked_area",
                "mark": "cleared",
                "location": {"inside": "forest"},
            }],
        })

    def test_binding_and_clear_references_are_checked(self):
        draft = CompileDraft.model_validate({
            "world_ir": {
                "regions": [{"id": "graveyard", "type": "graveyard"}],
                "networks": [],
                "entities": [],
                "distributions": [],
            },
            "runtime_bindings": [{
                "ir_object_id": "missing_ir_object",
                "runtime_fact_id": "missing_fact",
                "placement": "inside",
            }],
            "runtime_fact_ops": [{
                "op": "clear",
                "runtime_fact_id": "missing_fact",
            }],
        })
        result = RuntimeContractValidator().validate(draft, self.context)
        self.assertFalse(result.valid)
        self.assertEqual(len(result.issues), 3)

    def test_valid_binding_and_clear_are_accepted(self):
        draft = CompileDraft.model_validate({
            "world_ir": {
                "regions": [{"id": "graveyard", "type": "graveyard"}],
                "networks": [],
                "entities": [],
                "distributions": [],
            },
            "runtime_bindings": [{
                "ir_object_id": "graveyard",
                "runtime_fact_id": "clearing_01",
                "placement": "inside",
            }],
            "runtime_fact_ops": [{
                "op": "clear",
                "runtime_fact_id": "clearing_01",
            }],
        })
        result = RuntimeContractValidator().validate(draft, self.context)
        self.assertTrue(result.valid, result.issues)


if __name__ == "__main__":
    unittest.main()
