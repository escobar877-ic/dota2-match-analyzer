from __future__ import annotations

from datetime import date
from typing import Any

from worker.data_ingestion.pandascore_client import PandaScoreClient
from worker.data_ingestion.sources.base import BaseSourceClient, SourceResult


class PandaScoreSourceClient(BaseSourceClient):
    source_name = "pandascore"
    requires_api_key = True
    env_key = "PANDASCORE_API_KEY"

    def __init__(self) -> None:
        self.client = PandaScoreClient()

    def fetch_teams(self, limit: int = 50) -> SourceResult:
        return self._wrap_response(self.client._get("/dota2/teams", query={"per_page": max(1, min(limit, 100))}))

    def fetch_tournaments(self, limit: int = 50) -> SourceResult:
        return self._wrap_response(self.client.get_tournaments(limit=limit))

    def fetch_matches(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        tier1_only: bool = True,
        limit: int = 100,
    ) -> SourceResult:
        if not self.is_enabled():
            return self._disabled_or_empty()
        per_page = 100
        target = max(1, limit)
        records: list[Any] = []
        page = 1
        while len(records) < target:
            response = self.client.get_matches(start_date=start_date, end_date=end_date, limit=per_page, page=page)
            if not response.ok:
                return self._wrap_response(response)
            page_records = self.normalize_response(response.data)
            records.extend(page_records)
            if len(page_records) < per_page:
                break
            page += 1
        return SourceResult(ok=True, source=self.source_name, records=records[:target], error=None, warnings=[])

    def fetch_match_details(self, match_id: str) -> SourceResult:
        return self._wrap_response(self.client.get_match(match_id))

    def fetch_upcoming_matches(
        self,
        *,
        limit: int = 50,
        from_date: date | str | None = None,
        to_date: date | str | None = None,
    ) -> SourceResult:
        return self._wrap_response(self.client.get_upcoming_matches(limit=limit, from_date=from_date, to_date=to_date))

    def health_check(self) -> SourceResult:
        return self._wrap_response(self.client.health_check())

    def normalize_match(self, raw: Any):
        from worker.data_ingestion.normalizer import normalize_pandascore_matches

        matches = normalize_pandascore_matches([raw])
        return matches[0] if matches else None
