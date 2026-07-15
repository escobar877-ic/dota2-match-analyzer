#!/usr/bin/env bash
set -euo pipefail

CSV_PATH="${1:?Usage: bash scripts/real_batch_pipeline.sh imports/real_batches/file.csv [--apply]}"
MODE="${2:-}"

if [[ "$CSV_PATH" == *dev_seed* ]]; then
  echo "Refusing real batch path containing dev_seed."
  exit 1
fi

docker compose run --rm worker python -m worker.data_ingestion.real_batch_validator "$CSV_PATH"
docker compose run --rm worker python -m worker.data_ingestion.import_quality_report "$CSV_PATH"
docker compose run --rm worker python -m worker.data_ingestion.csv_import "$CSV_PATH" --dry-run
docker compose run --rm worker python -m worker.data_ingestion.data_coverage
docker compose run --rm worker python -m worker.data_ingestion.project_audit
docker compose run --rm worker python -m worker.data_ingestion.match_validation

if [[ "$MODE" != "--apply" ]]; then
  echo "Dry-run complete. Review reports before applying."
  exit 0
fi

STATUS="$(python3 - <<'PY'
import json
from pathlib import Path
path = Path("ml/artifacts/real_batch_validation_report.json")
print(json.loads(path.read_text()).get("status", "missing") if path.exists() else "missing")
PY
)"
if [[ "$STATUS" == "failed" || "$STATUS" == "missing" ]]; then
  echo "Validation status is $STATUS; refusing apply."
  exit 1
fi

docker compose run --rm worker python -m worker.data_ingestion.csv_import "$CSV_PATH" --apply
docker compose run --rm worker python -m worker.data_ingestion.data_coverage
docker compose run --rm worker python -m worker.data_ingestion.project_audit
docker compose run --rm worker python -m worker.data_ingestion.match_validation
docker compose run --rm backend python -m app.ratings.recalculate_elo
docker compose run --rm worker python -m ml.features.build_prematch_features
docker compose run --rm worker python -m ml.training.train_prematch_model
docker compose run --rm worker python -m ml.evaluation.backtest
docker compose run --rm worker python -m worker.data_ingestion.real_batch_report
