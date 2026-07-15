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

from app.database import Base
from app.db.models import Match, Team, TeamRating
from app.ratings.rating_service import recalculate_elo_ratings


class Tier1EloTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)

        self.tier1_a = Team(name="Team Liquid", is_active_tier1=True, tier="tier1")
        self.tier1_b = Team(name="Team Spirit", is_active_tier1=True, tier="tier1")
        self.lower = Team(name="Random Stack", is_active_tier1=False, excluded_reason="team_not_in_tier1_allowlist")
        self.db.add_all([self.tier1_a, self.tier1_b, self.lower])
        self.db.flush()

        self.db.add_all(
            [
                Match(
                    external_source="csv_import",
                    team_a_id=self.tier1_a.id,
                    team_b_id=self.tier1_b.id,
                    tournament_name="TI",
                    status="finished",
                    start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    winner_team_id=self.tier1_a.id,
                    is_tier1_match=True,
                ),
                Match(
                    external_source="dev_seed",
                    team_a_id=self.tier1_a.id,
                    team_b_id=self.tier1_b.id,
                    tournament_name="TI",
                    status="finished",
                    start_time=datetime(2026, 1, 3, tzinfo=timezone.utc),
                    winner_team_id=self.tier1_b.id,
                    is_tier1_match=True,
                ),
                Match(
                    team_a_id=self.tier1_a.id,
                    team_b_id=self.lower.id,
                    tournament_name="TI",
                    status="finished",
                    start_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
                    winner_team_id=self.lower.id,
                    is_tier1_match=False,
                    excluded_reason="team_b_not_tier1",
                ),
            ]
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_elo_recalculation_ignores_non_tier1_matches(self):
        result = recalculate_elo_ratings(self.db)
        ratings = self.db.query(TeamRating).all()

        self.assertEqual(result["processed_matches"], 1)
        self.assertEqual(result["dataset_scope"], "real_only")
        self.assertEqual({rating.team_id for rating in ratings}, {self.tier1_a.id, self.tier1_b.id})
        self.assertEqual({rating.matches_count for rating in ratings}, {1})

    def test_same_real_team_across_sources_shares_rating_history(self):
        liquid_pandascore = Team(
            name="Team Liquid",
            external_source="pandascore",
            external_id="liquid-pandascore",
            is_active_tier1=True,
            tier="tier1",
        )
        spirit_pandascore = Team(
            name="Team Spirit",
            external_source="pandascore",
            external_id="spirit-pandascore",
            is_active_tier1=True,
            tier="tier1",
        )
        self.tier1_a.external_source = "csv_import"
        self.tier1_b.external_source = "csv_import"
        self.db.add_all([liquid_pandascore, spirit_pandascore])
        self.db.flush()
        self.db.add(
            Match(
                external_source="pandascore",
                team_a_id=liquid_pandascore.id,
                team_b_id=spirit_pandascore.id,
                tournament_name="TI",
                status="finished",
                start_time=datetime(2026, 1, 4, tzinfo=timezone.utc),
                winner_team_id=spirit_pandascore.id,
                is_tier1_match=True,
            )
        )
        self.db.commit()

        result = recalculate_elo_ratings(self.db)
        ratings = self.db.query(TeamRating).all()
        elo = {row.team_id: row for row in ratings if row.rating_type == "elo"}

        self.assertEqual(result["processed_matches"], 2)
        self.assertEqual(elo[self.tier1_a.id].matches_count, 2)
        self.assertEqual(elo[liquid_pandascore.id].matches_count, 2)
        self.assertEqual(elo[self.tier1_a.id].rating_value, elo[liquid_pandascore.id].rating_value)

    def test_real_rating_scope_does_not_copy_into_dev_seed_team(self):
        self.tier1_a.external_source = "csv_import"
        synthetic_liquid = Team(
            name="Team Liquid",
            external_source="dev_seed",
            external_id="synthetic-liquid",
            is_active_tier1=True,
            tier="tier1",
        )
        self.db.add(synthetic_liquid)
        self.db.commit()

        recalculate_elo_ratings(self.db)
        rated_team_ids = {
            row.team_id
            for row in self.db.query(TeamRating).filter(TeamRating.rating_type == "elo")
        }

        self.assertIn(self.tier1_a.id, rated_team_ids)
        self.assertNotIn(synthetic_liquid.id, rated_team_ids)


if __name__ == "__main__":
    unittest.main()
