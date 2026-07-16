from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
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
from app.db.models import Match, Player, Team, TeamRoster
from worker.data_ingestion.opendota_detail_cache import write_cached_match_detail
from worker.data_ingestion.roster_history_enrichment import (
    PlayerObservation,
    RosterObservation,
    _aware,
    _parse_date_arg,
    apply_roster_segments,
    build_roster_segments,
    enrich_roster_history,
    extract_player_observations,
)


class RosterHistoryEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        team = Team(
            external_source="csv_import",
            external_id="team-spirit",
            name="Team Spirit",
            is_active_tier1=True,
        )
        self.db.add(team)
        self.db.flush()
        self.team_id = team.id

    def tearDown(self) -> None:
        self.db.close()

    def test_extract_requires_exactly_five_known_player_ids(self):
        players = [
            {"account_id": index, "personaname": f"player-{index}"}
            for index in range(1, 6)
        ]

        roster = extract_player_observations(players)

        self.assertIsNotNone(roster)
        self.assertEqual(len(roster or ()), 5)
        self.assertIsNone(extract_player_observations(players[:4]))
        self.assertIsNone(extract_player_observations([*players, {"account_id": 6}]))

    def test_date_filter_parser_normalizes_dates_to_utc(self):
        self.assertEqual(
            _parse_date_arg("2026-07-07").isoformat(),
            "2026-07-07T00:00:00+00:00",
        )
        self.assertEqual(
            _parse_date_arg("2026-07-07T04:00:00+04:00").isoformat(),
            "2026-07-07T00:00:00+00:00",
        )

    def test_changed_roster_closes_previous_segment_without_overlap(self):
        first_time = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
        changed_time = first_time + timedelta(days=10)
        first = self._observation(1, first_time, range(1, 6))
        changed = self._observation(2, changed_time, range(2, 7))

        segments = build_roster_segments([first, changed], max_gap_days=45)

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].end_date, changed_time)
        self.assertEqual(segments[1].start_date, changed_time)

    def test_large_observation_gap_does_not_claim_unknown_roster_period(self):
        first_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        next_time = first_time + timedelta(days=60)
        segments = build_roster_segments(
            [
                self._observation(1, first_time, range(1, 6)),
                self._observation(2, next_time, range(1, 6)),
            ],
            max_gap_days=45,
        )

        self.assertEqual(len(segments), 2)
        self.assertEqual(segments[0].end_date, first_time + timedelta(days=45))
        self.assertGreater(segments[1].start_date, segments[0].end_date)

    def test_post_match_observation_starts_after_match_cutoff(self):
        match_start = datetime(2026, 2, 1, 18, tzinfo=timezone.utc)
        observed_at = match_start + timedelta(seconds=1)

        segment = build_roster_segments(
            [self._observation(1, observed_at, range(1, 6))],
            max_gap_days=45,
        )[0]

        self.assertGreater(segment.start_date, match_start)

    def test_trusted_opendota_match_uses_exact_id_from_shared_cache(self):
        opponent = Team(
            external_source="opendota",
            external_id="20",
            name="Team Liquid",
            is_active_tier1=True,
        )
        self.db.add(opponent)
        self.db.flush()
        match = Match(
            external_source="opendota",
            external_id="123456",
            team_a_id=self.team_id,
            team_b_id=opponent.id,
            tournament_name="Esports World Cup",
            start_time=datetime(2026, 7, 1, tzinfo=timezone.utc),
            status="finished",
            winner_team_id=self.team_id,
            is_tier1_match=True,
            dataset_profile="historical_training",
            verification_status="verified",
            source_confidence="high",
        )
        self.db.add(match)
        self.db.commit()
        players = [
            {
                "account_id": index + 1,
                "personaname": f"player-{index + 1}",
                "isRadiant": index < 5,
                "player_slot": index if index < 5 else 128 + index,
            }
            for index in range(10)
        ]
        raw = {
            "match_id": 123456,
            "duration": 2400,
            "radiant_win": True,
            "radiant_team_id": 10,
            "dire_team_id": 20,
            "radiant_team": {"team_id": 10, "name": "Team Spirit"},
            "dire_team": {"team_id": 20, "name": "Team Liquid"},
            "players": players,
        }

        with TemporaryDirectory() as cache_dir:
            write_cached_match_detail("123456", raw, cache_dir=cache_dir)
            with patch(
                "worker.data_ingestion.roster_history_enrichment.get_session",
                return_value=self.db,
            ):
                result = enrich_roster_history(
                    apply=True,
                    limit=10,
                    sleep_seconds=0,
                    external_sources={"opendota"},
                    external_ids=["123456"],
                    recent_first=True,
                    cache_only=True,
                    merge_only=True,
                    detail_cache_dir=cache_dir,
                    artifact_path=None,
                )

        self.assertEqual(result["records_seen"], 1)
        self.assertEqual(result["matches_with_roster_observations"], 1)
        self.assertEqual(result["cache_hits"], 1)
        self.assertEqual(self.db.query(TeamRoster).count(), 10)

    def test_apply_is_idempotent_and_keeps_historical_rows_inactive(self):
        observed_at = datetime(2026, 3, 1, tzinfo=timezone.utc)
        segments = build_roster_segments(
            [self._observation(1, observed_at, range(1, 6))],
            max_gap_days=45,
        )

        first = apply_roster_segments(self.db, segments)
        self.db.flush()
        second = apply_roster_segments(self.db, segments)
        self.db.flush()

        self.assertEqual(first["players_created"], 5)
        self.assertEqual(first["roster_rows_created"], 5)
        self.assertEqual(second["roster_rows_created"], 0)
        self.assertEqual(second["roster_rows_updated"], 5)
        self.assertEqual(self.db.query(Player).count(), 5)
        self.assertEqual(self.db.query(TeamRoster).count(), 5)
        self.assertTrue(all(not row.is_active for row in self.db.query(TeamRoster).all()))

    def test_merge_only_extends_same_roster_without_overlap(self):
        first_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        later_time = first_time + timedelta(days=20)
        initial = build_roster_segments([self._observation(1, first_time, range(1, 6))])
        incoming = build_roster_segments([self._observation(2, later_time, range(1, 6))])
        apply_roster_segments(self.db, initial)
        self.db.flush()

        result = apply_roster_segments(self.db, incoming, merge_only=True)
        self.db.flush()

        active_at_later = self.db.query(TeamRoster).filter(
            TeamRoster.team_id == self.team_id,
            TeamRoster.start_date <= later_time + timedelta(days=1),
            TeamRoster.end_date > later_time + timedelta(days=1),
        ).all()
        self.assertEqual(len(active_at_later), 5)
        self.assertEqual(self.db.query(TeamRoster).count(), 5)
        self.assertEqual(result["roster_rows_created"], 0)
        self.assertEqual(result["roster_rows_merged"], 5)

    def test_merge_only_closes_changed_roster_at_new_observation(self):
        first_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        changed_time = first_time + timedelta(days=20)
        initial = build_roster_segments([self._observation(1, first_time, range(1, 6))])
        changed = build_roster_segments([self._observation(2, changed_time, range(2, 7))])
        apply_roster_segments(self.db, initial)
        self.db.flush()

        result = apply_roster_segments(self.db, changed, merge_only=True)
        self.db.flush()

        after_change = self.db.query(TeamRoster).filter(
            TeamRoster.team_id == self.team_id,
            TeamRoster.start_date <= changed_time + timedelta(days=1),
            TeamRoster.end_date > changed_time + timedelta(days=1),
        ).all()
        self.assertEqual(len(after_change), 5)
        self.assertEqual(result["roster_rows_truncated"], 5)
        self.assertEqual(result["roster_rows_created"], 5)

    def test_partial_window_preserves_older_generated_history(self):
        player = Player(external_source="opendota", external_id="99", nickname="old")
        self.db.add(player)
        self.db.flush()
        old_start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        old_end = datetime(2026, 2, 1, tzinfo=timezone.utc)
        self.db.add(
            TeamRoster(
                team_id=self.team_id,
                player_id=player.id,
                start_date=old_start,
                end_date=old_end,
                source="opendota_history",
                is_active=False,
            )
        )
        self.db.flush()
        july = datetime(2026, 7, 10, tzinfo=timezone.utc)
        segments = build_roster_segments([self._observation(2, july, range(1, 6))])

        result = apply_roster_segments(
            self.db,
            segments,
            replace_start=datetime(2026, 7, 7, tzinfo=timezone.utc),
            replace_end=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        self.db.flush()

        old_row = self.db.query(TeamRoster).filter_by(player_id=player.id).one()
        self.assertEqual(_aware(old_row.end_date), old_end)
        self.assertEqual(result["roster_rows_invalidated"], 0)
        self.assertEqual(result["roster_rows_truncated"], 0)

    def test_partial_window_truncates_only_overlapping_segment(self):
        player = Player(external_source="opendota", external_id="99", nickname="old")
        self.db.add(player)
        self.db.flush()
        old_start = datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.db.add(
            TeamRoster(
                team_id=self.team_id,
                player_id=player.id,
                start_date=old_start,
                end_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
                source="opendota_history",
                is_active=False,
            )
        )
        self.db.flush()
        replace_start = datetime(2026, 7, 7, tzinfo=timezone.utc)
        segments = build_roster_segments(
            [self._observation(2, datetime(2026, 7, 10, tzinfo=timezone.utc), range(1, 6))]
        )

        result = apply_roster_segments(
            self.db,
            segments,
            replace_start=replace_start,
            replace_end=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
        self.db.flush()

        old_row = self.db.query(TeamRoster).filter_by(player_id=player.id).one()
        self.assertEqual(_aware(old_row.end_date), replace_start)
        self.assertEqual(result["roster_rows_truncated"], 1)

    def _observation(
        self,
        match_id: int,
        observed_at: datetime,
        player_ids,
    ) -> RosterObservation:
        return RosterObservation(
            team_id=self.team_id,
            match_id=match_id,
            observed_at=observed_at,
            players=tuple(
                PlayerObservation(external_id=str(player_id), nickname=f"player-{player_id}")
                for player_id in player_ids
            ),
        )


if __name__ == "__main__":
    unittest.main()
