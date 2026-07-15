#!/usr/bin/env bash
set -euo pipefail

LABEL="com.local.dota-analyzer.keepalive"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOG_DIR="$HOME/Library/Logs/DotaAnalyzer"
APP_SUPPORT_DIR="$HOME/Library/Application Support/DotaAnalyzer"
PLIST_PATH="$AGENTS_DIR/$LABEL.plist"
HELPER_PATH="$APP_SUPPORT_DIR/ensure-containers.sh"

mkdir -p "$AGENTS_DIR" "$LOG_DIR" "$APP_SUPPORT_DIR"

cat > "$HELPER_PATH" <<'EOF'
#!/usr/bin/env bash
set -u

export PATH="/usr/local/bin:/opt/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:/usr/bin:/bin:/usr/sbin:/sbin"

CONTAINERS=(
  dota-analyzer-postgres
  dota-analyzer-backend
  dota-analyzer-worker
  dota-analyzer-frontend
  dota-analyzer-forecast-scheduler
)
BACKUP_DIR="$HOME/Library/Application Support/DotaAnalyzer/backups"

create_backup_if_due() {
  mkdir -p "$BACKUP_DIR"
  while IFS= read -r recent_dump; do
    if [[ -f "${recent_dump%.dump}.artifacts.tar.gz" ]]; then
      return 0
    fi
  done < <(find "$BACKUP_DIR" -type f -name 'dota_analyzer_*.dump' -mtime -1 -print)

  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  destination="$BACKUP_DIR/dota_analyzer_$timestamp.dump"
  temporary="$destination.tmp"
  artifacts_destination="$BACKUP_DIR/dota_analyzer_$timestamp.artifacts.tar.gz"
  artifacts_temporary="$artifacts_destination.tmp"
  if ! docker exec dota-analyzer-postgres sh -lc \
    'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
    >"$temporary"; then
    rm -f "$temporary"
    echo "Automatic PostgreSQL backup failed."
    return 1
  fi
  if ! docker exec -i dota-analyzer-postgres pg_restore --list <"$temporary" >/dev/null; then
    rm -f "$temporary"
    echo "Automatic PostgreSQL backup validation failed."
    return 1
  fi
  if ! docker exec dota-analyzer-backend tar -C /app -czf - ml/artifacts >"$artifacts_temporary"; then
    rm -f "$temporary" "$artifacts_temporary"
    echo "Automatic ML artifacts backup failed."
    return 1
  fi
  if [[ ! -s "$artifacts_temporary" ]] \
    || ! /usr/bin/tar -tzf "$artifacts_temporary" | /usr/bin/grep -qx 'ml/artifacts/prematch_model.pkl' \
    || ! /usr/bin/tar -tzf "$artifacts_temporary" | /usr/bin/grep -qx 'ml/artifacts/feature_schema.json'; then
    rm -f "$temporary" "$artifacts_temporary"
    echo "Automatic ML artifacts backup validation failed."
    return 1
  fi
  mv "$artifacts_temporary" "$artifacts_destination"
  mv "$temporary" "$destination"
  find "$BACKUP_DIR" -type f -name 'dota_analyzer_*.dump' -mtime +14 -delete
  find "$BACKUP_DIR" -type f -name 'dota_analyzer_*.artifacts.tar.gz' -mtime +14 -delete
  echo "Validated PostgreSQL backup: $destination"
  echo "Validated ML artifacts backup: $artifacts_destination"
}

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker CLI is not installed."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  /usr/bin/open -gja Docker
  for _ in {1..60}; do
    docker info >/dev/null 2>&1 && break
    sleep 2
  done
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon did not become ready within 120 seconds."
  exit 1
fi

missing=0
for container in "${CONTAINERS[@]}"; do
  if ! docker container inspect "$container" >/dev/null 2>&1; then
    echo "Required container is missing: $container"
    missing=1
    continue
  fi
  docker start "$container" >/dev/null
done

if (( missing )); then
  echo "Run scripts/ensure_local_services.sh once from the project to recreate missing containers."
  exit 1
fi

for _ in {1..30}; do
  if /usr/bin/curl -fsS http://localhost:8000/health/ready >/dev/null 2>&1; then
    create_backup_if_due || exit 1
    echo "Dota 2 Match Analyzer containers and readiness endpoint are healthy."
    exit 0
  fi
  sleep 2
done

echo "Containers started, but backend readiness did not become healthy within 60 seconds."
exit 1
EOF
chmod 700 "$HELPER_PATH"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$HELPER_PATH</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>ThrottleInterval</key>
  <integer>60</integer>
  <key>StandardOutPath</key>
  <string>$LOG_DIR/keepalive.log</string>
  <key>StandardErrorPath</key>
  <string>$LOG_DIR/keepalive-error.log</string>
</dict>
</plist>
EOF

launchctl bootout "gui/$UID" "$PLIST_PATH" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$UID" "$PLIST_PATH"
launchctl start "$LABEL"

echo "Installed $LABEL"
echo "Helper: $HELPER_PATH"
echo "Logs: $LOG_DIR"
