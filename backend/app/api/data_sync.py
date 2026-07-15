from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import with_db_error_handling
from app.database import get_db
from app.db.models import DataSyncLog, DotaPatch, Match, MatchPatchContext, Team, TeamRoster
from app.tier_filter.tier1_matcher import Tier1Matcher


router = APIRouter(tags=["data-sync"])

SOURCE_KEYS = {
    "opendota": "OPENDOTA_API_KEY",
    "stratz": "STRATZ_API_KEY",
    "pandascore": "PANDASCORE_API_KEY",
}

SOURCE_SETUP_HINTS = {
    "opendota": "Set OPENDOTA_API_KEY in .env to raise OpenDota rate limits; limited OpenDota sync may work without it.",
    "stratz": "Set STRATZ_API_KEY in .env to enable STRATZ sync.",
    "pandascore": "Set PANDASCORE_API_KEY in .env to enable PandaScore sync.",
}

COVERAGE_REPORT_PATH = Path("ml/artifacts/data_coverage_report.json")
AUDIT_REPORT_PATH = Path("ml/artifacts/project_audit_report.json")
MATCH_VALIDATION_REPORT_PATH = Path("ml/artifacts/match_validation_report.json")
REAL_INGESTION_PLAN_PATH = Path("ml/artifacts/real_ingestion_plan.json")
IMPORT_QUALITY_REPORT_PATH = Path("ml/artifacts/import_quality_report.json")
REAL_BATCH_REPORT_PATH = Path("ml/artifacts/real_batch_pipeline_report.json")
SOURCE_HEALTH_REPORT_PATH = Path("ml/artifacts/source_health_report.json")
HISTORICAL_FETCH_PLAN_PATH = Path("ml/artifacts/historical_fetch_plan.json")
HISTORICAL_SYNC_REPORT_PATH = Path("ml/artifacts/historical_sync_report.json")
SYNC_REVIEW_REPORT_PATH = Path("ml/artifacts/sync_review_report.json")
SOURCE_MAPPINGS_PATH = Path("config/source_mappings.json")
STRATZ_MATCH_ID_VALIDATION_REPORT_PATH = Path("ml/artifacts/stratz_match_id_validation_report.json")
STRATZ_MATCH_ID_IMPORT_REPORT_PATH = Path("ml/artifacts/stratz_match_id_import_report.json")
UPCOMING_SYNC_REPORT_PATH = Path("ml/artifacts/upcoming_sync_report.json")
MATCH_DETAIL_ENRICHMENT_REPORT_PATH = Path("ml/artifacts/match_detail_enrichment_report.json")

SOURCE_CAPABILITIES = {
    "opendota": {
        "requires_api_key": False,
        "supports_matches": True,
        "supports_teams": True,
        "supports_tournaments": True,
        "supports_rosters": False,
        "supports_drafts": True,
        "supports_finished_results": True,
        "supports_upcoming_matches": False,
        "reliability_notes": "Public match data may be incomplete and rate-limited; API key improves limits but is optional.",
    },
    "stratz": {
        "requires_api_key": True,
        "supports_matches": True,
        "supports_teams": True,
        "supports_tournaments": True,
        "supports_rosters": True,
        "supports_drafts": True,
        "supports_finished_results": True,
        "supports_upcoming_matches": False,
        "reliability_notes": "Best candidate for detailed historical match and draft stats when a key is configured.",
    },
    "pandascore": {
        "requires_api_key": True,
        "supports_matches": True,
        "supports_teams": True,
        "supports_tournaments": True,
        "supports_rosters": True,
        "supports_drafts": False,
        "supports_finished_results": True,
        "supports_upcoming_matches": True,
        "reliability_notes": "Useful for schedules, tournament metadata, teams, and upcoming matches.",
    },
    "csv_import": {
        "requires_api_key": False,
        "supports_matches": True,
        "supports_teams": True,
        "supports_tournaments": True,
        "supports_rosters": False,
        "supports_drafts": False,
        "supports_finished_results": True,
        "supports_upcoming_matches": True,
        "reliability_notes": "Manual fallback. Quality depends on curated source URLs and strict Tier 1 validation.",
    },
}


@router.get("/data-sources/status")
def get_data_sources_status(db: Session = Depends(get_db)) -> dict:
    return with_db_error_handling(
        lambda: {
            "sources": {
                **{source: _source_status(db, source) for source in SOURCE_KEYS},
                "csv_import": _csv_source_status(),
            },
            "capabilities": SOURCE_CAPABILITIES,
        }
    )


@router.get("/sync/logs")
def get_sync_logs(limit: int = 25, db: Session = Depends(get_db)) -> list[dict]:
    limit = max(1, min(100, limit))
    return with_db_error_handling(
        lambda: [
            _log_to_dict(log)
            for log in db.scalars(
                select(DataSyncLog).order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc()).limit(limit)
            ).all()
        ]
    )


@router.get("/sync/logs/latest")
def get_latest_sync_logs(db: Session = Depends(get_db)) -> dict:
    def load() -> dict:
        latest = {}
        for source in SOURCE_KEYS:
            log = db.scalar(
                select(DataSyncLog)
                .where(DataSyncLog.source == source)
                .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
                .limit(1)
            )
            latest[source] = _log_to_dict(log) if log else None
        return {"logs": latest}

    return with_db_error_handling(load)


@router.get("/data/coverage")
def get_data_coverage(db: Session = Depends(get_db)) -> dict:
    def load() -> dict:
        if COVERAGE_REPORT_PATH.exists():
            return json.loads(COVERAGE_REPORT_PATH.read_text(encoding="utf-8"))
        return _live_coverage_summary(db)

    return with_db_error_handling(load)


@router.get("/data/audit")
def get_project_audit() -> dict:
    def load() -> dict:
        if not AUDIT_REPORT_PATH.exists():
            return {
                "status": "missing",
                "message": "Run python -m worker.data_ingestion.project_audit",
            }
        return json.loads(AUDIT_REPORT_PATH.read_text(encoding="utf-8"))

    return with_db_error_handling(load)


@router.get("/data/match-validation")
def get_match_validation() -> dict:
    def load() -> dict:
        if not MATCH_VALIDATION_REPORT_PATH.exists():
            return {
                "status": "missing",
                "message": "Run python -m worker.data_ingestion.match_validation",
            }
        return json.loads(MATCH_VALIDATION_REPORT_PATH.read_text(encoding="utf-8"))

    return with_db_error_handling(load)


@router.get("/data/real-ingestion-plan")
def get_real_ingestion_plan() -> dict:
    def load() -> dict:
        if not REAL_INGESTION_PLAN_PATH.exists():
            return {
                "status": "missing",
                "message": "Run python -m worker.data_ingestion.real_ingestion_plan",
            }
        return json.loads(REAL_INGESTION_PLAN_PATH.read_text(encoding="utf-8"))

    return with_db_error_handling(load)


@router.get("/data/import-quality")
def get_import_quality_report() -> dict:
    def load() -> dict:
        if not IMPORT_QUALITY_REPORT_PATH.exists():
            return {
                "status": "missing",
                "message": "Run python -m worker.data_ingestion.import_quality_report imports/tier1_matches_template.csv",
            }
        return json.loads(IMPORT_QUALITY_REPORT_PATH.read_text(encoding="utf-8"))

    return with_db_error_handling(load)


@router.get("/data/real-batch-report")
def get_real_batch_report() -> dict:
    def load() -> dict:
        if not REAL_BATCH_REPORT_PATH.exists():
            return {
                "status": "missing",
                "message": "Run scripts/real_batch_pipeline.sh <csv>",
            }
        return json.loads(REAL_BATCH_REPORT_PATH.read_text(encoding="utf-8"))

    return with_db_error_handling(load)


@router.get("/data/source-health")
def get_source_health() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(SOURCE_HEALTH_REPORT_PATH, "Run python -m worker.data_ingestion.source_health")
    )


@router.get("/data/historical-fetch-plan")
def get_historical_fetch_plan() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            HISTORICAL_FETCH_PLAN_PATH,
            "Run python -m worker.data_ingestion.historical_fetch_planner",
        )
    )


@router.get("/data/historical-sync-report")
def get_historical_sync_report() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            HISTORICAL_SYNC_REPORT_PATH,
            "Run python -m worker.data_ingestion.sync_historical_matches --source opendota --start-date YYYY-MM-DD --end-date YYYY-MM-DD",
        )
    )


@router.get("/data/sync-review")
def get_sync_review() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            SYNC_REVIEW_REPORT_PATH,
            "Run python -m worker.data_ingestion.sync_review ml/artifacts/historical_sync_report.json",
        )
    )


@router.get("/data/source-mappings/status")
def get_source_mappings_status() -> dict:
    return with_db_error_handling(_source_mappings_status)


@router.get("/data/stratz-match-id-validation")
def get_stratz_match_id_validation() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            STRATZ_MATCH_ID_VALIDATION_REPORT_PATH,
            "Run python -m worker.data_ingestion.stratz_match_id_validator imports/stratz_match_ids_template.csv",
        )
    )


@router.get("/data/stratz-match-id-import")
def get_stratz_match_id_import() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            STRATZ_MATCH_ID_IMPORT_REPORT_PATH,
            "Run python -m worker.data_ingestion.stratz_match_id_import --file imports/stratz_match_ids_template.csv",
        )
    )


@router.get("/data/upcoming-sync-report")
def get_upcoming_sync_report() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            UPCOMING_SYNC_REPORT_PATH,
            "Run python -m worker.data_ingestion.sync_upcoming_matches --source pandascore --limit 50",
        )
    )


@router.get("/data/match-detail-enrichment")
def get_match_detail_enrichment_report() -> dict:
    return with_db_error_handling(
        lambda: _read_report_or_missing(
            MATCH_DETAIL_ENRICHMENT_REPORT_PATH,
            "Run bash scripts/enrich_match_details.sh --limit 50",
        )
    )


def _source_status(db: Session, source: str) -> dict:
    env_key = SOURCE_KEYS[source]
    has_api_key = bool(os.getenv(env_key))
    enabled = source == "opendota" or has_api_key
    log = db.scalar(
        select(DataSyncLog)
        .where(DataSyncLog.source == source)
        .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
        .limit(1)
    )
    missing_key_error = f"{env_key} missing" if not has_api_key and source != "opendota" else None
    return {
        "enabled": enabled,
        "has_api_key": has_api_key,
        "last_sync_status": log.status if log else "never",
        "last_error": log.error_message if log and log.error_message else missing_key_error,
        "setup_hint": SOURCE_SETUP_HINTS[source] if not has_api_key else None,
        "missing_key_reason": missing_key_error,
        "safe_to_sync": enabled and (has_api_key or not SOURCE_CAPABILITIES[source]["requires_api_key"]),
        "capabilities": SOURCE_CAPABILITIES[source],
    }


def _csv_source_status() -> dict:
    return {
        "enabled": True,
        "has_api_key": True,
        "last_sync_status": "manual",
        "last_error": None,
        "setup_hint": "Use imports/tier1_matches_template.csv with import quality check, then dry-run before apply.",
        "missing_key_reason": None,
        "safe_to_sync": True,
        "capabilities": SOURCE_CAPABILITIES["csv_import"],
    }


def _read_report_or_missing(path: Path, message: str) -> dict:
    if not path.exists():
        return {"status": "missing", "message": message}
    return json.loads(path.read_text(encoding="utf-8"))


def _source_mappings_status() -> dict:
    if not SOURCE_MAPPINGS_PATH.exists():
        return {
            "status": "missing",
            "message": "Create config/source_mappings.json with verified source-specific Tier 1 mappings.",
            "mapped_teams_count": 0,
            "mapped_tournaments_count": 0,
            "invalid_mappings_count": 0,
            "invalid_mappings": [],
        }
    mappings = json.loads(SOURCE_MAPPINGS_PATH.read_text(encoding="utf-8"))
    matcher = Tier1Matcher()
    invalid: list[dict[str, str]] = []
    mapped_teams_count = 0
    mapped_tournaments_count = 0

    for source, source_mapping in mappings.items():
        teams = source_mapping.get("teams", {}) if isinstance(source_mapping, dict) else {}
        tournaments = source_mapping.get("tournaments", {}) if isinstance(source_mapping, dict) else {}
        for key, canonical in teams.items():
            mapped_teams_count += 1
            if not matcher.is_tier1_team(str(canonical)):
                invalid.append(
                    {"source": str(source), "kind": "team", "key": str(key), "canonical_name": str(canonical)}
                )
        for key, canonical in tournaments.items():
            mapped_tournaments_count += 1
            if not matcher.is_tier1_tournament(str(canonical)):
                invalid.append(
                    {"source": str(source), "kind": "tournament", "key": str(key), "canonical_name": str(canonical)}
                )

    return {
        "status": "failed" if invalid else "ok",
        "mapping_path": str(SOURCE_MAPPINGS_PATH),
        "mapped_teams_count": mapped_teams_count,
        "mapped_tournaments_count": mapped_tournaments_count,
        "invalid_mappings_count": len(invalid),
        "invalid_mappings": invalid,
        "message": "Source mappings are manual only; unknown teams or tournaments are never auto-added to Tier 1.",
    }


def _log_to_dict(log: DataSyncLog) -> dict:
    return {
        "id": log.id,
        "source": log.source,
        "sync_type": log.sync_type,
        "status": log.status,
        "started_at": log.started_at,
        "finished_at": log.finished_at,
        "records_seen": log.records_seen,
        "records_created": log.records_created,
        "records_updated": log.records_updated,
        "records_excluded": log.records_excluded,
        "error_message": log.error_message,
        "metadata_json": log.metadata_json,
    }


def _live_coverage_summary(db: Session) -> dict:
    matches = db.scalars(
        select(Match).where(
            Match.is_tier1_match.is_(True),
            Match.status == "finished",
        )
    ).all()
    match_ids = [match.id for match in matches]
    patch_count = _patch_context_count(db, match_ids)
    roster_count = sum(1 for match in matches if _match_has_roster_context(db, match))
    start_times = [match.start_time for match in matches if match.start_time is not None]
    source_counts = Counter(match.external_source or "unknown" for match in matches)
    dev_seed_only = bool(matches) and set(source_counts) == {"dev_seed"}
    readiness = _training_readiness(len(matches))

    return {
        "generated_at": None,
        "tier1_teams_count": db.scalar(select(func.count()).select_from(Team).where(Team.is_active_tier1.is_(True))) or 0,
        "tier1_historical_matches_count": len(matches),
        "matches_with_winner_count": sum(1 for match in matches if match.winner_team_id is not None),
        "matches_with_patch_context_count": patch_count,
        "matches_with_roster_context_count": roster_count,
        "patch_coverage_ratio": _ratio(patch_count, len(matches)),
        "roster_coverage_ratio": _ratio(roster_count, len(matches)),
        "matches_by_tournament": dict(Counter(match.tournament_name or "Unknown" for match in matches)),
        "matches_by_patch": _matches_by_patch(db, match_ids),
        "matches_by_source": dict(source_counts),
        "date_range": {
            "from": min(start_times).isoformat() if start_times else None,
            "to": max(start_times).isoformat() if start_times else None,
        },
        "training_readiness": readiness,
        "enough_for_training": readiness in {"usable", "good"},
        "dev_seed_only": dev_seed_only,
        "warning": "Coverage is synthetic dev seed only and is not real accuracy." if dev_seed_only else None,
    }


def _patch_context_count(db: Session, match_ids: list[int]) -> int:
    if not match_ids:
        return 0
    return db.scalar(select(func.count()).select_from(MatchPatchContext).where(MatchPatchContext.match_id.in_(match_ids))) or 0


def _matches_by_patch(db: Session, match_ids: list[int]) -> dict[str, int]:
    if not match_ids:
        return {}
    rows = db.execute(
        select(DotaPatch.patch_version, func.count(MatchPatchContext.id))
        .join(MatchPatchContext, MatchPatchContext.patch_id == DotaPatch.id)
        .where(MatchPatchContext.match_id.in_(match_ids))
        .group_by(DotaPatch.patch_version)
        .order_by(DotaPatch.patch_version)
    ).all()
    return {patch_version: count for patch_version, count in rows}


def _match_has_roster_context(db: Session, match: Match) -> bool:
    if match.start_time is None:
        return False
    for team_id in (match.team_a_id, match.team_b_id):
        roster_id = db.scalar(
            select(TeamRoster.id)
            .where(
                TeamRoster.team_id == team_id,
                TeamRoster.start_date <= match.start_time,
                (TeamRoster.end_date.is_(None)) | (TeamRoster.end_date >= match.start_time),
            )
            .limit(1)
        )
        if not roster_id:
            return False
    return True


def _training_readiness(match_count: int) -> str:
    if match_count >= 1000:
        return "good"
    if match_count >= 300:
        return "usable"
    return "insufficient"


def _ratio(value: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(value / total, 4)
