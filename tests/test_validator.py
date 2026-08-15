from pathlib import Path
import copy
import json
import unittest

from worldir_agent.schema import IRSpec, IRValidator

ROOT = Path(__file__).resolve().parents[1]


class V0ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = IRValidator(IRSpec(ROOT / "config/world_ir_v0.json"))

    def test_valid_state(self):
        ir = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_via_must_be_list(self):
        ir = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        ir["networks"][0]["via"] = "church"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("array of strings" in x for x in result.issues))

    def test_unknown_reference_is_rejected(self):
        ir = json.loads((ROOT / "examples/state0.json").read_text(encoding="utf-8"))
        ir["entities"][0]["near"] = "missing_road"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown id" in x for x in result.issues))


class V1ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = IRValidator(IRSpec(ROOT / "config/world_ir_v1.json"))
        self.state0 = json.loads((ROOT / "examples/state0_v1.json").read_text(encoding="utf-8"))

    def test_valid_v1_state(self):
        result = self.validator.validate(self.state0)
        self.assertTrue(result.valid, result.issues)

    def test_region_can_be_located_only_by_direction_relation(self):
        ir = copy.deepcopy(self.state0)
        ir["regions"].append({
            "id": "village",
            "type": "village",
            "relations": [
                {
                    "type": "direction_of",
                    "target": "forest",
                    "direction": "south",
                }
            ],
        })
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_unknown_relation_target_is_rejected(self):
        ir = copy.deepcopy(self.state0)
        ir["entities"][0]["relations"][0]["target"] = "missing_road"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("references unknown id" in x for x in result.issues))

    def test_unknown_relation_type_is_rejected(self):
        ir = copy.deepcopy(self.state0)
        ir["entities"][0]["relations"][0]["type"] = "beside"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("relations[0].type must be one of" in x for x in result.issues))

    def test_direction_of_requires_direction(self):
        ir = copy.deepcopy(self.state0)
        ir["regions"].append({
            "id": "village",
            "type": "village",
            "relations": [
                {"type": "direction_of", "target": "forest"}
            ],
        })
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("missing required fields: ['direction']" in x for x in result.issues))

    def test_relation_source_target_compatibility_is_checked(self):
        ir = copy.deepcopy(self.state0)
        ir["regions"][0]["relations"] = [
            {"type": "along", "target": "main_road"}
        ]
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("does not allow source primitive Region" in x for x in result.issues))

    def test_legacy_near_field_is_rejected_in_v1(self):
        ir = copy.deepcopy(self.state0)
        ir["entities"][0]["near"] = "main_road"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown fields: ['near']" in x for x in result.issues))


class V2ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.validator = IRValidator(IRSpec(ROOT / "config/world_ir_v2.json"))
        self.state0 = json.loads((ROOT / "examples/state0_v2.json").read_text(encoding="utf-8"))

    def test_valid_v2_state(self):
        result = self.validator.validate(self.state0)
        self.assertTrue(result.valid, result.issues)

    def test_type_outside_world_catalog_is_rejected(self):
        ir = copy.deepcopy(self.state0)
        ir["regions"][0]["type"] = "abandoned_seaside_town"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("World Catalog V2" in issue for issue in result.issues))

    def test_network_type_is_catalog_single_source_of_truth(self):
        spec = self.validator.spec
        self.assertEqual(spec.data["primitives"]["Network"]["fields"]["type"], {
            "kind": "string"
        })
        ir = copy.deepcopy(self.state0)
        ir["networks"][0]["type"] = "road"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any(
            "for Network: 'road'" in issue and "allowed: ['path']" in issue
            for issue in result.issues
        ))

    def test_region_can_use_relative_placement_without_anchor(self):
        ir = copy.deepcopy(self.state0)
        ir["regions"].append({
            "id": "snow_forest_north",
            "type": "snow_forest",
            "placement": {
                "relations": [
                    {
                        "type": "direction_of",
                        "target": "coastal_forest",
                        "direction": "south",
                    }
                ]
            },
        })
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_placement_anchor_whole_remains_valid(self):
        ir = copy.deepcopy(self.state0)
        ir["regions"][0]["placement"] = {"anchor": "whole"}
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_all_primitive_ids_must_be_nonempty_strings(self):
        for collection in ("regions", "networks", "entities", "distributions"):
            with self.subTest(collection=collection):
                ir = copy.deepcopy(self.state0)
                ir[collection][0]["id"] = "   "
                result = self.validator.validate(ir)
                self.assertFalse(result.valid)
                self.assertTrue(
                    any(".id must be a non-empty string" in issue for issue in result.issues),
                    result.issues,
                )

    def test_network_topology_reference_is_checked(self):
        ir = copy.deepcopy(self.state0)
        ir["networks"][0]["topology"]["via"] = ["missing_place"]
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("references unknown id" in x for x in result.issues))

    def test_network_can_have_placement_relation(self):
        ir = copy.deepcopy(self.state0)
        ir["networks"][0]["placement"] = {
            "relations": [{"type": "inside", "target": "coastal_forest"}]
        }
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_amount_count_requires_nonnegative_integer(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][0]["population"]["amount"] = {
            "mode": "count",
            "value": "twelve",
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("must be a non-negative integer" in x for x in result.issues))

    def test_amount_rejects_unknown_mode(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][0]["population"]["amount"] = {
            "mode": "roughly",
            "value": 12,
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any(".mode must be one of" in x for x in result.issues))

    def test_arrangement_clustered_is_valid(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][1]["population"]["arrangement"] = {
            "type": "clustered"
        }
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_arrangement_unknown_type_is_rejected(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][1]["population"]["arrangement"] = {
            "type": "natural"
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("arrangement.type must be one of" in x for x in result.issues))

    def test_gradient_density_profile_is_valid(self):
        ir = copy.deepcopy(self.state0)
        trees = ir["distributions"][1]
        trees["population"] = {
            "arrangement": {"type": "clustered"},
            "density_profile": {
                "type": "gradient",
                "from": {
                    "selector": {"type": "near", "target": "main_path"},
                    "density": "low",
                },
                "to": {
                    "selector": {"type": "anchor", "value": "west"},
                    "density": "high",
                },
            },
        }
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_density_amount_and_density_profile_are_mutually_exclusive(self):
        ir = copy.deepcopy(self.state0)
        trees = ir["distributions"][1]
        trees["population"]["density_profile"] = {
            "type": "gradient",
            "from": {
                "selector": {"type": "near", "target": "main_path"},
                "density": "low",
            },
            "to": {
                "selector": {"type": "anchor", "value": "west"},
                "density": "high",
            },
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("mutually exclusive" in x for x in result.issues))

    def test_count_amount_can_coexist_with_density_profile(self):
        ir = copy.deepcopy(self.state0)
        trees = ir["distributions"][1]
        trees["population"] = {
            "amount": {"mode": "count", "value": 100},
            "density_profile": {
                "type": "gradient",
                "from": {
                    "selector": {"type": "near", "target": "main_path"},
                    "density": "low",
                },
                "to": {
                    "selector": {"type": "anchor", "value": "west"},
                    "density": "high",
                },
            },
        }
        result = self.validator.validate(ir)
        self.assertTrue(result.valid, result.issues)

    def test_gradient_selector_reference_is_checked(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][1]["population"]["density_profile"] = {
            "type": "gradient",
            "from": {
                "selector": {"type": "near", "target": "missing_road"},
                "density": "low",
            },
            "to": {
                "selector": {"type": "anchor", "value": "west"},
                "density": "high",
            },
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("references unknown id" in x for x in result.issues))

    def test_gradient_selector_requires_variant_fields(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][1]["population"]["density_profile"] = {
            "type": "gradient",
            "from": {
                "selector": {"type": "direction_of", "target": "forest"},
                "density": "low",
            },
            "to": {
                "selector": {"type": "anchor", "value": "west"},
                "density": "high",
            },
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("missing required fields: ['direction']" in x for x in result.issues))

    def test_gradient_selector_anchor_rejects_whole(self):
        ir = copy.deepcopy(self.state0)
        ir["distributions"][1]["population"] = {
            "density_profile": {
                "type": "gradient",
                "from": {
                    "selector": {"type": "anchor", "value": "whole"},
                    "density": "low",
                },
                "to": {
                    "selector": {"type": "anchor", "value": "west"},
                    "density": "high",
                },
            }
        }
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("selector.value must be one of" in issue for issue in result.issues),
            result.issues,
        )

    def test_v1_flat_fields_are_rejected_in_v2(self):
        ir = copy.deepcopy(self.state0)
        ir["entities"][0]["location"] = "north"
        ir["distributions"][0]["count"] = 12
        ir["networks"][0]["from"] = "south"
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown fields" in x for x in result.issues))

    def _minimal_world(self, region_type, primitive, object_type, *, owners=1):
        relations = [
            {"type": "inside", "target": f"owner_{index}"}
            for index in range(owners)
        ]
        regions = [
            {"id": f"owner_{index}", "type": region_type}
            for index in range(max(owners, 1))
        ]
        collection = "entities" if primitive == "Entity" else "distributions"
        world = {
            "regions": regions,
            "networks": [],
            "entities": [],
            "distributions": [],
        }
        world[collection].append({
            "id": "subject",
            "type": object_type,
            "placement": {"relations": relations},
        })
        return world

    def test_entity_requires_exactly_one_region_owner(self):
        ir = self._minimal_world("coastal_forest", "Entity", "tent", owners=0)
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("exactly one inside relation" in x for x in result.issues))

    def test_distribution_requires_exactly_one_region_owner(self):
        ir = self._minimal_world("coastal_forest", "Distribution", "tree", owners=0)
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("exactly one inside relation" in x for x in result.issues))

    def test_entity_two_inside_regions_invalid(self):
        ir = self._minimal_world("coastal_forest", "Entity", "tent", owners=2)
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("found 2" in x for x in result.issues))

    def test_distribution_two_inside_regions_invalid(self):
        ir = self._minimal_world("coastal_forest", "Distribution", "tree", owners=2)
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("found 2" in x for x in result.issues))

    def test_region_inside_region_invalid(self):
        ir = {
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
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any("Region nesting is unsupported" in x for x in result.issues))

    def test_rowboat_inside_coastal_forest_valid(self):
        ir = self._minimal_world("coastal_forest", "Entity", "rowboat")
        self.assertTrue(self.validator.validate(ir).valid)

    def test_rowboat_inside_snow_forest_invalid(self):
        ir = self._minimal_world("snow_forest", "Entity", "rowboat")
        result = self.validator.validate(ir)
        self.assertFalse(result.valid)
        self.assertTrue(any(
            "type 'rowboat' is not allowed inside Region type 'snow_forest'" in x
            for x in result.issues
        ))

    def test_research_station_inside_research_base_valid(self):
        ir = self._minimal_world("research_base", "Entity", "research_station")
        self.assertTrue(self.validator.validate(ir).valid)

    def test_research_station_inside_coastal_forest_invalid(self):
        ir = self._minimal_world("coastal_forest", "Entity", "research_station")
        self.assertFalse(self.validator.validate(ir).valid)

    def test_grass_inside_snow_forest_invalid(self):
        ir = self._minimal_world("snow_forest", "Distribution", "grass")
        self.assertFalse(self.validator.validate(ir).valid)

    def test_fallen_log_inside_snow_forest_is_not_exposed_without_backend_evidence(self):
        ir = self._minimal_world("snow_forest", "Distribution", "fallen_log")
        self.assertFalse(self.validator.validate(ir).valid)

    def test_cabin_inside_coastal_forest_valid(self):
        ir = self._minimal_world("coastal_forest", "Entity", "cabin")
        self.assertTrue(self.validator.validate(ir).valid)

    def test_cabin_inside_snow_forest_valid(self):
        ir = self._minimal_world("snow_forest", "Entity", "cabin")
        self.assertTrue(self.validator.validate(ir).valid)


if __name__ == "__main__":
    unittest.main()
