from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.matches import get_match_analysis_preview, list_upcoming_matches
from app.database import Base
from app.db.models import Match, Team


class UpcomingMatchesApiTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.liquid = Team(name="Team Liquid", external_source="pandascore", external_id="10", is_active_tier1=True, tier="tier1")
        self.spirit = Team(name="Team Spirit", external_source="pandascore", external_id="20", is_active_tier1=True, tier="tier1")
        self.unknown = Team(name="Unknown Stack", external_source="pandascore", external_id="30", is_active_tier1=False)
        self.mouz = Team(name="MOUZ", external_source="pandascore", external_id="40", is_active_tier1=False)
        self.db.add_all([self.liquid, self.spirit, self.unknown, self.mouz])
        self.db.flush()
        tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
        day_after = datetime.now(timezone.utc) + timedelta(days=2)
        self.match = Match(
            external_source="pandascore",
            external_id="1000",
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="The International",
            start_time=tomorrow,
            format="BO3",
            status="upcoming",
            is_tier1_match=True,
            dataset_profile="upcoming",
            competition_tier="tier1",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
            is_prediction_eligible=True,
            prediction_guard_level="normal",
        )
        self.blocked = Match(
            external_source="pandascore",
            external_id="1001",
            team_a_id=self.liquid.id,
            team_b_id=self.unknown.id,
            tournament_name="The International",
            start_time=day_after,
            format="BO3",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
            dataset_profile="upcoming",
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
            is_prediction_eligible=True,
            prediction_guard_level="high",
        )
        self.db.add_all([self.match, self.blocked])
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_upcoming_search_returns_prediction_metadata(self):
        response = list_upcoming_matches(source="pandascore", db=self.db)

        self.assertEqual(response["total"], 2)
        eligible = next(item for item in response["items"] if item["external_id"] == "1000")
        self.assertTrue(eligible["prediction_eligible"])
        self.assertEqual(eligible["verification_status"], "verified")
        blocked = next(item for item in response["items"] if item["external_id"] == "1001")
        self.assertFalse(blocked["prediction_eligible"])
        self.assertTrue(blocked["preview_eligible"])
        self.assertEqual(blocked["analysis_mode"], "verified_pro_preview")
        self.assertTrue(blocked["source_prediction_eligible"])
        self.assertEqual(blocked["competition_tier"], "pro")
        self.assertEqual(blocked["prediction_guard_level"], "high")
        self.assertIn("team_b_not_tier1", blocked["prediction_block_reason"])
        self.assertFalse(blocked["is_training_eligible"])
        self.assertEqual(response["scope_summary"]["strict_prediction_count"], 1)
        self.assertEqual(response["scope_summary"]["verified_pro_preview_count"], 1)

    def test_upcoming_filters(self):
        self.assertEqual(list_upcoming_matches(q="Liquid", db=self.db)["total"], 2)
        self.assertEqual(list_upcoming_matches(team="Spirit", db=self.db)["total"], 1)
        self.assertEqual(list_upcoming_matches(tournament="International", db=self.db)["total"], 2)
        self.assertEqual(list_upcoming_matches(source="pandascore", prediction_eligible=True, db=self.db)["total"], 1)
        self.assertEqual(list_upcoming_matches(source="opendota", db=self.db)["total"], 0)

    def test_include_finished_does_not_return_stale_upcoming_rows(self):
        stale = Match(
            external_source="pandascore",
            external_id="stale-upcoming",
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="The International",
            start_time=datetime.now(timezone.utc) - timedelta(days=5),
            format="BO3",
            status="upcoming",
            is_tier1_match=True,
        )
        finished = Match(
            external_source="pandascore",
            external_id="recent-finished",
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="The International",
            start_time=datetime.now(timezone.utc) - timedelta(days=5),
            format="BO3",
            status="finished",
            winner_team_id=self.liquid.id,
            is_tier1_match=True,
        )
        self.db.add_all([stale, finished])
        self.db.commit()

        response = list_upcoming_matches(include_finished=True, db=self.db)
        external_ids = {item["external_id"] for item in response["items"]}

        self.assertIn("recent-finished", external_ids)
        self.assertNotIn("stale-upcoming", external_ids)

    def test_actionable_scope_includes_strict_and_verified_preview(self):
        response = list_upcoming_matches(analysis_scope="actionable", db=self.db)

        self.assertEqual(response["total"], 2)
        self.assertEqual(
            {item["analysis_mode"] for item in response["items"]},
            {"strict_prediction", "verified_pro_preview"},
        )

    def test_preview_scope_rejects_blocked_tournament_and_tbd(self):
        tbd = Team(name="TBD", external_source="pandascore", external_id="tbd")
        self.db.add(tbd)
        self.db.flush()
        lower_tournament = Match(
            external_source="pandascore",
            external_id="lower-1",
            team_a_id=self.liquid.id,
            team_b_id=self.unknown.id,
            tournament_name="Regional Qualifier",
            start_time=datetime.now(timezone.utc) + timedelta(days=3),
            format="BO3",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="tournament_not_tier1_allowlist",
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
        )
        unknown_opponent = Match(
            external_source="pandascore",
            external_id="tbd-1",
            team_a_id=self.liquid.id,
            team_b_id=tbd.id,
            tournament_name="The International",
            start_time=datetime.now(timezone.utc) + timedelta(days=4),
            format="BO3",
            status="upcoming",
            is_tier1_match=False,
            excluded_reason="team_b_not_tier1",
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
        )
        self.db.add_all([lower_tournament, unknown_opponent])
        self.db.commit()

        response = list_upcoming_matches(analysis_scope="preview", db=self.db)

        self.assertEqual(response["total"], 1)
        self.assertEqual(response["items"][0]["external_id"], "1001")

    def test_preview_decision_summary_is_cautious_and_not_training_eligible(self):
        preview = SimpleNamespace(
            prediction_type="verified_pro_preview",
            team_a_probability=0.54,
            team_b_probability=0.46,
            probability_unit="map_strength",
            confidence="low",
            confidence_score=0.4,
            confidence_guard_applied=True,
            confidence_reasons=["Verified pro preview only."],
            weight_source=None,
            series_outcomes=None,
        )

        with patch("app.api.matches._build_verified_pro_preview", return_value=preview):
            response = list_upcoming_matches(
                analysis_scope="preview",
                include_prediction=True,
                db=self.db,
            )

        item = response["items"][0]
        self.assertEqual(item["decision_status"], "preview")
        self.assertEqual(item["prediction_summary"]["prediction_type"], "verified_pro_preview")
        self.assertEqual(item["prediction_summary"]["confidence"], "low")
        self.assertFalse(item["is_training_eligible"])

    def test_include_prediction_adds_decision_summary(self):
        prediction = SimpleNamespace(
            prediction_type="ensemble",
            team_a_probability=0.58,
            team_b_probability=0.42,
            probability_unit="map_strength",
            confidence="medium",
            confidence_score=0.64,
            confidence_guard_applied=False,
            confidence_reasons=[],
            weight_source="backtest",
            series_outcomes=None,
        )

        with patch("app.api.matches.build_match_prediction", return_value=prediction):
            response = list_upcoming_matches(
                source="pandascore",
                prediction_eligible=True,
                include_prediction=True,
                db=self.db,
            )

        item = response["items"][0]
        self.assertEqual(item["decision_status"], "needs_odds")
        self.assertEqual(item["prediction_summary"]["prediction_type"], "ensemble")
        self.assertEqual(item["prediction_summary"]["probability_unit"], "map_strength")
        self.assertEqual(item["prediction_summary"]["best_side"], "team_a")

    def test_live_prediction_never_requests_market_odds(self):
        self.match.status = "live"
        self.match.start_time = datetime.now(timezone.utc) - timedelta(minutes=20)
        self.db.commit()
        prediction = SimpleNamespace(
            prediction_type="ensemble",
            team_a_probability=0.58,
            team_b_probability=0.42,
            probability_unit="map_strength",
            confidence="high",
            confidence_score=0.8,
            confidence_guard_applied=False,
            confidence_reasons=[],
            weight_source="backtest",
            series_outcomes=None,
        )

        with patch("app.api.matches.build_match_prediction", return_value=prediction):
            response = list_upcoming_matches(
                source="pandascore",
                include_prediction=True,
                db=self.db,
            )

        item = next(row for row in response["items"] if row["external_id"] == "1000")
        self.assertEqual(item["decision_status"], "watch")
        self.assertIn("market evaluation is disabled", item["decision_reason"])

    def test_upcoming_hides_stale_scheduled_rows_by_default(self):
        self.match.start_time = datetime.now(timezone.utc) - timedelta(days=1)
        self.db.commit()

        response = list_upcoming_matches(db=self.db)

        self.assertEqual(response["total"], 1)
        self.assertEqual(response["items"][0]["external_id"], "1001")

    def test_search_can_include_recent_finished_verified_rows(self):
        finished = Match(
            external_source="pandascore",
            external_id="1002",
            team_a_id=self.spirit.id,
            team_b_id=self.mouz.id,
            tournament_name="Esports World Cup",
            start_time=datetime.now(timezone.utc) - timedelta(hours=1),
            format="BO2",
            status="finished",
            is_draw=True,
            is_tier1_match=False,
            dataset_profile="ewc_2026",
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
            is_prediction_eligible=False,
            prediction_guard_level="high",
            prediction_block_reason="team_b_not_active_tier1,match_already_finished",
        )
        self.db.add(finished)
        self.db.commit()

        response = list_upcoming_matches(q="MOUZ", include_finished=True, db=self.db)

        self.assertEqual(response["total"], 1)
        item = response["items"][0]
        self.assertEqual(item["external_id"], "1002")
        self.assertFalse(item["prediction_eligible"])
        self.assertIn("not_upcoming", item["prediction_block_reason"])

    def test_live_and_upcoming_are_sorted_before_finished_results(self):
        now = datetime.now(timezone.utc)
        live = Match(
            external_source="pandascore",
            external_id="live-1",
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="Esports World Cup",
            start_time=now - timedelta(hours=2),
            format="BO2",
            status="live",
            is_tier1_match=True,
            verification_status="verified",
            source_confidence="high",
        )
        finished = Match(
            external_source="pandascore",
            external_id="finished-1",
            team_a_id=self.liquid.id,
            team_b_id=self.spirit.id,
            tournament_name="Esports World Cup",
            start_time=now - timedelta(hours=1),
            format="BO2",
            status="finished",
            winner_team_id=self.liquid.id,
            is_tier1_match=True,
            verification_status="verified",
            source_confidence="high",
        )
        self.db.add_all([live, finished])
        self.db.commit()

        response = list_upcoming_matches(include_finished=True, db=self.db)

        statuses = [item["status"] for item in response["items"]]
        self.assertEqual(statuses[0], "live")
        self.assertLess(statuses.index("upcoming"), statuses.index("finished"))

    def test_verified_pro_analysis_preview_for_blocked_match(self):
        spirit_history = Team(name="Team Spirit", external_source="csv_import", external_id="7119388", is_active_tier1=True)
        mouz_history = Team(name="mouz", external_source="csv_import", external_id="9338413", is_active_tier1=False)
        spirit_academy = Team(
            name="Spirit Academy",
            external_source="pandascore",
            external_id="137540",
            is_active_tier1=False,
        )
        self.db.add_all([spirit_history, mouz_history, spirit_academy])
        self.db.flush()
        target_time = datetime.now(timezone.utc) - timedelta(hours=1)
        historical = Match(
            external_source="csv_import",
            external_id="history-1",
            team_a_id=spirit_history.id,
            team_b_id=mouz_history.id,
            tournament_name="PGL Wallachia",
            start_time=target_time - timedelta(days=30),
            format="BO3",
            status="finished",
            winner_team_id=mouz_history.id,
            is_tier1_match=False,
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=True,
        )
        academy_history = Match(
            external_source="pandascore",
            external_id="academy-history",
            team_a_id=spirit_academy.id,
            team_b_id=mouz_history.id,
            tournament_name="PGL Wallachia",
            start_time=target_time - timedelta(days=20),
            format="BO3",
            status="finished",
            winner_team_id=spirit_academy.id,
            is_tier1_match=False,
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=True,
        )
        future_result = Match(
            external_source="csv_import",
            external_id="future-history",
            team_a_id=spirit_history.id,
            team_b_id=mouz_history.id,
            tournament_name="PGL Wallachia",
            start_time=target_time + timedelta(days=1),
            format="BO3",
            status="finished",
            winner_team_id=spirit_history.id,
            is_tier1_match=False,
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=True,
        )
        finished = Match(
            external_source="pandascore",
            external_id="1003",
            team_a_id=self.spirit.id,
            team_b_id=self.mouz.id,
            tournament_name="Esports World Cup",
            start_time=target_time,
            format="BO2",
            status="finished",
            is_draw=True,
            is_tier1_match=False,
            dataset_profile="ewc_2026",
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
            is_prediction_eligible=False,
            prediction_guard_level="high",
        )
        self.db.add_all([historical, academy_history, future_result, finished])
        self.db.commit()

        preview = get_match_analysis_preview(finished.id, db=self.db)

        self.assertEqual(preview.prediction_type, "verified_pro_preview")
        self.assertEqual(preview.confidence, "low")
        self.assertTrue(preview.confidence_guard_applied)
        self.assertEqual(preview.series_outcomes["format"], "BO2")
        self.assertEqual(preview.model_version, "verified_pro_ensemble_preview_v1")
        self.assertTrue(preview.components["formula"].available)
        self.assertTrue(preview.components["elo"].available)
        self.assertFalse(preview.components["ml"].available)
        self.assertEqual(preview.analytics_context["history_scope"], "verified_pro")
        self.assertEqual(preview.analytics_context["team_a"]["matches_count"], 1)
        self.assertEqual(preview.analytics_context["team_b"]["matches_count"], 2)
        self.assertEqual(preview.analytics_context["head_to_head_matches"], 1)
        self.assertLess(preview.factors.team_rating, 0)
        self.assertFalse(preview.analytics_context["dev_seed_included"])


if __name__ == "__main__":
    unittest.main()
