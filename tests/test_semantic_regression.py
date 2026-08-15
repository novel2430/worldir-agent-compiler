import json
from pathlib import Path
import unittest

from scripts.run_semantic_regression import _load_current_ir


ROOT = Path(__file__).resolve().parents[1]


class SemanticRegressionInputTests(unittest.TestCase):
    def test_suite_is_input_only_and_resolves_current_ir_fixture(self):
        suite_path = ROOT / "evals/semantic_regression_v1.json"
        suite = json.loads(suite_path.read_text(encoding="utf-8"))

        self.assertEqual(suite["version"], "Semantic Regression Inputs V1")
        self.assertTrue(suite["cases"])
        for case in suite["cases"]:
            self.assertNotIn("expect", case)
            self.assertNotIn("expected_ir", case)

        case_ids = {case["id"] for case in suite["cases"]}
        self.assertIn("initial_coast_does_not_invent_lighthouse", case_ids)
        self.assertIn("initial_swamp_does_not_invent_constituents", case_ids)
        self.assertIn("edit_does_not_expand_unrelated_regions", case_ids)

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
