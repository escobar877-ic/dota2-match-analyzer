from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.db.models import MarketOddsSnapshot, Match
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.normalizer import normalize_datetime, normalize_lookup_key
from worker.odds_ingestion.sportsgameodds_client import SportsGameOddsClient


REPORT_PATH = Path(ML_ARTIFACT_DIR) / "odds_sync_report.json"


def sync_market_odds(*, limit: int = 100, dry_run: bool = True) -> dict[str, Any]:
    client = SportsGameOddsClient()
    source_status = client.get_status()
    if not client.is_enabled():
        report = _empty_report(source_status, client.fetch_upcoming_odds(limit=limit).error)
        _write_report(report)
        return report

    result = client.fetch_upcoming_odds(limit=limit)
    if not result.ok:
        report = _empty_report(source_status, result.error)
        _write_report(report)
        return report

    db = SessionLocal()
    try:
        matches = list(
            db.scalars(
                select(Match)
                .options(selectinload(Match.team_a), selectinload(Match.team_b))
                .where(
                    Match.status.in_(["upcoming", "live"]),
                    Match.is_tier1_match.is_(True),
                )
            ).all()
        )
        matched = 0
        unmatched = 0
        would_create = 0
        created = 0
        bookmakers: Counter[str] = Counter()
        markets: Counter[str] = Counter()
        samples = []
        for record in result.records:
            match, swapped = _find_match(record, matches)
            if match is None:
                unmatched += 1
                continue
            matched += 1
            outcome = _local_outcome(record["outcome"], swapped)
            captured_at = normalize_datetime(record.get("captured_at")) or datetime.now(timezone.utc)
            bookmakers.update([record["bookmaker"]])
            markets.update([record["market_type"]])
            if len(samples) < 20:
                samples.append(
                    {
                        "match_id": match.id,
                        "bookmaker": record["bookmaker"],
                        "market_type": record["market_type"],
                        "outcome": outcome,
                        "decimal_odds": record["decimal_odds"],
                    }
                )
            if _snapshot_exists(
                db,
                match.id,
                record["bookmaker"],
                record["market_type"],
                outcome,
                captured_at,
            ):
                continue
            would_create += 1
            if not dry_run:
                db.add(
                    MarketOddsSnapshot(
                        match_id=match.id,
                        bookmaker=record["bookmaker"],
                        market_type=record["market_type"],
                        outcome=outcome,
                        decimal_odds=record["decimal_odds"],
                        captured_at=captured_at,
                    )
                )
                created += 1
        if dry_run:
            db.rollback()
        else:
            db.commit()
        report = {
            "status": "ok",
            "mode": "dry_run" if dry_run else "apply",
            "source": client.source_name,
            "records_seen": len(result.records),
            "records_matched": matched,
            "records_unmatched": unmatched,
            "would_create": would_create if dry_run else 0,
            "records_created": created,
            "bookmakers_count": len(bookmakers),
            "bookmakers": sorted(bookmakers),
            "markets": dict(markets),
            "sample_quotes": samples,
            "source_errors": [],
            "apply_allowed": matched > 0,
            "recommendation": (
                "review_then_apply" if dry_run and matched > 0 else "no_matching_tier1_events"
            ),
        }
        _write_report(report)
        return report
    finally:
        db.close()


def _find_match(record: dict[str, Any], matches: list[Match]) -> tuple[Match | None, bool]:
    start_time = normalize_datetime(record.get("start_time"))
    if start_time is None:
        return None, False
    home = normalize_lookup_key(record.get("home_team") or "")
    away = normalize_lookup_key(record.get("away_team") or "")
    for match in matches:
        if match.start_time is None or abs(match.start_time - start_time) > timedelta(hours=12):
            continue
        team_a = normalize_lookup_key(match.team_a.name)
        team_b = normalize_lookup_key(match.team_b.name)
        if home == team_a and away == team_b:
            return match, False
        if home == team_b and away == team_a:
            return match, True
    return None, False


def _local_outcome(outcome: str, swapped: bool) -> str:
    if outcome == "draw":
        return "draw"
    if outcome == "home":
        return "team_b" if swapped else "team_a"
    return "team_a" if swapped else "team_b"


def _snapshot_exists(
    db,
    match_id: int,
    bookmaker: str,
    market_type: str,
    outcome: str,
    captured_at: datetime,
) -> bool:
    return (
        db.scalar(
            select(MarketOddsSnapshot.id)
            .where(
                MarketOddsSnapshot.match_id == match_id,
                MarketOddsSnapshot.bookmaker == bookmaker,
                MarketOddsSnapshot.market_type == market_type,
                MarketOddsSnapshot.outcome == outcome,
                MarketOddsSnapshot.captured_at == captured_at,
            )
            .limit(1)
        )
        is not None
    )


def _empty_report(status: dict[str, Any], error: str | None) -> dict[str, Any]:
    return {
        "status": "disabled" if not status["enabled"] else "warning",
        "mode": "dry_run",
        "source": status["source"],
        "records_seen": 0,
        "records_matched": 0,
        "records_unmatched": 0,
        "would_create": 0,
        "records_created": 0,
        "bookmakers_count": 0,
        "bookmakers": [],
        "markets": {},
        "sample_quotes": [],
        "source_errors": [error] if error else [],
        "apply_allowed": False,
        "recommendation": status["setup_hint"],
    }


def _write_report(report: dict[str, Any]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = REPORT_PATH.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary_path.replace(REPORT_PATH)


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync official aggregated bookmaker odds.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = sync_market_odds(limit=args.limit, dry_run=not args.apply)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
