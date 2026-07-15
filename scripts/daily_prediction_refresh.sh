#!/usr/bin/env bash
set -euo pipefail

TODAY_UTC="$(date -u +%F)"

echo "1/7 Sync upcoming schedule"
docker compose run --rm worker python -m worker.data_ingestion.sync_upcoming_matches \
  --source pandascore --from-date "$TODAY_UTC" --limit 100 --apply

echo "2/7 Sync upcoming rosters"
docker compose run --rm worker python -m worker.data_ingestion.sync_upcoming_rosters --apply

echo "3/7 Create immutable forecasts"
docker compose run --rm backend python -m app.prediction.forecast_tracker \
  --snapshot-upcoming --hours-ahead 168

echo "4/7 Refresh tracked results"
docker compose run --rm worker python -m worker.data_ingestion.sync_tracked_results --apply

echo "5/7 Settle completed forecasts"
docker compose run --rm backend python -m app.prediction.forecast_tracker --settle

echo "6/7 Settle local paper bets"
docker compose run --rm backend python -m app.betting.paper_bet_settlement

echo "7/7 Validate project"
docker compose run --rm worker python -m worker.data_ingestion.project_audit
