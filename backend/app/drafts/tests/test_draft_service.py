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

from app.api.matches import get_match_draft, get_match_draft_features
from app.database import Base
from app.db.models import Hero, Match, MatchDraft, Team
from app.drafts.draft_service import get_draft_completeness


class DraftServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", is_active_tier1=True)
        self.team_b = Team(name="Team Spirit", is_active_tier1=True)
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.heroes = [
            Hero(hero_id=index + 1, name=f"hero_{index + 1}", localized_name=f"Hero {index + 1}", roles_json=["Carry"])
            for index in range(12)
        ]
        self.db.add_all(self.heroes)
        self.db.flush()
        self.match = Match(
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            status="upcoming",
            is_tier1_match=True,
        )
        self.db.add(self.match)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_draft_completeness_false_when_missing(self):
        completeness = get_draft_completeness(self.db, self.match.id)

        self.assertFalse(completeness["draft_available"])
        self.assertFalse(completeness["draft_complete"])

    def test_draft_completeness_true_when_5v5_picks_exist(self):
        self._add_picks(5)

        completeness = get_draft_completeness(self.db, self.match.id)

        self.assertTrue(completeness["draft_available"])
        self.assertTrue(completeness["draft_complete"])
        self.assertEqual(completeness["team_a_picks_count"], 5)
        self.assertEqual(completeness["team_b_picks_count"], 5)

    def test_api_match_draft_works(self):
        self._add_picks(1)

        response = get_match_draft(self.match.id, db=self.db)

        self.assertTrue(response["draft_available"])
        self.assertEqual(len(response["entries"]), 2)

    def test_api_uses_read_only_live_context_when_stored_draft_is_missing(self):
        self.match.status = "live"
        self.db.commit()
        context = {
            "draft_available": True,
            "dota_match_id": "8899120700",
            "series_id": "1120911",
            "game_time_seconds": 900,
            "source_note": "Live picks only.",
            "team_a": {
                "name": self.team_a.name,
                "side": "dire",
                "score": 6,
                "picks": [{"hero_id": 1, "localized_name": "Anti-Mage", "hero_name": "antimage"}],
            },
            "team_b": {
                "name": self.team_b.name,
                "side": "radiant",
                "score": 8,
                "picks": [{"hero_id": 2, "localized_name": "Axe", "hero_name": "axe"}],
            },
        }

        with patch("app.api.matches.load_live_match_context", return_value=context):
            response = get_match_draft(self.match.id, db=self.db)

        self.assertTrue(response["draft_available"])
        self.assertEqual(response["live_context"]["dota_match_id"], "8899120700")
        self.assertEqual([entry["hero"]["localized_name"] for entry in response["entries"]], ["Anti-Mage", "Axe"])
        self.assertEqual(self.db.query(MatchDraft).count(), 0)

    def test_api_explains_when_live_draft_identity_is_not_verified(self):
        self.match.status = "live"
        self.db.commit()
        availability = {
            "status": "unavailable",
            "reason": "no_exact_5v5_account_identity_match",
            "message": "OpenDota live rows could not be matched safely.",
            "identity_method": None,
        }

        with patch("app.api.matches.load_live_match_context", return_value=None), patch(
            "app.api.matches.load_live_match_availability",
            return_value=availability,
        ):
            response = get_match_draft(self.match.id, db=self.db)

        self.assertFalse(response["draft_available"])
        self.assertEqual(response["live_availability"]["reason"], "no_exact_5v5_account_identity_match")

    def test_api_returns_verified_series_map_drafts_for_finished_schedule_row(self):
        self.match.external_source = "pandascore"
        self.match.external_id = "series-1"
        self.match.status = "finished"
        map_match = Match(
            external_source="csv_import",
            external_id="8899120700",
            team_a_id=self.team_b.id,
            team_b_id=self.team_a.id,
            tournament_name=self.match.tournament_name,
            start_time=self.match.start_time,
            status="finished",
            winner_team_id=self.team_a.id,
        )
        self.db.add(map_match)
        self.db.flush()
        self.db.add(
            MatchDraft(
                match_id=map_match.id,
                team_id=self.team_a.id,
                hero_id=self.heroes[0].id,
                action_type="pick",
                pick_order=1,
                draft_order=1,
                source="opendota_detail",
            )
        )
        self.db.commit()

        response = get_match_draft(self.match.id, db=self.db)

        self.assertFalse(response["draft_available"])
        self.assertEqual(response["series_context"]["mapping_status"], "matched")
        self.assertEqual(response["series_context"]["maps"][0]["dota_match_id"], "8899120700")

    def test_api_match_draft_features_works(self):
        response = get_match_draft_features(self.match.id, db=self.db)

        self.assertTrue(response["experimental"])
        self.assertFalse(response["features"]["draft_available"])

    def _add_picks(self, count: int) -> None:
        draft_order = 1
        for index in range(count):
            self.db.add(
                MatchDraft(
                    match_id=self.match.id,
                    team_id=self.team_a.id,
                    hero_id=self.heroes[index].id,
                    action_type="pick",
                    pick_order=index + 1,
                    draft_order=draft_order,
                    source="test",
                )
            )
            draft_order += 1
            self.db.add(
                MatchDraft(
                    match_id=self.match.id,
                    team_id=self.team_b.id,
                    hero_id=self.heroes[index + 5].id,
                    action_type="pick",
                    pick_order=index + 1,
                    draft_order=draft_order,
                    source="test",
                )
            )
            draft_order += 1
        self.db.commit()


if __name__ == "__main__":
    unittest.main()
