#!/usr/bin/env bash
set -euo pipefail

CSV_PATH="${1:?Usage: bash scripts/validate_real_batch.sh imports/real_batches/file.csv}"

docker compose run --rm worker python -m worker.data_ingestion.real_batch_validator "$CSV_PATH"
docker compose run --rm worker python -m worker.data_ingestion.import_quality_report "$CSV_PATH"
