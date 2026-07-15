#!/usr/bin/env bash
set -euo pipefail

CSV_PATH="${1:-imports/tier1_matches_template.csv}"
docker compose run --rm worker python -m worker.data_ingestion.import_quality_report "$CSV_PATH"
