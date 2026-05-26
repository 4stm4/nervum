#!/usr/bin/env bash
set -Eeuo pipefail

BASE_URL="${1:-${E2E_QEMU_API_URL:-http://127.0.0.1:${E2E_QEMU_API_PORT:-18080}}}"
TIMEOUT="${E2E_QEMU_WAIT_TIMEOUT:-300}"
INTERVAL="${E2E_QEMU_WAIT_INTERVAL:-2}"
HEALTH_PATHS="${E2E_QEMU_HEALTH_PATHS:-/health /healthz /readyz /api/v1/healthz /api/v1/readyz /api/v1/health /api/v1/livez /api/v1/version}"

try_get() {
    local url="$1"
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 3 "$url" >/dev/null
        return $?
    fi
    python3 - "$url" <<'PY'
from __future__ import annotations

import sys
import urllib.request

url = sys.argv[1]
try:
    with urllib.request.urlopen(url, timeout=3) as response:
        raise SystemExit(0 if 200 <= response.status < 500 else 1)
except Exception:
    raise SystemExit(1)
PY
}

deadline=$((SECONDS + TIMEOUT))
while (( SECONDS < deadline )); do
    for path in $HEALTH_PATHS; do
        url="${BASE_URL%/}${path}"
        if try_get "$url"; then
            echo "Nervum API is ready at $url"
            exit 0
        fi
    done
    sleep "$INTERVAL"
done

echo "Timed out waiting ${TIMEOUT}s for Nervum API at ${BASE_URL}" >&2
echo "Tried health endpoints: ${HEALTH_PATHS}" >&2
exit 1
