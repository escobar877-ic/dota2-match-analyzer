#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm worker python -m worker.data_ingestion.data_coverage
docker compose run --rm worker python -m ml.features.build_prematch_features
docker compose run --rm worker python -m ml.training.train_prematch_model
docker compose run --rm worker python -m ml.evaluation.backtest
docker compose run --rm worker python -m ml.training.model_promotion --list

echo "Candidate retraining complete. No model was promoted automatically."
