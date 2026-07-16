from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.build_real_match_ids_dataset import (
    CSV_FIELDS,
    collect_matches,
    enrich_team_names,
    normalize_league_match,
    validate_output,
    write_csv_atomic,
)
from worker.data_ingestion.base_client import ClientResponse


class BuildRealMatchIdsDatasetTests(unittest.TestCase):
    def test_filters_unfinished_and_deduplicates(self):
        payload = [
            self._match(10, duration=100),
            self._match(10, duration=100),
            self._match(11, duration=0),
            {"duration": 100},
        ]
        result = collect_matches(
            {"ti2025": 1},
            lambda _league_id: ClientResponse(ok=True, data=payload),
            limit=1000,
            sleep_seconds=0,
        )
        self.assertEqual([row["match_id"] for row in result.matches], ["10"])
        self.assertEqual(result.duplicates_removed, 1)

    def test_limit_keeps_newest_matches(self):
        payload = [
            self._match(10, start_time=100),
            self._match(11, start_time=300),
            self._match(12, start_time=200),
        ]
        result = collect_matches(
            {"ti2025": 1},
            lambda _league_id: ClientResponse(ok=True, data=payload),
            limit=2,
            sleep_seconds=0,
        )
        self.assertEqual([row["match_id"] for row in result.matches], ["11", "12"])

    def test_completion_grace_excludes_live_or_unsettled_map(self):
        payload = [
            self._match(10, start_time=100, duration=100),
            self._match(11, start_time=250, duration=100),
        ]
        result = collect_matches(
            {"ewc_2026": 19785},
            lambda _league_id: ClientResponse(ok=True, data=payload),
            limit=10,
            sleep_seconds=0,
            completed_before_unix=300,
        )
        self.assertEqual([row["match_id"] for row in result.matches], ["10"])

    def test_failed_or_broken_league_does_not_stop_collection(self):
        def fetch(league_id):
            if league_id == 1:
                return ClientResponse(ok=False, error="timeout")
            return ClientResponse(ok=True, data=[self._match(20)])

        result = collect_matches(
            {"broken": 1, "ti2025": 2},
            fetch,
            limit=10,
            sleep_seconds=0,
        )
        self.assertEqual(len(result.matches), 1)
        self.assertEqual(result.failed_leagues, {"broken": "timeout"})
        self.assertEqual(result.processed_leagues, ["ti2025"])

    def test_failed_league_does_not_expose_api_key(self):
        with patch.dict("os.environ", {"OPENDOTA_API_KEY": "secret-token"}):
            result = collect_matches(
                {"broken": 1},
                lambda _league_id: ClientResponse(
                    ok=False,
                    error="GET ?api_key=secret-token failed",
                ),
                limit=10,
                sleep_seconds=0,
            )
        self.assertNotIn("secret-token", result.failed_leagues["broken"])
        self.assertIn("***", result.failed_leagues["broken"])

    def test_csv_has_expected_structure(self):
        row = normalize_league_match(self._match(10), "ti2025", 18324)
        self.assertIsNotNone(row)
        with tempfile.TemporaryDirectory() as directory:
            path = write_csv_atomic([row], Path(directory) / "matches.csv")
            with path.open(encoding="utf-8", newline="") as file:
                reader = csv.DictReader(file)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, CSV_FIELDS)
            self.assertEqual(rows[0]["match_id"], "10")
            self.assertEqual(rows[0]["tournament_key"], "ti2025")
        self.assertEqual(validate_output(rows), [])

    def test_team_name_enrichment_fetches_each_team_once(self):
        rows = [
            normalize_league_match(self._match(10), "ti2025", 18324),
            normalize_league_match(self._match(11), "ti2025", 18324),
        ]
        calls = []

        def fetch(team_id):
            calls.append(team_id)
            return ClientResponse(ok=True, data={"team_id": int(team_id), "name": f"Team {team_id}"})

        for row in rows:
            row["radiant_name"] = ""
            row["dire_name"] = ""
        enriched, failures = enrich_team_names(rows, fetch, sleep_seconds=0)
        self.assertEqual(calls, ["1", "2"])
        self.assertEqual(failures, {})
        self.assertEqual(enriched[0]["radiant_name"], "Team 1")

    @staticmethod
    def _match(match_id, *, duration=1200, start_time=200):
        return {
            "match_id": match_id,
            "duration": duration,
            "start_time": start_time,
            "radiant_win": True,
            "leagueid": 18324,
            "radiant_team_id": 1,
            "radiant_team_name": "Team Liquid",
            "dire_team_id": 2,
            "dire_team_name": "Team Spirit",
            "series_id": 3,
            "series_type": 2,
        }


if __name__ == "__main__":
    unittest.main()
