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

from worker.data_ingestion.sync_review import build_sync_review_report


class SyncReviewTests(unittest.TestCase):
    def test_sync_review_extracts_unknowns_and_writes_report(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "historical_sync_report.json"
            output_path = Path(temp_dir) / "sync_review_report.json"
            input_path.write_text(
                json.dumps(
                    {
                        "source": "opendota",
                        "records_seen": 2,
                        "would_create": 0,
                        "would_update": 0,
                        "would_exclude": 2,
                        "exclusion_reasons": {
                            "team_a_not_tier1_or_unmapped": 1,
                            "tournament_not_tier1_or_unmapped": 1,
                        },
                        "excluded_samples": [
                            {
                                "raw_team_a": "Team Liqud",
                                "raw_team_b": "Team Spirit",
                                "raw_tournament": "The Internatonal",
                                "normalized_team_a": "Team Liqud",
                                "normalized_team_b": "Team Spirit",
                                "normalized_tournament": "The Internatonal",
                                "exclusion_reasons": [
                                    "team_a_not_tier1",
                                    "tournament_not_tier1_allowlist",
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_sync_review_report(input_path, artifact_path=output_path)
            self.assertTrue(output_path.exists())

        self.assertEqual(report["valid_rows"], 0)
        self.assertIn("Team Liqud", report["unknown_teams"])
        self.assertIn("The Internatonal", report["unknown_tournaments"])
        self.assertEqual(report["recommended_action"], "do_not_apply_opendota_generic_feed_collect_csv_or_use_verified_source")
        self.assertIn("OpenDota generic feed is not a reliable", report["recommendation_detail"])

    def test_blocked_alias_suggestions_are_split_out(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "historical_sync_report.json"
            input_path.write_text(
                json.dumps(
                    {
                        "source": "opendota",
                        "records_seen": 1,
                        "would_create": 0,
                        "would_update": 0,
                        "would_exclude": 1,
                        "source_trust_level": "discovery",
                        "exclusion_reasons": {"team_a_not_tier1": 1},
                        "excluded_samples": [
                            {"raw_team_a": "Team Spirit Academy", "exclusion_reasons": ["team_a_not_tier1"]}
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_sync_review_report(input_path, artifact_path=None)

        self.assertFalse(report["apply_allowed"])
        self.assertEqual(report["source_trust_level"], "discovery")
        self.assertEqual(report["recommended_action"], "do_not_apply_opendota_generic_feed_collect_csv_or_use_verified_source")
        self.assertTrue(report["blocked_alias_suggestions"])
        self.assertEqual(report["blocked_alias_suggestions"][0]["risk"], "blocked")

    def test_no_raw_fields_gives_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "historical_sync_report.json"
            input_path.write_text(
                json.dumps(
                    {
                        "source": "opendota",
                        "records_seen": 1,
                        "would_create": 0,
                        "would_update": 0,
                        "would_exclude": 1,
                        "exclusion_reasons": {"team_a_not_tier1": 1},
                        "excluded_samples": [
                            {
                                "normalized_team_a": "Unknown Stack",
                                "exclusion_reasons": ["team_a_not_tier1"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            report = build_sync_review_report(input_path, artifact_path=None)

        self.assertIn("Unknown Stack", report["unknown_teams"])
        self.assertIn("historical_sync_report has no raw team names", report["warnings"][0])
        self.assertEqual(report["recommended_action"], "do_not_apply_opendota_generic_feed_collect_csv_or_use_verified_source")

    def test_stratz_unsupported_date_range_keeps_clean_recommendation(self):
        unsupported = (
            "stratz: STRATZ date-range historical fetch is not implemented for current GraphQL schema; "
            "use match ids, PandaScore schedule, or CSV batch."
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            input_path = Path(temp_dir) / "historical_sync_report.json"
            input_path.write_text(
                json.dumps(
                    {
                        "source": "stratz",
                        "records_seen": 0,
                        "would_create": 0,
                        "would_update": 0,
                        "would_exclude": 0,
                        "source_errors": [unsupported],
                    }
                ),
                encoding="utf-8",
            )

            report = build_sync_review_report(input_path, artifact_path=None)

        self.assertFalse(report["apply_allowed"])
        self.assertIn("STRATZ date-range historical fetch is unsupported", report["apply_block_reason"])
        self.assertEqual(report["source_errors"], [unsupported])
        self.assertEqual(report["recommended_action"], "use_stratz_match_ids_or_pandascore_schedule_or_csv_batch")
        self.assertIn("use match ids, PandaScore schedule, or CSV batch", report["recommendation_detail"])


if __name__ == "__main__":
    unittest.main()
