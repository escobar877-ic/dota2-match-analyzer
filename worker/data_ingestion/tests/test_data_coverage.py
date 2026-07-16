from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.database import Base
from app.db.models import DotaPatch, Match, MatchPatchContext, Player, Team, TeamRoster
from worker.data_ingestion.data_coverage import build_data_coverage_report, training_readiness


class DataCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_readiness_thresholds(self):
        self.assertEqual(training_readiness(299), "insufficient")
        self.assertEqual(training_readiness(300), "usable")
        self.assertEqual(training_readiness(999), "usable")
        self.assertEqual(training_readiness(1000), "good")

    def test_coverage_report_writes_json(self):
        team_a = self._team("Team Liquid")
        team_b = self._team("Team Spirit")
        players_a = [self._player(f"liquid-player-{index}") for index in range(5)]
        players_b = [self._player(f"spirit-player-{index}") for index in range(5)]
        self.db.add_all([team_a, team_b, *players_a, *players_b])
        self.db.flush()
        started_at = datetime(2026, 1, 10, tzinfo=timezone.utc)
        match = Match(
            external_source="dev_seed",
            external_id="m1",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="The International",
            tournament_tier="tier1",
            start_time=started_at,
            format="BO3",
            status="finished",
            winner_team_id=team_a.id,
            is_tier1_match=True,
        )
        patch = DotaPatch(
            patch_name="7.40",
            patch_version="7.40",
            release_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
            is_current=True,
        )
        self.db.add_all([match, patch])
        self.db.flush()
        self.db.add_all(
            [
                MatchPatchContext(match_id=match.id, patch_id=patch.id, days_since_patch=9, is_current_patch=True),
                *[
                    TeamRoster(
                        team_id=team_a.id,
                        player_id=player.id,
                        start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    )
                    for player in players_a
                ],
                *[
                    TeamRoster(
                        team_id=team_b.id,
                        player_id=player.id,
                        start_date=datetime(2025, 1, 1, tzinfo=timezone.utc),
                    )
                    for player in players_b
                ],
            ]
        )
        self.db.commit()
        path = Path(self.temp_dir.name) / "coverage.json"

        report = build_data_coverage_report(self.db, artifact_path=path)

        self.assertTrue(path.exists())
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["tier1_historical_matches_count"], 1)
        self.assertEqual(report["matches_with_patch_context_count"], 1)
        self.assertEqual(report["matches_with_roster_context_count"], 1)
        self.assertEqual(report["training_readiness"], "insufficient")
        self.assertTrue(report["dev_seed_only"])

    def _team(self, name: str) -> Team:
        return Team(
            external_source="dev_seed",
            external_id=name.lower().replace(" ", "-"),
            name=name,
            tier="tier1",
            is_active_tier1=True,
        )

    def _player(self, nickname: str) -> Player:
        return Player(external_source="dev_seed", external_id=nickname, nickname=nickname)


if __name__ == "__main__":
    unittest.main()
