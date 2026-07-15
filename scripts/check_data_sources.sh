#!/usr/bin/env bash
set -euo pipefail

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

mask_value() {
  local value="${1:-}"
  if [[ -z "$value" ]]; then
    echo "(missing)"
    return
  fi
  if (( ${#value} <= 6 )); then
    echo "***"
    return
  fi
  echo "${value:0:3}***${value: -3}"
}

print_source() {
  local name="$1"
  local env_name="$2"
  local requires_key="$3"
  local value="${!env_name:-}"
  local masked
  masked="$(mask_value "$value")"

  if [[ -n "$value" || "$requires_key" == "false" ]]; then
    echo "$name: enabled"
  else
    echo "$name: disabled"
  fi
  echo "  $env_name=$masked"
  if [[ -z "$value" && "$requires_key" == "true" ]]; then
    echo "  hint: set $env_name in .env to enable $name sync"
  elif [[ -z "$value" ]]; then
    echo "  hint: $name may work partly without a key; a key can improve limits"
  fi
}

echo "Data source environment status"
print_source "OpenDota" "OPENDOTA_API_KEY" "false"
print_source "STRATZ" "STRATZ_API_KEY" "true"
print_source "PandaScore" "PANDASCORE_API_KEY" "true"
echo "Missing API keys do not break local mode."
