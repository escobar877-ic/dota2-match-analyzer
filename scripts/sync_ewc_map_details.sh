#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm worker python -m worker.data_ingestion.sync_ewc_map_details "$@"
