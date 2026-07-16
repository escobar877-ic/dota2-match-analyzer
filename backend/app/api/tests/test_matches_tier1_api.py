from __future__ import annotations

import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.matches import get_match_context, get_match_forecast_history, get_match_prediction, list_matches
from app.database import Base
from app.db.models import Match, Player, Prediction, PredictionForecast, Team, TeamRoster


class MatchesTier1ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        self.tier1_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.tier1_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.tier1_a, self.tier1_b, self.lower])
        self.db.flush()

        self.tier1_match = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="TI",
            status="upcoming",
            is_tier1_match=True,
        )
        self.excluded_match = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.lower.id,
            tournament_name="TI",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
        )
        self.stale_tier1_match = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.lower.id,
            tournament_name="TI",
            status="upcoming",
            is_tier1_match=True,
        )
        self.db.add_all([self.tier1_match, self.excluded_match, self.stale_tier1_match])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_list_matches_excludes_non_tier1_by_default(self):
        matches = list_matches(db=self.db)
        self.assertEqual([match.id for match in matches], [self.tier1_match.id])

    def test_list_matches_include_excluded_returns_excluded_matches(self):
        matches = list_matches(include_excluded=True, db=self.db)
        self.assertEqual(
            {match.id for match in matches},
            {self.tier1_match.id, self.excluded_match.id, self.stale_tier1_match.id},
        )

    def test_list_matches_excludes_synthetic_by_default_and_supports_explicit_opt_in(self):
        synthetic = Match(
            external_source="dev_seed",
            external_id="synthetic-visible-only-by-opt-in",
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="Synthetic TI",
            start_time=datetime.now(timezone.utc) + timedelta(hours=1),
            status="upcoming",
            is_tier1_match=True,
        )
        self.db.add(synthetic)
        self.db.commit()

        self.assertNotIn(synthetic.id, [match.id for match in list_matches(db=self.db)])
        self.assertIn(
            synthetic.id,
            [match.id for match in list_matches(include_synthetic=True, db=self.db)],
        )

    def test_list_matches_excludes_map_training_rows_by_default(self):
        training_map = Match(
            external_source="csv_import",
            external_id="map-training-row",
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="The International",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            status="finished",
            winner_team_id=self.tier1_a.id,
            is_tier1_match=True,
            dataset_profile="historical_training",
            is_training_eligible=True,
        )
        self.db.add(training_map)
        self.db.commit()

        self.assertNotIn(training_map.id, [match.id for match in list_matches(db=self.db)])
        self.assertIn(
            training_map.id,
            [match.id for match in list_matches(include_training_rows=True, db=self.db)],
        )

    def test_list_matches_hides_stale_upcoming_and_respects_limit(self):
        stale = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="TI",
            status="upcoming",
            start_time=datetime.now(timezone.utc) - timedelta(days=1),
            is_tier1_match=True,
        )
        future = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="TI",
            status="upcoming",
            start_time=datetime.now(timezone.utc) + timedelta(days=1),
            is_tier1_match=True,
        )
        self.db.add_all([stale, future])
        self.db.commit()

        matches = list_matches(limit=1, db=self.db)

        self.assertEqual([match.id for match in matches], [future.id])
        self.assertNotIn(stale.id, [match.id for match in list_matches(db=self.db)])
        self.assertIn(
            stale.id,
            [match.id for match in list_matches(include_stale_upcoming=True, db=self.db)],
        )

    def test_prediction_endpoint_rejects_non_tier1_match(self):
        response = get_match_prediction(self.excluded_match.id, db=self.db)
        payload = json.loads(response.body)
        predictions_count = self.db.query(Prediction).count()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(payload["error"], "This match is excluded from analysis because it is not Tier 1.")
        self.assertEqual(payload["excluded_reason"], "team_b_not_tier1,team_b_not_active_tier1")
        self.assertEqual(predictions_count, 0)

    def test_match_context_distinguishes_known_roster_from_unknown_stability_date(self):
        self.tier1_match.start_time = datetime.now(timezone.utc) + timedelta(days=1)
        players = [Player(nickname=f"p{index}", team_id=self.tier1_a.id) for index in range(5)]
        self.db.add_all(players)
        self.db.flush()
        self.db.add_all(
            TeamRoster(
                team_id=self.tier1_a.id,
                player_id=player.id,
                is_active=True,
                source="pandascore",
            )
            for player in players
        )
        self.db.commit()

        response = get_match_context(self.tier1_match.id, db=self.db)
        team_context = response["teams"]["team_a"]

        self.assertEqual(team_context["roster_count"], 5)
        self.assertTrue(team_context["roster_known"])
        self.assertFalse(team_context["roster_ambiguous"])
        self.assertFalse(team_context["roster_stability_known"])
        self.assertIsNone(team_context["roster_stability_days"])

    def test_forecast_history_distinguishes_captured_snapshot_from_recomputed_prediction(self):
        now = datetime.now(timezone.utc)
        finished = Match(
            team_a_id=self.tier1_a.id,
            team_b_id=self.tier1_b.id,
            tournament_name="TI",
            start_time=now - timedelta(hours=2),
            status="finished",
            winner_team_id=self.tier1_a.id,
            is_tier1_match=True,
        )
        self.db.add(finished)
        self.db.flush()
        forecast = PredictionForecast(
            match_id=finished.id,
            horizon_bucket="final",
            is_primary=True,
            generated_at=now - timedelta(hours=3),
            scheduled_start=finished.start_time,
            lead_time_hours=1.0,
            prediction_type="ensemble",
            model_version="ensemble_v1",
            team_a_probability=0.61,
            team_b_probability=0.39,
            confidence_label="medium",
            confidence_score=0.68,
            predicted_outcomes_json={"team_a": 0.61, "team_b": 0.39},
            status="settled",
            actual_outcome="team_a",
            correct=True,
        )
        self.db.add(forecast)
        self.db.commit()

        response = get_match_forecast_history(finished.id, db=self.db)

        self.assertTrue(response["prospective_snapshot_available"])
        self.assertEqual(response["winner_team_name"], "Team Liquid")
        self.assertEqual(response["actual_outcome"], "team_a")
        self.assertEqual(response["preferred_snapshot"]["horizon_bucket"], "final")
        self.assertEqual(response["preferred_snapshot"]["evaluated_horizon"], "final")
        self.assertEqual(response["preferred_snapshot"]["actual_lead_time_hours"], 1.0)
        self.assertEqual(response["preferred_snapshot"]["evaluation_scope"], "strict_tier1")
        self.assertEqual(response["preferred_snapshot"]["team_a_probability"], 0.61)


if __name__ == "__main__":
    unittest.main()
