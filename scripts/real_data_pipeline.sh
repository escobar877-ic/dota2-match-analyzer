#!/usr/bin/env bash
set -euo pipefail

APPLY=false
if [[ "${1:-}" == "--apply" ]]; then
  APPLY=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: bash scripts/real_data_pipeline.sh [--apply]" >&2
  exit 2
fi

echo "==> Applying migrations"
docker compose run --rm backend alembic upgrade head

echo "==> Checking configured data sources"
if [[ -x scripts/check_data_sources.sh ]]; then
  bash scripts/check_data_sources.sh
else
  echo "scripts/check_data_sources.sh not found; skipping local source check"
fi

echo "==> Running real data sync dry-run"
docker compose run --rm worker python -m worker.data_ingestion.sync_all --dry-run

echo "Review dry-run output before applying real sync."

if [[ "$APPLY" != "true" ]]; then
  echo "Dry-run complete. Re-run with --apply to write real sync results."
  exit 0
fi

echo "==> Applying real data sync"
docker compose run --rm worker python -m worker.data_ingestion.sync_all

echo "==> Reviewing Tier 1 cleanup markers"
docker compose run --rm backend python -m app.tier_filter.cleanup_service --dry-run

echo "==> Applying Tier 1 cleanup markers"
docker compose run --rm backend python -m app.tier_filter.cleanup_service --apply

echo "==> Recalculating Elo"
docker compose run --rm backend python -m app.ratings.recalculate_elo

echo "==> Building prematch features"
docker compose run --rm worker python -m ml.features.build_prematch_features

echo "==> Training local prematch ML model"
docker compose run --rm worker python -m ml.training.train_prematch_model

echo "==> Running local backtest"
docker compose run --rm worker python -m ml.evaluation.backtest

echo "Real data pipeline complete."
