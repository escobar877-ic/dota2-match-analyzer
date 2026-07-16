#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

echo "SYSTEM CHECK"
echo "Project: $PROJECT_ROOT"

docker compose config --quiet
bash scripts/ensure_local_services.sh

docker compose run --rm worker python -m worker.data_ingestion.data_coverage >/dev/null
docker compose run --rm worker python -m worker.data_ingestion.real_ingestion_plan >/dev/null
docker compose run --rm worker python -m worker.data_ingestion.patch_freshness >/dev/null
docker compose run --rm worker python -m worker.data_ingestion.match_validation >/dev/null
docker compose run --rm worker python -m worker.data_ingestion.project_audit >/dev/null

readiness_file="$(mktemp)"
trap 'rm -f "$readiness_file"' EXIT
curl --fail --silent --show-error http://localhost:8000/health/ready >"$readiness_file"
curl --fail --silent --show-error http://localhost:3000/ >/dev/null

python3 - "$readiness_file" <<'PY'
import json
import sys
from pathlib import Path

readiness = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
audit = json.loads(Path("ml/artifacts/project_audit_report.json").read_text(encoding="utf-8"))
validation = json.loads(Path("ml/artifacts/match_validation_report.json").read_text(encoding="utf-8"))
coverage = json.loads(Path("ml/artifacts/data_coverage_report.json").read_text(encoding="utf-8"))
patch = json.loads(Path("ml/artifacts/patch_freshness_report.json").read_text(encoding="utf-8"))

print(f"Readiness: {readiness.get('status')} (ready={readiness.get('ready')})")
print(f"Active model: {readiness.get('active_model_version') or 'formula/Elo fallback'}")
print(f"Scheduler age: {readiness.get('scheduler_age_minutes')} minutes")
print(f"Project audit: {audit.get('status')} ({len(audit.get('errors') or [])} errors)")
print(f"Match validation: {validation.get('status')} ({len(validation.get('errors') or [])} errors)")
print(f"Real strict Tier 1 matches: {coverage.get('real_tier1_historical_matches_count', 0)}")
print(f"Verified pro matches: {coverage.get('verified_pro_historical_matches_count', 0)}")
print(f"Roster coverage: {float(coverage.get('roster_coverage_ratio') or 0):.1%}")
print(f"Patch coverage: {float(coverage.get('patch_coverage_ratio') or 0):.1%}")
print(
    "Patch freshness: "
    f"{patch.get('status')} (config={patch.get('configured_current_patch')}, "
    f"database={patch.get('database_current_patch')}, source_family={patch.get('latest_source_patch_family')})"
)

errors = list(audit.get("errors") or []) + list(validation.get("errors") or [])
if patch.get("status") == "failed" or patch.get("stale") is True:
    errors.extend(patch.get("errors") or [])
    if patch.get("stale") is True:
        errors.append("Configured Dota patch family is stale.")
if not readiness.get("ready") or errors:
    print("SYSTEM CHECK FAILED")
    for error in errors:
        print(f"- {error}")
    raise SystemExit(1)

for warning in readiness.get("warnings") or []:
    print(f"Warning: {warning}")
for warning in validation.get("warnings") or []:
    print(f"Warning: {warning}")
for warning in patch.get("warnings") or []:
    print(f"Warning: {warning}")
print("SYSTEM CHECK PASSED")
PY
