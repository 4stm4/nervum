#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
ROOT_DIR="$(cd "$(dirname "$SCRIPT_PATH")/../.." && pwd)"
E2E_QEMU_HOST="${E2E_QEMU_HOST:-rpi4-codex}"
E2E_QEMU_USER="${E2E_QEMU_USER:-}"
E2E_QEMU_REMOTE_DIR="${E2E_QEMU_REMOTE_DIR:-/tmp/nervum-e2e-qemu}"
E2E_QEMU_API_PORT="${E2E_QEMU_API_PORT:-18080}"
E2E_QEMU_SSH_PORT="${E2E_QEMU_SSH_PORT:-10022}"
LOCAL_STATE_DIR="${ROOT_DIR}/.e2e/qemu-n0"
LOCAL_LOG_DIR="${LOCAL_STATE_DIR}/logs"
TUNNEL_PID_FILE="${LOCAL_STATE_DIR}/qemu-tunnel.pid"

ssh_target() {
    if [[ -n "$E2E_QEMU_USER" ]]; then
        printf "%s@%s" "$E2E_QEMU_USER" "$E2E_QEMU_HOST"
    else
        printf "%s" "$E2E_QEMU_HOST"
    fi
}

cleanup_local_tunnel() {
    if [[ -f "$TUNNEL_PID_FILE" ]]; then
        local pid command
        pid="$(cat "$TUNNEL_PID_FILE" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
            if [[ "$command" == *"127.0.0.1:${E2E_QEMU_API_PORT}:127.0.0.1:${E2E_QEMU_API_PORT}"* ]]; then
                kill "$pid" 2>/dev/null || true
                sleep 1
                kill -9 "$pid" 2>/dev/null || true
            fi
        fi
        rm -f "$TUNNEL_PID_FILE"
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

kill_process() {
    local signal="${1:-TERM}"
    local pid="${2:-}"
    [[ -n "$pid" ]] || return 0
    kill "-$signal" "$pid" 2>/dev/null || sudo -n kill "-$signal" "$pid" 2>/dev/null || true
}

is_qemu_process() {
    local pid="$1"
    local exe command
    exe="$(readlink -f "/proc/$pid/exe" 2>/dev/null || true)"
    command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ "$exe" == */qemu-system-aarch64 ]] || {
        [[ "$command" == qemu-system-aarch64* || "$command" == "sudo -n qemu-system-aarch64"* ]]
    }
}

cleanup_remote() {
    local run_dir="$E2E_QEMU_REMOTE_DIR/run"
    local pid_file="$run_dir/qemu.pid"

    if guest_ssh "test -f /tmp/nervum-e2e-n0/nervum.pid" >/dev/null 2>&1; then
        guest_ssh "kill \$(cat /tmp/nervum-e2e-n0/nervum.pid) 2>/dev/null || true; rm -f /tmp/nervum-e2e-n0/nervum.pid" >/dev/null 2>&1 || true
    fi

    if [[ -f "$pid_file" ]]; then
        local pid command
        pid="$(cat "$pid_file" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
            command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
            if is_qemu_process "$pid" && [[ "$command" == *"$E2E_QEMU_REMOTE_DIR"* ]]; then
                kill_process TERM "$pid"
                for _ in 1 2 3 4 5; do
                    kill -0 "$pid" 2>/dev/null || break
                    sleep 1
                done
                kill_process KILL "$pid"
            fi
        fi
    fi

    if command -v pgrep >/dev/null 2>&1; then
        while IFS= read -r line; do
            [[ -z "$line" ]] && continue
            local pid="${line%% *}"
            local command="${line#* }"
            if is_qemu_process "$pid" && [[ "$command" == *"$E2E_QEMU_REMOTE_DIR"* ]]; then
                kill_process TERM "$pid"
            fi
        done < <(pgrep -af qemu-system-aarch64 || true)
    fi

    rm -rf "$run_dir"
    mkdir -p "$E2E_QEMU_REMOTE_DIR/logs"
    rm -f "$E2E_QEMU_REMOTE_DIR/logs/qemu.pid"
    echo "Remote QEMU cleanup complete in $E2E_QEMU_REMOTE_DIR"
}

if [[ "${1:-}" == "--remote" ]]; then
    cleanup_remote
    exit 0
fi

mkdir -p "$LOCAL_LOG_DIR"
cleanup_local_tunnel

ssh -o BatchMode=yes -o ConnectTimeout=10 "$(ssh_target)" \
    "E2E_QEMU_REMOTE_DIR='$E2E_QEMU_REMOTE_DIR' E2E_QEMU_API_PORT='$E2E_QEMU_API_PORT' E2E_QEMU_SSH_PORT='$E2E_QEMU_SSH_PORT' bash -s -- --remote" \
    < "$ROOT_DIR/scripts/e2e/qemu-cleanup.sh"
