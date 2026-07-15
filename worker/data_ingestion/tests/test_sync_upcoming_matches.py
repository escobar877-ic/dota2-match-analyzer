from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.database import Base
from app.db.models import DataSyncLog, Match
from worker.data_ingestion.sources.base import SourceResult
from worker.data_ingestion.sync_upcoming_matches import sync_upcoming_matches


class FakePandaScoreClient:
    source_name = "pandascore"

    def __init__(self, records: list[dict], ok: bool = True) -> None:
        self.records = records
        self.ok = ok

    def is_enabled(self) -> bool:
        return True

    def get_status(self) -> dict:
        return {"missing_key_reason": None}

    def health_check(self) -> SourceResult:
        return SourceResult(ok=self.ok, source="pandascore", records=[])

    def fetch_upcoming_matches(self, **kwargs) -> SourceResult:
        if not self.ok:
            return SourceResult(ok=False, source="pandascore", records=[], error="timeout")
        return SourceResult(ok=True, source="pandascore", records=self.records)


class SyncUpcomingMatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_upcoming_rows_never_training_eligible_and_dry_run_no_db_write(self):
        report = self._sync(apply=False, records=[_raw_upcoming()])

        self.assertFalse(report["is_training_eligible"])
        self.assertEqual(report["prediction_eligible_count"], 1)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(self.db.query(Match).count(), 0)

    def test_missing_teams_block_prediction_eligibility(self):
        report = self._sync(apply=False, records=[_raw_upcoming(opponents=[])])

        self.assertEqual(report["prediction_eligible_count"], 0)
        self.assertEqual(report["missing_team_count"], 1)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(report["would_exclude"], 0)
        self.assertEqual(report["unknown_upcoming_count"], 1)
        self.assertEqual(report["sample_prediction_blocked"][0]["prediction_block_reason"], "missing_teams")

    def test_non_tier1_upcoming_is_classified_not_excluded(self):
        report = self._sync(
            apply=False,
            records=[
                _raw_upcoming(
                    opponents=[
                        {"opponent": {"id": 99, "name": "Unknown A"}},
                        {"opponent": {"id": 100, "name": "Unknown B"}},
                    ],
                    league_name="Unknown Invitational",
                )
            ],
        )

        sample = report["sample_upcoming"][0]
        self.assertEqual(sample["team_a"], "Unknown A")
        self.assertEqual(sample["team_b"], "Unknown B")
        self.assertEqual(sample["tournament"], "Unknown Invitational")
        self.assertEqual(sample["competition_tier"], "pro")
        self.assertFalse(sample["prediction_eligible"])
        self.assertTrue(sample["source_prediction_eligible"])
        self.assertIn("tournament_not_tier1_allowlist", sample["classification_reasons"])
        self.assertEqual(report["would_exclude"], 0)
        self.assertEqual(report["would_create"], 1)

    def test_verified_pro_upcoming_accepts_current_named_tournament_without_tier1_teams(self):
        report = self._sync(
            apply=False,
            records=[
                _raw_upcoming(
                    opponents=[
                        {"opponent": {"id": 1651, "name": "Virtus.pro"}},
                        {"opponent": {"id": 138842, "name": "HULIGANI"}},
                    ],
                    league_name="The International",
                )
            ],
        )

        self.assertEqual(report["prediction_eligible_count"], 0)
        self.assertEqual(report["source_prediction_eligible_count"], 1)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(report["would_exclude"], 0)
        self.assertEqual(report["quality_scope"], "broad_upcoming")
        self.assertEqual(report["pro_upcoming_count"], 1)

    def test_non_allowlisted_tournament_is_classification_reason(self):
        report = self._sync(
            apply=False,
            records=[
                _raw_upcoming(
                    opponents=[
                        {"opponent": {"id": 132981, "name": "Carstensz"}},
                        {"opponent": {"id": 138732, "name": "Mentality Monster"}},
                    ],
                    league_name="EPL World Series",
                )
            ],
        )

        self.assertEqual(report["prediction_eligible_count"], 0)
        self.assertEqual(report["source_prediction_eligible_count"], 1)
        self.assertEqual(report["would_create"], 1)
        self.assertEqual(report["would_exclude"], 0)
        self.assertIn("tournament_not_tier1_allowlist", report["classification_reasons"])

    def test_cancelled_match_is_hard_excluded(self):
        raw = _raw_upcoming()
        raw["status"] = "canceled"
        report = self._sync(apply=False, records=[raw])

        self.assertEqual(report["would_exclude"], 1)
        self.assertEqual(report["saved_upcoming_candidates"], 0)
        self.assertEqual(report["hard_exclusion_reasons"], {"cancelled": 1})

    def test_apply_idempotent(self):
        broad = _raw_upcoming(
            opponents=[
                {"opponent": {"id": 99, "name": "Unknown A"}},
                {"opponent": {"id": 100, "name": "Unknown B"}},
            ],
            league_name="Unknown Invitational",
        )
        first = self._sync(apply=True, records=[broad])
        second = self._sync(apply=True, records=[broad])

        self.assertEqual(first["records_created"], 1)
        self.assertEqual(second["records_updated"], 1)
        self.assertEqual(self.db.query(Match).count(), 1)
        self.assertEqual(self.db.query(DataSyncLog).count(), 2)
        match = self.db.query(Match).one()
        self.assertFalse(match.is_tier1_match)
        self.assertEqual(match.dataset_profile, "upcoming")
        self.assertEqual(match.competition_tier, "pro")
        self.assertFalse(match.is_training_eligible)
        self.assertFalse(match.is_prediction_eligible)

    def test_source_error_no_crash_and_apply_blocked(self):
        report = self._sync(apply=False, records=[], ok=False)

        self.assertEqual(report["status"], "warning")
        self.assertFalse(report["apply_allowed"])
        self.assertTrue(report["source_errors"])

    def _sync(self, *, apply: bool, records: list[dict], ok: bool = True):
        output = Path(self.temp_dir.name) / "upcoming.json"
        with patch("worker.data_ingestion.sync_upcoming_matches.get_session", return_value=self.db), patch(
            "worker.data_ingestion.sync_upcoming_matches.get_source_client",
            return_value=FakePandaScoreClient(records, ok=ok),
        ):
            return sync_upcoming_matches(source="pandascore", dry_run=not apply, artifact_path=output)


def _raw_upcoming(opponents: list[dict] | None = None, league_name: str = "The International") -> dict:
    if opponents is None:
        opponents = [
            {"opponent": {"id": 10, "name": "Team Liquid"}},
            {"opponent": {"id": 20, "name": "Team Spirit"}},
        ]
    return {
        "id": 1000,
        "opponents": opponents,
        "league": {"id": 1, "name": league_name},
        "serie": {"full_name": f"{league_name} 2026"},
        "begin_at": "2026-07-01T12:00:00+00:00",
        "status": "not_started",
        "number_of_games": 3,
    }


if __name__ == "__main__":
    unittest.main()
