from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.source_mapping import (
    known_team_aliases,
    known_tournament_aliases,
    suggest_alias_matches,
)


SYNC_REVIEW_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "sync_review_report.json"


def build_sync_review_report(
    report_path: str | Path,
    *,
    artifact_path: str | Path | None = SYNC_REVIEW_REPORT_PATH,
) -> dict[str, Any]:
    historical_report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    exclusion_reasons = historical_report.get("exclusion_reasons") or {}
    excluded_samples = historical_report.get("excluded_samples") or []
    warnings = list(historical_report.get("warnings") or [])

    if excluded_samples and not _has_any_raw_team_names(excluded_samples):
        warnings.append("historical_sync_report has no raw team names; source client normalization must expose raw fields.")

    unknown_teams = _extract_unknown_teams(excluded_samples)
    unknown_tournaments = _extract_unknown_tournaments(excluded_samples)

    alias_suggestions = _build_alias_suggestions(unknown_teams, unknown_tournaments)
    grouped_suggestions = _group_suggestions_by_risk(alias_suggestions)
    valid_rows = int(historical_report.get("would_create") or 0) + int(historical_report.get("would_update") or 0)
    source = str(historical_report.get("source") or "unknown")
    source_errors = list(historical_report.get("source_errors") or [])
    source_trust_level = _source_trust_level(source, historical_report)
    apply_allowed, apply_block_reason = _apply_status(source, source_trust_level, valid_rows, exclusion_reasons, source_errors)
    recommendation = _recommendation(source, valid_rows, exclusion_reasons, source_errors)
    report = {
        "status": "warning" if exclusion_reasons or historical_report.get("source_errors") else "ok",
        "generated_at": datetime.now(UTC).isoformat(),
        "source": source,
        "source_trust_level": source_trust_level,
        "records_seen": int(historical_report.get("records_seen") or 0),
        "would_create": int(historical_report.get("would_create") or 0),
        "would_update": int(historical_report.get("would_update") or 0),
        "would_exclude": int(historical_report.get("would_exclude") or historical_report.get("records_excluded") or 0),
        "valid_rows": valid_rows,
        "top_exclusion_reasons": dict(Counter(exclusion_reasons).most_common(10)),
        "source_errors": source_errors,
        "unknown_teams": unknown_teams,
        "unknown_tournaments": unknown_tournaments,
        "alias_suggestions": alias_suggestions,
        "blocked_alias_suggestions": grouped_suggestions["blocked"],
        "risky_alias_suggestions": grouped_suggestions["risky"],
        "safe_alias_suggestions": grouped_suggestions["safe"],
        "possible_tier1_candidates": _possible_candidates(alias_suggestions),
        "apply_allowed": apply_allowed,
        "apply_block_reason": apply_block_reason,
        "warnings": _unique(warnings),
        "recommended_action": recommendation,
        "recommendation_detail": _recommendation_detail(source, valid_rows, excluded_samples, warnings, source_errors),
    }

    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return report


def _extract_unknown_teams(samples: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for sample in samples:
        reasons = _sample_reasons(sample)
        if reasons & {"team_a_not_tier1", "team_a_not_tier1_or_unmapped"}:
            value = _first_value(sample, "raw_team_a", "normalized_team_a", "team_a_name")
            if value:
                values.add(value)
        if reasons & {"team_b_not_tier1", "team_b_not_tier1_or_unmapped"}:
            value = _first_value(sample, "raw_team_b", "normalized_team_b", "team_b_name")
            if value:
                values.add(value)
    return sorted(values)


def _extract_unknown_tournaments(samples: list[dict[str, Any]]) -> list[str]:
    values: set[str] = set()
    for sample in samples:
        reasons = _sample_reasons(sample)
        if reasons & {"tournament_not_tier1_allowlist", "tournament_not_tier1_or_unmapped"}:
            value = _first_value(sample, "raw_tournament", "normalized_tournament", "tournament_name")
            if value:
                values.add(value)
    return sorted(values)


def _sample_reasons(sample: dict[str, Any]) -> set[str]:
    return set(sample.get("exclusion_reasons") or sample.get("reasons") or [])


def _first_value(sample: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = sample.get(key)
        if value:
            return str(value)
    return None


def _has_any_raw_team_names(samples: list[dict[str, Any]]) -> bool:
    return any(sample.get("raw_team_a") or sample.get("raw_team_b") for sample in samples)


def _build_alias_suggestions(unknown_teams: list[str], unknown_tournaments: list[str]) -> dict[str, dict[str, list[dict[str, str]]]]:
    return {
        "teams": {
            name: suggestions
            for name in unknown_teams
            if (suggestions := suggest_alias_matches(name, known_team_aliases()))
        },
        "tournaments": {
            name: suggestions
            for name in unknown_tournaments
            if (suggestions := suggest_alias_matches(name, known_tournament_aliases()))
        },
    }


def _group_suggestions_by_risk(alias_suggestions: dict[str, dict[str, list[dict[str, str]]]]) -> dict[str, list[dict[str, str]]]:
    grouped = {"blocked": [], "risky": [], "safe": []}
    for kind, suggestions_by_name in alias_suggestions.items():
        for suggestions in suggestions_by_name.values():
            for suggestion in suggestions:
                item = {"kind": kind[:-1], **suggestion}
                grouped.setdefault(suggestion["risk"], []).append(item)
    return grouped


def _possible_candidates(alias_suggestions: dict[str, dict[str, list[dict[str, str]]]]) -> list[dict[str, Any]]:
    candidates = []
    for kind, suggestions_by_name in alias_suggestions.items():
        for raw_name, suggestions in suggestions_by_name.items():
            candidates.append({"kind": kind[:-1], "raw_name": raw_name, "suggestions": suggestions})
    return candidates


def _source_trust_level(source: str, historical_report: dict[str, Any]) -> str:
    return str(historical_report.get("source_trust_level") or historical_report.get("source_mode") or ("discovery" if source == "opendota" else "trusted"))


def _apply_status(
    source: str,
    source_trust_level: str,
    valid_rows: int,
    exclusion_reasons: dict[str, Any],
    source_errors: list[str],
) -> tuple[bool, str | None]:
    if source == "opendota" and source_trust_level == "discovery":
        return False, "OpenDota generic feed is discovery-only and not safe for apply."
    if _has_stratz_date_range_unsupported_error(source, source_errors):
        return False, "STRATZ date-range historical fetch is unsupported; use match ids, PandaScore schedule, or CSV batch."
    if valid_rows == 0:
        return False, "No valid Tier 1 rows found; mappings needed and apply must stay blocked."
    if exclusion_reasons:
        return False, "Excluded rows require review before apply."
    return True, None


def _recommendation(source: str, valid_rows: int, exclusion_reasons: dict[str, Any], source_errors: list[str]) -> str:
    if valid_rows > 0:
        return "valid_rows_found_review_then_apply_with_explicit_flag"
    if _has_stratz_date_range_unsupported_error(source, source_errors):
        return "use_stratz_match_ids_or_pandascore_schedule_or_csv_batch"
    if source == "opendota":
        return "do_not_apply_opendota_generic_feed_collect_csv_or_use_verified_source"
    if source == "stratz":
        return "use_stratz_match_ids_or_pandascore_schedule_or_csv_batch"
    if exclusion_reasons:
        return "mappings_needed_do_not_apply"
    return "no_records_to_apply"


def _recommendation_detail(
    source: str,
    valid_rows: int,
    excluded_samples: list[dict[str, Any]],
    warnings: list[str],
    source_errors: list[str],
) -> str:
    if valid_rows > 0:
        return "Review valid rows, then apply only with an explicit --apply flag."
    if _has_stratz_date_range_unsupported_error(source, source_errors):
        return "STRATZ date-range historical fetch is unsupported for the current GraphQL schema; use match ids, PandaScore schedule, or CSV batch."
    if source == "opendota":
        return "OpenDota generic feed is not a reliable Tier 1 schedule source; collect CSV or use verified PandaScore/STRATZ scope."
    if any("OpenDota endpoint lacks team/tournament names" in warning for warning in warnings):
        return "Use detailed match endpoint or stronger source like PandaScore/STRATZ/CSV."
    if excluded_samples:
        return "Review unknown teams/tournaments and add verified mappings only if they are real Tier 1."
    return "Run historical sync dry-run again after source normalization exposes excluded samples."


def _has_stratz_date_range_unsupported_error(source: str, source_errors: list[str]) -> bool:
    return source == "stratz" and any("date-range historical fetch is not implemented" in error for error in source_errors)


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Review historical sync exclusions before first real apply.")
    parser.add_argument("report_path")
    args = parser.parse_args()
    report = build_sync_review_report(args.report_path)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
