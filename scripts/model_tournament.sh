#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm worker python -m ml.evaluation.model_tournament "$@"
