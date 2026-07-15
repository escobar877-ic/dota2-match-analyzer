from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from worker.data_ingestion.import_stratz_ids import (
    classify_training_match,
    normalized_match_from_trusted_league_csv,
    read_match_ids,
    validate_batch_metadata,
)
from worker.data_ingestion.normalizer import NormalizedMatch
from worker.data_ingestion.normalizer import normalize_tournament_name


class ImportStratzIdsTests(unittest.TestCase):
    def test_reads_plain_ids_and_ignores_comments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ids.txt"
            path.write_text("# verified IDs\n123\n456\n", encoding="utf-8")
            self.assertEqual(read_match_ids(path), ["123", "456"])

    def test_classifies_allowlisted_tier1_match(self):
        match = self._match("Team Liquid", "Team Spirit", "The International")
        classification, reasons = classify_training_match(match)
        self.assertEqual(classification, "tier1")
        self.assertEqual(reasons, [])

    def test_classifies_verified_pro_match(self):
        match = self._match("Nigma Galaxy", "OG", "Dota 2 Champions League")
        classification, reasons = classify_training_match(match)
        self.assertEqual(classification, "pro")
        self.assertEqual(reasons, [])

    def test_blocks_academy_and_qualifier(self):
        match = self._match("Team Spirit Academy", "OG", "The International Qualifiers")
        classification, reasons = classify_training_match(match)
        self.assertEqual(classification, "excluded")
        self.assertIn("team_a_low_confidence_name", reasons)
        self.assertIn("qualifier_not_training_eligible", reasons)

    def test_csv_metadata_detects_winner_mismatch(self):
        errors = validate_batch_metadata(
            {
                "league_id": "16935",
                "radiant_team_id": "1",
                "dire_team_id": "2",
                "start_time_unix": "100",
                "duration_sec": "200",
                "radiant_win": "True",
            },
            {
                "leagueid": 16935,
                "radiant_team_id": 1,
                "dire_team_id": 2,
                "start_time": 100,
                "duration": 200,
                "radiant_win": False,
            },
            "opendota",
        )
        self.assertEqual(errors, ["winner_mismatch"])

    def test_ti_year_normalizes_to_allowlisted_tournament(self):
        self.assertEqual(normalize_tournament_name("The International 2024"), "The International")
        self.assertEqual(normalize_tournament_name("The International 2025"), "The International")

    def test_trusted_league_csv_builds_finished_match_without_detail_request(self):
        match = normalized_match_from_trusted_league_csv(
            {
                "source": "opendota",
                "tournament_key": "dreamleague_s27",
                "tournament_name": "DreamLeague Season 27",
                "league_id": "18988",
                "match_id": "123456",
                "start_time_unix": "1760000000",
                "duration_sec": "2400",
                "radiant_team_id": "1",
                "radiant_name": "Team Liquid",
                "dire_team_id": "2",
                "dire_name": "Team Spirit",
                "radiant_win": "True",
                "winner_side": "radiant",
                "opendota_match_url": "https://www.opendota.com/matches/123456",
            }
        )
        self.assertIsNotNone(match)
        self.assertEqual(match.tournament_name, "DreamLeague")
        self.assertEqual(match.winner_team_external_id, "1")

    @staticmethod
    def _match(team_a: str, team_b: str, tournament: str) -> NormalizedMatch:
        return NormalizedMatch(
            external_source="stratz",
            external_id="123",
            team_a_external_id="1",
            team_b_external_id="2",
            team_a_name=team_a,
            team_b_name=team_b,
            tournament_name=tournament,
            start_time=datetime.now(UTC),
            status="finished",
            winner_team_external_id="1",
        )


if __name__ == "__main__":
    unittest.main()
