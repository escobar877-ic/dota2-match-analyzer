from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
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

from sqlalchemy.orm import Session

from app.patches.patch_service import get_current_patch, load_patch_config
from ml.config import ML_ARTIFACT_DIR
from worker.data_ingestion.db import get_session
from worker.data_ingestion.opendota_client import OpenDotaClient


PATCH_FRESHNESS_REPORT_PATH = Path(ML_ARTIFACT_DIR) / "patch_freshness_report.json"
PATCH_FAMILY_PATTERN = re.compile(r"^(\d+)\.(\d+)")


def build_patch_freshness_report(
    *,
    client: OpenDotaClient | None = None,
    config_path: str | Path | None = None,
    db: Session | None = None,
    check_database: bool = True,
    artifact_path: str | Path | None = PATCH_FRESHNESS_REPORT_PATH,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []
    limitations = [
        "OpenDota constants/patch exposes base gameplay patch families and does not verify lettered hotfix subpatches."
    ]
    configured = _configured_current_patch(config_path, errors)
    configured_version = str(configured.get("patch_version")) if configured else None
    configured_family = _patch_family(configured_version)

    source_client = client or OpenDotaClient()
    source_response = source_client.get_patches()
    latest_source = _latest_source_patch(source_response.data, errors) if source_response.ok else None
    if not source_response.ok:
        warnings.append(source_response.error or "OpenDota patch metadata request failed.")
    source_version = str(latest_source.get("name")) if latest_source else None
    source_family = _patch_family(source_version)

    family_matches: bool | None = None
    stale: bool | None = None
    if configured_family and source_family:
        family_matches = configured_family == source_family
        stale = configured_family < source_family
        if stale:
            warnings.append(
                f"Configured patch family {configured_family[0]}.{configured_family[1]} is older than "
                f"OpenDota patch family {source_family[0]}.{source_family[1]}."
            )
        elif configured_family > source_family:
            limitations.append(
                "The local config is newer than OpenDota base-patch metadata; verify the local release manually."
            )

    database_version = None
    database_matches_config: bool | None = None
    owns_session = False
    if check_database:
        try:
            if db is None:
                db = get_session()
                owns_session = True
            database_patch = get_current_patch(db)
            database_version = database_patch.patch_version if database_patch else None
            database_matches_config = bool(database_version and database_version == configured_version)
            if not database_matches_config:
                warnings.append(
                    "Database current patch does not match config/dota_patches.json; run patch_service --sync-config."
                )
        except Exception as exc:
            warnings.append(f"Database patch check failed: {exc}")
        finally:
            if owns_session and db is not None:
                db.close()

    source_checked = bool(source_response.ok and latest_source)
    if errors:
        status = "failed"
        recommendation = "Fix patch config or source metadata errors before relying on patch context."
    elif stale:
        status = "warning"
        recommendation = "Verify the new gameplay patch, update config/dota_patches.json, then sync and backfill contexts."
    elif warnings:
        status = "warning"
        recommendation = "Resolve the reported warning; do not update patch history automatically."
    else:
        status = "ok"
        recommendation = "Patch family matches OpenDota. Continue manual review for lettered hotfix subpatches."

    report = {
        "status": status,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "opendota_constants_patch",
        "source_scope": "base_gameplay_patch_families_only",
        "source_checked": source_checked,
        "configured_current_patch": configured_version,
        "configured_release_date": configured.get("release_date") if configured else None,
        "database_current_patch": database_version,
        "database_matches_config": database_matches_config,
        "latest_source_patch_family": source_version,
        "latest_source_release_date": latest_source.get("date") if latest_source else None,
        "family_matches": family_matches,
        "stale": stale,
        "manual_subpatch_review_required": bool(configured_version and re.search(r"[a-z]$", configured_version, re.I)),
        "limitations": limitations,
        "warnings": warnings,
        "errors": errors,
        "recommendation": recommendation,
    }
    _write_report(report, artifact_path)
    return report


def _configured_current_patch(config_path: str | Path | None, errors: list[str]) -> dict[str, Any] | None:
    try:
        items = load_patch_config(Path(config_path) if config_path else None)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"Patch config could not be loaded: {exc}")
        return None
    current = [item for item in items if item.get("is_current") is True]
    if len(current) != 1:
        errors.append(f"Patch config must contain exactly one current patch; found {len(current)}.")
        return None
    if _patch_family(str(current[0].get("patch_version", ""))) is None:
        errors.append("Current patch_version is invalid.")
        return None
    return current[0]


def _latest_source_patch(payload: Any, errors: list[str]) -> dict[str, Any] | None:
    if not isinstance(payload, list):
        errors.append("OpenDota patch metadata must be a list.")
        return None
    candidates = [item for item in payload if isinstance(item, dict) and _patch_family(str(item.get("name", "")))]
    if not candidates:
        errors.append("OpenDota patch metadata contains no valid patch families.")
        return None
    return max(candidates, key=lambda item: (_patch_family(str(item["name"])) or (0, 0), int(item.get("id", 0))))


def _patch_family(version: str | None) -> tuple[int, int] | None:
    match = PATCH_FAMILY_PATTERN.match((version or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _write_report(report: dict[str, Any], artifact_path: str | Path | None) -> None:
    if artifact_path is None:
        return
    path = Path(artifact_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check local Dota patch config freshness without changing data.")
    parser.parse_args()
    print(json.dumps(build_patch_freshness_report(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
