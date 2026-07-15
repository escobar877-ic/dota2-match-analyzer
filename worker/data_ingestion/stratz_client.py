from __future__ import annotations

import argparse
import json
import os
from typing import Any

from worker.data_ingestion.base_client import BaseDotaDataClient, ClientResponse, JsonHttpClient


class StratzClient(BaseDotaDataClient):
    source_name = "stratz"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else os.getenv("STRATZ_API_KEY", "")
        self.enabled = bool(self.api_key)
        self.http = JsonHttpClient("https://api.stratz.com/graphql")

    def get_teams(self) -> ClientResponse:
        query = """
        query Teams {
          teams(request: {take: 100}) {
            id
            name
            tag
            logo
          }
        }
        """
        return self._graphql(query)

    def get_team(self, external_id: str) -> ClientResponse:
        query = """
        query Team($id: Long!) {
          team(id: $id) {
            id
            name
            tag
            logo
          }
        }
        """
        return self._graphql(query, {"id": int(external_id)})

    def get_matches(self) -> ClientResponse:
        return ClientResponse(
            ok=False,
            error="STRATZ date-range historical fetch is not implemented for current GraphQL schema; use match ids, PandaScore schedule, or CSV batch.",
        )

    def get_match(self, external_id: str) -> ClientResponse:
        query = """
        query Match($ids: [Long]!) {
          matches(ids: $ids) {
            id
            startDateTime
            didRadiantWin
            radiantTeam { id name }
            direTeam { id name }
            league { id name }
          }
        }
        """
        return self._graphql(query, {"ids": [int(external_id)]})

    def get_recent_matches_for_team(self, team_id: str) -> ClientResponse:
        return ClientResponse(
            ok=False,
            error="STRATZ team historical fetch is not implemented for current GraphQL schema; use match ids, PandaScore schedule, or CSV batch.",
        )

    def health_check(self) -> ClientResponse:
        query = "query Health { __typename }"
        return self._graphql(query)

    def get_upcoming_matches(self) -> ClientResponse:
        return ClientResponse(
            ok=False,
            error="STRATZ upcoming match sync is not enabled in this base client.",
        )

    def _graphql(self, query: str, variables: dict[str, Any] | None = None) -> ClientResponse:
        if not self.enabled:
            return ClientResponse(ok=False, error="STRATZ_API_KEY is not configured; STRATZ client is disabled.")

        response = self.http.post(
            "",
            body={"query": query, "variables": variables or {}},
            headers={"Authorization": f"Bearer {self.api_key}"},
        )
        if not response.ok:
            return ClientResponse(ok=False, error=f"STRATZ request failed: {response.error}")
        if isinstance(response.data, dict) and response.data.get("errors"):
            return ClientResponse(ok=False, error=f"STRATZ GraphQL errors: {response.data['errors']}")
        return response


def main() -> None:
    parser = argparse.ArgumentParser(description="STRATZ source client diagnostics.")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--match-id")
    args = parser.parse_args()
    client = StratzClient()
    if args.health:
        response = client.health_check()
        print(json.dumps({"ok": response.ok, "can_connect": response.ok, "error": response.error}, indent=2))
        return
    if args.match_id:
        response = client.get_match(args.match_id)
        print(json.dumps({"ok": response.ok, "records": response.data if response.ok else [], "error": response.error}, indent=2))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
