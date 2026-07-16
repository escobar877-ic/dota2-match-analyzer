from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import UTC, datetime
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
from app.db.models import Match, MatchDraft, Team
from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.sync_live_match_context import sync_live_match_context


class FakeOpenDotaClient:
    def __init__(
        self,
        live_records: list[dict] | None = None,
        team_players: dict[str, list[int]] | None = None,
    ) -> None:
        self.live_records = live_records if live_records is not None else [_live_record()]
        self.team_players = team_players or {}

    def get_live_matches(self) -> ClientResponse:
        return ClientResponse(ok=True, data=self.live_records)

    def get_heroes(self) -> ClientResponse:
        return ClientResponse(
            ok=True,
            data={
                str(hero_id): {
                    "id": hero_id,
                    "name": f"npc_dota_hero_{hero_id}",
                    "localized_name": f"Live Hero {hero_id}",
                }
                for hero_id in range(1, 11)
            },
        )

    def get_team_players(self, team_id: str) -> ClientResponse:
        account_ids = self.team_players.get(str(team_id), [])
        return ClientResponse(
            ok=True,
            data=[
                {"account_id": account_id, "is_current_team_member": True}
                for account_id in account_ids
            ],
        )


class SyncLiveMatchContextTests(unittest.TestCase):
    def setUp(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        self.db = Session(engine)
        self.team_a = Team(name="Team Yandex")
        self.team_b = Team(name="Team Spirit")
        self.db.add_all([self.team_a, self.team_b])
        self.db.flush()
        self.match = Match(
            external_source="pandascore",
            external_id="1566157",
            team_a_id=self.team_a.id,
            team_b_id=self.team_b.id,
            tournament_name="Esports World Cup",
            start_time=datetime.now(UTC),
            status="live",
        )
        self.db.add(self.match)
        self.db.commit()
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_matches_live_series_and_writes_read_only_pick_context(self):
        output = Path(self.temp_dir.name) / "live.json"

        report = sync_live_match_context(
            artifact_path=output,
            db=self.db,
            client=FakeOpenDotaClient(),
        )

        context = report["matches"][str(self.match.id)]
        self.assertEqual(report["matched_live_matches"], 1)
        self.assertEqual(report["drafts_available"], 1)
        self.assertEqual(context["dota_match_id"], "8899120700")
        self.assertEqual(len(context["team_a"]["picks"]), 5)
        self.assertEqual(len(context["team_b"]["picks"]), 5)
        self.assertEqual(context["team_a"]["picks"][0]["localized_name"], "Live Hero 6")
        self.assertFalse(context["bans_available"])
        self.assertEqual(self.db.query(MatchDraft).count(), 0)
        self.assertTrue(output.exists())

    def test_unrelated_live_match_is_ignored(self):
        raw = _live_record()
        raw["team_name_dire"] = "Unknown Team"

        report = sync_live_match_context(
            artifact_path=Path(self.temp_dir.name) / "live.json",
            db=self.db,
            client=FakeOpenDotaClient([raw]),
        )

        self.assertEqual(report["matched_live_matches"], 0)
        self.assertEqual(report["matches"], {})

    def test_verified_team_alias_matches_live_record(self):
        self.team_a.name = "PARIVISION"
        self.team_b.name = "Rune Eaters"
        self.db.commit()
        raw = _live_record()
        raw["team_name_radiant"] = "PVISION"
        raw["team_name_dire"] = "Rune Eaters"

        report = sync_live_match_context(
            artifact_path=Path(self.temp_dir.name) / "live.json",
            db=self.db,
            client=FakeOpenDotaClient([raw]),
        )

        self.assertEqual(report["matched_live_matches"], 1)
        self.assertEqual(report["drafts_available"], 1)

    def test_anonymous_live_record_matches_only_exact_verified_5v5_accounts(self):
        raw = _live_record()
        raw["team_name_radiant"] = ""
        raw["team_name_dire"] = ""
        client = FakeOpenDotaClient(
            [raw],
            team_players={
                "9823272": list(range(1005, 1010)),
                "7119388": list(range(1000, 1005)),
            },
        )

        report = sync_live_match_context(
            artifact_path=Path(self.temp_dir.name) / "live.json",
            db=self.db,
            client=client,
        )

        context = report["matches"][str(self.match.id)]
        self.assertEqual(report["identity_fallback_attempts"], 1)
        self.assertEqual(report["identity_fallback_matches"], 1)
        self.assertEqual(context["identity_method"], "verified_5v5_account_ids")
        self.assertEqual(context["team_a"]["side"], "dire")
        self.assertEqual(context["team_b"]["side"], "radiant")

    def test_incomplete_roster_does_not_link_anonymous_live_record(self):
        raw = _live_record()
        raw["team_name_radiant"] = ""
        raw["team_name_dire"] = ""
        client = FakeOpenDotaClient(
            [raw],
            team_players={
                "9823272": list(range(1005, 1009)),
                "7119388": list(range(1000, 1005)),
            },
        )

        report = sync_live_match_context(
            artifact_path=Path(self.temp_dir.name) / "live.json",
            db=self.db,
            client=client,
        )

        self.assertEqual(report["matched_live_matches"], 0)
        availability = report["availability"][str(self.match.id)]
        self.assertEqual(availability["status"], "unavailable")
        self.assertEqual(availability["reason"], "opendota_current_roster_not_exactly_five")

    def test_roster_identity_prefers_single_active_map_over_completed_map(self):
        completed = _live_record()
        completed["team_name_radiant"] = ""
        completed["team_name_dire"] = ""
        completed["match_id"] = "8899000001"
        completed["deactivate_time"] = completed["activate_time"] + 1800
        active = _live_record()
        active["team_name_radiant"] = ""
        active["team_name_dire"] = ""
        client = FakeOpenDotaClient(
            [completed, active],
            team_players={
                "9823272": list(range(1005, 1010)),
                "7119388": list(range(1000, 1005)),
            },
        )

        report = sync_live_match_context(
            artifact_path=Path(self.temp_dir.name) / "live.json",
            db=self.db,
            client=client,
        )

        self.assertEqual(report["matches"][str(self.match.id)]["dota_match_id"], "8899120700")


def _live_record() -> dict:
    now = int(datetime.now(UTC).timestamp())
    players = [
        {
            "account_id": 1000 + index,
            "hero_id": index + 1,
            "team": 0 if index < 5 else 1,
            "team_slot": (index % 5) + 1,
            "name": f"Player {index + 1}",
        }
        for index in range(10)
    ]
    return {
        "match_id": "8899120700",
        "series_id": 1120911,
        "league_id": 19785,
        "activate_time": now,
        "game_time": 900,
        "team_name_radiant": "Team Spirit",
        "team_name_dire": "Team Yandex",
        "radiant_score": 8,
        "dire_score": 6,
        "players": players,
    }


if __name__ == "__main__":
    unittest.main()
