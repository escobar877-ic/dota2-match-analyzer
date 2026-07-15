from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any

from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.source_capabilities import get_source_capability


@dataclass(frozen=True)
class SourceResult:
    ok: bool
    source: str
    records: list[Any]
    error: str | None = None
    warnings: list[str] | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "source": self.source,
            "records": self.records,
            "error": self.error,
            "warnings": self.warnings or [],
        }


class BaseSourceClient:
    source_name = ""
    requires_api_key = False
    env_key: str | None = None

    def is_enabled(self) -> bool:
        return not self.requires_api_key or bool(self.env_key and os.getenv(self.env_key))

    def has_api_key(self) -> bool:
        return bool(self.env_key and os.getenv(self.env_key))

    def get_status(self) -> dict:
        missing = self.requires_api_key and not self.has_api_key()
        return {
            "source": self.source_name,
            "enabled": self.is_enabled(),
            "has_api_key": self.has_api_key(),
            "missing_key_reason": f"{self.env_key} missing" if missing else None,
            "capabilities": get_source_capability(self.source_name),
        }

    def fetch_teams(self) -> SourceResult:
        return self._disabled_or_empty()

    def fetch_tournaments(self) -> SourceResult:
        return self._disabled_or_empty(warning="Tournament fetch is not implemented for this source.")

    def fetch_matches(
        self,
        start_date: date | None = None,
        end_date: date | None = None,
        tier1_only: bool = True,
        limit: int = 100,
    ) -> SourceResult:
        return self._disabled_or_empty()

    def fetch_match_details(self, match_id: str) -> SourceResult:
        return self._disabled_or_empty()

    def normalize_response(self, raw) -> list[Any]:
        if isinstance(raw, list):
            return raw
        if raw is None:
            return []
        return [raw]

    def _wrap_response(self, response: ClientResponse, *, warning: str | None = None) -> SourceResult:
        if not self.is_enabled():
            return self._disabled_or_empty()
        if not response.ok:
            return SourceResult(ok=False, source=self.source_name, records=[], error=_sanitize_error(response.error), warnings=[])
        records = self.normalize_response(response.data)
        return SourceResult(ok=True, source=self.source_name, records=records, error=None, warnings=[warning] if warning else [])

    def _disabled_or_empty(self, warning: str | None = None) -> SourceResult:
        if not self.is_enabled():
            return SourceResult(
                ok=False,
                source=self.source_name,
                records=[],
                error=f"{self.env_key} missing" if self.env_key else "source disabled",
                warnings=[],
            )
        return SourceResult(ok=True, source=self.source_name, records=[], error=None, warnings=[warning] if warning else [])


def _sanitize_error(error: str | None) -> str | None:
    if not error:
        return error
    for key in ("OPENDOTA_API_KEY", "STRATZ_API_KEY", "PANDASCORE_API_KEY"):
        value = os.getenv(key)
        if value:
            error = error.replace(value, "***")
    return error
