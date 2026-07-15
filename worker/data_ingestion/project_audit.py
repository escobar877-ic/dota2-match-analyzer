from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Backtest, DataSyncLog, Match, ModelVersion, Team
from app.prediction.ensemble_prediction_service import try_predict_with_ensemble
from app.tier_filter.tier1_matcher import Tier1Matcher
from ml.config import ML_ARTIFACT_DIR
from ml.models import model_loader
from worker.data_ingestion.data_coverage import build_data_coverage_report
from worker.data_ingestion.db import get_session
from worker.data_ingestion.normalizer import normalize_lookup_key
from worker.data_ingestion.source_status import get_source_statuses


AUDIT_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "project_audit_report.json"
VALID_STATUSES = {"upcoming", "live", "finished"}
VALID_PREDICTION_TYPES = {"formula", "ml", "ensemble"}


def build_project_audit_report(db: Session, *, artifact_path: str | Path | None = AUDIT_REPORT_PATH) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    checks = {
        "tier1_filtering": "ok",
        "duplicates": "ok",
        "match_status": "ok",
        "sources": "ok",
        "coverage": "ok",
        "model_artifacts": "ok",
        "prediction_sanity": "ok",
    }

    matches = list(db.scalars(select(Match).options(selectinload(Match.team_a), selectinload(Match.team_b))).all())
    source_counts = Counter(match.external_source or "unknown" for match in matches)
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "total_teams": db.scalar(select(func.count()).select_from(Team)) or 0,
        "active_tier1_teams": db.scalar(select(func.count()).select_from(Team).where(Team.is_active_tier1.is_(True))) or 0,
        "total_matches": len(matches),
        "tier1_matches": sum(1 for match in matches if match.is_tier1_match),
        "finished_tier1_matches": sum(1 for match in matches if match.is_tier1_match and match.status == "finished"),
        "upcoming_tier1_matches": sum(1 for match in matches if match.is_tier1_match and match.status == "upcoming"),
        "excluded_matches": sum(1 for match in matches if not match.is_tier1_match),
        "dev_seed_matches": source_counts.get("dev_seed", 0),
        "real_source_matches": len(matches) - source_counts.get("dev_seed", 0),
        "external_source_distribution": dict(sorted(source_counts.items())),
    }

    _check_match_correctness(matches, errors, warnings, checks)
    _check_duplicates(matches, errors, warnings, checks)
    _check_sources(db, source_counts, warnings, checks)
    coverage = _check_coverage(db, warnings, checks)
    model_summary = _check_model_artifacts(db, warnings, errors, checks)
    _check_prediction_sanity(db, errors, warnings, checks)

    summary["coverage"] = coverage
    summary["model"] = model_summary
    summary["latest_sync_logs"] = _latest_sync_logs(db)

    status = "failed" if errors else "warning" if warnings else "ok"
    report = {
        "status": status,
        "summary": summary,
        "warnings": warnings,
        "errors": errors,
        "checks": checks,
    }
    if artifact_path is not None:
        path = Path(artifact_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.tmp")
        temp_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
        temp_path.replace(path)
    return report


def _check_match_correctness(matches: list[Match], errors: list[str], warnings: list[str], checks: dict[str, str]) -> None:
    matcher = Tier1Matcher()
    for match in matches:
        prefix = f"match_id={match.id}"
        if match.team_a_id is None or match.team_b_id is None:
            errors.append(f"{prefix}: missing team_a or team_b.")
        if match.team_a_id == match.team_b_id:
            errors.append(f"{prefix}: team_a and team_b are the same.")
        if not match.tournament_name:
            errors.append(f"{prefix}: missing tournament_name.")
        if match.start_time is None:
            errors.append(f"{prefix}: missing start_time.")
        if match.status not in VALID_STATUSES:
            errors.append(f"{prefix}: invalid status '{match.status}'.")
        if match.status == "finished" and match.winner_team_id is None and not match.is_draw:
            errors.append(f"{prefix}: finished match missing winner_team_id.")
        if match.status == "upcoming" and match.winner_team_id is not None:
            warnings.append(f"{prefix}: upcoming match has winner_team_id.")
        teams_are_tier1 = bool(match.team_a and match.team_b and match.team_a.is_active_tier1 and match.team_b.is_active_tier1)
        tournament_is_tier1 = matcher.is_tier1_tournament(match.tournament_name)
        if match.is_tier1_match and not (teams_are_tier1 and tournament_is_tier1):
            errors.append(f"{prefix}: marked Tier 1 but teams or tournament are not Tier 1.")
        if not match.is_tier1_match and not match.excluded_reason:
            warnings.append(f"{prefix}: non-Tier 1 match has no excluded_reason.")
    if errors:
        checks["match_status"] = "failed"
        checks["tier1_filtering"] = "failed"
    elif any("upcoming match has winner_team_id" in item or "excluded_reason" in item for item in warnings):
        checks["match_status"] = "warning"


def _check_duplicates(matches: list[Match], errors: list[str], warnings: list[str], checks: dict[str, str]) -> None:
    by_external: dict[tuple[str, str], list[int]] = defaultdict(list)
    by_tuple: dict[tuple[int, int, str, str], list[int]] = defaultdict(list)
    for match in matches:
        if match.external_source and match.external_id:
            by_external[(match.external_source, match.external_id)].append(match.id)
        if match.start_time and match.tournament_name:
            key = (
                match.team_a_id,
                match.team_b_id,
                normalize_lookup_key(match.tournament_name),
                match.start_time.isoformat(),
            )
            by_tuple[key].append(match.id)
    duplicate_external = [ids for ids in by_external.values() if len(ids) > 1]
    duplicate_tuples = [ids for ids in by_tuple.values() if len(ids) > 1]
    for ids in duplicate_external[:10]:
        errors.append(f"duplicate external_source+external_id: match_ids={ids}")
    for ids in duplicate_tuples[:10]:
        errors.append(f"duplicate normalized match tuple: match_ids={ids}")
    if duplicate_external or duplicate_tuples:
        checks["duplicates"] = "failed"


def _check_sources(db: Session, source_counts: Counter[str], warnings: list[str], checks: dict[str, str]) -> None:
    if source_counts and set(source_counts) == {"dev_seed"}:
        warnings.append("dev_seed_only=true: dataset is synthetic and not real accuracy.")
        checks["sources"] = "warning"
    statuses = get_source_statuses(db)
    for source, status in statuses.items():
        if not status.enabled:
            warnings.append(f"{source} disabled: {status.last_error or 'missing API key'}")
            checks["sources"] = "warning"


def _check_coverage(db: Session, warnings: list[str], checks: dict[str, str]) -> dict[str, Any]:
    coverage = build_data_coverage_report(db, artifact_path=None)
    if coverage["training_readiness"] == "insufficient":
        warnings.append(
            f"only {coverage['tier1_historical_matches_count']} historical matches, readiness insufficient"
        )
        checks["coverage"] = "warning"
    if coverage.get("dev_seed_only"):
        warnings.append("coverage dev_seed_only=true")
        checks["coverage"] = "warning"
    return {
        "training_readiness": coverage["training_readiness"],
        "tier1_historical_matches_count": coverage["tier1_historical_matches_count"],
        "patch_coverage_ratio": coverage["patch_coverage_ratio"],
        "roster_coverage_ratio": coverage["roster_coverage_ratio"],
        "dev_seed_only": coverage["dev_seed_only"],
    }


def _check_model_artifacts(db: Session, warnings: list[str], errors: list[str], checks: dict[str, str]) -> dict[str, Any]:
    active = db.scalar(
        select(ModelVersion)
        .where(ModelVersion.is_active.is_(True))
        .order_by(ModelVersion.trained_at.desc(), ModelVersion.id.desc())
        .limit(1)
    )
    latest_backtest = db.scalar(select(Backtest).order_by(Backtest.started_at.desc(), Backtest.id.desc()).limit(1))
    artifacts_exist = model_loader.model_artifacts_exist()
    artifacts_readable = False
    if artifacts_exist:
        try:
            _load_active_artifacts_with_retry()
            artifacts_readable = True
        except Exception as exc:
            warnings.append(f"ML active artifacts unreadable: {exc}")
            checks["model_artifacts"] = "warning"
    else:
        warnings.append("ML active artifacts missing.")
        checks["model_artifacts"] = "warning"
    if active is None:
        warnings.append("No active model version found.")
        checks["model_artifacts"] = "warning"
    if latest_backtest is None:
        warnings.append("No latest backtest found.")
        checks["model_artifacts"] = "warning"
    elif latest_backtest.dataset_type == "dev_seed":
        warnings.append("latest backtest uses dev_seed synthetic data.")
    return {
        "active_model_id": active.id if active else None,
        "active_model_status": active.status if active else None,
        "active_model_version": active.version if active else None,
        "artifacts_exist": artifacts_exist,
        "artifacts_readable": artifacts_readable,
        "latest_backtest_id": latest_backtest.id if latest_backtest else None,
        "latest_backtest_dataset_type": latest_backtest.dataset_type if latest_backtest else None,
    }


def _load_active_artifacts_with_retry(attempts: int = 3) -> None:
    last_error: OSError | None = None
    for attempt in range(attempts):
        try:
            model_loader.load_feature_schema()
            model_loader.load_active_model()
            return
        except OSError as exc:
            last_error = exc
            if getattr(exc, "errno", None) != 35 or attempt == attempts - 1:
                raise
            time.sleep(0.5)
    if last_error is not None:
        raise last_error


def _check_prediction_sanity(db: Session, errors: list[str], warnings: list[str], checks: dict[str, str]) -> None:
    sample_matches = list(
        db.scalars(
            select(Match)
            .options(selectinload(Match.team_a), selectinload(Match.team_b))
            .where(
                Match.is_tier1_match.is_(True),
                Match.status == "upcoming",
                Match.start_time.is_not(None),
            )
            .order_by(Match.start_time.asc(), Match.id.asc())
            .limit(5)
        ).all()
    )
    if not sample_matches:
        warnings.append("No upcoming Tier 1 matches available for prediction sanity sample.")
        checks["prediction_sanity"] = "warning"
        return
    for match in sample_matches:
        result = try_predict_with_ensemble(db, match)
        if result is None:
            errors.append(f"match_id={match.id}: prediction service returned no result.")
            continue
        if not hasattr(result, "team_a_probability") or not hasattr(result, "team_b_probability"):
            warnings.append(f"match_id={match.id}: prediction unavailable during audit.")
            checks["prediction_sanity"] = "warning"
            continue
        probability_sum = round(result.team_a_probability + result.team_b_probability, 4)
        if abs(probability_sum - 1.0) > 0.0001:
            errors.append(f"match_id={match.id}: probabilities sum to {probability_sum}.")
        if result.prediction_type not in VALID_PREDICTION_TYPES:
            errors.append(f"match_id={match.id}: invalid prediction_type {result.prediction_type}.")
    non_tier1_exists = bool(
        db.scalar(select(Match.id).where(Match.is_tier1_match.is_(False)).limit(1))
    )
    if not non_tier1_exists:
        warnings.append("No non-Tier 1 sample match found to verify rejection path.")
        checks["prediction_sanity"] = "warning"
    if any("prediction service" in item or "probabilities sum" in item or "prediction_type" in item for item in errors):
        checks["prediction_sanity"] = "failed"


def _latest_sync_logs(db: Session) -> dict[str, dict[str, Any] | None]:
    logs = {}
    for source in ["opendota", "stratz", "pandascore", "csv_import"]:
        log = db.scalar(
            select(DataSyncLog)
            .where(DataSyncLog.source == source)
            .order_by(DataSyncLog.started_at.desc(), DataSyncLog.id.desc())
            .limit(1)
        )
        logs[source] = (
            {
                "status": log.status,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "records_seen": log.records_seen,
                "records_created": log.records_created,
                "records_updated": log.records_updated,
                "records_excluded": log.records_excluded,
                "error_message": log.error_message,
            }
            if log
            else None
        )
    return logs


def print_human_report(report: dict[str, Any]) -> None:
    print("PROJECT AUDIT")
    print(f"Status: {report['status']}")
    print("")
    print("Errors:")
    if report["errors"]:
        for item in report["errors"]:
            print(f"* {item}")
    else:
        print("* none")
    print("")
    print("Warnings:")
    if report["warnings"]:
        for item in report["warnings"]:
            print(f"* {item}")
    else:
        print("* none")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Dota 2 Match Analyzer data and prediction correctness.")
    parser.parse_args()
    db = get_session()
    try:
        report = build_project_audit_report(db)
        print_human_report(report)
    finally:
        db.close()


if __name__ == "__main__":
    main()
