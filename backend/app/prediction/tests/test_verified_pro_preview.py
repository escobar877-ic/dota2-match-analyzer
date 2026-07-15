from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Match, Team
from app.prediction.schemas import EnsembleComponent, FormulaPredictionResponse, PredictionFactors
from app.prediction.verified_pro_preview import build_verified_pro_preview


class VerifiedProPreviewTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        team_a = Team(name="Team Yandex", external_source="pandascore")
        team_b = Team(name="Team Spirit", external_source="pandascore")
        self.db.add_all([team_a, team_b])
        self.db.flush()
        self.match = Match(
            external_source="pandascore",
            external_id="ewc-test",
            team_a_id=team_a.id,
            team_b_id=team_b.id,
            tournament_name="Esports World Cup",
            start_time=datetime(2026, 7, 20, tzinfo=timezone.utc),
            format="BO3",
            status="upcoming",
            competition_tier="pro",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=False,
            is_tier1_match=False,
        )
        self.db.add(self.match)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_guarded_ensemble_components_remain_isolated_preview(self):
        ensemble = FormulaPredictionResponse(
            match_id=str(self.match.id),
            prediction_type="ensemble",
            model_version="ensemble_v1",
            team_a_probability=0.58,
            team_b_probability=0.42,
            confidence="medium",
            confidence_score=0.64,
            factors=PredictionFactors(
                recent_form=0.01,
                team_rating=0.02,
                head_to_head=0.0,
                hero_pool=0.0,
                roster_stability=0.0,
            ),
            explanation={
                "summary": "Components combined.",
                "positive_factors": [],
                "negative_factors": [],
            },
            warning="Ensemble warning.",
            components={
                "formula": EnsembleComponent(available=True, team_a_probability=0.56, weight=0.55),
                "elo": EnsembleComponent(available=True, team_a_probability=0.57, weight=0.21),
                "ml": EnsembleComponent(available=True, team_a_probability=0.62, weight=0.24),
            },
            weights={"formula": 0.55, "elo": 0.21, "ml": 0.24},
            analytics_context={
                "head_to_head_matches": 3,
                "team_a": {"matches_count": 20, "roster_count": 5, "stats_count": 10},
                "team_b": {"matches_count": 20, "roster_count": 5, "stats_count": 10},
            },
        )

        with patch(
            "app.prediction.verified_pro_preview.try_predict_with_ensemble",
            return_value=ensemble,
        ) as builder:
            result = build_verified_pro_preview(self.db, self.match)

        builder.assert_called_once()
        self.assertEqual(result.prediction_type, "verified_pro_preview")
        self.assertEqual(result.model_version, "verified_pro_ensemble_preview_v1")
        self.assertEqual(result.confidence, "low")
        self.assertLessEqual(result.confidence_score, 0.45)
        self.assertTrue(result.fallback_used)
        self.assertEqual(result.fallback_reason, "verified_pro_not_strict_tier1")
        self.assertEqual(set(result.components), {"formula", "elo", "ml"})
        self.assertEqual(
            result.explanation["preview_scope"],
            "verified_pro_only_not_used_in_main_prediction",
        )
        self.assertFalse(self.match.is_training_eligible)


if __name__ == "__main__":
    unittest.main()
