#!/usr/bin/env bash
set -euo pipefail

CSV_PATH="${1:?Usage: bash scripts/validate_stratz_match_ids.sh imports/stratz_batches/file.csv}"

docker compose run --rm worker python -m worker.data_ingestion.stratz_match_id_validator "$CSV_PATH"
