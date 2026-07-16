from __future__ import annotations

import tempfile
import unittest
import sys
from datetime import datetime, timezone
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
from app.db.models import DataSyncLog, DraftSnapshot, Match, MatchDraft, Team, TeamMatchStats
from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.match_detail_enrichment import enrich_match_details


class FakeOpenDotaClient:
    def __init__(self, response: ClientResponse | None = None) -> None:
        self.response = response or ClientResponse(ok=True, data=_raw_match())
        self.calls = 0

    def get_match(self, external_id: str) -> ClientResponse:
        self.calls += 1
        return self.response


class SequenceOpenDotaClient:
    def __init__(self, responses: list[ClientResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def get_match(self, external_id: str) -> ClientResponse:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        return response


class MatchDetailEnrichmentTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Liquid", external_source="csv_import", external_id="10", is_active_tier1=True)
        self.team_b = Team(name="Team Spirit", external_source="csv_import", external_id="20", is_active_tier1=True)
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.match = Match(
            external_source="csv_import",
            external_id="123456",
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="The International",
            start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
            format="BO3",
            status="finished",
            winner_team_id=self.team_a.id,
            is_tier1_match=True,
            dataset_profile="historical_training",
            competition_tier="tier1",
            verification_status="verified",
            source_confidence="high",
            is_training_eligible=True,
        )
        self.db.add(self.match)
        self.db.commit()
        self.team_a_id = self.team_a.id
        self.team_b_id = self.team_b.id
        self.match_id = self.match.id
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_dry_run_fetches_but_does_not_write(self):
        result = self._run(apply=False)

        self.assertEqual(result["would_enrich"], 1)
        self.assertEqual(self.db.query(TeamMatchStats).count(), 0)
        self.assertEqual(self.db.query(MatchDraft).count(), 0)
        self.assertEqual(self.db.query(DataSyncLog).count(), 0)

    def test_apply_creates_stats_draft_and_snapshot(self):
        result = self._run(apply=True)

        self.assertEqual(result["matches_enriched"], 1)
        self.assertEqual(result["total_enriched_matches"], 1)
        self.assertEqual(result["total_stats_rows"], 2)
        self.assertEqual(result["total_draft_entries"], 12)
        self.assertEqual(result["total_draft_snapshots"], 1)
        self.assertEqual(self.db.query(TeamMatchStats).count(), 2)
        self.assertEqual(self.db.query(MatchDraft).count(), 12)
        snapshot = self.db.query(DraftSnapshot).one()
        self.assertTrue(snapshot.draft_complete)
        radiant_stats = self.db.query(TeamMatchStats).filter_by(team_id=self.team_a_id).one()
        dire_stats = self.db.query(TeamMatchStats).filter_by(team_id=self.team_b_id).one()
        self.assertEqual(radiant_stats.result, "win")
        self.assertEqual(dire_stats.result, "loss")
        self.assertEqual(radiant_stats.gold_diff_10, 500)
        self.assertEqual(dire_stats.gold_diff_10, -500)
        self.assertTrue(self.db.get(Match, self.match_id).is_training_eligible)

    def test_force_apply_updates_without_duplicates(self):
        self._run(apply=True)
        second = self._run(apply=True, force=True)

        self.assertEqual(second["stats_rows_updated"], 2)
        self.assertEqual(second["draft_entries_updated"], 12)
        self.assertEqual(self.db.query(TeamMatchStats).count(), 2)
        self.assertEqual(self.db.query(MatchDraft).count(), 12)
        self.assertEqual(self.db.query(DraftSnapshot).count(), 1)

    def test_team_identity_mismatch_is_excluded(self):
        raw = _raw_match()
        raw["radiant_team_id"] = 999
        raw["radiant_team"] = {"team_id": 999, "name": "Unknown Stack"}
        result = self._run(apply=True, client=FakeOpenDotaClient(ClientResponse(ok=True, data=raw)))

        self.assertEqual(result["records_excluded"], 1)
        self.assertIn("team_identity_mismatch", result["exclusion_reasons"])
        self.assertEqual(self.db.query(TeamMatchStats).count(), 0)

    def test_source_error_is_clean_and_report_is_written(self):
        output = Path(self.temp_dir.name) / "enrichment.json"
        client = FakeOpenDotaClient(ClientResponse(ok=False, error="OpenDota timeout"))
        result = self._run(apply=False, client=client, artifact_path=output)

        self.assertEqual(result["status"], "warning")
        self.assertIn("OpenDota timeout", result["source_errors"][0])
        self.assertTrue(output.exists())

    def test_team_filter_limits_selection(self):
        client = FakeOpenDotaClient()
        with patch("worker.data_ingestion.match_detail_enrichment.get_session", return_value=self.db):
            result = enrich_match_details(
                apply=False,
                limit=10,
                team="MOUZ",
                sleep_seconds=0,
                client=client,
                artifact_path=None,
            )

        self.assertEqual(result["records_seen"], 0)
        self.assertEqual(client.calls, 0)

    def test_explicit_opendota_source_scope_is_supported(self):
        self.match.external_source = "opendota"
        self.team_a.external_source = "opendota"
        self.team_b.external_source = "opendota"
        self.db.commit()
        client = FakeOpenDotaClient()
        with patch("worker.data_ingestion.match_detail_enrichment.get_session", return_value=self.db):
            result = enrich_match_details(
                apply=False,
                limit=10,
                sleep_seconds=0,
                external_sources={"opendota"},
                external_ids=[self.match.external_id],
                client=client,
                artifact_path=None,
            )

        self.assertEqual(result["records_seen"], 1)
        self.assertEqual(result["would_enrich"], 1)

    def test_rate_limit_response_is_retried_with_backoff(self):
        client = SequenceOpenDotaClient(
            [
                ClientResponse(ok=False, error="OpenDota request failed: HTTP 429"),
                ClientResponse(ok=True, data=_raw_match()),
            ]
        )
        with (
            patch("worker.data_ingestion.match_detail_enrichment.get_session", return_value=self.db),
            patch("worker.data_ingestion.match_detail_enrichment.time.sleep") as sleep,
        ):
            result = enrich_match_details(
                apply=False,
                limit=10,
                sleep_seconds=0,
                rate_limit_retries=1,
                rate_limit_backoff_seconds=1,
                client=client,
                artifact_path=None,
            )

        self.assertEqual(result["would_enrich"], 1)
        self.assertEqual(result["rate_limit_retries_used"], 1)
        self.assertEqual(client.calls, 2)
        sleep.assert_called_once_with(1.0)

    def _run(
        self,
        *,
        apply: bool,
        force: bool = False,
        client: FakeOpenDotaClient | None = None,
        artifact_path: Path | None = None,
    ) -> dict:
        with patch("worker.data_ingestion.match_detail_enrichment.get_session", return_value=self.db):
            return enrich_match_details(
                apply=apply,
                limit=10,
                sleep_seconds=0,
                force=force,
                client=client or FakeOpenDotaClient(),
                artifact_path=artifact_path,
            )


def _raw_match() -> dict:
    players = []
    for index in range(10):
        radiant = index < 5
        team_index = index % 5
        gold_base = 1000 + team_index * 10 + (100 if radiant else 0)
        xp_base = 900 + team_index * 10 + (60 if radiant else 0)
        players.append(
            {
                "isRadiant": radiant,
                "player_slot": index if radiant else 128 + index,
                "kills": 2 + index,
                "deaths": 1 + index,
                "assists": 4 + index,
                "gold_t": [gold_base] * 11,
                "xp_t": [xp_base] * 11,
            }
        )
    picks_bans = []
    order = 0
    for team in (0, 1):
        for hero_id in range(1 + team * 10, 6 + team * 10):
            picks_bans.append({"team": team, "hero_id": hero_id, "is_pick": True, "order": order})
            order += 1
        picks_bans.append({"team": team, "hero_id": 30 + team, "is_pick": False, "order": order})
        order += 1
    return {
        "match_id": 123456,
        "duration": 2400,
        "radiant_win": True,
        "radiant_team_id": 10,
        "dire_team_id": 20,
        "radiant_team": {"team_id": 10, "name": "Team Liquid"},
        "dire_team": {"team_id": 20, "name": "Team Spirit"},
        "players": players,
        "picks_bans": picks_bans,
    }


if __name__ == "__main__":
    unittest.main()
