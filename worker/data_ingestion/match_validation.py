from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
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
from sqlalchemy.orm import Session, selectinload

from app.db.models import DataSyncLog, Match
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session
from worker.data_ingestion.normalizer import normalize_lookup_key, normalize_match_format
from worker.data_ingestion.cross_source_match_resolver import choose_preferred_source
from worker.data_ingestion.pro_match_quality import is_verified_pro_tournament


MATCH_VALIDATION_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "match_validation_report.json"
API_SOURCES_REQUIRING_EXTERNAL_ID = {"opendota", "stratz", "pandascore"}
VALID_STATUSES = {"upcoming", "live", "finished"}
VALID_FORMATS = {"BO1", "BO2", "BO3", "BO5", "unknown"}


def build_match_validation_report(
    db: Session,
    *,
    artifact_path: str | Path | None = MATCH_VALIDATION_REPORT_PATH,
) -> dict[str, Any]:
    matcher = Tier1Matcher()
    matches = list(
        db.scalars(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b), selectinload(Match.winner_team))
            .order_by(Match.start_time.asc().nullsfirst(), Match.id.asc())
        ).all()
    )
    warnings: list[str] = []
    errors: list[str] = []
    suspect_matches: list[dict[str, Any]] = []
    source_stats: dict[str, Counter[str]] = defaultdict(Counter)

    seen_external: dict[tuple[str, str], list[Match]] = defaultdict(list)
    seen_tuple: dict[tuple[int, int, str, str], list[Match]] = defaultdict(list)
    by_source = Counter(match.external_source or "unknown" for match in matches)
    metadata_by_external_id = _csv_metadata_by_external_id(db)
    seen_series_game: dict[tuple[str, str], list[Match]] = defaultdict(list)

    for match in matches:
        source = match.external_source or "unknown"
        source_stats[source]["total_matches"] += 1
        reasons = _validate_match(match, matcher, source_stats[source])
        metadata = metadata_by_external_id.get(str(match.external_id or match.id), {})
        reasons.extend(_validate_match_metadata(match, metadata))
        if reasons:
            source_stats[source]["invalid_matches"] += 1
            for reason in reasons:
                _add_suspect(suspect_matches, match, reason)
                if _is_error_reason(reason):
                    errors.append(f"match_id={match.id}: {reason}")
                else:
                    warnings.append(f"match_id={match.id}: {reason}")
        else:
            source_stats[source]["valid_matches"] += 1
        if not match.is_tier1_match:
            source_stats[source]["excluded_matches"] += 1

        if match.external_source and match.external_id:
            seen_external[(match.external_source, match.external_id)].append(match)
        if match.start_time and match.tournament_name:
            seen_tuple[_normalized_tuple_key(match)].append(match)
        series_id = metadata.get("series_id")
        game_number = metadata.get("game_number")
        if series_id and game_number:
            seen_series_game[(series_id, game_number)].append(match)

    _detect_duplicate_external_ids(seen_external, errors, suspect_matches, source_stats)
    _detect_duplicate_tuples(seen_tuple, errors, suspect_matches, source_stats)
    _detect_duplicate_series_games(seen_series_game, errors, suspect_matches, source_stats)
    _detect_cross_source_possible_duplicates(matches, warnings, suspect_matches, source_stats)

    if by_source and "dev_seed" in by_source and len(by_source) > 1:
        warnings.append("dev_seed is mixed with non-dev sources; do not treat mixed data as real accuracy without review.")
    elif by_source and set(by_source) == {"dev_seed"}:
        warnings.append("dev_seed_only=true: dataset is synthetic and not real accuracy.")

    summary = {
        "total_matches": len(matches),
        "tier1_matches": sum(1 for match in matches if match.is_tier1_match),
        "excluded_matches": sum(1 for match in matches if not match.is_tier1_match),
        "external_source_distribution": dict(sorted(by_source.items())),
        "suspect_matches_count": len(suspect_matches),
    }
    status = "failed" if errors else "warning" if warnings else "ok"
    report = {
        "status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": summary,
        "source_summary": _source_summary(source_stats),
        "warnings": warnings,
        "errors": errors,
        "suspect_matches": suspect_matches[:200],
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp_path.replace(path)
    return report


def _validate_match(match: Match, matcher: Tier1Matcher, stats: Counter[str]) -> list[str]:
    reasons = []
    source = match.external_source or "unknown"
    if not match.external_source:
        reasons.append("missing_external_source")
    if source in API_SOURCES_REQUIRING_EXTERNAL_ID and not match.external_id:
        reasons.append("missing_external_id_for_api_source")
    if match.team_a is None or match.team_b is None:
        reasons.append("missing_team")
        stats["unknown_team_count"] += 1
    elif match.team_a_id == match.team_b_id:
        reasons.append("team_a_equals_team_b")
    verified_pro_context = _is_verified_pro_context(match)
    if match.team_a and not matcher.is_tier1_team(match.team_a.name):
        stats["unknown_team_count"] += 1
        if match.is_tier1_match and not verified_pro_context:
            reasons.append("team_a_not_tier1_allowlist")
    if match.team_b and not matcher.is_tier1_team(match.team_b.name):
        stats["unknown_team_count"] += 1
        if match.is_tier1_match and not verified_pro_context:
            reasons.append("team_b_not_tier1_allowlist")
    if not match.tournament_name:
        reasons.append("missing_tournament")
        stats["missing_tournament_count"] += 1
    elif not matcher.is_tier1_tournament(match.tournament_name) and match.is_tier1_match:
        reasons.append("tournament_not_tier1_allowlist")
    if match.start_time is None:
        reasons.append("missing_start_time")
    elif match.start_time.year < 2011 or match.start_time.year > datetime.now(UTC).year + 2:
        reasons.append("unreasonable_start_time")
    if match.status not in VALID_STATUSES:
        reasons.append("invalid_status")
    if match.status == "finished":
        if match.winner_team_id is None and not match.is_draw:
            reasons.append("finished_missing_winner")
            stats["missing_winner_count"] += 1
        elif match.winner_team_id is not None and match.winner_team_id not in {match.team_a_id, match.team_b_id}:
            reasons.append("winner_not_in_match_teams")
    if match.status == "upcoming" and match.winner_team_id is not None:
        reasons.append("upcoming_has_final_winner")
    normalized_format = normalize_match_format(match.format) or "unknown"
    if normalized_format not in VALID_FORMATS:
        reasons.append("invalid_match_format")
    if match.is_tier1_match and not _is_valid_tier1_match(match, matcher):
        reasons.append("marked_tier1_but_invalid_tier1_context")
    if not match.is_tier1_match and not match.excluded_reason:
        reasons.append("excluded_match_missing_excluded_reason")
    return reasons


def _detect_duplicate_external_ids(
    seen_external: dict[tuple[str, str], list[Match]],
    errors: list[str],
    suspect_matches: list[dict[str, Any]],
    source_stats: dict[str, Counter[str]],
) -> None:
    for (_source, _external_id), grouped in seen_external.items():
        if len(grouped) <= 1:
            continue
        ids = [match.id for match in grouped]
        for match in grouped:
            source_stats[match.external_source or "unknown"]["duplicate_warnings"] += 1
            _add_suspect(suspect_matches, match, f"duplicate_external_id match_ids={ids}")
        errors.append(f"duplicate external_source+external_id: match_ids={ids}")


def _detect_duplicate_tuples(
    seen_tuple: dict[tuple[int, int, str, str], list[Match]],
    errors: list[str],
    suspect_matches: list[dict[str, Any]],
    source_stats: dict[str, Counter[str]],
) -> None:
    for grouped in seen_tuple.values():
        if len(grouped) <= 1:
            continue
        ids = [match.id for match in grouped]
        sources = {match.external_source for match in grouped}
        reason = f"duplicate_normalized_tuple match_ids={ids}"
        for match in grouped:
            source_stats[match.external_source or "unknown"]["duplicate_warnings"] += 1
            _add_suspect(suspect_matches, match, reason)
        if len(sources) == 1:
            errors.append(f"duplicate normalized tuple: match_ids={ids}")
        else:
            errors.append(f"duplicate normalized tuple across sources: match_ids={ids}")


def _detect_cross_source_possible_duplicates(
    matches: list[Match],
    warnings: list[str],
    suspect_matches: list[dict[str, Any]],
    source_stats: dict[str, Counter[str]],
) -> None:
    candidates = [match for match in matches if match.start_time and match.tournament_name and match.team_a_id != match.team_b_id]
    for index, left in enumerate(candidates):
        for right in candidates[index + 1 :]:
            if (left.external_source or "unknown") == (right.external_source or "unknown"):
                continue
            if {left.team_a_id, left.team_b_id} != {right.team_a_id, right.team_b_id}:
                continue
            if normalize_lookup_key(left.tournament_name or "") != normalize_lookup_key(right.tournament_name or ""):
                continue
            if abs(left.start_time - right.start_time) >= timedelta(hours=6):
                continue
            reason = f"possible_same_match_cross_source match_ids={[left.id, right.id]}"
            warnings.append(reason)
            if choose_preferred_source(left, right) == "incoming":
                warnings.append(f"source_priority_conflict weaker_existing_match_id={left.id} stronger_match_id={right.id}")
            elif choose_preferred_source(right, left) == "incoming":
                warnings.append(f"source_priority_conflict weaker_existing_match_id={right.id} stronger_match_id={left.id}")
            if abs(left.start_time - right.start_time) >= timedelta(hours=1):
                warnings.append(f"suspicious_start_time_difference match_ids={[left.id, right.id]}")
            for match in (left, right):
                source_stats[match.external_source or "unknown"]["duplicate_warnings"] += 1
                _add_suspect(suspect_matches, match, reason)


def _detect_duplicate_series_games(
    seen_series_game: dict[tuple[str, str], list[Match]],
    errors: list[str],
    suspect_matches: list[dict[str, Any]],
    source_stats: dict[str, Counter[str]],
) -> None:
    for (series_id, game_number), grouped in seen_series_game.items():
        if len(grouped) <= 1:
            continue
        ids = [match.id for match in grouped]
        reason = f"duplicate_series_id_game_number series_id={series_id} game_number={game_number} match_ids={ids}"
        errors.append(reason)
        for match in grouped:
            source_stats[match.external_source or "unknown"]["duplicate_warnings"] += 1
            _add_suspect(suspect_matches, match, reason)


def _is_valid_tier1_match(match: Match, matcher: Tier1Matcher) -> bool:
    if _is_verified_pro_context(match):
        return True
    return bool(
        match.team_a
        and match.team_b
        and match.team_a.is_active_tier1
        and match.team_b.is_active_tier1
        and matcher.is_tier1_team(match.team_a.name)
        and matcher.is_tier1_team(match.team_b.name)
        and matcher.is_tier1_tournament(match.tournament_name)
    )


def _is_verified_pro_context(match: Match) -> bool:
    return bool(
        match.external_source in {"pandascore", "stratz", "csv_import"}
        and match.tournament_name
        and is_verified_pro_tournament(match.tournament_name)
    )


def _normalized_tuple_key(match: Match) -> tuple[int, int, str, str]:
    team_pair = tuple(sorted([match.team_a_id, match.team_b_id]))
    return (
        team_pair[0],
        team_pair[1],
        normalize_lookup_key(match.tournament_name or ""),
        match.start_time.isoformat() if match.start_time else "",
    )


def _is_error_reason(reason: str) -> bool:
    return reason not in {
        "upcoming_has_final_winner",
        "excluded_match_missing_excluded_reason",
        "invalid_source_url",
        "invalid_vod_url",
    }


def _validate_match_metadata(match: Match, metadata: dict[str, Any]) -> list[str]:
    reasons = []
    if not metadata:
        return reasons
    team_a_score = _metadata_int(metadata.get("team_a_score"))
    team_b_score = _metadata_int(metadata.get("team_b_score"))
    if team_a_score is not None and team_b_score is not None and match.winner_team_id:
        if match.winner_team_id == match.team_a_id and team_a_score <= team_b_score:
            reasons.append("score_winner_mismatch")
        if match.winner_team_id == match.team_b_id and team_b_score <= team_a_score:
            reasons.append("score_winner_mismatch")
        max_wins = {"BO1": 1, "BO2": 2, "BO3": 2, "BO5": 3}.get(normalize_match_format(match.format))
        if max_wins is not None and (team_a_score > max_wins or team_b_score > max_wins):
            reasons.append("score_impossible_for_format")
    for field in ("source_url", "vod_url"):
        value = str(metadata.get(field) or "")
        if value and not value.startswith(("http://", "https://")):
            reasons.append(f"invalid_{field}")
    return reasons


def _metadata_int(value: Any) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _csv_metadata_by_external_id(db: Session) -> dict[str, dict]:
    logs = db.scalars(
        select(DataSyncLog)
        .where(DataSyncLog.source == "csv_import")
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
    ).all()
    metadata: dict[str, dict] = {}
    for log in logs:
        row_metadata = (log.metadata_json or {}).get("row_metadata") or {}
        for key, value in row_metadata.items():
            metadata.setdefault(str(key), value)
    return metadata


def _add_suspect(suspect_matches: list[dict[str, Any]], match: Match, reason: str) -> None:
    suspect_matches.append(
        {
            "match_id": match.id,
            "external_source": match.external_source,
            "external_id": match.external_id,
            "teams": [
                match.team_a.name if match.team_a else None,
                match.team_b.name if match.team_b else None,
            ],
            "tournament": match.tournament_name,
            "start_time": match.start_time.isoformat() if match.start_time else None,
            "reason": reason,
        }
    )


def _source_summary(source_stats: dict[str, Counter[str]]) -> dict[str, dict[str, int]]:
    fields = [
        "total_matches",
        "valid_matches",
        "invalid_matches",
        "excluded_matches",
        "duplicate_warnings",
        "missing_winner_count",
        "missing_tournament_count",
        "unknown_team_count",
    ]
    return {
        source: {field: int(stats.get(field, 0)) for field in fields}
        for source, stats in sorted(source_stats.items())
    }


def print_human_report(report: dict[str, Any]) -> None:
    print("MATCH VALIDATION")
    print(f"Status: {report['status']}")
    print("")
    print("Errors:")
    if report["errors"]:
        for item in report["errors"][:25]:
            print(f"* {item}")
    else:
        print("* none")
    print("")
    print("Warnings:")
    if report["warnings"]:
        for item in report["warnings"][:25]:
            print(f"* {item}")
    else:
        print("* none")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate stored matches and cross-source consistency.")
    parser.parse_args()
    db = get_session()
    try:
        report = build_match_validation_report(db)
        print_human_report(report)
    finally:
        db.close()


if __name__ == "__main__":
    main()
