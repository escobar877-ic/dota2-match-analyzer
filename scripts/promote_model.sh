#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 ]]; then
  echo "Usage: bash scripts/promote_model.sh MODEL_VERSION_ID \"reason\""
  exit 1
fi

MODEL_VERSION_ID="$1"
REASON="$2"

docker compose run --rm worker python -m ml.training.model_promotion --promote "$MODEL_VERSION_ID" --reason "$REASON"
