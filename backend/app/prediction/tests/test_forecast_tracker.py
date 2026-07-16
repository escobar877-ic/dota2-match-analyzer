from __future__ import annotations

import unittest
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.db.models import Match, PredictionForecast, Team
from app.prediction.forecast_tracker import (
    build_prospective_report,
    horizon_bucket_for_lead_time,
    score_outcome,
    settle_forecasts,
    snapshot_upcoming_forecasts,
)
from app.prediction.forecast_gap_report import build_forecast_gap_report
from app.prediction.schemas import FormulaPredictionResponse, PredictionFactors


class ForecastTrackerTests(unittest.TestCase):
    def test_verified_preview_full_prospective_lifecycle(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        db = Session(engine)
        try:
            team_a = Team(external_source="pandascore", external_id="preview-a", name="Team Yandex")
            team_b = Team(external_source="pandascore", external_id="preview-b", name="Team Spirit")
            db.add_all([team_a, team_b])
            db.flush()
            match = Match(
                external_source="pandascore",
                external_id="preview-match",
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="Esports World Cup",
                start_time=now + timedelta(hours=1),
                format="BO3",
                status="upcoming",
                is_tier1_match=False,
                dataset_profile="upcoming",
                competition_tier="pro",
                verification_status="verified",
                source_confidence="high",
                is_training_eligible=False,
                is_prediction_eligible=False,
                prediction_block_reason="team_a_not_tier1",
            )
            db.add(match)
            db.commit()
            match_id = match.id
            team_a_id = team_a.id
        finally:
            db.close()

        prediction = FormulaPredictionResponse(
            match_id=str(match_id),
            prediction_type="verified_pro_preview",
            model_version="formula_verified_pro_preview_v2",
            team_a_probability=0.6,
            team_b_probability=0.4,
            confidence="low",
            confidence_score=0.4,
            factors=PredictionFactors(
                recent_form=0.0,
                team_rating=0.0,
                head_to_head=0.0,
                hero_pool=0.0,
                roster_stability=0.0,
            ),
            explanation=["Verified preview lifecycle test."],
            warning="Preview only.",
            fallback_used=True,
            series_outcomes={"team_a_win": 0.65, "team_b_win": 0.35, "draw": 0.0},
        )
        factory = lambda: Session(engine)

        snapshot_report = snapshot_upcoming_forecasts(
            hours_ahead=4,
            now=now,
            db_factory=factory,
            preview_prediction_builder=lambda _db, _match: prediction,
        )

        self.assertEqual(snapshot_report["created"], 1)
        self.assertEqual(snapshot_report["preview_eligible_matches"], 1)
        self.assertEqual(snapshot_report["strict_eligible_matches"], 0)
        self.assertEqual(snapshot_report["samples"][0]["horizon_bucket"], "final")
        self.assertEqual(snapshot_report["samples"][0]["evaluation_scope"], "verified_pro_preview")

        db = Session(engine)
        try:
            stored_match = db.get(Match, match_id)
            stored_match.status = "finished"
            stored_match.winner_team_id = team_a_id
            db.commit()
        finally:
            db.close()

        settlement_report = settle_forecasts(
            now=now + timedelta(hours=3),
            db_factory=factory,
            report_writer=lambda _report: None,
        )

        self.assertEqual(settlement_report["settled_now"], 1)
        self.assertEqual(settlement_report["report"]["metrics"]["sample_size"], 0)
        preview_report = settlement_report["report"]["verified_pro_preview"]
        self.assertEqual(preview_report["metrics"]["sample_size"], 1)
        self.assertEqual(preview_report["primary_settled_forecasts"], 1)
        self.assertTrue(preview_report["isolated_from_strict_metrics"])

    def test_strict_snapshot_is_created_after_preview_scope_upgrade(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        db = Session(engine)
        try:
            team_a = Team(name="Team A", is_active_tier1=False)
            team_b = Team(name="Team B", is_active_tier1=True)
            db.add_all([team_a, team_b])
            db.flush()
            match = Match(
                external_source="pandascore",
                external_id="scope-upgrade",
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="Esports World Cup",
                start_time=now + timedelta(hours=1),
                format="BO3",
                status="upcoming",
                is_tier1_match=False,
                competition_tier="pro",
                verification_status="verified",
                source_confidence="high",
                is_prediction_eligible=False,
            )
            db.add(match)
            db.commit()
            match_id = match.id
        finally:
            db.close()

        preview = self._prediction(match_id, "verified_pro_preview")
        strict = self._prediction(match_id, "ensemble")
        factory = lambda: Session(engine)
        first = snapshot_upcoming_forecasts(
            hours_ahead=4,
            now=now,
            db_factory=factory,
            preview_prediction_builder=lambda _db, _match: preview,
        )
        self.assertEqual(first["created"], 1)

        db = Session(engine)
        try:
            stored = db.get(Match, match_id)
            stored.is_tier1_match = True
            stored.is_prediction_eligible = True
            stored.competition_tier = "tier1"
            stored.team_a.is_active_tier1 = True
            db.commit()
        finally:
            db.close()

        second = snapshot_upcoming_forecasts(
            hours_ahead=4,
            now=now,
            db_factory=factory,
            strict_prediction_builder=lambda _db, _match: strict,
        )

        self.assertEqual(second["created"], 1)
        self.assertEqual(second["scope_upgrade_snapshots"], 1)
        self.assertEqual(second["strict_eligible_matches"], 1)
        db = Session(engine)
        try:
            forecasts = db.query(PredictionForecast).order_by(PredictionForecast.id).all()
            self.assertEqual(len(forecasts), 2)
            self.assertEqual(
                {forecast.evaluation_scope for forecast in forecasts},
                {"verified_pro_preview", "strict_tier1"},
            )
        finally:
            db.close()

    def test_scores_correct_bo2_draw(self):
        result = score_outcome(
            {"team_a": 0.2, "draw": 0.5, "team_b": 0.3},
            "draw",
        )

        self.assertTrue(result["correct"])
        self.assertAlmostEqual(result["log_loss"], 0.693147, places=6)
        self.assertAlmostEqual(result["brier_score"], 0.38, places=6)

    def test_wrong_prediction_has_higher_log_loss(self):
        good = score_outcome({"team_a": 0.7, "team_b": 0.3}, "team_a")
        bad = score_outcome({"team_a": 0.2, "team_b": 0.8}, "team_a")

        self.assertLess(good["log_loss"], bad["log_loss"])
        self.assertTrue(good["correct"])
        self.assertFalse(bad["correct"])

    def test_horizon_boundaries(self):
        self.assertIsNone(horizon_bucket_for_lead_time(0))
        self.assertEqual(horizon_bucket_for_lead_time(1.9), "final")
        self.assertEqual(horizon_bucket_for_lead_time(2), "final")
        self.assertEqual(horizon_bucket_for_lead_time(2.1), "day_before")
        self.assertEqual(horizon_bucket_for_lead_time(24), "day_before")
        self.assertEqual(horizon_bucket_for_lead_time(24.1), "early")
        self.assertEqual(horizon_bucket_for_lead_time(168), "early")
        self.assertIsNone(horizon_bucket_for_lead_time(168.1))

    def test_primary_metrics_use_final_horizon_only(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            team_a = Team(name="A")
            team_b = Team(name="B")
            db.add_all([team_a, team_b])
            db.flush()
            match = Match(
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="Test",
                start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
                format="BO3",
                status="finished",
                winner_team_id=team_a.id,
            )
            db.add(match)
            db.flush()
            early = self._forecast(match.id, "early", 100, log_loss=1.0, correct=False)
            final = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            final.components_json = {
                "formula": {
                    "available": True,
                    "team_a_probability": 0.62,
                    "weight": 0.35,
                }
            }
            preview_match = Match(
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="Test Preview",
                start_time=datetime(2026, 1, 3, tzinfo=timezone.utc),
                format="BO3",
                status="finished",
                winner_team_id=team_b.id,
            )
            db.add(preview_match)
            db.flush()
            preview = self._forecast(
                preview_match.id,
                "final",
                1,
                log_loss=0.4,
                correct=True,
            )
            preview.prediction_type = "verified_pro_preview"
            preview.evaluation_scope = "verified_pro_preview"
            preview.actual_outcome = "team_b"
            preview.scheduled_start = preview_match.start_time
            preview.generated_at = preview_match.start_time - timedelta(hours=1)
            db.add_all([early, final, preview])
            db.commit()

            report = build_prospective_report(db)

            self.assertEqual(report["primary_horizon"], "final")
            self.assertEqual(report["metrics"]["sample_size"], 1)
            self.assertEqual(report["metrics"]["log_loss"], 0.2)
            self.assertEqual(report["all_horizons_metrics"]["sample_size"], 2)
            self.assertEqual(report["by_horizon"]["early"]["settled"], 1)
            self.assertEqual(report["by_format"]["BO3"]["sample_size"], 1)
            self.assertEqual(report["all_horizons_by_format"]["BO3"]["sample_size"], 2)
            self.assertEqual(report["component_metrics"]["formula"]["sample_size"], 1)
            self.assertEqual(report["component_metrics"]["ensemble"]["sample_size"], 1)
            self.assertEqual(report["coverage"]["final_capture_rate"], 1.0)
            self.assertEqual(report["quality_gates"]["final_sample_size"], "collecting")
            self.assertFalse(report["quality_gates"]["betting_claims_allowed"])
            self.assertEqual(report["all_scopes_total_forecasts"], 3)
            self.assertEqual(report["verified_pro_preview"]["metrics"]["sample_size"], 1)
            self.assertIn("component_metrics", report["verified_pro_preview"])
            self.assertTrue(report["verified_pro_preview"]["isolated_from_strict_metrics"])
            self.assertFalse(report["verified_pro_preview"]["used_for_training"])
        finally:
            db.close()

    def test_gap_report_flags_missing_final_snapshot(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        try:
            match = self._upcoming_match(db, now + timedelta(hours=1))
            existing = self._forecast(match.id, "early", 100, log_loss=1.0, correct=False)
            existing.status = "pending"
            db.add(existing)
            db.commit()

            report = build_forecast_gap_report(db, now=now, artifact_path=None, refresh_report_path=None)

            self.assertEqual(report["status"], "failed")
            self.assertEqual(report["summary"]["missing_final_snapshots"], 1)
            self.assertEqual(report["missing_snapshots"][0]["missing_horizon"], "final")
        finally:
            db.close()

    def test_gap_report_accepts_existing_final_snapshot(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        try:
            match = self._upcoming_match(db, now + timedelta(hours=1))
            forecast = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            forecast.scheduled_start = match.start_time
            forecast.generated_at = match.start_time - timedelta(hours=1)
            forecast.status = "pending"
            db.add(forecast)
            db.commit()

            report = build_forecast_gap_report(db, now=now, artifact_path=None, refresh_report_path=None)

            self.assertEqual(report["status"], "ok")
            self.assertEqual(report["summary"]["missing_final_snapshots"], 0)
            self.assertEqual(report["checks"]["final_horizon_snapshots"], "ok")
        finally:
            db.close()

    def test_gap_report_finds_historical_missing_final(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 3, 10, tzinfo=timezone.utc)
        try:
            match = self._finished_tracked_match(db, now - timedelta(hours=3))
            forecast = self._forecast(match.id, "early", 48, log_loss=0.4, correct=True)
            forecast.scheduled_start = match.start_time
            db.add(forecast)
            db.commit()

            report = build_forecast_gap_report(
                db,
                now=now,
                artifact_path=None,
                refresh_report_path=None,
            )

            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["summary"]["tracked_finished_matches"], 1)
            self.assertEqual(report["summary"]["historical_missing_final_snapshots"], 1)
            self.assertEqual(report["historical_final_gaps"][0]["match_id"], match.id)
        finally:
            db.close()

    def test_gap_report_detects_schedule_drift(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        try:
            match = self._upcoming_match(db, now + timedelta(hours=10))
            forecast = self._forecast(match.id, "day_before", 10, log_loss=0.2, correct=True)
            forecast.scheduled_start = match.start_time - timedelta(hours=2)
            forecast.status = "pending"
            db.add(forecast)
            db.commit()

            report = build_forecast_gap_report(
                db,
                now=now,
                artifact_path=None,
                refresh_report_path=None,
            )

            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["summary"]["schedule_drift_forecasts"], 1)
            self.assertEqual(report["schedule_drift_gaps"][0]["drift_minutes"], 120.0)
        finally:
            db.close()

    def test_gap_report_detects_stale_refresh(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "prediction_refresh_report.json"
                path.write_text(
                    json.dumps(
                        {
                            "status": "ok",
                            "generated_at": (now - timedelta(hours=2)).isoformat(),
                        }
                    ),
                    encoding="utf-8",
                )
                report = build_forecast_gap_report(
                    db,
                    now=now,
                    artifact_path=None,
                    refresh_report_path=path,
                    max_refresh_age_minutes=45,
                )

            self.assertEqual(report["status"], "warning")
            self.assertTrue(report["summary"]["refresh_stale"])
            self.assertEqual(report["summary"]["refresh_age_minutes"], 120.0)
            self.assertEqual(report["checks"]["scheduler_freshness"], "warning")
        finally:
            db.close()

    def test_gap_report_uses_cycle_status_for_latest_refresh(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        try:
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "prediction_refresh_report.json"
                path.write_text(
                    json.dumps(
                        {
                            "status": "warning",
                            "cycle_status": "ok",
                            "generated_at": now.isoformat(),
                        }
                    ),
                    encoding="utf-8",
                )
                report = build_forecast_gap_report(
                    db,
                    now=now,
                    artifact_path=None,
                    refresh_report_path=path,
                )

            self.assertEqual(report["summary"]["refresh_status"], "ok")
            self.assertEqual(report["checks"]["refresh_report"], "ok")
        finally:
            db.close()

    def test_primary_metrics_ignore_superseded_final_schedule(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            match = self._finished_tracked_match(
                db,
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
            superseded = self._forecast(match.id, "final", 1, log_loss=1.2, correct=False)
            superseded.is_primary = False
            current = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            current.scheduled_start = superseded.scheduled_start + timedelta(hours=2)
            db.add_all([superseded, current])
            db.commit()

            report = build_prospective_report(db)

            self.assertEqual(report["metrics"]["sample_size"], 1)
            self.assertEqual(report["metrics"]["log_loss"], 0.2)
            self.assertEqual(report["superseded_final_forecasts"], 1)
        finally:
            db.close()

    def test_metrics_deduplicate_schedule_revisions_per_match_and_horizon(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            match = self._finished_tracked_match(
                db,
                datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
            older = self._forecast(match.id, "day_before", 8, log_loss=1.1, correct=False)
            newer = self._forecast(match.id, "day_before", 6, log_loss=0.3, correct=True)
            older.scheduled_start = older.scheduled_start - timedelta(hours=2)
            db.add_all([older, newer])
            db.commit()

            report = build_prospective_report(db)

            self.assertEqual(report["raw_settled_forecasts"], 2)
            self.assertEqual(report["settled_forecasts"], 1)
            self.assertEqual(report["by_horizon"]["day_before"]["settled"], 1)
            self.assertEqual(report["all_horizons_metrics"]["log_loss"], 0.3)
        finally:
            db.close()

    def test_metrics_use_actual_start_to_reclassify_forecast_horizon(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        try:
            actual_start = datetime(2026, 1, 2, tzinfo=timezone.utc)
            match = self._finished_tracked_match(db, actual_start)
            forecast = self._forecast(match.id, "day_before", 1, log_loss=0.2, correct=True)
            forecast.horizon_bucket = "day_before"
            db.add(forecast)
            db.commit()

            report = build_prospective_report(db)

            self.assertEqual(report["by_horizon"]["final"]["settled"], 1)
            self.assertEqual(report["by_horizon"]["day_before"]["settled"], 0)
            self.assertEqual(report["primary_settled_forecasts"], 1)
        finally:
            db.close()

    def test_settlement_voids_forecast_generated_after_actual_start(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        now = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
        db = Session(engine)
        try:
            match = self._finished_tracked_match(db, now - timedelta(hours=1))
            forecast = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            forecast.generated_at = now
            forecast.status = "pending"
            db.add(forecast)
            db.commit()
            forecast_id = forecast.id
        finally:
            db.close()

        report = settle_forecasts(
            now=now + timedelta(hours=1),
            db_factory=lambda: Session(engine),
            report_writer=lambda _report: None,
        )

        self.assertEqual(report["settled_now"], 0)
        self.assertEqual(report["voided_now"], 1)
        db = Session(engine)
        try:
            stored = db.get(PredictionForecast, forecast_id)
            self.assertEqual(stored.status, "void")
            self.assertIn("after actual match start", stored.guard_reasons_json[-1])
        finally:
            db.close()

    def test_preview_snapshot_does_not_satisfy_strict_gap_check(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 1, 10, tzinfo=timezone.utc)
        try:
            match = self._upcoming_match(db, now + timedelta(hours=1))
            preview = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            preview.status = "pending"
            preview.prediction_type = "verified_pro_preview"
            preview.evaluation_scope = "verified_pro_preview"
            preview.scheduled_start = match.start_time
            db.add(preview)
            db.commit()

            report = build_forecast_gap_report(db, now=now, artifact_path=None, refresh_report_path=None)

            self.assertEqual(report["summary"]["missing_final_snapshots"], 1)
            self.assertEqual(report["checks"]["final_horizon_snapshots"], "failed")
        finally:
            db.close()

    def test_preview_final_does_not_fill_historical_strict_final_gap(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 3, 10, tzinfo=timezone.utc)
        try:
            match = self._finished_tracked_match(db, now - timedelta(hours=3))
            strict_early = self._forecast(match.id, "early", 48, log_loss=0.4, correct=True)
            preview_final = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            preview_final.prediction_type = "verified_pro_preview"
            preview_final.evaluation_scope = "verified_pro_preview"
            db.add_all([strict_early, preview_final])
            db.commit()

            report = build_forecast_gap_report(db, now=now, artifact_path=None, refresh_report_path=None)

            self.assertEqual(report["summary"]["tracked_finished_matches"], 1)
            self.assertEqual(report["summary"]["historical_missing_final_snapshots"], 1)
        finally:
            db.close()

    def test_gap_report_finds_settlement_gap(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        db = Session(engine)
        now = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
        try:
            team_a = Team(name="A")
            team_b = Team(name="B")
            db.add_all([team_a, team_b])
            db.flush()
            match = Match(
                team_a_id=team_a.id,
                team_b_id=team_b.id,
                tournament_name="Test",
                start_time=now - timedelta(hours=2),
                format="BO3",
                status="finished",
                winner_team_id=team_a.id,
                is_tier1_match=True,
            )
            db.add(match)
            db.flush()
            forecast = self._forecast(match.id, "final", 1, log_loss=0.2, correct=True)
            forecast.status = "pending"
            db.add(forecast)
            db.commit()

            report = build_forecast_gap_report(db, now=now, artifact_path=None, refresh_report_path=None)

            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["summary"]["pending_settlement_gaps"], 1)
            self.assertEqual(report["settlement_gaps"][0]["match_id"], match.id)
        finally:
            db.close()

    @staticmethod
    def _prediction(match_id: int, prediction_type: str) -> FormulaPredictionResponse:
        return FormulaPredictionResponse(
            match_id=str(match_id),
            prediction_type=prediction_type,
            model_version="test-model",
            team_a_probability=0.6,
            team_b_probability=0.4,
            confidence="medium",
            confidence_score=0.6,
            factors=PredictionFactors(
                recent_form=0.0,
                team_rating=0.0,
                head_to_head=0.0,
                hero_pool=0.0,
                roster_stability=0.0,
            ),
            explanation=["Forecast scope test."],
            warning="Forecast scope test.",
            fallback_used=False,
            series_outcomes={"team_a_win": 0.65, "team_b_win": 0.35, "draw": 0.0},
        )

    @staticmethod
    def _forecast(
        match_id: int,
        horizon: str,
        lead_hours: float,
        *,
        log_loss: float,
        correct: bool,
    ) -> PredictionForecast:
        scheduled_start = datetime(2026, 1, 2, tzinfo=timezone.utc)
        generated_at = scheduled_start - timedelta(hours=lead_hours)
        return PredictionForecast(
            match_id=match_id,
            horizon_bucket=horizon,
            is_primary=horizon == "final",
            generated_at=generated_at,
            scheduled_start=scheduled_start,
            lead_time_hours=lead_hours,
            prediction_type="ensemble",
            model_version="test",
            team_a_probability=0.6,
            team_b_probability=0.4,
            confidence_label="medium",
            confidence_score=0.6,
            predicted_outcomes_json={"team_a": 0.6, "team_b": 0.4},
            status="settled",
            actual_outcome="team_a",
            log_loss=log_loss,
            brier_score=0.2,
            correct=correct,
            settled_at=scheduled_start,
        )

    @staticmethod
    def _upcoming_match(db: Session, start_time: datetime) -> Match:
        team_a = Team(name="A")
        team_b = Team(name="B")
        db.add_all([team_a, team_b])
        db.flush()
        match = Match(
            external_source="pandascore",
            external_id="scheduled-1",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="Test",
            start_time=start_time,
            format="BO3",
            status="upcoming",
            is_tier1_match=True,
            is_prediction_eligible=True,
        )
        db.add(match)
        db.flush()
        return match

    @staticmethod
    def _finished_tracked_match(db: Session, start_time: datetime) -> Match:
        team_a = Team(name=f"A-{start_time.timestamp()}")
        team_b = Team(name=f"B-{start_time.timestamp()}")
        db.add_all([team_a, team_b])
        db.flush()
        match = Match(
            external_source="pandascore",
            external_id=f"finished-{start_time.timestamp()}",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="Test",
            start_time=start_time,
            format="BO3",
            status="finished",
            winner_team_id=team_a.id,
            is_tier1_match=True,
            is_prediction_eligible=True,
        )
        db.add(match)
        db.flush()
        return match


if __name__ == "__main__":
    unittest.main()
