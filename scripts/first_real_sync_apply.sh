#!/usr/bin/env bash
set -euo pipefail

APPLY=false
ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=true
      shift
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

echo "Checking source health..."
docker compose run --rm worker python -m worker.data_ingestion.source_health

echo "Planning historical fetch..."
docker compose run --rm worker python -m worker.data_ingestion.historical_fetch_planner

echo "Running historical sync dry-run..."
docker compose run --rm worker python -m worker.data_ingestion.sync_historical_matches "${ARGS[@]}"

echo "Reviewing sync output..."
docker compose run --rm worker python -m worker.data_ingestion.sync_review ml/artifacts/historical_sync_report.json

VALID_ROWS="$(
python3 - <<'PY'
import json
from pathlib import Path
path = Path("ml/artifacts/historical_sync_report.json")
if not path.exists():
    print(0)
else:
    report = json.loads(path.read_text(encoding="utf-8"))
    print(int(report.get("would_create") or 0) + int(report.get("would_update") or 0))
PY
)"

if [[ "${VALID_ROWS}" -le 0 ]]; then
  echo "No valid Tier 1 rows found. Add verified source mappings/aliases before apply."
  exit 0
fi

if [[ "${APPLY}" != "true" ]]; then
  echo "Valid rows found: ${VALID_ROWS}. Review reports, then rerun with --apply to write them."
  exit 0
fi

echo "Applying ${VALID_ROWS} validated Tier 1 rows..."
docker compose run --rm worker python -m worker.data_ingestion.sync_historical_matches "${ARGS[@]}" --apply
