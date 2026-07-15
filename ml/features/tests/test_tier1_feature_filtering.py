from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

backend_dir = Path(__file__).resolve().parents[3] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.database import Base
from app.db.models import Match, MatchPrematchFeature, Team
from ml.features.build_prematch_features import build_and_store_prematch_features


class Tier1FeatureFilteringTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        self.tier1_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.tier1_a, self.lower])
        self.db.flush()
        self.db.add(
            Match(
                team_a_id=self.tier1_a.id,
                team_b_id=self.lower.id,
                tournament_name="TI",
                status="upcoming",
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                is_tier1_match=False,
                excluded_reason="team_b_not_tier1",
            )
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_feature_builder_ignores_non_tier1_matches(self):
        with patch("ml.features.build_prematch_features.SessionLocal", return_value=self.db):
            result = build_and_store_prematch_features()

        self.assertEqual(result, {"created": 0, "updated": 0, "skipped": 0})
        self.assertEqual(self.db.query(MatchPrematchFeature).count(), 0)

    def test_feature_builder_includes_verified_pro_without_active_tier1_teams(self):
        self.db.add(
            Match(
                team_a_id=self.tier1_a.id,
                team_b_id=self.lower.id,
                tournament_name="The International",
                status="finished",
                start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
                winner_team_id=self.tier1_a.id,
                is_tier1_match=True,
            )
        )
        self.db.commit()

        with patch("ml.features.build_prematch_features.SessionLocal", return_value=self.db):
            result = build_and_store_prematch_features()

        self.assertEqual(result["created"], 1)
        self.assertEqual(self.db.query(MatchPrematchFeature).count(), 1)


if __name__ == "__main__":
    unittest.main()
