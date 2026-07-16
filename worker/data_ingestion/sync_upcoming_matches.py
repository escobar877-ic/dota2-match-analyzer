from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

backend_dir = Path(__file__).resolve().parents[2] / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

if "WORKER_DATABASE_URL" in os.environ:
    os.environ["DATABASE_URL"] = os.environ["WORKER_DATABASE_URL"]
elif not Path("/.dockerenv").exists():
    current_url = os.getenv("DATABASE_URL")
    if current_url and "@postgres:" in current_url:
        os.environ["DATABASE_URL"] = current_url.replace("@postgres:", "@localhost:")

from sqlalchemy import select

from app.db.models import Match
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session, upsert_match
from worker.data_ingestion.normalizer import (
    NormalizedMatch,
    normalize_datetime,
    normalize_lookup_key,
    normalize_match_format,
    normalize_match_status,
    normalize_team_name,
    normalize_tournament_name,
)
from worker.data_ingestion.sources import get_source_client, get_source_clients
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


UPCOMING_SYNC_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "upcoming_sync_report.json"


def sync_upcoming_matches(
    *,
    source: str = "pandascore",
    from_date: str | None = None,
    to_date: str | None = None,
    limit: int = 50,
    dry_run: bool = True,
    artifact_path: str | Path | None = UPCOMING_SYNC_REPORT_PATH,
) -> dict[str, Any]:
    db = get_session()
    matcher = Tier1Matcher()
    counters = SyncCounters()
    source_errors: list[str] = []
    warnings: list[str] = []
    hard_exclusion_reasons: Counter[str] = Counter()
    classification_reasons: Counter[str] = Counter()
    prediction_block_reasons: Counter[str] = Counter()
    sample_upcoming: list[dict[str, Any]] = []
    sample_prediction_blocked: list[dict[str, Any]] = []
    would_create = 0
    would_update = 0
    upcoming_count = 0
    saved_upcoming_candidates = 0
    preserved_started_matches = 0
    prediction_eligible_count = 0
    source_prediction_eligible_count = 0
    prediction_blocked_count = 0
    missing_team_count = 0
    missing_tournament_count = 0
    competition_counts: Counter[str] = Counter()
    started_at = datetime.now(UTC)
    source_can_connect = False

    try:
        clients = get_source_clients() if source == "all" else [get_source_client(source)]
        for client in clients:
            if client.source_name != "pandascore":
                warnings.append(f"{client.source_name}: upcoming sync is not implemented for this source.")
                continue
            if not client.is_enabled():
                source_errors.append(f"{client.source_name}: {client.get_status().get('missing_key_reason') or 'disabled'}")
                continue
            health = client.health_check() if hasattr(client, "health_check") else None
            source_can_connect = bool(health and health.ok)
            if health and health.error:
                source_errors.append(f"{client.source_name}: {health.error}")
                continue
            result = client.fetch_upcoming_matches(limit=limit, from_date=from_date, to_date=to_date)
            if not result.ok:
                source_errors.append(f"{client.source_name}: {result.error}")
                continue
            records = result.records[:limit]
            counters.records_seen += len(records)
            for raw in records:
                match, hard_reasons, missing_teams, missing_tournament = _normalize_upcoming_record(raw)
                if hard_reasons:
                    counters.records_excluded += 1
                    hard_exclusion_reasons.update(hard_reasons)
                    continue

                existing = _existing_match(db, match)
                if existing is not None and existing.status in {"live", "finished"}:
                    preserved_started_matches += 1
                    continue

                upcoming_count += 1
                missing_team_count += int(missing_teams)
                missing_tournament_count += int(missing_tournament)
                classification = _classify_upcoming(match, matcher, missing_teams, missing_tournament)
                classification_reasons.update(classification["classification_reasons"])
                competition_counts.update([classification["competition_tier"]])
                source_prediction_eligible_count += int(classification["source_prediction_eligible"])
                prediction_eligible_count += int(classification["prediction_eligible"])
                prediction_blocked_count += int(not classification["prediction_eligible"])
                if classification["prediction_block_reason"]:
                    prediction_block_reasons.update([classification["prediction_block_reason"]])
                    _append_sample(
                        sample_prediction_blocked,
                        match,
                        classification,
                        limit=10,
                    )
                _append_sample(sample_upcoming, match, classification, limit=10)

                saved_upcoming_candidates += 1
                if dry_run:
                    would_update += int(existing is not None)
                    would_create += int(existing is None)
                    continue
                db_match, was_created = upsert_match(db, match, matcher=matcher, enforce_tier1=False)
                if db_match is None:
                    counters.records_excluded += 1
                    hard_exclusion_reasons.update(["upsert_failed"])
                    continue
                _apply_upcoming_classification(db_match, classification)
                counters.records_created += int(was_created)
                counters.records_updated += int(not was_created)

        valid_rows = would_create + would_update if dry_run else counters.records_created + counters.records_updated
        apply_allowed, apply_block_reason = _apply_status(
            source_can_connect,
            valid_rows,
            source_errors,
            preserved_started_matches=preserved_started_matches,
        )
        if not dry_run and not apply_allowed:
            db.rollback()
            source_errors.append(f"apply blocked: {apply_block_reason}")
        elif dry_run:
            db.rollback()
        else:
            write_sync_log(
                db,
                source=source,
                sync_type="upcoming_matches",
                status="warning" if source_errors or counters.records_excluded else "ok",
                started_at=started_at,
                counters=counters,
                error_message="; ".join(source_errors) if source_errors else None,
                metadata_json={
                    "training_eligible": False,
                    "prediction_eligible_count": prediction_eligible_count,
                    "source_prediction_eligible_count": source_prediction_eligible_count,
                    "hard_exclusion_reasons": dict(hard_exclusion_reasons),
                    "classification_reasons": dict(classification_reasons),
                },
            )
            db.commit()

        report = {
            "status": "warning" if source_errors or counters.records_excluded else "ok",
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "dry_run" if dry_run else "apply",
            "source": source,
            "quality_scope": "broad_upcoming",
            "records_seen": counters.records_seen,
            "would_create": would_create if dry_run else 0,
            "would_update": would_update if dry_run else 0,
            "would_exclude": counters.records_excluded if dry_run else 0,
            "records_created": counters.records_created if not dry_run else 0,
            "records_updated": counters.records_updated if not dry_run else 0,
            "records_excluded": counters.records_excluded,
            "upcoming_count": upcoming_count,
            "saved_upcoming_candidates": saved_upcoming_candidates,
            "preserved_started_matches": preserved_started_matches,
            "truly_invalid_count": counters.records_excluded,
            "tier1_upcoming_count": competition_counts["tier1"],
            "pro_upcoming_count": competition_counts["pro"],
            "qualifier_upcoming_count": competition_counts["qualifier"],
            "academy_upcoming_count": competition_counts["academy"],
            "unknown_upcoming_count": competition_counts["unknown"],
            "prediction_eligible_count": prediction_eligible_count,
            "source_prediction_eligible_count": source_prediction_eligible_count,
            "prediction_blocked_count": prediction_blocked_count,
            "missing_team_count": missing_team_count,
            "missing_tournament_count": missing_tournament_count,
            "source_errors": source_errors,
            "warnings": warnings,
            "hard_exclusion_reasons": dict(hard_exclusion_reasons),
            "classification_reasons": dict(classification_reasons),
            "top_prediction_block_reasons": dict(prediction_block_reasons.most_common(10)),
            "sample_upcoming": sample_upcoming,
            "sample_prediction_blocked": sample_prediction_blocked,
            "apply_allowed": apply_allowed,
            "apply_block_reason": apply_block_reason,
            "recommendation": _recommendation(
                source_errors,
                valid_rows,
                preserved_started_matches=preserved_started_matches,
            ),
            "is_training_eligible": False,
        }
        _write_report(report, artifact_path)
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
        return report
    finally:
        db.close()


def _existing_match(db, match: NormalizedMatch) -> Match | None:
    if not match.external_id:
        return None
    return db.scalar(select(Match).where(Match.external_source == match.external_source, Match.external_id == match.external_id))


def _normalize_upcoming_record(
    raw: Any,
) -> tuple[NormalizedMatch | None, list[str], bool, bool]:
    if not isinstance(raw, dict):
        return None, ["invalid_record"], True, True

    external_id = str(raw.get("id") or "").strip()
    start_time = normalize_datetime(raw.get("begin_at") or raw.get("scheduled_at"))
    raw_status = normalize_lookup_key(raw.get("status") or "")
    hard_reasons: list[str] = []
    if not external_id:
        hard_reasons.append("no_external_id")
    if start_time is None:
        hard_reasons.append("no_start_time")
    if raw_status in {"canceled", "cancelled"}:
        hard_reasons.append("cancelled")
    elif raw_status == "postponed":
        hard_reasons.append("postponed")
    elif raw_status == "deleted":
        hard_reasons.append("deleted")
    elif raw_status not in {"not started", "scheduled", "upcoming", "pending", "running", "live", "in progress", "started"}:
        hard_reasons.append("invalid_status")

    opponents = raw.get("opponents") if isinstance(raw.get("opponents"), list) else []
    team_a_id = _raw_opponent_id(opponents, 0)
    team_b_id = _raw_opponent_id(opponents, 1)
    team_a_name = normalize_team_name(_raw_opponent_name(opponents, 0))
    team_b_name = normalize_team_name(_raw_opponent_name(opponents, 1))
    missing_teams = not (team_a_id and team_b_id and team_a_name and team_b_name)

    league = raw.get("league") if isinstance(raw.get("league"), dict) else {}
    serie = raw.get("serie") if isinstance(raw.get("serie"), dict) else {}
    raw_tournament = league.get("name") or serie.get("full_name") or serie.get("name")
    tournament_name = normalize_tournament_name(raw_tournament)
    missing_tournament = not bool(tournament_name)
    if missing_teams and missing_tournament:
        hard_reasons.append("no_meaningful_match_info")
    if hard_reasons:
        return None, list(dict.fromkeys(hard_reasons)), missing_teams, missing_tournament

    team_a_external_id = team_a_id or f"tbd:{external_id}:a"
    team_b_external_id = team_b_id or f"tbd:{external_id}:b"
    match = NormalizedMatch(
        external_source="pandascore",
        external_id=external_id,
        team_a_external_id=team_a_external_id,
        team_b_external_id=team_b_external_id,
        team_a_name=team_a_name or "TBD",
        team_b_name=team_b_name or "TBD",
        tournament_name=tournament_name,
        start_time=start_time,
        format=normalize_match_format(raw.get("number_of_games")),
        status=normalize_match_status(raw.get("status")),
        raw_team_a=_raw_opponent_name(opponents, 0),
        raw_team_b=_raw_opponent_name(opponents, 1),
        raw_team_a_id=team_a_id,
        raw_team_b_id=team_b_id,
        raw_tournament=str(raw_tournament) if raw_tournament else None,
        raw_tournament_id=str(league.get("id")) if league.get("id") is not None else None,
    )
    return match, [], missing_teams, missing_tournament


def _raw_opponent_name(opponents: Any, index: int) -> str | None:
    if not isinstance(opponents, list) or len(opponents) <= index:
        return None
    opponent = opponents[index].get("opponent") if isinstance(opponents[index], dict) else None
    if not isinstance(opponent, dict):
        return None
    return str(opponent.get("name")) if opponent.get("name") is not None else None


def _raw_opponent_id(opponents: Any, index: int) -> str | None:
    if not isinstance(opponents, list) or len(opponents) <= index:
        return None
    opponent = opponents[index].get("opponent") if isinstance(opponents[index], dict) else None
    if not isinstance(opponent, dict):
        return None
    return str(opponent.get("id")) if opponent.get("id") is not None else None


def _classify_upcoming(
    match: NormalizedMatch,
    matcher: Tier1Matcher,
    missing_teams: bool,
    missing_tournament: bool,
) -> dict[str, Any]:
    team_a_tier1 = not missing_teams and matcher.is_tier1_team(match.team_a_name or "")
    team_b_tier1 = not missing_teams and matcher.is_tier1_team(match.team_b_name or "")
    tournament_tier1 = not missing_tournament and matcher.is_tier1_tournament(match.tournament_name or "")
    reasons: list[str] = []
    if missing_teams:
        reasons.append("missing_teams")
    else:
        if not team_a_tier1:
            reasons.append("team_a_not_tier1")
        if not team_b_tier1:
            reasons.append("team_b_not_tier1")
    if missing_tournament:
        reasons.append("missing_tournament")
    elif not tournament_tier1:
        reasons.append("tournament_not_tier1_allowlist")

    competition_tier = _competition_tier(match, missing_teams, missing_tournament, team_a_tier1, team_b_tier1, tournament_tier1)
    verified = not missing_teams and not missing_tournament
    source_prediction_eligible = verified
    prediction_eligible = verified and competition_tier == "tier1"
    block_reason = None if prediction_eligible else ",".join(reasons)
    return {
        "competition_tier": competition_tier,
        "verification_status": "verified" if verified else "unverified",
        "source_confidence": "high" if verified else "medium",
        "is_training_eligible": False,
        "source_prediction_eligible": source_prediction_eligible,
        "prediction_eligible": prediction_eligible,
        "prediction_block_reason": block_reason or None,
        "prediction_guard_level": "normal" if competition_tier == "tier1" else "high",
        "classification_reasons": reasons,
        "is_tier1": competition_tier == "tier1",
    }


def _competition_tier(
    match: NormalizedMatch,
    missing_teams: bool,
    missing_tournament: bool,
    team_a_tier1: bool,
    team_b_tier1: bool,
    tournament_tier1: bool,
) -> str:
    text = normalize_lookup_key(f"{match.team_a_name or ''} {match.team_b_name or ''} {match.tournament_name or ''}")
    if any(token in text.split() for token in {"academy", "youth", "junior", "juniors"}):
        return "academy"
    if "qualifier" in text:
        return "qualifier"
    if team_a_tier1 and team_b_tier1 and tournament_tier1:
        return "tier1"
    if not missing_teams and not missing_tournament:
        return "pro"
    return "unknown"


def _apply_upcoming_classification(db_match: Match, classification: dict[str, Any]) -> None:
    db_match.dataset_profile = "upcoming"
    db_match.competition_tier = classification["competition_tier"]
    db_match.verification_status = classification["verification_status"]
    db_match.source_confidence = classification["source_confidence"]
    db_match.is_training_eligible = False
    db_match.is_prediction_eligible = classification["prediction_eligible"]
    db_match.prediction_block_reason = classification["prediction_block_reason"]
    db_match.prediction_guard_level = classification["prediction_guard_level"]


def _append_sample(
    samples: list[dict[str, Any]],
    match: NormalizedMatch,
    classification: dict[str, Any],
    *,
    limit: int,
) -> None:
    if len(samples) >= limit:
        return
    samples.append(
        {
            "external_id": match.external_id,
            "team_a": match.team_a_name,
            "team_b": match.team_b_name,
            "tournament": match.tournament_name,
            "start_time": match.start_time.isoformat() if match.start_time else None,
            "competition_tier": classification["competition_tier"],
            "verification_status": classification["verification_status"],
            "source_prediction_eligible": classification["source_prediction_eligible"],
            "prediction_eligible": classification["prediction_eligible"],
            "prediction_guard_level": classification["prediction_guard_level"],
            "prediction_block_reason": classification["prediction_block_reason"],
            "classification_reasons": classification["classification_reasons"],
        }
    )


def _apply_status(
    source_can_connect: bool,
    valid_rows: int,
    source_errors: list[str],
    *,
    preserved_started_matches: int = 0,
) -> tuple[bool, str | None]:
    if not source_can_connect:
        return False, "Source health check failed."
    if source_errors:
        return False, "Source errors must be resolved before apply."
    if valid_rows <= 0 and preserved_started_matches <= 0:
        return False, "No valid upcoming candidates found."
    return True, None


def _recommendation(
    source_errors: list[str],
    valid_rows: int,
    *,
    preserved_started_matches: int = 0,
) -> str:
    if source_errors:
        return "review_source_errors"
    if valid_rows <= 0 and preserved_started_matches > 0:
        return "started_matches_preserved_no_upcoming_changes"
    if valid_rows <= 0:
        return "no_valid_upcoming_candidates"
    return "review_dry_run_then_apply_with_explicit_flag"


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run/apply upcoming match sync from schedule sources.")
    parser.add_argument("--source", choices=["pandascore", "all"], default="pandascore")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sync_upcoming_matches(
        source=args.source,
        from_date=args.from_date,
        to_date=args.to_date,
        limit=args.limit,
        dry_run=not args.apply,
    )


if __name__ == "__main__":
    main()
