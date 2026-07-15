#!/usr/bin/env bash
set -euo pipefail

docker compose run --rm worker \
  python -m ml.evaluation.walk_forward \
  --training-profile tier1_plus_verified_pro \
  --feature-set differential \
  "$@"
