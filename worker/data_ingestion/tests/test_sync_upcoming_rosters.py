from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
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
from app.db.models import Match, Team
from worker.data_ingestion.sync_upcoming_rosters import _upcoming_actionable_teams


class SyncUpcomingRostersTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.now = datetime.now(timezone.utc)

    def tearDown(self) -> None:
        self.db.close()

    def test_includes_strict_and_verified_preview_teams_only(self):
        strict_a, strict_b = self._teams("Strict A", "Strict B", active_tier1=True)
        preview_a, preview_b = self._teams("Team Yandex", "Team Spirit")
        blocked_a, blocked_b = self._teams("Unknown A", "Unknown B")
        self.db.add_all(
            [
                self._match(strict_a, strict_b, is_tier1=True),
                self._match(preview_a, preview_b, verified_preview=True),
                self._match(blocked_a, blocked_b),
            ]
        )
        self.db.commit()

        teams = _upcoming_actionable_teams(self.db)

        self.assertEqual(
            {team.name for team in teams},
            {"Strict A", "Strict B", "Team Yandex", "Team Spirit"},
        )

    def test_preview_selection_does_not_change_training_eligibility(self):
        team_a, team_b = self._teams("PARIVISION", "Rune Eaters")
        match = self._match(team_a, team_b, verified_preview=True)
        self.db.add(match)
        self.db.commit()

        teams = _upcoming_actionable_teams(self.db)

        self.assertEqual(len(teams), 2)
        self.assertFalse(match.is_training_eligible)
        self.assertFalse(match.is_prediction_eligible)
        self.assertFalse(match.is_tier1_match)

    def _teams(self, name_a: str, name_b: str, *, active_tier1: bool = False) -> tuple[Team, Team]:
        suffix = str(self.db.query(Team).count())
        team_a = Team(
            external_source="pandascore",
            external_id=f"a-{suffix}",
            name=name_a,
            is_active_tier1=active_tier1,
        )
        team_b = Team(
            external_source="pandascore",
            external_id=f"b-{suffix}",
            name=name_b,
            is_active_tier1=active_tier1,
        )
        self.db.add_all([team_a, team_b])
        self.db.flush()
        return team_a, team_b

    def _match(
        self,
        team_a: Team,
        team_b: Team,
        *,
        is_tier1: bool = False,
        verified_preview: bool = False,
    ) -> Match:
        return Match(
            external_source="pandascore",
            external_id=f"match-{team_a.external_id}-{team_b.external_id}",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="Esports World Cup",
            start_time=self.now + timedelta(days=1),
            format="BO3",
            status="upcoming",
            is_tier1_match=is_tier1,
            dataset_profile="upcoming",
            competition_tier="pro" if verified_preview else "tier1" if is_tier1 else "unknown",
            verification_status="verified" if verified_preview else "unverified",
            source_confidence="high" if verified_preview else "low",
            is_training_eligible=False,
            is_prediction_eligible=is_tier1,
            prediction_block_reason="team_a_not_tier1" if verified_preview else None,
        )


if __name__ == "__main__":
    unittest.main()
