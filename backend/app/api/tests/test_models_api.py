from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.models import (
    get_active_model,
    get_candidate_models,
    get_draft_experiments,
    get_forecast_health,
    get_latest_backtest,
    get_model_promotion_status,
    get_prospective_decision,
)
from app.database import Base
from app.db.models import Backtest, ModelVersion


class ModelsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

    def tearDown(self) -> None:
        self.db.close()

    def test_get_models_active_works(self):
        self.db.add(self._model("active", is_active=True, status="active"))
        self.db.commit()

        response = get_active_model(db=self.db)

        self.assertEqual(response["status"], "active")
        self.assertTrue(response["is_active"])

    def test_get_models_candidates_works(self):
        self.db.add(self._model("candidate", status="candidate"))
        self.db.commit()

        response = get_candidate_models(db=self.db)

        self.assertEqual(len(response), 1)
        self.assertEqual(response[0]["status"], "candidate")

    def test_get_models_candidates_excludes_draft_experiments(self):
        self.db.add(self._model("draft", status="candidate", draft_aware=True))
        self.db.add(self._model("prematch", status="candidate"))
        self.db.commit()

        response = get_candidate_models(db=self.db)

        self.assertEqual([model["version"] for model in response], ["prematch"])

    def test_get_promotion_status_works(self):
        model = self._model("candidate", status="candidate")
        self.db.add(model)
        self.db.commit()

        response = get_model_promotion_status(model.id, db=self.db)

        self.assertEqual(response["id"], model.id)
        self.assertEqual(response["status"], "candidate")
        self.assertIn("dev_seed_warning", response)

    def test_get_draft_experiments_works(self):
        self.db.add(self._model("draft", status="candidate", draft_aware=True))
        self.db.add(self._model("prematch", status="candidate"))
        self.db.commit()

        response = get_draft_experiments(db=self.db)

        self.assertFalse(response["promotion_enabled"])
        self.assertTrue(response["not_used_in_main_prediction"])
        self.assertEqual(len(response["draft_candidates"]), 1)
        self.assertEqual(response["draft_candidates"][0]["version"], "draft")

    def test_get_forecast_health_works(self):
        response = get_forecast_health(db=self.db)

        self.assertIn(response["status"], {"ok", "warning", "failed"})
        self.assertIn("summary", response)
        self.assertIn("missing_final_snapshots", response["summary"])

    def test_get_prospective_decision_is_read_only_and_collecting_without_forecasts(self):
        response = get_prospective_decision(db=self.db)

        self.assertEqual(response["decision_status"], "collecting")
        self.assertFalse(response["automatic_training_enabled"])
        self.assertFalse(response["promotion_allowed"])

    def test_latest_backtest_uses_active_model_not_newer_rejected_candidate(self):
        active = self._model("active", is_active=True, status="active")
        rejected = self._model("rejected", status="rejected")
        self.db.add_all([active, rejected])
        self.db.flush()
        self.db.add_all(
            [
                self._backtest(active.id, datetime(2026, 1, 1, tzinfo=timezone.utc), "active.json"),
                self._backtest(rejected.id, datetime(2026, 1, 2, tzinfo=timezone.utc), "rejected.json"),
            ]
        )
        self.db.commit()

        response = get_latest_backtest(db=self.db)

        self.assertEqual(response["model_version_id"], active.id)
        self.assertEqual(response["model_version"], "active")
        self.assertEqual(response["model_status"], "active")

    def _model(
        self,
        version: str,
        *,
        is_active: bool = False,
        status: str = "candidate",
        draft_aware: bool = False,
    ) -> ModelVersion:
        return ModelVersion(
            model_name="draft_logistic_regression" if draft_aware else "logistic_regression",
            model_type="sklearn",
            version=version,
            trained_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            metrics_json={"dataset_metadata": {"dataset_type": "real"}},
            artifact_path="ml/artifacts/candidates/test/prematch_model.pkl",
            artifact_metadata_json={"draft_aware": True} if draft_aware else {},
            is_active=is_active,
            status=status,
        )

    @staticmethod
    def _backtest(model_version_id: int, started_at: datetime, path: str) -> Backtest:
        return Backtest(
            model_version_id=model_version_id,
            started_at=started_at,
            dataset_type="real",
            matches_count=40,
            metrics_json={"models": {}},
            report_path=path,
        )


if __name__ == "__main__":
    unittest.main()
