from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.database import Base
from app.db.models import Hero, Match, MatchDraft, Team
from app.drafts.series_draft_context import build_series_draft_context


class SeriesDraftContextTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Yandex")
        self.team_b = Team(name="Team Spirit")
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.hero = Hero(hero_id=1, name="hero_1", localized_name="Hero 1", roles_json=[])
        self.db.add(self.hero)
        self.db.flush()
        self.start = datetime(2026, 7, 16, 11, 8, tzinfo=UTC)
        self.series = Match(
            external_source="pandascore",
            external_id="1566157",
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="Esports World Cup",
            start_time=self.start,
            format="BO3",
            status="finished",
        )
        self.db.add(self.series)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def test_links_reversed_map_pair_with_exact_tournament_and_time(self):
        map_match = self._add_map(1, reverse=True)

        context = build_series_draft_context(self.db, self.series)

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["mapping_status"], "matched")
        self.assertEqual(context["map_count"], 1)
        self.assertEqual(context["maps"][0]["database_match_id"], map_match.id)
        self.assertEqual(context["maps"][0]["dota_match_id"], "8899000001")

    def test_links_safe_cross_source_duplicate_team_rows(self):
        map_team_a = Team(name=self.team_a.name, external_source="csv_import")
        map_team_b = Team(name=self.team_b.name, external_source="csv_import")
        self.team_a.external_source = "pandascore"
        self.team_b.external_source = "pandascore"
        self.db.add_all([map_team_a, map_team_b])
        self.db.flush()
        map_match = Match(
            external_source="csv_import",
            external_id="8899000010",
            team_a_id=map_team_a.id,
            team_b_id=map_team_b.id,
            tournament_name=self.series.tournament_name,
            start_time=self.start,
            status="finished",
            winner_team_id=map_team_a.id,
        )
        self.db.add(map_match)
        self.db.flush()
        self.db.add(
            MatchDraft(
                match_id=map_match.id,
                team_id=map_team_a.id,
                hero_id=self.hero.id,
                action_type="pick",
                pick_order=1,
                draft_order=1,
                source="opendota_detail",
            )
        )
        self.db.commit()

        context = build_series_draft_context(self.db, self.series)

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["mapping_status"], "matched")
        self.assertEqual(context["maps"][0]["dota_match_id"], "8899000010")
        self.assertEqual(context["maps"][0]["entries"][0]["team_id"], self.team_a.id)

    def test_rejects_map_outside_series_window(self):
        self._add_map(1, start_time=self.start + timedelta(hours=13))

        context = build_series_draft_context(self.db, self.series)

        self.assertIsNone(context)

    def test_rejects_different_tournament(self):
        self._add_map(1, tournament="Regional Qualifier")

        context = build_series_draft_context(self.db, self.series)

        self.assertIsNone(context)

    def test_returns_ambiguous_instead_of_linking_too_many_maps(self):
        for game_number in range(1, 5):
            self._add_map(game_number)

        context = build_series_draft_context(self.db, self.series)

        self.assertIsNotNone(context)
        assert context is not None
        self.assertEqual(context["mapping_status"], "ambiguous")
        self.assertEqual(context["maps"], [])

    def _add_map(
        self,
        game_number: int,
        *,
        reverse: bool = False,
        start_time: datetime | None = None,
        tournament: str = "Esports World Cup",
    ) -> Match:
        match = Match(
            external_source="csv_import",
            external_id=f"889900000{game_number}",
            team_a_id=self.team_b.id if reverse else self.team_a.id,
            team_b_id=self.team_a.id if reverse else self.team_b.id,
            tournament_name=tournament,
            start_time=start_time or self.start + timedelta(hours=game_number - 1),
            status="finished",
            winner_team_id=self.team_a.id,
        )
        self.db.add(match)
        self.db.flush()
        self.db.add(
            MatchDraft(
                match_id=match.id,
                team_id=self.team_a.id,
                hero_id=self.hero.id,
                action_type="pick",
                pick_order=1,
                draft_order=1,
                source="opendota_detail",
            )
        )
        self.db.commit()
        return match


if __name__ == "__main__":
    unittest.main()
