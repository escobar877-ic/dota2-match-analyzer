from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
from app.db.models import Backtest, ModelVersion
from ml.training.model_promotion import (
    archive_old_models,
    auto_promote_if_better,
    compare_candidate_to_active,
    promote_model_version,
    reject_model_version,
    should_promote_candidate,
)


class ModelPromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.artifact_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_training_candidate_does_not_replace_active_by_default(self):
        active = self._model("active", is_active=True, status="active")
        candidate = self._model("candidate", is_active=False, status="candidate")
        self.db.add_all([active, candidate])
        self.db.commit()

        active_rows = self.db.query(ModelVersion).filter(ModelVersion.is_active.is_(True)).all()

        self.assertEqual(len(active_rows), 1)
        self.assertEqual(active_rows[0].version, "active")
        self.assertEqual(candidate.status, "candidate")

    def test_promote_changes_active_model(self):
        old_active = self._model("active", is_active=True, status="active")
        candidate = self._model("candidate", is_active=False, status="candidate")
        self.db.add_all([old_active, candidate])
        self.db.commit()
        active_paths = self._active_paths()

        with self._patch_active_paths(active_paths):
            result = promote_model_version(candidate.id, "reviewed", db=self.db)

        self.assertTrue(result["promoted"])
        self.assertTrue(candidate.is_active)
        self.assertEqual(candidate.status, "active")
        self.assertFalse(old_active.is_active)
        self.assertEqual(old_active.status, "archived")
        self.assertTrue(active_paths["model"].exists())

    def test_reject_marks_rejected(self):
        candidate = self._model("candidate", status="candidate")
        self.db.add(candidate)
        self.db.commit()

        result = reject_model_version(candidate.id, "bad calibration", db=self.db)

        self.assertTrue(result["rejected"])
        self.assertEqual(candidate.status, "rejected")
        self.assertIsNotNone(candidate.rejected_at)

    def test_cannot_auto_promote_without_backtest(self):
        candidate = self._model("candidate", status="candidate")
        self.db.add(candidate)
        self.db.commit()

        with patch("ml.training.model_promotion.SessionLocal", return_value=self.db):
            result = auto_promote_if_better()

        self.assertFalse(result["promoted"])
        self.assertIn("Backtest is required", result["reason"])

    def test_insufficient_coverage_blocks_real_auto_promotion(self):
        active = self._model("active", is_active=True, status="active")
        candidate = self._model("candidate", status="candidate")
        self.db.add_all([active, candidate])
        self.db.flush()
        self.db.add(self._backtest(candidate.id, dataset_type="real"))
        self.db.commit()
        coverage_path = self.artifact_dir / "coverage.json"
        coverage_path.write_text(json.dumps({"training_readiness": "insufficient"}), encoding="utf-8")

        with patch("ml.training.model_promotion.SessionLocal", return_value=self.db), patch(
            "ml.training.model_promotion.DATA_COVERAGE_REPORT_PATH",
            coverage_path,
        ):
            result = auto_promote_if_better()

        self.assertFalse(result["promoted"])
        self.assertIn("insufficient", result["reason"])

    def test_real_auto_promotion_requires_minimum_real_rows(self):
        active = self._model("active", is_active=True, status="active")
        candidate = self._model("candidate", status="candidate")
        self.db.add_all([active, candidate])
        self.db.flush()
        self.db.add(self._backtest(candidate.id, dataset_type="real"))
        self.db.commit()
        coverage_path = self.artifact_dir / "coverage.json"
        coverage_path.write_text(
            json.dumps({"training_readiness": "usable", "real_tier1_historical_matches_count": 299}),
            encoding="utf-8",
        )

        with patch("ml.training.model_promotion.SessionLocal", return_value=self.db), patch(
            "ml.training.model_promotion.DATA_COVERAGE_REPORT_PATH",
            coverage_path,
        ):
            result = auto_promote_if_better()

        self.assertFalse(result["promoted"])
        self.assertIn("at least 300 real Tier 1", result["reason"])

    def test_real_auto_promotion_rejects_candidate_with_dev_seed_rows(self):
        active = self._model("active", is_active=True, status="active")
        candidate = self._model("candidate", status="candidate")
        candidate.metrics_json["dataset_metadata"]["dev_seed_rows_count"] = 120
        self.db.add_all([active, candidate])
        self.db.flush()
        self.db.add(self._backtest(candidate.id, dataset_type="real"))
        self.db.commit()
        coverage_path = self.artifact_dir / "coverage.json"
        coverage_path.write_text(
            json.dumps(
                {
                    "training_readiness": "usable",
                    "real_tier1_historical_matches_count": 300,
                    "dev_seed_only": False,
                    "matches_by_source": {"dev_seed": 120, "pandascore": 300},
                }
            ),
            encoding="utf-8",
        )

        with patch("ml.training.model_promotion.SessionLocal", return_value=self.db), patch(
            "ml.training.model_promotion.DATA_COVERAGE_REPORT_PATH",
            coverage_path,
        ):
            result = auto_promote_if_better()

        self.assertFalse(result["promoted"])
        self.assertIn("Candidate training dataset includes dev_seed rows", result["reason"])

    def test_dev_seed_promotion_requires_explicit_dev_flag(self):
        candidate = self._model("candidate", status="candidate", dataset_type="dev_seed")
        self.db.add(candidate)
        self.db.flush()
        self.db.add(self._backtest(candidate.id, dataset_type="dev_seed"))
        self.db.commit()

        with patch("ml.training.model_promotion.SessionLocal", return_value=self.db):
            result = auto_promote_if_better()

        self.assertFalse(result["promoted"])
        self.assertIn("Dev seed", result["reason"])

    def test_archive_old_models_keeps_last_n(self):
        for index in range(7):
            self.db.add(self._model(f"candidate-{index}", status="candidate", offset=index))
        self.db.commit()

        archived = archive_old_models(keep_last=5, db=self.db)

        self.assertEqual(archived, 2)
        self.assertEqual(self.db.query(ModelVersion).filter(ModelVersion.status == "candidate").count(), 5)

    def test_should_promote_candidate_requires_non_worse_metrics(self):
        result = should_promote_candidate(
            {"log_loss": 0.45, "brier_score": 0.2, "calibration_error": 0.05, "training_completed": True, "artifacts_exist": True},
            {"log_loss": 0.44, "brier_score": 0.19},
            "real",
        )

        self.assertFalse(result["should_promote"])
        self.assertIn("Candidate log_loss is worse than active model.", result["reasons"])

    def test_comparison_uses_same_window_backtest_metrics(self):
        active = self._model("active", is_active=True, status="active")
        candidate = self._model("candidate", status="candidate")
        self.db.add_all([active, candidate])
        self.db.flush()
        backtest = self._backtest(
            candidate.id,
            dataset_type="real",
            metrics_json={
                "models": {
                    "ml": {"log_loss": 0.41, "brier_score": 0.19, "calibration_error": 0.05}
                },
                "active_ml_comparison": {
                    "metrics": {"log_loss": 0.40, "brier_score": 0.18, "calibration_error": 0.05}
                },
            },
        )

        result = compare_candidate_to_active(candidate, active, backtest)

        self.assertFalse(result["should_promote"])
        self.assertIn("Candidate log_loss is worse than active model.", result["reasons"])

    def _model(
        self,
        version: str,
        *,
        is_active: bool = False,
        status: str = "candidate",
        dataset_type: str = "real",
        offset: int = 0,
    ) -> ModelVersion:
        metadata = self._artifact_bundle(version)
        return ModelVersion(
            model_name="logistic_regression",
            model_type="sklearn",
            version=version,
            trained_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=offset),
            metrics_json={
                "dataset_metadata": {"dataset_type": dataset_type, "source": dataset_type},
                "test_metrics": {"log_loss": 0.4, "brier_score": 0.18, "calibration_error": 0.05},
            },
            artifact_path=metadata["model_path"],
            artifact_metadata_json=metadata,
            is_active=is_active,
            status=status,
        )

    def _artifact_bundle(self, version: str) -> dict[str, str]:
        directory = self.artifact_dir / version
        directory.mkdir(parents=True, exist_ok=True)
        model_path = directory / "prematch_model.pkl"
        schema_path = directory / "feature_schema.json"
        calibrator_path = directory / "calibrator.pkl"
        model_path.write_bytes(b"model")
        schema_path.write_text("{}", encoding="utf-8")
        calibrator_path.write_bytes(b"calibrator")
        return {
            "model_path": str(model_path),
            "feature_schema_path": str(schema_path),
            "calibrator_path": str(calibrator_path),
            "training_report_path": str(directory / "training_report.json"),
        }

    def _backtest(
        self,
        model_version_id: int,
        *,
        dataset_type: str,
        metrics_json: dict | None = None,
    ) -> Backtest:
        return Backtest(
            model_version_id=model_version_id,
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
            finished_at=datetime(2026, 1, 2, 1, tzinfo=timezone.utc),
            dataset_type=dataset_type,
            matches_count=100,
            metrics_json=metrics_json or {},
            report_path="ml/artifacts/backtest_report.json",
        )

    def _active_paths(self) -> dict[str, Path]:
        return {
            "model": self.artifact_dir / "active_model.pkl",
            "schema": self.artifact_dir / "active_schema.json",
            "calibrator": self.artifact_dir / "active_calibrator.pkl",
        }

    def _patch_active_paths(self, paths: dict[str, Path]):
        return patch.multiple(
            "ml.training.model_promotion",
            MODEL_ARTIFACT_PATH=paths["model"],
            FEATURE_SCHEMA_PATH=paths["schema"],
            CALIBRATOR_ARTIFACT_PATH=paths["calibrator"],
        )


if __name__ == "__main__":
    unittest.main()
