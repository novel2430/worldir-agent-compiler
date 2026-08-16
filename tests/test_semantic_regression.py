import json
from pathlib import Path
import unittest

from scripts.run_semantic_regression import _load_current_ir


ROOT = Path(__file__).resolve().parents[1]


class SemanticRegressionInputTests(unittest.TestCase):
    def test_suite_is_input_only_and_resolves_current_ir_fixture(self):
        suite_path = ROOT / "evals/semantic_regression_v2.json"
        suite = json.loads(suite_path.read_text(encoding="utf-8"))

        self.assertEqual(suite["version"], "Semantic Regression Inputs V2")
        self.assertTrue(suite["cases"])
        for case in suite["cases"]:
            self.assertNotIn("expect", case)
            self.assertNotIn("expected_ir", case)

        case_ids = {case["id"] for case in suite["cases"]}
        required = {
            "bare_forest_normalizes_to_coastal_forest",
            "snowy_forest_normalizes_to_snow_forest",
            "research_facility_normalizes_to_research_base",
            "coastal_forest_gets_catalog_realization",
            "research_base_gets_catalog_realization",
            "snow_forest_gets_catalog_realization",
            "snow_forest_without_cabin_respects_exception",
            "snow_forest_sparse_trees_overrides_default_density",
            "generic_road_lowers_to_path",
            "generic_boat_lowers_to_rowboats",
            "literary_boat_lowers_to_rowboat",
            "slight_density_reduction_uses_qualitative_step",
            "explicit_highway_is_ir_gap",
            "explicit_ship_is_ir_gap",
            "initial_desert_is_ir_gap",
            "initial_graveyard_is_ir_gap",
            "initial_medieval_town_is_ir_gap",
            "initial_non_snow_pine_forest_gap",
            "replace_coastal_forest_with_snow_forest",
            "remove_default_then_unrelated_edit_does_not_restore_it",
            "add_memorial_to_snow_forest",
            "add_tent_to_coastal_forest",
            "add_radiation_warning_sign_to_research_base",
            "add_rowboat_to_snow_forest_is_ir_gap",
        }
        self.assertTrue(required.issubset(case_ids))

        edit_case = next(
            case for case in suite["cases"] if "current_ir_file" in case
        )
        current_ir = _load_current_ir(edit_case, suite_path)
        self.assertEqual(
            set(current_ir),
            {"regions", "networks", "entities", "distributions"},
        )


if __name__ == "__main__":
    unittest.main()
