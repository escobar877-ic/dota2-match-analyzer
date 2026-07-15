#!/usr/bin/env bash
set -euo pipefail

CSV_PATH="${1:?Usage: bash scripts/import_stratz_match_ids.sh imports/stratz_batches/file.csv [--apply]}"
shift || true

docker compose run --rm worker python -m worker.data_ingestion.stratz_match_id_import --file "$CSV_PATH" "$@"
