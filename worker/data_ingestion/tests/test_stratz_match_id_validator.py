from __future__ import annotations

import csv
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

from worker.data_ingestion.sources.base import SourceResult
from worker.data_ingestion.stratz_match_id_validator import validate_stratz_match_id_batch


class FakeStratzClient:
    def __init__(self, records: dict[str, dict] | None = None, errors: dict[str, str] | None = None) -> None:
        self.records = records or {}
        self.errors = errors or {}

    def fetch_match_details(self, match_id: str) -> SourceResult:
        if match_id in self.errors:
            return SourceResult(ok=False, source="stratz", records=[], error=self.errors[match_id], warnings=[])
        record = self.records.get(match_id)
        return SourceResult(ok=True, source="stratz", records=[record] if record else [], error=None, warnings=[])


class StratzMatchIdValidatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_validator_catches_missing_source_url(self):
        path = self._write_rows([self._row(source_url="")])
        report = validate_stratz_match_id_batch(path, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)

        self.assertFalse(report["safe_to_apply"])
        self.assertTrue(any("source_url_required" in error for error in report["errors"]))

    def test_validator_catches_duplicate_match_id(self):
        path = self._write_rows([self._row(match_id="1"), self._row(match_id="1")])
        report = validate_stratz_match_id_batch(path, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)

        self.assertFalse(report["safe_to_apply"])
        self.assertIn("1", report["invalid_match_ids"])
        self.assertTrue(any("duplicate_match_id" in error for error in report["errors"]))

    def test_validator_catches_expected_team_mismatch(self):
        path = self._write_rows([self._row(expected_team_a_name="Team Liquid", expected_team_b_name="Gaimin Gladiators")])
        report = validate_stratz_match_id_batch(path, client=FakeStratzClient({"1": _raw_match()}), artifact_path=None)

        self.assertFalse(report["safe_to_apply"])
        self.assertEqual(report["mismatched_expected_fields"][0]["field"], "expected_teams")

    def test_validator_catches_non_tier1_tournament(self):
        path = self._write_rows([self._row(expected_tournament_name="Small Local Cup")])
        raw = _raw_match(league_name="Small Local Cup")
        report = validate_stratz_match_id_batch(path, client=FakeStratzClient({"1": raw}), artifact_path=None)

        self.assertFalse(report["safe_to_apply"])
        self.assertTrue(any("tournament_not_tier1_allowlist" in error for error in report["errors"]))

    def test_validator_handles_fetch_error_cleanly(self):
        path = self._write_rows([self._row()])
        report = validate_stratz_match_id_batch(path, client=FakeStratzClient(errors={"1": "STRATZ timeout"}), artifact_path=None)

        self.assertFalse(report["safe_to_apply"])
        self.assertIn("STRATZ timeout", report["errors"][0])

    def test_validator_writes_report_and_marks_valid_batch_safe(self):
        path = self._write_rows([self._row()])
        output = Path(self.temp_dir.name) / "validation.json"

        report = validate_stratz_match_id_batch(path, client=FakeStratzClient({"1": _raw_match()}), artifact_path=output)

        self.assertTrue(output.exists())
        self.assertTrue(report["safe_to_apply"])
        self.assertEqual(report["tier1_valid_count"], 1)
        self.assertEqual(report["valid_match_ids"], ["1"])

    def _write_rows(self, rows: list[dict[str, str]]) -> Path:
        path = Path(self.temp_dir.name) / "stratz_ids.csv"
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _row(self, **overrides: str) -> dict[str, str]:
        row = {
            "match_id": "1",
            "expected_team_a_name": "Team Liquid",
            "expected_team_b_name": "Team Spirit",
            "expected_tournament_name": "The International",
            "expected_start_date": "2026-01-01",
            "source_url": "https://example.com/source",
            "verification_note": "manual test",
        }
        row.update(overrides)
        return row


def _raw_match(*, league_name: str = "The International") -> dict:
    return {
        "id": 1,
        "startDateTime": "2026-01-01T12:00:00+00:00",
        "didRadiantWin": True,
        "radiantTeam": {"id": 10, "name": "Team Liquid"},
        "direTeam": {"id": 20, "name": "Team Spirit"},
        "league": {"id": 100, "name": league_name},
    }


if __name__ == "__main__":
    unittest.main()
