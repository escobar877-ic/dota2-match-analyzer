#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm worker python -m ml.training.draft_dataset_builder --summary
docker compose run --rm worker python -m ml.training.train_draft_model --output-json
docker compose run --rm worker python -m ml.evaluation.draft_backtest --output-json

echo "Draft experiment report: ml/artifacts/draft_backtest_report.json"
