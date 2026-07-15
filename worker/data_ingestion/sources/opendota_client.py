from __future__ import annotations

from datetime import date

from worker.data_ingestion.opendota_client import OpenDotaClient
from worker.data_ingestion.sources.base import BaseSourceClient, SourceResult


class OpenDotaSourceClient(BaseSourceClient):
    source_name = "opendota"
    requires_api_key = False
    env_key = "OPENDOTA_API_KEY"

    def __init__(self) -> None:
        self.client = OpenDotaClient()

    def fetch_teams(self) -> SourceResult:
        return self._wrap_response(self.client.get_teams())

    def fetch_matches(self, start_date: date | None = None, end_date: date | None = None, tier1_only: bool = True, limit: int = 100) -> SourceResult:
        warning = "OpenDota historical endpoint is public but may be incomplete and rate-limited."
        return self._wrap_response(self.client.get_matches(), warning=warning)

    def fetch_match_details(self, match_id: str) -> SourceResult:
        return self._wrap_response(self.client.get_match(match_id))
