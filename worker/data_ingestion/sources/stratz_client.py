from __future__ import annotations

from datetime import date
import argparse
import json
from typing import Any

from worker.data_ingestion.sources.base import BaseSourceClient, SourceResult
from worker.data_ingestion.stratz_client import StratzClient


class StratzSourceClient(BaseSourceClient):
    source_name = "stratz"
    requires_api_key = True
    env_key = "STRATZ_API_KEY"

    def __init__(self) -> None:
        self.client = StratzClient()

    def fetch_teams(self) -> SourceResult:
        return self._wrap_response(self.client.get_teams())

    def fetch_matches(self, start_date: date | None = None, end_date: date | None = None, tier1_only: bool = True, limit: int = 100) -> SourceResult:
        return self._wrap_response(self.client.get_matches())

    def fetch_match_details(self, match_id: str) -> SourceResult:
        return self._wrap_response(self.client.get_match(match_id))

    def health_check(self) -> SourceResult:
        return self._wrap_response(self.client.health_check())

    def normalize_response(self, raw: Any) -> list[Any]:
        if isinstance(raw, dict):
            data = raw.get("data")
            if isinstance(data, dict) and isinstance(data.get("matches"), list):
                return data["matches"]
        return super().normalize_response(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="STRATZ source client diagnostics.")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--match-id")
    args = parser.parse_args()
    client = StratzSourceClient()
    if args.health:
        result = client.health_check()
        print(json.dumps({"ok": result.ok, "can_connect": result.ok, "error": result.error, "warnings": result.warnings or []}, indent=2))
        return
    if args.match_id:
        result = client.fetch_match_details(args.match_id)
        print(json.dumps(result.to_dict(), indent=2, default=str))
        return
    parser.print_help()


if __name__ == "__main__":
    main()
