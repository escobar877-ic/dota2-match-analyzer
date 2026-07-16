#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Union

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from worker.data_ingestion.base_client import ClientResponse
from worker.data_ingestion.opendota_client import OpenDotaClient


LEAGUES = {
    "ti2025": 18324,
    "ti2024": 16935,
    "ti2023": 15728,
    "riyadh_masters_2024": 16881,
    "riyadh_masters_2023": 15475,
    "ewc_2025": 18375,
    "ewc_2026": 19785,
    "dreamleague_s20": 15439,
    "dreamleague_s22": 16201,
    "dreamleague_s25": 17765,
    "dreamleague_s26": 18111,
    "dreamleague_s27": 18988,
    "esl_one_bangkok_2024": 17509,
    "esl_one_raleigh_2025": 17795,
    "pgl_wallachia_2025_s4": 18058,
    "pgl_wallachia_2025_s5": 18358,
    "pgl_wallachia_2026_s8": 19543,
    "blast_slam_i": 17414,
    "blast_slam_vii": 19101,
}

TOURNAMENT_NAMES = {
    "ti2025": "The International 2025",
    "ti2024": "The International 2024",
    "ti2023": "The International 2023",
    "riyadh_masters_2024": "Riyadh Masters 2024",
    "riyadh_masters_2023": "Riyadh Masters 2023",
    "ewc_2025": "Esports World Cup 2025",
    "ewc_2026": "Esports World Cup 2026",
    "dreamleague_s20": "DreamLeague Season 20",
    "dreamleague_s22": "DreamLeague Season 22",
    "dreamleague_s25": "DreamLeague Season 25",
    "dreamleague_s26": "DreamLeague Season 26",
    "dreamleague_s27": "DreamLeague Season 27",
    "esl_one_bangkok_2024": "ESL One Bangkok 2024",
    "esl_one_raleigh_2025": "ESL One Raleigh 2025",
    "pgl_wallachia_2025_s4": "PGL Wallachia Season 4",
    "pgl_wallachia_2025_s5": "PGL Wallachia Season 5",
    "pgl_wallachia_2026_s8": "PGL Wallachia Season 8",
    "blast_slam_i": "BLAST Slam I",
    "blast_slam_vii": "BLAST Slam VII",
}

CSV_FIELDS = [
    "source",
    "tournament_key",
    "tournament_name",
    "league_id",
    "match_id",
    "start_time_unix",
    "start_time_utc",
    "duration_sec",
    "duration_min",
    "radiant_team_id",
    "radiant_name",
    "dire_team_id",
    "dire_name",
    "radiant_win",
    "winner_side",
    "series_id",
    "series_type",
    "league_name_from_api",
    "opendota_match_url",
]


@dataclass(frozen=True)
class CollectionResult:
    matches: list[dict[str, Any]]
    processed_leagues: list[str]
    failed_leagues: dict[str, str]
    total_raw_matches: int
    duplicates_removed: int
    matches_by_tournament: dict[str, int]


def collect_matches(
    leagues: dict[str, int],
    fetch_league: Callable[[int], ClientResponse],
    *,
    limit: int,
    sleep_seconds: float,
    completed_before_unix: int | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> CollectionResult:
    collected: list[dict[str, Any]] = []
    processed: list[str] = []
    failed: dict[str, str] = {}
    total_raw = 0

    for index, (tournament_key, league_id) in enumerate(leagues.items()):
        try:
            response = fetch_league(league_id)
        except Exception as exc:
            failed[tournament_key] = _safe_error(exc)
        else:
            if not response.ok or not isinstance(response.data, list):
                failed[tournament_key] = _sanitize_text(
                    response.error or "OpenDota returned a non-list response"
                )
            else:
                processed.append(tournament_key)
                total_raw += len(response.data)
                for raw in response.data:
                    normalized = normalize_league_match(
                        raw,
                        tournament_key,
                        league_id,
                        completed_before_unix=completed_before_unix,
                    )
                    if normalized is not None:
                        collected.append(normalized)
        if index < len(leagues) - 1 and sleep_seconds > 0:
            sleeper(sleep_seconds)

    deduplicated: dict[str, dict[str, Any]] = {}
    for match in collected:
        match_id = str(match["match_id"])
        existing = deduplicated.get(match_id)
        if existing is None or int(match["start_time_unix"] or 0) > int(existing["start_time_unix"] or 0):
            deduplicated[match_id] = match

    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (int(item["start_time_unix"] or 0), int(item["match_id"])),
        reverse=True,
    )[:limit]
    by_tournament = Counter(str(match["tournament_key"]) for match in ordered)
    return CollectionResult(
        matches=ordered,
        processed_leagues=processed,
        failed_leagues=failed,
        total_raw_matches=total_raw,
        duplicates_removed=len(collected) - len(deduplicated),
        matches_by_tournament=dict(by_tournament),
    )


def normalize_league_match(
    raw: Any,
    tournament_key: str,
    league_id: int,
    *,
    completed_before_unix: int | None = None,
) -> Optional[dict[str, Any]]:
    if not isinstance(raw, dict):
        return None
    match_id = raw.get("match_id")
    duration = _as_int(raw.get("duration"))
    if match_id in (None, "") or duration <= 0:
        return None

    start_time = _as_int(raw.get("start_time"))
    if completed_before_unix is not None and (
        start_time <= 0 or start_time + duration > completed_before_unix
    ):
        return None
    radiant_win = raw.get("radiant_win")
    if radiant_win is True:
        winner_side = "radiant"
    elif radiant_win is False:
        winner_side = "dire"
    else:
        winner_side = ""

    api_league_id = raw.get("leagueid") or raw.get("league_id") or league_id
    api_league_name = raw.get("league_name") or ""
    return {
        "source": "opendota",
        "tournament_key": tournament_key,
        "tournament_name": TOURNAMENT_NAMES.get(tournament_key, tournament_key),
        "league_id": str(api_league_id),
        "match_id": str(match_id),
        "start_time_unix": str(start_time) if start_time else "",
        "start_time_utc": _format_utc(start_time),
        "duration_sec": str(duration),
        "duration_min": f"{duration / 60:.2f}",
        "radiant_team_id": _string_value(raw.get("radiant_team_id")),
        "radiant_name": _string_value(raw.get("radiant_name") or raw.get("radiant_team_name")),
        "dire_team_id": _string_value(raw.get("dire_team_id")),
        "dire_name": _string_value(raw.get("dire_name") or raw.get("dire_team_name")),
        "radiant_win": "" if radiant_win is None else ("True" if radiant_win else "False"),
        "winner_side": winner_side,
        "series_id": _string_value(raw.get("series_id")),
        "series_type": _string_value(raw.get("series_type")),
        "league_name_from_api": _string_value(api_league_name),
        "opendota_match_url": f"https://www.opendota.com/matches/{match_id}",
    }


def write_csv_atomic(matches: Iterable[dict[str, Any]], output_path: Union[str, Path]) -> Path:
    target = Path(output_path)
    if not target.is_absolute():
        target = PROJECT_ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(f"{target.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(matches)
    temporary.replace(target)
    return target


def enrich_team_names(
    matches: list[dict[str, Any]],
    fetch_team: Callable[[str], ClientResponse],
    *,
    sleep_seconds: float,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    team_ids = sorted(
        {
            str(match[field])
            for match in matches
            for field in ("radiant_team_id", "dire_team_id")
            if match.get(field)
        }
    )
    names: dict[str, str] = {}
    failures: dict[str, str] = {}
    for index, team_id in enumerate(team_ids):
        response = fetch_team(team_id)
        if response.ok and isinstance(response.data, dict) and response.data.get("name"):
            names[team_id] = str(response.data["name"]).strip()
        else:
            failures[team_id] = _sanitize_text(response.error or "team name unavailable")
        if index < len(team_ids) - 1 and sleep_seconds > 0:
            sleeper(sleep_seconds)

    enriched = []
    for match in matches:
        row = dict(match)
        row["radiant_name"] = row.get("radiant_name") or names.get(str(row.get("radiant_team_id") or ""), "")
        row["dire_name"] = row.get("dire_name") or names.get(str(row.get("dire_team_id") or ""), "")
        enriched.append(row)
    return enriched, failures


def validate_output(matches: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    ids = [str(match.get("match_id") or "") for match in matches]
    if any(not match_id for match_id in ids):
        warnings.append("CSV contains empty match_id values.")
    if len(ids) != len(set(ids)):
        warnings.append("CSV contains duplicate match_id values.")
    if any(not match.get("tournament_key") for match in matches):
        warnings.append("CSV contains rows without tournament_key.")
    if any(not match.get("league_id") for match in matches):
        warnings.append("CSV contains rows without league_id.")
    missing_names = sum(
        1 for match in matches if not match.get("radiant_name") or not match.get("dire_name")
    )
    if missing_names:
        warnings.append(
            f"{missing_names} rows have missing team names because the league endpoint did not provide them; "
            "the existing detail importer will enrich them by match_id."
        )
    return warnings


def print_summary(
    result: CollectionResult,
    output_path: Path,
    *,
    min_matches: int,
    warnings: list[str],
) -> None:
    print(f"Processed leagues: {len(result.processed_leagues)}")
    print(f"Total raw matches: {result.total_raw_matches}")
    print(f"Duplicates removed: {result.duplicates_removed}")
    print(f"Saved matches: {len(result.matches)}")
    print(f"Output: {output_path}")
    print("\nMatches by tournament:")
    for key in LEAGUES:
        print(f"- {key}: {result.matches_by_tournament.get(key, 0)}")
    print("\nFailed leagues:")
    if result.failed_leagues:
        for key, error in result.failed_leagues.items():
            print(f"- {key}: {error}")
    else:
        print("- none")
    combined_warnings = list(warnings)
    if len(result.matches) < min_matches:
        combined_warnings.append(
            f"Collected {len(result.matches)} matches, below requested minimum {min_matches}."
        )
    print("\nWarnings:")
    if combined_warnings:
        for warning in combined_warnings:
            print(f"- {warning}")
    else:
        print("- none")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a real finished-match ID dataset from trusted OpenDota league endpoints."
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--min-matches", type=int, default=800)
    parser.add_argument("--output", default="data/real/real_match_ids_800_1000.csv")
    parser.add_argument("--sleep", type=float, default=1.0)
    parser.add_argument(
        "--league",
        action="append",
        choices=sorted(LEAGUES),
        help="Collect only this configured league key. Repeat to select multiple leagues.",
    )
    parser.add_argument(
        "--completion-grace-minutes",
        type=float,
        default=60.0,
        help="Exclude maps whose reported end is newer than this safety window.",
    )
    parser.add_argument(
        "--skip-team-enrichment",
        action="store_true",
        help="Do not resolve missing team names through /api/teams/{team_id}.",
    )
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be greater than zero")
    if args.min_matches < 0:
        parser.error("--min-matches cannot be negative")
    if args.sleep < 0:
        parser.error("--sleep cannot be negative")
    if args.completion_grace_minutes < 0:
        parser.error("--completion-grace-minutes cannot be negative")
    return args


def main() -> None:
    args = parse_args()
    client = OpenDotaClient()
    selected_leagues = (
        {key: LEAGUES[key] for key in args.league}
        if args.league
        else LEAGUES
    )
    result = collect_matches(
        selected_leagues,
        client.get_league_matches,
        limit=args.limit,
        sleep_seconds=args.sleep,
        completed_before_unix=int(time.time() - args.completion_grace_minutes * 60),
    )
    matches = result.matches
    team_failures: dict[str, str] = {}
    if not args.skip_team_enrichment:
        matches, team_failures = enrich_team_names(
            matches,
            client.get_team,
            sleep_seconds=args.sleep,
        )
        result = CollectionResult(
            matches=matches,
            processed_leagues=result.processed_leagues,
            failed_leagues=result.failed_leagues,
            total_raw_matches=result.total_raw_matches,
            duplicates_removed=result.duplicates_removed,
            matches_by_tournament=result.matches_by_tournament,
        )
    output_path = write_csv_atomic(matches, args.output)
    warnings = validate_output(matches)
    if team_failures:
        warnings.append(f"Could not resolve {len(team_failures)} OpenDota team IDs.")
    print_summary(result, output_path, min_matches=args.min_matches, warnings=warnings)


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _format_utc(timestamp: int) -> str:
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _string_value(value: Any) -> str:
    return "" if value is None else str(value)


def _safe_error(error: Exception) -> str:
    return _sanitize_text(str(error) or error.__class__.__name__)


def _sanitize_text(text: str) -> str:
    for key in ("OPENDOTA_API_KEY", "STRATZ_API_KEY", "PANDASCORE_API_KEY"):
        secret = os.getenv(key)
        if secret:
            text = text.replace(secret, "***")
    return text


if __name__ == "__main__":
    main()
