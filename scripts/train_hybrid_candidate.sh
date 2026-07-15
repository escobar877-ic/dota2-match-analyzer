#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm backend python -m app.ratings.recalculate_elo
docker compose run --rm worker python -m ml.features.build_prematch_features
docker compose run --rm worker python -m ml.training.train_prematch_model \
  --training-profile tier1_plus_verified_pro

echo "Candidate created only. Review Tier 1 validation metrics before promotion."
