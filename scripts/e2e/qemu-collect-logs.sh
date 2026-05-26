#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_PATH")/../.." && pwd)"
E2E_QEMU_HOST="${E2E_QEMU_HOST:-rpi4-codex}"
E2E_QEMU_USER="${E2E_QEMU_USER:-}"
E2E_QEMU_REMOTE_DIR="${E2E_QEMU_REMOTE_DIR:-/tmp/nervum-e2e-qemu}"
E2E_QEMU_API_PORT="${E2E_QEMU_API_PORT:-18080}"
E2E_QEMU_SSH_PORT="${E2E_QEMU_SSH_PORT:-10022}"
LOCAL_LOG_DIR="${ROOT_DIR}/.e2e/qemu-n0/logs"

ssh_target() {
    if [[ -n "$E2E_QEMU_USER" ]]; then
        printf "%s@%s" "$E2E_QEMU_USER" "$E2E_QEMU_HOST"
    else
        printf "%s" "$E2E_QEMU_HOST"
    fi
}

guest_ssh() {
    ssh \
        -p "$E2E_QEMU_SSH_PORT" \
        -o BatchMode=yes \
        -o ConnectTimeout=3 \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile="$E2E_QEMU_REMOTE_DIR/run/known_hosts" \
        root@127.0.0.1 "$@"
}

collect_remote() {
    local log_dir="$E2E_QEMU_REMOTE_DIR/logs"
    local run_dir="$E2E_QEMU_REMOTE_DIR/run"
    mkdir -p "$log_dir"

    if [[ -f "$run_dir/qemu.pid" ]]; then
        cp "$run_dir/qemu.pid" "$log_dir/qemu.pid" 2>/dev/null || true
    fi

    if guest_ssh "true" >/dev/null 2>&1; then
        guest_ssh "ip -o addr || true" >"$log_dir/guest-ip.txt" 2>&1 || true
        guest_ssh "journalctl --no-pager -u sdn-controller -u nervum -n 1000 2>/dev/null || true" >"$log_dir/journal.log" 2>&1 || true
        guest_ssh "cat /tmp/nervum-e2e-n0/nervum.log 2>/dev/null || true" >"$log_dir/nervum.log" 2>&1 || true
    else
        printf "guest ssh unavailable on forwarded port %s\n" "$E2E_QEMU_SSH_PORT" >"$log_dir/guest-ip.txt"
        : >"$log_dir/journal.log"
        [[ -f "$log_dir/nervum.log" ]] || : >"$log_dir/nervum.log"
    fi

    echo "Remote logs are in $log_dir"
}

if [[ "${1:-}" == "--remote" ]]; then
    collect_remote
    exit 0
fi

mkdir -p "$LOCAL_LOG_DIR"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$(ssh_target)" \
    "E2E_QEMU_REMOTE_DIR='$E2E_QEMU_REMOTE_DIR' E2E_QEMU_API_PORT='$E2E_QEMU_API_PORT' E2E_QEMU_SSH_PORT='$E2E_QEMU_SSH_PORT' bash -s -- --remote" \
    < "$ROOT_DIR/scripts/e2e/qemu-collect-logs.sh" || true

if command -v rsync >/dev/null 2>&1; then
    rsync -az "$(ssh_target):$E2E_QEMU_REMOTE_DIR/logs/" "$LOCAL_LOG_DIR/" || true
else
    scp -q "$(ssh_target):$E2E_QEMU_REMOTE_DIR/logs/*" "$LOCAL_LOG_DIR/" >/dev/null 2>&1 || true
fi

echo "Local E2E logs are in $LOCAL_LOG_DIR"
