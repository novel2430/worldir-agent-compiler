from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest

from worldir_agent.catalog import WorldCatalog


ROOT = Path(__file__).resolve().parents[1]


class WorldCatalogV2Tests(unittest.TestCase):
    def setUp(self):
        self.catalog = WorldCatalog(ROOT / "config/world_catalog_v2.json")

    def test_public_metadata_helpers(self):
        self.assertEqual(
            self.catalog.allowed_types("Entity"),
            {
                "rowboat", "tent", "cabin", "research_station", "radar_tower",
                "radiation_warning_sign", "tidal_danger_sign", "cargo_truck",
                "crate", "maritime_memorial", "ruined_archway", "bunker",
                "concrete_wall",
            },
        )
        self.assertEqual(self.catalog.roles("Region", "snow_forest"), ("environment_region",))
        self.assertIn("snowy forest", self.catalog.aliases("Region", "snow_forest"))
        self.assertEqual(
            self.catalog.allowed_regions("Entity", "cabin"),
            {"coastal_forest", "snow_forest"},
        )
        self.assertEqual(
            self.catalog.canonical_type_for_alias("Region", "  SNOWY   FOREST "),
            "snow_forest",
        )
        self.assertEqual(
            self.catalog.canonical_type_for_alias("Region", "森林"),
            "coastal_forest",
        )
        self.assertEqual(
            self.catalog.canonical_type_for_alias("Network", "路"),
            "path",
        )
        self.assertEqual(
            self.catalog.canonical_type_for_alias("Entity", "船"),
            "rowboat",
        )
        self.assertEqual(
            self.catalog.canonical_type_for_alias("Entity", "舟"),
            "rowboat",
        )
        self.assertIsNone(
            self.catalog.canonical_type_for_alias("Network", "高速公路")
        )
        self.assertIsNone(
            self.catalog.canonical_type_for_alias("Entity", "轮船")
        )
        self.assertIsNone(
            self.catalog.canonical_type_for_alias("Region", "pine forest")
        )

    def test_returned_metadata_is_not_mutable_catalog_state(self):
        metadata = self.catalog.metadata("Region", "coastal_forest")
        metadata["aliases"].append("not actually supported")
        self.assertNotIn(
            "not actually supported",
            self.catalog.aliases("Region", "coastal_forest"),
        )

    def test_defaults_reference_declared_compatible_types(self):
        for region_type in self.catalog.allowed_types("Region"):
            realization = self.catalog.default_realization(region_type)
            for item in realization["entities"]:
                self.assertIn(item["type"], self.catalog.allowed_types("Entity"))
                self.assertIn(
                    region_type,
                    self.catalog.allowed_regions("Entity", item["type"]),
                )
            for item in realization["distributions"]:
                self.assertIn(item["type"], self.catalog.allowed_types("Distribution"))
                self.assertIn(
                    region_type,
                    self.catalog.allowed_regions("Distribution", item["type"]),
                )

    def test_archetype_defaults_are_the_backend_supported_contract(self):
        expected = {
            "coastal_forest": {
                "entities": ["rowboat"],
                "distributions": {
                    "tree": "high", "grass": "high", "shrub": "medium", "rock": "low"
                },
            },
            "research_base": {
                "entities": [
                    "research_station", "radar_tower", "cargo_truck",
                    "tidal_danger_sign", "crate",
                ],
                "distributions": {
                    "tree": "low", "grass": "low", "shrub": "low", "rock": "medium"
                },
            },
            "snow_forest": {
                "entities": ["cabin", "ruined_archway"],
                "distributions": {"tree": "high", "shrub": "low", "rock": "high"},
            },
        }
        for region_type, contract in expected.items():
            with self.subTest(region_type=region_type):
                realization = self.catalog.default_realization(region_type)
                self.assertEqual(
                    [item["type"] for item in realization["entities"]],
                    contract["entities"],
                )
                self.assertEqual(
                    {
                        item["type"]: item["population"]["amount"]["value"]
                        for item in realization["distributions"]
                    },
                    contract["distributions"],
                )

    def test_unknown_allowed_region_is_rejected(self):
        data = copy.deepcopy(self.catalog.data)
        data["types"]["Entity"]["rowboat"]["allowed_regions"] = ["desert"]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unknown Region types"):
                WorldCatalog(path)

    def test_incompatible_default_is_rejected(self):
        data = copy.deepcopy(self.catalog.data)
        data["types"]["Region"]["snow_forest"]["default_realization"][
            "entities"
        ].append({"type": "rowboat"})
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "catalog.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "incompatible Entity"):
                WorldCatalog(path)


if __name__ == "__main__":
    unittest.main()
