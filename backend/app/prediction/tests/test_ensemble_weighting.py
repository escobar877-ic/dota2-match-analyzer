from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Backtest, ModelVersion
from app.prediction.ensemble_weighting import (
    MAX_WEIGHT,
    MIN_WEIGHT,
    apply_weight_safety_limits,
    build_weighting_decision,
    calculate_weights_from_backtest,
    get_default_weights,
    get_latest_backtest_metrics,
    get_walk_forward_weight_metrics,
)


class EnsembleWeightingTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

    def tearDown(self) -> None:
        self.db.close()

    def test_default_weights_sum_to_one(self):
        weights = get_default_weights()

        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)

    def test_weights_respect_min_max_limits(self):
        weights = apply_weight_safety_limits({"formula": 0.95, "elo": 0.03, "ml": 0.02})

        self.assertAlmostEqual(sum(weights.values()), 1.0, places=4)
        self.assertTrue(all(MIN_WEIGHT <= value <= MAX_WEIGHT for value in weights.values()))

    def test_ml_weight_decreases_when_ml_worse_than_formula(self):
        weights = calculate_weights_from_backtest(
            self._metrics(
                formula={"log_loss": 0.45, "brier_score": 0.18},
                ml={"log_loss": 0.70, "brier_score": 0.28},
            )
        )

        self.assertLess(weights["ml"], get_default_weights()["ml"])
        self.assertGreater(weights["formula"], get_default_weights()["formula"])

    def test_formula_weight_increases_when_formula_best(self):
        weights = calculate_weights_from_backtest(
            self._metrics(
                formula={"log_loss": 0.40, "brier_score": 0.15},
                elo={"log_loss": 0.55, "brier_score": 0.22},
                ml={"log_loss": 0.60, "brier_score": 0.24},
            )
        )

        self.assertGreater(weights["formula"], get_default_weights()["formula"])

    def test_elo_weight_increases_when_elo_best(self):
        weights = calculate_weights_from_backtest(
            self._metrics(
                formula={"log_loss": 0.55, "brier_score": 0.22},
                elo={"log_loss": 0.39, "brier_score": 0.16},
                ml={"log_loss": 0.58, "brier_score": 0.23},
            )
        )

        self.assertGreater(weights["elo"], get_default_weights()["elo"])

    def test_no_backtest_returns_default_weights(self):
        self.assertIsNone(get_latest_backtest_metrics(self.db))
        decision = build_weighting_decision(None)

        self.assertEqual(decision.weights, get_default_weights())
        self.assertEqual(decision.weight_source, "default")
        self.assertFalse(decision.backtest_metrics_used)

    def test_dev_seed_backtest_adds_warning(self):
        self.db.add(
            Backtest(
                started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                dataset_type="dev_seed",
                matches_count=30,
                metrics_json=self._metrics()["metrics_json"],
                report_path="test",
            )
        )
        self.db.commit()

        decision = build_weighting_decision(get_latest_backtest_metrics(self.db))

        self.assertEqual(decision.weight_source, "backtest")
        self.assertTrue(decision.backtest_metrics_used)
        self.assertEqual(decision.warning, "Weights are based on synthetic dev seed backtest and are not real accuracy.")

    def test_rejected_candidate_backtest_cannot_replace_active_metrics(self):
        active = self._model("active", is_active=True, status="active")
        rejected = self._model("rejected", status="rejected")
        self.db.add_all([active, rejected])
        self.db.flush()
        self.db.add_all(
            [
                Backtest(
                    model_version_id=active.id,
                    started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    dataset_type="real",
                    matches_count=40,
                    metrics_json=self._metrics(ml={"log_loss": 0.45, "brier_score": 0.18})["metrics_json"],
                    report_path="active.json",
                ),
                Backtest(
                    model_version_id=rejected.id,
                    started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    dataset_type="real",
                    matches_count=40,
                    metrics_json=self._metrics(ml={"log_loss": 0.90, "brier_score": 0.40})["metrics_json"],
                    report_path="rejected.json",
                ),
            ]
        )
        self.db.commit()

        metrics = get_latest_backtest_metrics(self.db)

        self.assertEqual(metrics["model_version_id"], active.id)
        self.assertEqual(metrics["metrics_json"]["models"]["ml"]["log_loss"], 0.45)

    def test_approved_walk_forward_weights_take_priority(self):
        decision = build_weighting_decision(
            self._metrics(),
            {
                "production_approved": True,
                "weights": {"formula": 0.2, "elo": 0.15, "ml": 0.65},
                "validation_rows": 45,
            },
        )

        self.assertEqual(decision.weight_source, "walk_forward")
        self.assertEqual(decision.weights, {"formula": 0.2, "elo": 0.15, "ml": 0.65})
        self.assertTrue(decision.walk_forward_metrics_used)
        self.assertFalse(decision.backtest_metrics_used)

    def test_unapproved_walk_forward_falls_back_to_backtest(self):
        decision = build_weighting_decision(
            self._metrics(),
            {
                "production_approved": False,
                "weights": {"formula": 0.1, "elo": 0.25, "ml": 0.65},
            },
        )

        self.assertEqual(decision.weight_source, "backtest")
        self.assertFalse(decision.walk_forward_metrics_used)

    def test_walk_forward_older_than_active_backtest_is_ignored(self):
        now = datetime.now(timezone.utc)
        active = self._model("active", is_active=True, status="active")
        self.db.add(active)
        self.db.flush()
        self.db.add(
            Backtest(
                model_version_id=active.id,
                started_at=now,
                dataset_type="real",
                matches_count=40,
                metrics_json=self._metrics()["metrics_json"],
                report_path="active.json",
            )
        )
        self.db.commit()
        report = {
            "generated_at": (now - timedelta(minutes=1)).isoformat(),
            "active_model_version_id": active.id,
            "stability_gate": {"passed": True},
            "weight_optimization": {
                "production_approved": True,
                "production_weights": {"formula": 0.2, "elo": 0.15, "ml": 0.65},
                "validation_rows": 40,
            },
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "walk_forward.json"
            path.write_text(json.dumps(report), encoding="utf-8")

            result = get_walk_forward_weight_metrics(self.db, report_path=path)

        self.assertIsNone(result)

    def _metrics(
        self,
        *,
        formula: dict | None = None,
        elo: dict | None = None,
        ml: dict | None = None,
        dataset_type: str = "real",
    ) -> dict:
        return {
            "dataset_type": dataset_type,
            "matches_count": 30,
            "metrics_json": {
                "models": {
                    "formula": formula or {"log_loss": 0.50, "brier_score": 0.20},
                    "elo": elo or {"log_loss": 0.55, "brier_score": 0.22},
                    "ml": ml or {"log_loss": 0.45, "brier_score": 0.18},
                }
            },
        }

    @staticmethod
    def _model(version: str, *, is_active: bool = False, status: str) -> ModelVersion:
        return ModelVersion(
            model_name="random_forest",
            model_type="sklearn",
            version=version,
            trained_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metrics_json={},
            artifact_path=f"{version}.pkl",
            is_active=is_active,
            status=status,
        )


if __name__ == "__main__":
    unittest.main()
