from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

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

from app.db.models import DotaPatch, Match, MatchPatchContext
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.data_quality import validate_match
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
from worker.data_ingestion.sync_logging import SyncCounters, write_sync_log


SOURCE = "csv_import"
CSV_IMPORT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "csv_import_report.json"
OPTIONAL_METADATA_FIELDS = {
    "team_a_score",
    "team_b_score",
    "series_id",
    "game_number",
    "radiant_team_name",
    "dire_team_name",
    "duration_seconds",
    "vod_url",
    "source_url",
}


def import_csv(
    path: str | Path,
    *,
    apply: bool = False,
    artifact_path: str | Path | None = None,
) -> dict[str, object]:
    started_at = datetime.now(timezone.utc)
    db = get_session()
    counters = SyncCounters()
    exclusion_reasons: Counter[str] = Counter()
    try:
        matcher = Tier1Matcher()
        rows = _read_rows(path)
        counters.records_seen = len(rows)
        would_create = 0
        would_update = 0
        row_metadata: dict[str, dict] = {}

        for row in rows:
            match = _row_to_match(row)
            quality = validate_match(match, matcher=matcher)
            row_errors = validate_csv_row_metadata(row)
            existing = _existing_match(db, match)
            if quality.reasons or row_errors:
                counters.records_excluded += 1
                exclusion_reasons.update(quality.reasons)
                exclusion_reasons.update(row_errors)
                continue

            if not apply:
                if existing:
                    would_update += 1
                else:
                    would_create += 1
                continue

            db_match, was_created = upsert_match(db, match, matcher=matcher)
            if db_match is None:
                counters.records_excluded += 1
                exclusion_reasons.update(["not_tier1_match"])
                continue
            _apply_patch_context(db, db_match, row.get("patch_version"))
            metadata = _row_metadata(row)
            if metadata:
                row_metadata[str(db_match.external_id or db_match.id)] = metadata
            counters.records_created += int(was_created)
            counters.records_updated += int(not was_created)

        if apply:
            write_sync_log(
                db,
                source=SOURCE,
                sync_type="matches",
                status="ok",
                started_at=started_at,
                counters=counters,
                metadata_json={
                    "file": str(path),
                    "exclusion_reasons": dict(exclusion_reasons),
                    "row_metadata": row_metadata,
                },
            )
            db.commit()
        else:
            db.rollback()
            counters.records_created = would_create
            counters.records_updated = would_update

        result = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "status": "warning" if counters.records_excluded else "ok",
            "source": SOURCE,
            "file": str(path),
            "mode": "apply" if apply else "dry_run",
            "records_seen": counters.records_seen,
            "created": counters.records_created,
            "updated": counters.records_updated,
            "would_create": counters.records_created if not apply else 0,
            "would_update": counters.records_updated if not apply else 0,
            "excluded": counters.records_excluded,
            "exclusion_reasons": dict(exclusion_reasons),
        }
        _write_report(result, artifact_path)
        print(result)
        return result
    except Exception as exc:
        db.rollback()
        if apply:
            write_sync_log(
                db,
                source=SOURCE,
                sync_type="matches",
                status="failed",
                started_at=started_at,
                counters=counters,
                error_message=str(exc),
                metadata_json={"file": str(path)},
            )
        if artifact_path is not None:
            _write_report(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "status": "failed",
                    "source": SOURCE,
                    "file": str(path),
                    "mode": "apply" if apply else "dry_run",
                    "records_seen": counters.records_seen,
                    "created": counters.records_created,
                    "updated": counters.records_updated,
                    "excluded": counters.records_excluded,
                    "errors": [str(exc)],
                },
                artifact_path,
            )
        raise
    finally:
        db.close()


def _read_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def _row_to_match(row: dict[str, str]) -> NormalizedMatch:
    team_a_name = normalize_team_name(row.get("team_a_name"))
    team_b_name = normalize_team_name(row.get("team_b_name"))
    winner_name = normalize_team_name(row.get("winner_team_name"))
    return NormalizedMatch(
        external_source=SOURCE,
        external_id=_external_id(row),
        team_a_external_id=_team_external_id(team_a_name),
        team_b_external_id=_team_external_id(team_b_name),
        team_a_name=team_a_name,
        team_b_name=team_b_name,
        tournament_name=normalize_tournament_name(row.get("tournament_name")),
        start_time=normalize_datetime(row.get("start_time")),
        format=normalize_match_format(row.get("format")),
        status=normalize_match_status(row.get("status")),
        winner_team_external_id=_team_external_id(winner_name) if winner_name else None,
    )


def _external_id(row: dict[str, str]) -> str:
    explicit = (row.get("external_id") or "").strip()
    if explicit:
        return explicit
    series_id = (row.get("series_id") or "").strip()
    game_number = (row.get("game_number") or "").strip()
    if series_id and game_number:
        return f"{series_id}:game:{game_number}"
    return ""


def _team_external_id(name: str) -> str:
    return normalize_lookup_key(name).replace(" ", "-")


def _existing_match(db, match: NormalizedMatch) -> Match | None:
    if match.external_id:
        existing = db.scalar(select(Match).where(Match.external_source == SOURCE, Match.external_id == match.external_id))
        if existing:
            return existing
    if not match.start_time or not match.tournament_name:
        return None
    return db.scalar(
        select(Match)
        .where(
            Match.external_source == SOURCE,
            Match.tournament_name == match.tournament_name,
            Match.start_time == match.start_time,
        )
        .limit(1)
    )


def validate_csv_row_metadata(row: dict[str, str]) -> list[str]:
    errors = []
    team_a_score = _optional_int(row.get("team_a_score"))
    team_b_score = _optional_int(row.get("team_b_score"))
    if (row.get("team_a_score") or row.get("team_b_score")) and (team_a_score is None or team_b_score is None):
        errors.append("invalid_score")
    winner = normalize_team_name(row.get("winner_team_name"))
    team_a = normalize_team_name(row.get("team_a_name"))
    team_b = normalize_team_name(row.get("team_b_name"))
    if winner and winner not in {team_a, team_b}:
        errors.append("invalid_winner")
    if team_a_score is not None and team_b_score is not None:
        if winner == team_a and team_a_score <= team_b_score:
            errors.append("score_winner_mismatch")
        if winner == team_b and team_b_score <= team_a_score:
            errors.append("score_winner_mismatch")
        match_format = normalize_match_format(row.get("format"))
        max_wins = {"BO1": 1, "BO2": 2, "BO3": 2, "BO5": 3}.get(match_format)
        if max_wins is not None and (team_a_score > max_wins or team_b_score > max_wins):
            errors.append("score_impossible_for_format")
    duration = row.get("duration_seconds")
    if duration and _optional_int(duration) is None:
        errors.append("invalid_duration_seconds")
    for field in ("source_url", "vod_url"):
        value = (row.get(field) or "").strip()
        if value and not value.startswith(("http://", "https://")):
            errors.append(f"invalid_{field}")
    return errors


def _optional_int(value: str | None) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        parsed = int(str(value).strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _row_metadata(row: dict[str, str]) -> dict:
    metadata = {
        key: (row.get(key) or "").strip()
        for key in OPTIONAL_METADATA_FIELDS
        if (row.get(key) or "").strip()
    }
    if metadata.get("source_url"):
        metadata["source_url_verified"] = False
    return metadata


def _apply_patch_context(db, match: Match, patch_version: str | None) -> None:
    if not patch_version:
        return
    patch = db.scalar(select(DotaPatch).where(DotaPatch.patch_version == str(patch_version).strip()))
    if patch is None or match.start_time is None:
        return
    days_since_patch = max(0, (match.start_time.date() - patch.release_date.date()).days)
    existing = db.scalar(select(MatchPatchContext).where(MatchPatchContext.match_id == match.id))
    if existing:
        existing.patch_id = patch.id
        existing.days_since_patch = days_since_patch
        existing.is_current_patch = patch.is_current
        return
    db.add(
        MatchPatchContext(
            match_id=match.id,
            patch_id=patch.id,
            days_since_patch=days_since_patch,
            is_current_patch=patch.is_current,
        )
    )


def _write_report(report: dict[str, object], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temp_path.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Import manually curated Tier 1 matches from CSV.")
    parser.add_argument("path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    import_csv(args.path, apply=args.apply, artifact_path=CSV_IMPORT_REPORT_PATH)


if __name__ == "__main__":
    main()
