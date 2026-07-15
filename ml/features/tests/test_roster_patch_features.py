from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import DotaPatch, Match, Player, Team, TeamRoster
from ml.features.build_prematch_features import build_features_for_match
from ml.features.roster_patch_features import build_roster_patch_features


class RosterPatchFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.team_a, self.team_b, self.lower])
        self.db.flush()
        self.match_time = datetime(2026, 2, 20, tzinfo=timezone.utc)
        self.db.add_all(
            [
                DotaPatch(
                    patch_name="7.39",
                    patch_version="7.39",
                    release_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    is_current=False,
                ),
                DotaPatch(
                    patch_name="7.40",
                    patch_version="7.40",
                    release_date=datetime(2026, 2, 1, tzinfo=timezone.utc),
                    is_current=True,
                ),
            ]
        )
        for team in [self.team_a, self.team_b]:
            for index in range(5):
                player = Player(nickname=f"{team.name}-{index}", team_id=team.id)
                self.db.add(player)
                self.db.flush()
                self.db.add(
                    TeamRoster(
                        team_id=team.id,
                        player_id=player.id,
                        role=str(index),
                        start_date=self.match_time - timedelta(days=60 if team == self.team_a else 10),
                        is_active=True,
                        source="test",
                    )
                )
        self.db.add_all(
            [
                Match(
                    team_a_id=self.team_a.id,
                    team_b_id=self.team_b.id,
                    tournament_name="The International",
                    status="finished",
                    start_time=self.match_time - timedelta(days=5),
                    winner_team_id=self.team_a.id,
                    is_tier1_match=True,
                ),
                Match(
                    team_a_id=self.team_a.id,
                    team_b_id=self.lower.id,
                    tournament_name="Small Cup",
                    status="finished",
                    start_time=self.match_time - timedelta(days=4),
                    winner_team_id=self.lower.id,
                    is_tier1_match=False,
                ),
                Match(
                    team_a_id=self.team_a.id,
                    team_b_id=self.team_b.id,
                    tournament_name="The International",
                    status="finished",
                    start_time=self.match_time + timedelta(days=1),
                    winner_team_id=self.team_b.id,
                    is_tier1_match=True,
                ),
            ]
        )
        self.current_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            status="upcoming",
            start_time=self.match_time,
            is_tier1_match=True,
        )
        self.db.add(self.current_match)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_feature_builder_includes_roster_features(self):
        features = build_features_for_match(self.db, self.current_match)

        self.assertIn("team_a_roster_stability_days", features)
        self.assertIn("team_b_recent_roster_change", features)

    def test_feature_builder_includes_patch_features(self):
        features = build_features_for_match(self.db, self.current_match)

        self.assertEqual(features["current_patch"], "7.40")
        self.assertEqual(features["days_since_patch"], 19)
        self.assertIn("team_a_current_patch_winrate", features)

    def test_feature_builder_does_not_use_future_data(self):
        features = build_roster_patch_features(self.db, self.current_match)

        self.assertEqual(features["team_a_matches_current_patch"], 1)
        self.assertEqual(features["team_b_matches_current_patch"], 1)
        self.assertEqual(features["team_a_current_patch_winrate"], 1.0)

    def test_unknown_roster_patch_returns_safe_defaults(self):
        empty_engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(empty_engine)
        db = Session(empty_engine)
        try:
            team_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
            team_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
            db.add_all([team_a, team_b])
            db.flush()
            match = Match(
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="The International",
                status="upcoming",
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                is_tier1_match=True,
            )
            db.add(match)
            db.commit()
            features = build_roster_patch_features(db, match)
        finally:
            db.close()

        self.assertEqual(features["team_a_roster_stability_days"], 0)
        self.assertIsNone(features["current_patch"])
        self.assertEqual(features["patch_recency_weight"], 1.0)


if __name__ == "__main__":
    unittest.main()
