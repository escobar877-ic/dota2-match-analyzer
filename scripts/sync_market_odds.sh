#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm worker python -m worker.odds_ingestion.sync_market_odds "$@"
