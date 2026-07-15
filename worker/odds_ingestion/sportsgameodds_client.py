from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from worker.data_ingestion.base_client import JsonHttpClient


BASE_URL = "https://api.sportsgameodds.com/v2"
ENV_KEY = "SPORTSGAMEODDS_API_KEY"


@dataclass(frozen=True)
class OddsSourceResult:
    ok: bool
    records: list[dict[str, Any]]
    error: str | None = None
    warnings: list[str] | None = None


class SportsGameOddsClient:
    source_name = "sportsgameodds"

    def __init__(self) -> None:
        self.api_key = os.getenv(ENV_KEY, "").strip()
        self.http = JsonHttpClient(BASE_URL, timeout_seconds=20)

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def health_check(self) -> OddsSourceResult:
        if not self.is_enabled():
            return self._disabled()
        response = self.http.get(
            "/account/usage/",
            headers=self._headers(),
        )
        return OddsSourceResult(
            ok=response.ok,
            records=[],
            error=_safe_error(response.error, self.api_key),
            warnings=[],
        )

    def fetch_upcoming_odds(self, *, limit: int = 100) -> OddsSourceResult:
        if not self.is_enabled():
            return self._disabled()
        response = self.http.get(
            "/events/",
            headers=self._headers(),
            query={
                "sportID": "ESPORTS",
                "oddsAvailable": "true",
                "started": "false",
                "limit": max(1, min(limit, 100)),
            },
        )
        if not response.ok:
            return OddsSourceResult(
                ok=False,
                records=[],
                error=_safe_error(response.error, self.api_key),
                warnings=[],
            )
        payload = response.data if isinstance(response.data, dict) else {}
        if payload.get("success") is False:
            return OddsSourceResult(
                ok=False,
                records=[],
                error=_safe_error(str(payload.get("error") or "Odds API request failed."), self.api_key),
                warnings=[],
            )
        events = payload.get("data") if isinstance(payload.get("data"), list) else []
        records = [
            record
            for event in events
            for record in normalize_event_odds(event)
        ]
        return OddsSourceResult(ok=True, records=records, error=None, warnings=[])

    def get_status(self) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "enabled": self.is_enabled(),
            "has_api_key": self.is_enabled(),
            "missing_key_reason": None if self.is_enabled() else f"{ENV_KEY} missing",
            "setup_hint": f"Set {ENV_KEY} in .env to enable multi-bookmaker odds sync.",
        }

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.api_key}

    def _disabled(self) -> OddsSourceResult:
        return OddsSourceResult(
            ok=False,
            records=[],
            error=f"{ENV_KEY} missing",
            warnings=[],
        )


def normalize_event_odds(event: Any) -> list[dict[str, Any]]:
    if not isinstance(event, dict):
        return []
    event_id = str(event.get("eventID") or "").strip()
    teams = event.get("teams") if isinstance(event.get("teams"), dict) else {}
    home_name = _team_name(teams.get("home"))
    away_name = _team_name(teams.get("away"))
    status = event.get("status") if isinstance(event.get("status"), dict) else {}
    start_time = status.get("startsAt")
    odds = event.get("odds") if isinstance(event.get("odds"), dict) else {}
    if not event_id or not home_name or not away_name or not start_time:
        return []

    records: list[dict[str, Any]] = []
    for odd in odds.values():
        if not isinstance(odd, dict) or odd.get("cancelled") or odd.get("ended"):
            continue
        market_type = _market_type(odd)
        outcome = _market_outcome(odd)
        if market_type is None or outcome is None:
            continue
        bookmakers = odd.get("byBookmaker")
        if not isinstance(bookmakers, dict):
            continue
        for bookmaker, quote in bookmakers.items():
            if not isinstance(quote, dict) or quote.get("available") is False:
                continue
            decimal_odds = american_or_decimal_to_decimal(quote.get("odds"))
            if decimal_odds is None:
                continue
            records.append(
                {
                    "external_event_id": event_id,
                    "home_team": home_name,
                    "away_team": away_name,
                    "start_time": str(start_time),
                    "market_type": market_type,
                    "outcome": outcome,
                    "bookmaker": str(bookmaker),
                    "decimal_odds": decimal_odds,
                    "captured_at": quote.get("lastUpdatedAt") or datetime.utcnow().isoformat(),
                }
            )
    return records


def american_or_decimal_to_decimal(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return None
    if text.startswith("+") or text.startswith("-"):
        if number > 0:
            return round(1.0 + number / 100.0, 4)
        if number < 0:
            return round(1.0 + 100.0 / abs(number), 4)
        return None
    return round(number, 4) if number > 1.0 else None


def _team_name(team: Any) -> str | None:
    if not isinstance(team, dict):
        return None
    names = team.get("names") if isinstance(team.get("names"), dict) else {}
    return names.get("long") or names.get("medium") or team.get("teamID")


def _market_type(odd: dict[str, Any]) -> str | None:
    bet_type = str(odd.get("betTypeID") or "").lower()
    period = str(odd.get("periodID") or "").lower()
    if period not in {"game", "reg", "match", ""}:
        return None
    if bet_type == "ml":
        return "series_winner"
    if bet_type == "ml3way":
        return "series_result"
    return None


def _market_outcome(odd: dict[str, Any]) -> str | None:
    side = str(odd.get("sideID") or "").lower()
    entity = str(odd.get("statEntityID") or "").lower()
    combined = f"{side} {entity}"
    if "draw" in combined and "home+draw" not in combined:
        return "draw"
    if side in {"home", "away"}:
        return side
    if entity in {"home", "away"}:
        return entity
    return None


def _safe_error(error: str | None, api_key: str) -> str | None:
    if error and api_key:
        return error.replace(api_key, "***")
    return error
