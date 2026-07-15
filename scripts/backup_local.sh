#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output="${1:-$BACKUP_DIR/dota_analyzer_$timestamp.dump}"
temporary="$output.tmp"
artifacts_output="${output%.dump}.artifacts.tar.gz"
artifacts_temporary="$artifacts_output.tmp"

mkdir -p "$(dirname "$output")"
cd "$PROJECT_ROOT"

docker compose up -d --wait postgres >/dev/null
trap 'rm -f "$temporary" "$artifacts_temporary"' EXIT
docker compose exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  >"$temporary"

if [[ ! -s "$temporary" ]]; then
  echo "Backup failed: output is empty."
  exit 1
fi

docker compose exec -T postgres pg_restore --list <"$temporary" >/dev/null
docker compose exec -T backend tar -C /app -czf - ml/artifacts >"$artifacts_temporary"
if [[ ! -s "$artifacts_temporary" ]]; then
  echo "Backup failed: ML artifacts archive is empty."
  exit 1
fi
artifact_listing="$(tar -tzf "$artifacts_temporary")"
grep -qx 'ml/artifacts/prematch_model.pkl' <<<"$artifact_listing" || {
  echo "Backup failed: active model artifact is missing from archive."
  exit 1
}
grep -qx 'ml/artifacts/feature_schema.json' <<<"$artifact_listing" || {
  echo "Backup failed: feature schema is missing from archive."
  exit 1
}
mv "$artifacts_temporary" "$artifacts_output"
mv "$temporary" "$output"
trap - EXIT

echo "Database backup: $output ($(du -h "$output" | awk '{print $1}'))"
echo "ML artifacts backup: $artifacts_output ($(du -h "$artifacts_output" | awk '{print $1}'))"
