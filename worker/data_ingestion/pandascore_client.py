from __future__ import annotations

from datetime import date
import os

from worker.data_ingestion.base_client import BaseDotaDataClient, ClientResponse, JsonHttpClient


class PandaScoreClient(BaseDotaDataClient):
    source_name = "pandascore"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("PANDASCORE_API_KEY", "")
        self.enabled = bool(self.api_key)
        self.http = JsonHttpClient("https://api.pandascore.co")

    def get_teams(self) -> ClientResponse:
        return self._get("/dota2/teams", query={"per_page": 100})

    def get_team(self, external_id: str) -> ClientResponse:
        return self._get(f"/teams/{external_id}")

    def get_matches(
        self,
        start_date: date | str | None = None,
        end_date: date | str | None = None,
        limit: int = 100,
        page: int = 1,
    ) -> ClientResponse:
        query: dict[str, object] = {"per_page": max(1, min(limit, 100)), "page": max(1, page)}
        if start_date and end_date:
            query["range[begin_at]"] = f"{start_date},{end_date}"
        return self._get("/dota2/matches/past", query=query)

    def get_match(self, external_id: str) -> ClientResponse:
        return self._get(f"/matches/{external_id}")

    def get_tournament_rosters(self, tournament_id: str) -> ClientResponse:
        return self._get(f"/tournaments/{tournament_id}/rosters")

    def get_recent_matches_for_team(self, team_id: str) -> ClientResponse:
        return self._get(
            "/dota2/matches/past",
            query={"filter[opponent_id]": team_id, "per_page": 50},
        )

    def get_upcoming_matches(
        self,
        *,
        limit: int = 100,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
    ) -> ClientResponse:
        query: dict[str, object] = {"per_page": max(1, min(limit, 100))}
        if from_date and to_date:
            query["range[begin_at]"] = f"{from_date},{to_date}"
        return self._get("/dota2/matches/upcoming", query=query)

    def get_running_matches(self, *, limit: int = 100) -> ClientResponse:
        return self._get("/dota2/matches/running", query={"per_page": max(1, min(limit, 100))})

    def get_tournaments(self, *, limit: int = 50) -> ClientResponse:
        return self._get("/dota2/tournaments", query={"per_page": max(1, min(limit, 100))})

    def health_check(self) -> ClientResponse:
        return self.get_upcoming_matches(limit=1)

    def get_players(self) -> ClientResponse:
        return self._get("/dota2/players", query={"per_page": 100})

    def _get(self, path: str, query: dict[str, object] | None = None) -> ClientResponse:
        if not self.enabled:
            return ClientResponse(ok=False, error="PANDASCORE_API_KEY is not configured; PandaScore client is disabled.")

        response = self.http.get(path, headers={"Authorization": f"Bearer {self.api_key}"}, query=query)
        if not response.ok:
            return ClientResponse(ok=False, error=f"PandaScore request failed: {response.error}")
        return response
