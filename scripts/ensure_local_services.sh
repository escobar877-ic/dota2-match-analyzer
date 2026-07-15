#!/usr/bin/env bash
set -euo pipefail

export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRED_SERVICES=(postgres backend worker frontend forecast-scheduler)

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is not installed."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "Docker daemon is unavailable."
    exit 1
  fi
  open -gja Docker
  for _ in {1..60}; do
    if docker info >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon did not become ready within 120 seconds."
  exit 1
fi

cd "$PROJECT_ROOT"
docker compose up -d --wait --wait-timeout 180 "${REQUIRED_SERVICES[@]}"

running="$(docker compose ps --status running --services)"
for service in "${REQUIRED_SERVICES[@]}"; do
  if ! printf '%s\n' "$running" | grep -Fxq "$service"; then
    echo "Required service is not running: $service"
    exit 1
  fi
done

echo "Dota 2 Match Analyzer services are running."
