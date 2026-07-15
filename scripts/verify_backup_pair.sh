#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_ROOT/backups}"

if [[ $# -gt 1 ]]; then
  echo "Usage: bash scripts/verify_backup_pair.sh [path/to/dota_analyzer_TIMESTAMP.dump]"
  exit 2
fi

if [[ $# -eq 1 ]]; then
  database_backup="$1"
else
  database_backup="$(ls -t "$BACKUP_DIR"/dota_analyzer_*.dump 2>/dev/null | head -1 || true)"
fi

if [[ -z "$database_backup" || ! -f "$database_backup" ]]; then
  echo "Backup verification failed: PostgreSQL dump not found."
  exit 1
fi

artifacts_backup="${database_backup%.dump}.artifacts.tar.gz"
if [[ ! -f "$artifacts_backup" ]]; then
  echo "Backup verification failed: paired ML artifacts archive not found: $artifacts_backup"
  exit 1
fi

cd "$PROJECT_ROOT"
docker compose up -d --wait postgres >/dev/null
docker compose exec -T postgres pg_restore --list <"$database_backup" >/dev/null

listing="$(tar -tzf "$artifacts_backup")"
grep -qx 'ml/artifacts/prematch_model.pkl' <<<"$listing" || {
  echo "Backup verification failed: active model artifact is missing."
  exit 1
}
grep -qx 'ml/artifacts/feature_schema.json' <<<"$listing" || {
  echo "Backup verification failed: feature schema is missing."
  exit 1
}

model_hash="$(tar -xOzf "$artifacts_backup" ml/artifacts/prematch_model.pkl | shasum -a 256 | awk '{print $1}')"
schema_hash="$(tar -xOzf "$artifacts_backup" ml/artifacts/feature_schema.json | shasum -a 256 | awk '{print $1}')"

echo "Backup pair is readable and structurally valid."
echo "Database: $database_backup"
echo "Artifacts: $artifacts_backup"
echo "Archived model SHA-256: $model_hash"
echo "Archived schema SHA-256: $schema_hash"
