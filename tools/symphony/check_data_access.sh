#!/usr/bin/env sh
set -eu

cache_path="${HIVEFI_DATA_ACCESS_CACHE:-/tmp/hivefi-data-access-ok}"
output_path="${HIVEFI_DATA_ACCESS_OUTPUT:-/tmp/hivefi-data-fetch-smoke.csv}"
ttl_seconds="${HIVEFI_DATA_ACCESS_TTL_SECONDS:-300}"

: "${HIVEFI_API_KEY:?HIVEFI_API_KEY must be exported before starting Symphony}"
: "${CLICKHOUSE_USER:?CLICKHOUSE_USER must be exported before starting Symphony}"
: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD must be exported before starting Symphony}"

cache_mtime() {
  stat -f %m "$cache_path" 2>/dev/null || stat -c %Y "$cache_path" 2>/dev/null || echo 0
}

if [ -f "$cache_path" ] && [ "$ttl_seconds" -gt 0 ]; then
  now="$(date +%s)"
  modified="$(cache_mtime)"
  age=$((now - modified))
  if [ "$age" -ge 0 ] && [ "$age" -lt "$ttl_seconds" ]; then
    exit 0
  fi
fi

hivefi-factory health >/dev/null
hivefi-factory data fetch hyperliquid_kline_1d \
  --symbols BTC \
  --start 2024-01-01 \
  --end 2024-01-03 \
  --value-col close \
  --output "$output_path" >/dev/null
touch "$cache_path"
