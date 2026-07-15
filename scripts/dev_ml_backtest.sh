#!/usr/bin/env bash
set -euo pipefail

echo "==> Applying migrations"
docker compose run --rm backend alembic upgrade head

echo "==> Running local Tier 1 backtest"
docker compose run --rm worker python -m ml.evaluation.backtest

echo "==> Backtest complete. Dev seed metrics are synthetic and not real accuracy claims."
