from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from worker.data_ingestion.source_mapping import (
    classify_alias_suggestion,
    resolve_source_team,
    suggest_alias_matches,
    validate_source_mapping,
)


class SourceMappingTests(unittest.TestCase):
    def test_valid_mapping_resolves_source_team_to_tier1_canonical(self):
        mappings = {"opendota": {"teams": {"101": "Team Liquid"}, "tournaments": {}}}

        self.assertEqual(resolve_source_team("opendota", "101", "Liquid", mappings), "Team Liquid")

    def test_project_mapping_covers_verified_ewc_2026_opendota_team_ids(self):
        expected = {
            "55": "Poor Rangers",
            "8255888": "BetBoom Team",
            "9256405": "Level UP",
            "9824702": "PARIVISION",
            "10182299": "L1ga Team",
            "10182309": "PlayTime",
            "10182357": "1win",
        }
        for external_id, canonical in expected.items():
            with self.subTest(external_id=external_id):
                self.assertEqual(resolve_source_team("opendota", external_id, "source label"), canonical)
        self.assertEqual(validate_source_mapping()["status"], "ok")

    def test_invalid_mapping_to_non_tier1_fails_validation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source_mappings.json"
            path.write_text(
                json.dumps({"opendota": {"teams": {"x": "Random Stack"}, "tournaments": {}}}),
                encoding="utf-8",
            )

            report = validate_source_mapping(path)

        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["invalid_mappings_count"], 1)

    def test_fuzzy_suggestion_does_not_auto_apply(self):
        suggestions = suggest_alias_matches("Team Liqud", ["Team Liquid", "Team Spirit"])

        self.assertIn("Team Liquid", [suggestion["suggested_canonical"] for suggestion in suggestions])
        self.assertIsNone(resolve_source_team("opendota", "999", "Team Liqud", {"opendota": {"teams": {}}}))

    def test_false_positive_alias_suggestions_are_blocked(self):
        examples = [
            ("Team Spirit Academy", "Team Spirit", "Academy"),
            ("x5 Gaming", "Xtreme Gaming", "tokens differ"),
            ("Amaru Gaming", "Aurora Gaming", "tokens differ"),
        ]
        for raw_name, canonical, reason_fragment in examples:
            with self.subTest(raw_name=raw_name):
                risk, reason = classify_alias_suggestion(raw_name, canonical)
                self.assertEqual(risk, "blocked")
                self.assertIn(reason_fragment.lower(), reason.lower())

    def test_academy_to_main_mapping_rejected_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source_mappings.json"
            path.write_text(
                json.dumps({"opendota": {"teams": {"Team Spirit Academy": "Team Spirit"}, "tournaments": {}}}),
                encoding="utf-8",
            )

            report = validate_source_mapping(path)

        self.assertEqual(report["status"], "failed")
        self.assertIn("Academy", report["invalid_mappings"][0]["reason"])

    def test_qualifier_tournament_mapping_rejected_unless_allowlisted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "source_mappings.json"
            path.write_text(
                json.dumps(
                    {
                        "opendota": {
                            "teams": {},
                            "tournaments": {
                                "The International 2026 - Regional Qualifier China": "The International"
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )

            report = validate_source_mapping(path)

        self.assertEqual(report["status"], "failed")
        self.assertIn("Qualifier", report["invalid_mappings"][0]["reason"])


if __name__ == "__main__":
    unittest.main()
