#!/usr/bin/env bash
set -euo pipefail

echo "==> Applying migrations"
docker compose run --rm backend alembic upgrade head

echo "==> Syncing local patch config"
docker compose run --rm backend python -m app.patches.patch_service --sync-config

echo "==> Seeding synthetic Tier 1 dev data"
docker compose run --rm backend python -m app.dev_seed.seed_tier1_training_data

echo "==> Applying Tier 1 cleanup markers"
docker compose run --rm backend python -m app.tier_filter.cleanup_service --apply

echo "==> Recalculating Elo"
docker compose run --rm backend python -m app.ratings.recalculate_elo

echo "==> Building prematch features"
docker compose run --rm worker python -m ml.features.build_prematch_features

echo "==> Training local prematch ML model"
docker compose run --rm worker python -m ml.training.train_prematch_model

echo "==> Dev ML cycle complete. Data is synthetic and must not be used for real accuracy claims."
