from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

repo_root = Path(__file__).resolve().parents[3]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from app.database import Base
from app.db.models import Team
from app.tier_filter.schemas import Tier1Config, Tier1TeamConfig, Tier1TournamentConfig
from app.tier_filter.tier1_matcher import Tier1Matcher
from worker.data_ingestion.data_quality import validate_match, validate_team
from worker.data_ingestion.db import upsert_match
from worker.data_ingestion.pro_match_quality import validate_verified_pro_match
from worker.data_ingestion.normalizer import NormalizedMatch, NormalizedTeam


class DataQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.matcher = Tier1Matcher(
            Tier1Config(
                teams=[Tier1TeamConfig(name="Team Liquid", aliases=["Liquid"])],
                tournaments=[Tier1TournamentConfig(name="The International", aliases=["TI"])],
            )
        )

    def tearDown(self) -> None:
        self.db.close()

    def test_unknown_team_is_not_tier1(self):
        result = validate_team(NormalizedTeam(external_source="test", external_id="1", name="Unknown Stack"), self.matcher)

        self.assertFalse(result.is_tier1)
        self.assertIn("team_not_in_tier1_allowlist", result.reasons)

    def test_known_alias_matches_tier1(self):
        result = validate_team(NormalizedTeam(external_source="test", external_id="1", name="Liquid"), self.matcher)

        self.assertTrue(result.is_tier1)

    def test_lower_tier_tournament_excluded(self):
        result = validate_match(
            NormalizedMatch(
                external_source="test",
                external_id="m1",
                team_a_external_id="a",
                team_b_external_id="b",
                tournament_name="Small Local Cup",
                start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            team_a_is_tier1=True,
            team_b_is_tier1=True,
            matcher=self.matcher,
        )

        self.assertIn("tournament_not_tier1_allowlist", result.reasons)

    def test_verified_pro_accepts_current_named_tournament_without_tier1_teams(self):
        result = validate_verified_pro_match(
            NormalizedMatch(
                external_source="pandascore",
                external_id="m1",
                team_a_external_id="1651",
                team_b_external_id="138842",
                team_a_name="Virtus.pro",
                team_b_name="HULIGANI",
                tournament_name="The International",
                start_time=datetime(2026, 6, 27, tzinfo=timezone.utc),
                status="finished",
                winner_team_external_id="1651",
            ),
            now=datetime(2026, 6, 27, tzinfo=timezone.utc),
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.quality_tier, "verified_pro")

    def test_verified_pro_rejects_low_confidence_tournament_even_when_recent(self):
        result = validate_verified_pro_match(
            NormalizedMatch(
                external_source="pandascore",
                external_id="m2",
                team_a_external_id="132981",
                team_b_external_id="138732",
                team_a_name="Carstensz",
                team_b_name="Mentality Monster",
                tournament_name="EPL World Series",
                start_time=datetime(2026, 6, 27, tzinfo=timezone.utc),
                status="finished",
                winner_team_external_id="132981",
            ),
            now=datetime(2026, 6, 27, tzinfo=timezone.utc),
        )

        self.assertFalse(result.valid)
        self.assertIn("tournament_not_verified_pro", result.reasons)

    def test_verified_pro_rejects_missing_tournament_and_stale_match(self):
        result = validate_verified_pro_match(
            NormalizedMatch(
                external_source="pandascore",
                external_id="m3",
                team_a_external_id="a",
                team_b_external_id="b",
                team_a_name="Team A",
                team_b_name="Team B",
                tournament_name=None,
                start_time=datetime(2023, 1, 1, tzinfo=timezone.utc),
                status="finished",
                winner_team_external_id="a",
            ),
            now=datetime(2026, 6, 27, tzinfo=timezone.utc),
        )

        self.assertFalse(result.valid)
        self.assertIn("missing_tournament_name", result.reasons)
        self.assertIn("match_too_old_for_verified_pro", result.reasons)

    def test_duplicate_external_id_updates_existing(self):
        self.db.add_all(
            [
                Team(external_source="test", external_id="a", name="Liquid", is_active_tier1=True, tier="tier1"),
                Team(external_source="test", external_id="b", name="Liquid", is_active_tier1=True, tier="tier1"),
            ]
        )
        self.db.commit()
        match = NormalizedMatch(
            external_source="test",
            external_id="m1",
            team_a_external_id="a",
            team_b_external_id="b",
            tournament_name="TI",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="upcoming",
        )

        _, created_first = upsert_match(self.db, match, matcher=self.matcher)
        _, created_second = upsert_match(self.db, match, matcher=self.matcher)

        self.assertTrue(created_first)
        self.assertFalse(created_second)

    def test_upsert_match_can_store_verified_pro_without_tier1_team_allowlist(self):
        match = NormalizedMatch(
            external_source="pandascore",
            external_id="1540327",
            team_a_external_id="1651",
            team_b_external_id="138842",
            team_a_name="Virtus.pro",
            team_b_name="HULIGANI",
            tournament_name="The International",
            start_time=datetime(2026, 6, 27, tzinfo=timezone.utc),
            status="finished",
            winner_team_external_id="1651",
        )

        created_match, was_created = upsert_match(self.db, match, matcher=self.matcher, quality_scope="verified_pro")

        self.assertTrue(was_created)
        self.assertIsNotNone(created_match)
        self.assertTrue(created_match.is_tier1_match)
        self.assertIsNone(created_match.excluded_reason)


if __name__ == "__main__":
    unittest.main()
