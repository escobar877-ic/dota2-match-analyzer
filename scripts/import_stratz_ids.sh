#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/import_stratz_ids.sh path/to/match_ids.txt [--apply]"
  exit 1
fi

file="$1"
shift

docker compose run --rm worker \
  python -m worker.data_ingestion.import_stratz_ids "$file" "$@"
