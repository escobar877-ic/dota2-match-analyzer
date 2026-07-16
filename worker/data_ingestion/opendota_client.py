from __future__ import annotations

import os
from typing import Any

from worker.data_ingestion.base_client import BaseDotaDataClient, ClientResponse, JsonHttpClient


class OpenDotaClient(BaseDotaDataClient):
    source_name = "opendota"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("OPENDOTA_API_KEY", "")
        self.http = JsonHttpClient("https://api.opendota.com/api", timeout_seconds=8)
        self.enabled = True

    def get_teams(self) -> ClientResponse:
        return self._get("/teams")

    def get_team(self, external_id: str) -> ClientResponse:
        return self._get(f"/teams/{external_id}")

    def get_matches(self) -> ClientResponse:
        return self._get("/proMatches")

    def get_match(self, external_id: str) -> ClientResponse:
        return self._get(f"/matches/{external_id}")

    def get_live_matches(self) -> ClientResponse:
        return self._get("/live")

    def get_heroes(self) -> ClientResponse:
        return self._get("/constants/heroes")

    def get_patches(self) -> ClientResponse:
        return self._get("/constants/patch")

    def get_league_matches(self, league_id: int | str) -> ClientResponse:
        return self._get(f"/leagues/{league_id}/matches")

    def get_recent_matches_for_team(self, team_id: str) -> ClientResponse:
        return self._get(f"/teams/{team_id}/matches")

    def get_team_players(self, team_id: str) -> ClientResponse:
        return self._get(f"/teams/{team_id}/players")

    def get_upcoming_matches(self) -> ClientResponse:
        return ClientResponse(
            ok=False,
            error="OpenDota does not provide a stable public upcoming pro matches endpoint.",
        )

    def _get(self, path: str, query: dict[str, Any] | None = None) -> ClientResponse:
        request_query = dict(query or {})
        if self.api_key:
            request_query["api_key"] = self.api_key
        response = self.http.get(path, query=request_query or None)
        if not response.ok:
            error = response.error or "unknown error"
            if self.api_key:
                error = error.replace(self.api_key, "***")
            return ClientResponse(ok=False, error=f"OpenDota request failed: {error}")
        return response
