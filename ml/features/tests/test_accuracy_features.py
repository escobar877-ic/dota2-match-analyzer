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
from app.db.models import Match, Team
from ml.features.build_prematch_features import build_features_for_match
from ml.features.feature_schema import FEATURE_VERSION
from ml.features.recent_form_features import recency_weighted_winrate
from ml.features.tournament_features import build_tournament_features


class AccuracyFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = self._team("Team Liquid")
        self.team_b = self._team("Team Spirit")
        self.strong = self._team("Gaimin Gladiators")
        self.weak = self._team("Random Tier1 Slot")
        self.db.add_all([self.team_a, self.team_b, self.strong, self.weak])
        self.db.flush()
        self.current_time = datetime(2026, 3, 1, tzinfo=timezone.utc)
        for days_ago in [40, 35, 30, 25]:
            self._match(self.strong, self.weak, days_ago, self.strong, "The International", "BO3")
        self._match(self.team_a, self.strong, 20, self.team_a, "The International", "BO3")
        self._match(self.team_a, self.weak, 15, self.weak, "The International", "BO3")
        self._match(self.team_b, self.weak, 10, self.team_b, "DreamLeague", "BO5")
        self._match(self.team_b, self.strong, 8, self.strong, "DreamLeague", "BO3")
        self._match(self.team_a, self.team_b, -1, self.team_b, "The International", "BO5")
        self.current_match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            tournament_tier="tier1",
            format="BO3",
            status="upcoming",
            start_time=self.current_time,
            is_tier1_match=True,
            team_a=self.team_a,
            team_b=self.team_b,
        )
        self.db.add(self.current_match)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_feature_builder_includes_prematch_v4_fields(self):
        features = build_features_for_match(self.db, self.current_match)

        self.assertEqual(FEATURE_VERSION, "prematch_v4")
        for field in [
            "team_a_glicko",
            "glicko_diff",
            "opponent_elo_diff_last_10",
            "strong_team_wins_diff",
            "weak_loss_diff",
            "recency_weighted_form_diff",
            "momentum_diff",
            "team_a_tournament_recent_winrate",
            "bo3_winrate_diff",
        ]:
            self.assertIn(field, features)

    def test_strength_of_schedule_uses_only_past_matches(self):
        features = build_features_for_match(self.db, self.current_match)

        self.assertEqual(features["team_a_wins_vs_strong_teams_last_20"], 1)
        self.assertEqual(features["team_a_losses_vs_weaker_teams_last_20"], 1)
        self.assertIsNotNone(features["opponent_elo_diff_last_10"])

    def test_recency_weighted_winrate_weights_recent_matches_more(self):
        older_win = self._simple_match(2, self.team_a.id)
        recent_loss = self._simple_match(1, self.team_b.id)

        weighted = recency_weighted_winrate([recent_loss, older_win], self.team_a.id)

        self.assertLess(weighted, 0.5)

    def test_tournament_context_safe_with_missing_data(self):
        empty = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="Unknown Event",
            status="upcoming",
            start_time=self.current_time,
            is_tier1_match=True,
        )

        features = build_tournament_features(empty, self.db)

        self.assertIsNone(features["team_a_tournament_recent_winrate"])
        self.assertIsNone(features["bo5_winrate_diff"])

    def test_future_data_not_used(self):
        features = build_features_for_match(self.db, self.current_match)

        self.assertEqual(features["h2h_matches_count"], 0)

    def test_same_real_team_across_sources_shares_history_without_dev_seed(self):
        liquid_alt = Team(
            name="Team Liquid",
            external_source="pandascore",
            external_id="liquid-alt",
            is_active_tier1=True,
            tier="tier1",
        )
        spirit_alt = Team(
            name="Team Spirit",
            external_source="opendota",
            external_id="spirit-alt",
            is_active_tier1=True,
            tier="tier1",
        )
        liquid_seed = Team(
            name="Team Liquid",
            external_source="dev_seed",
            external_id="liquid-seed",
            is_active_tier1=True,
            tier="tier1",
        )
        spirit_seed = Team(
            name="Team Spirit",
            external_source="dev_seed",
            external_id="spirit-seed",
            is_active_tier1=True,
            tier="tier1",
        )
        self.db.add_all([liquid_alt, spirit_alt, liquid_seed, spirit_seed])
        self.db.flush()
        self.db.add_all(
            [
                Match(
                    external_source="pandascore",
                    team_a_id=liquid_alt.id,
                    team_b_id=spirit_alt.id,
                    tournament_name="The International",
                    tournament_tier="tier1",
                    format="BO3",
                    status="finished",
                    start_time=self.current_time - timedelta(days=2),
                    winner_team_id=liquid_alt.id,
                    is_tier1_match=True,
                ),
                Match(
                    external_source="dev_seed",
                    team_a_id=liquid_seed.id,
                    team_b_id=spirit_seed.id,
                    tournament_name="The International",
                    tournament_tier="tier1",
                    format="BO3",
                    status="finished",
                    start_time=self.current_time - timedelta(days=1),
                    winner_team_id=spirit_seed.id,
                    is_tier1_match=True,
                ),
            ]
        )
        self.db.commit()

        features = build_features_for_match(self.db, self.current_match)

        self.assertEqual(features["h2h_matches_count"], 1)
        self.assertEqual(features["h2h_team_a_winrate"], 1.0)

    def _team(self, name: str) -> Team:
        return Team(name=name, is_active_tier1=True, tier="tier1")

    def _match(self, team_a: Team, team_b: Team, days_ago: int, winner: Team, tournament: str, match_format: str) -> Match:
        match = Match(
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name=tournament,
            tournament_tier="tier1",
            format=match_format,
            status="finished",
            start_time=self.current_time - timedelta(days=days_ago),
            winner_team_id=winner.id,
            is_tier1_match=True,
        )
        self.db.add(match)
        return match

    def _simple_match(self, days_ago: int, winner_team_id: int) -> Match:
        return Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            status="finished",
            start_time=self.current_time - timedelta(days=days_ago),
            winner_team_id=winner_team_id,
            is_tier1_match=True,
        )


if __name__ == "__main__":
    unittest.main()
