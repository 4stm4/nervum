#!/usr/bin/env bash
# qemu-dp.sh — E2E датаплейн: загружает QEMU, запускает NetOS Agent с реальными
# backend'ами (OVS subprocess + nftables), прокидывает туннели и запускает DP тесты.
#
# Переменные окружения (с умолчаниями):
#   E2E_QEMU_HOST              — SSH-хост с QEMU (по умолч. rpi4-codex)
#   E2E_QEMU_API_PORT          — порт SDN-контроллера на localhost (18080)
#   E2E_QEMU_SSH_PORT          — SSH-порт QEMU-госта (10022)
#   E2E_QEMU_IMAGE             — путь к образу
#   E2E_QEMU_ACCEL             — ускоритель QEMU (auto)
#   E2E_DP_AGENT_PORT          — порт туннеля агента на localhost (19100)
#   E2E_QEMU_GUEST_AGENT_PORT  — порт агента внутри госта (9100)
#   SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E_QEMU_HOST="${E2E_QEMU_HOST:-rpi4-codex}"
E2E_QEMU_USER="${E2E_QEMU_USER:-}"
E2E_QEMU_REMOTE_DIR="${E2E_QEMU_REMOTE_DIR:-/tmp/nervum-e2e-qemu}"
E2E_QEMU_API_PORT="${E2E_QEMU_API_PORT:-18080}"
E2E_QEMU_SSH_PORT="${E2E_QEMU_SSH_PORT:-10022}"
E2E_QEMU_IMAGE="${E2E_QEMU_IMAGE:-/mnt/build-ssd/litainer-build/litainer/qemu-virt.img}"
E2E_QEMU_ACCEL="${E2E_QEMU_ACCEL:-auto}"
E2E_QEMU_GUEST_PERSISTENCE="${E2E_QEMU_GUEST_PERSISTENCE:-memory}"
E2E_QEMU_GUEST_DATABASE_URL="${E2E_QEMU_GUEST_DATABASE_URL:-sqlite+aiosqlite:////tmp/nervum-e2e-n0/sdn_controller.db}"
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN="${SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN:-e2e-admin-token}"
# Порт агента (локальный туннель → гость)
E2E_DP_AGENT_PORT="${E2E_DP_AGENT_PORT:-19100}"
E2E_QEMU_GUEST_AGENT_PORT="${E2E_QEMU_GUEST_AGENT_PORT:-9100}"

LOCAL_STATE_DIR="$ROOT_DIR/.e2e/qemu-dp"
LOCAL_LOG_DIR="$LOCAL_STATE_DIR/logs"
TUNNEL_API_PID_FILE="$LOCAL_STATE_DIR/qemu-tunnel-api.pid"
TUNNEL_AGENT_PID_FILE="$LOCAL_STATE_DIR/qemu-tunnel-agent.pid"

REMOTE_REPO="$E2E_QEMU_REMOTE_DIR/repo"
REMOTE_ARTIFACTS="$E2E_QEMU_REMOTE_DIR/artifacts"

ssh_target() {
    if [[ -n "$E2E_QEMU_USER" ]]; then
        printf "%s@%s" "$E2E_QEMU_USER" "$E2E_QEMU_HOST"
    else
        printf "%s" "$E2E_QEMU_HOST"
    fi
}

SSH_TARGET="$(ssh_target)"
PYTEST_BIN="${E2E_QEMU_PYTEST:-$ROOT_DIR/.venv/bin/pytest}"

if [[ "$E2E_QEMU_IMAGE" == */qemu-virt.img ]]; then
    E2E_QEMU_KERNEL="${E2E_QEMU_KERNEL:-/mnt/build-ssd/litainer-build/litainer/temp/mainline_linux/arch/arm64/boot/Image}"
    E2E_QEMU_MEM="${E2E_QEMU_MEM:-1024}"
    E2E_QEMU_GUEST_API_PORT="${E2E_QEMU_GUEST_API_PORT:-8090}"
    E2E_QEMU_NET_DEVICE="${E2E_QEMU_NET_DEVICE:-virtio-net-pci}"
    E2E_QEMU_DRIVE_IF="${E2E_QEMU_DRIVE_IF:-virtio}"
    E2E_QEMU_HOSTFWD_ADDR="${E2E_QEMU_HOSTFWD_ADDR:-127.0.0.1}"
    E2E_QEMU_APPEND="${E2E_QEMU_APPEND:-console=ttyAMA0 root=/dev/vda2 rootfstype=ext4 rw rootwait}"
fi

mkdir -p "$LOCAL_LOG_DIR"

cleanup_tunnels() {
    for pid_file in "$TUNNEL_API_PID_FILE" "$TUNNEL_AGENT_PID_FILE"; do
        if [[ -f "$pid_file" ]]; then
            local pid
            pid="$(cat "$pid_file" 2>/dev/null || true)"
            if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                sleep 1
                kill -9 "$pid" 2>/dev/null || true
            fi
            rm -f "$pid_file"
        fi
    done
}

collect_logs() {
    bash "$ROOT_DIR/scripts/e2e/qemu-collect-logs.sh" || true
}

cleanup_all() {
    local status=$?
    set +e
    collect_logs
    if [[ "${E2E_QEMU_KEEP_VM:-0}" != "1" ]]; then
        bash "$ROOT_DIR/scripts/e2e/qemu-cleanup.sh" || true
    else
        echo "Keeping QEMU VM (E2E_QEMU_KEEP_VM=1)"
    fi
    echo "DP E2E logs: $LOCAL_LOG_DIR"
    exit "$status"
}

trap cleanup_all EXIT

echo "Checking SSH connectivity to $SSH_TARGET"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" "true"

cleanup_tunnels
ssh "$SSH_TARGET" "mkdir -p '$REMOTE_REPO' '$REMOTE_ARTIFACTS' '$E2E_QEMU_REMOTE_DIR/logs'"

echo "Syncing repository to $SSH_TARGET:$REMOTE_REPO"
if command -v rsync >/dev/null 2>&1; then
    rsync -az --delete \
        --exclude '.git/' \
        --exclude '.venv/' \
        --exclude '.e2e/' \
        --exclude '__pycache__/' \
        --exclude '.pytest_cache/' \
        --exclude '.mypy_cache/' \
        --exclude '.ruff_cache/' \
        --exclude '.e2e_guest_py/' \
        "$ROOT_DIR/" "$SSH_TARGET:$REMOTE_REPO/"
else
    (
        cd "$ROOT_DIR"
        tar \
            --exclude './.git' \
            --exclude './.venv' \
            --exclude './.e2e' \
            --exclude './.e2e_guest_py' \
            --exclude './__pycache__' \
            -czf - .
    ) | ssh "$SSH_TARGET" "rm -rf '$REMOTE_REPO' && mkdir -p '$REMOTE_REPO' && tar -xzf - -C '$REMOTE_REPO'"
fi

REMOTE_IMAGE="$E2E_QEMU_IMAGE"
if [[ -f "$E2E_QEMU_IMAGE" ]]; then
    echo "Copying local QEMU image $(basename "$E2E_QEMU_IMAGE")"
    scp -q "$E2E_QEMU_IMAGE" "$SSH_TARGET:$REMOTE_ARTIFACTS/$(basename "$E2E_QEMU_IMAGE")"
    REMOTE_IMAGE="$REMOTE_ARTIFACTS/$(basename "$E2E_QEMU_IMAGE")"
fi

REMOTE_KERNEL="${E2E_QEMU_KERNEL:-}"
if [[ -n "$REMOTE_KERNEL" && -f "$REMOTE_KERNEL" ]]; then
    scp -q "$REMOTE_KERNEL" "$SSH_TARGET:$REMOTE_ARTIFACTS/$(basename "$REMOTE_KERNEL")"
    REMOTE_KERNEL="$REMOTE_ARTIFACTS/$(basename "$REMOTE_KERNEL")"
fi

if [[ "${E2E_QEMU_PREPARE_GUEST_PY_DEPS:-1}" == "1" ]]; then
    echo "Preparing pure-Python guest dependency overlay"
    ssh "$SSH_TARGET" "cd '$REMOTE_REPO' && rm -rf .e2e_guest_py && python3 -m pip install --quiet --target .e2e_guest_py --no-deps pydantic-settings structlog prometheus-client opentelemetry-api python-dotenv" \
        || echo "WARNING: failed to prepare .e2e_guest_py"
fi

REMOTE_PAYLOAD_IMAGE="${E2E_QEMU_PAYLOAD_IMAGE:-}"
if [[ "${E2E_QEMU_PREPARE_GUEST_PAYLOAD:-1}" == "1" ]]; then
    REMOTE_PAYLOAD_IMAGE="${REMOTE_PAYLOAD_IMAGE:-$E2E_QEMU_REMOTE_DIR/payload.ext4}"
    echo "Preparing guest payload disk $REMOTE_PAYLOAD_IMAGE"
    ssh "$SSH_TARGET" "REMOTE_REPO='$REMOTE_REPO' E2E_QEMU_PAYLOAD_IMAGE='$REMOTE_PAYLOAD_IMAGE' E2E_QEMU_PAYLOAD_SIZE='${E2E_QEMU_PAYLOAD_SIZE:-128M}' bash -s" <<'REMOTE_PAYLOAD'
set -Eeuo pipefail
command -v mkfs.ext4 >/dev/null 2>&1 || { echo "mkfs.ext4 required" >&2; exit 1; }
rm -f "$E2E_QEMU_PAYLOAD_IMAGE"
truncate -s "$E2E_QEMU_PAYLOAD_SIZE" "$E2E_QEMU_PAYLOAD_IMAGE"
mkfs.ext4 -q -F "$E2E_QEMU_PAYLOAD_IMAGE"
payload_mount="$(mktemp -d)"
cleanup_payload_mount() {
    sudo -n umount "$payload_mount" >/dev/null 2>&1 || true
    rmdir "$payload_mount" >/dev/null 2>&1 || true
}
trap cleanup_payload_mount EXIT
sudo -n mount -o loop "$E2E_QEMU_PAYLOAD_IMAGE" "$payload_mount"
sudo -n cp -a "$REMOTE_REPO/src" "$payload_mount/src"
if [[ -f "$REMOTE_REPO/alembic.ini" ]]; then
    sudo -n cp -a "$REMOTE_REPO/alembic.ini" "$payload_mount/alembic.ini"
fi
if [[ -d "$REMOTE_REPO/.e2e_guest_py" ]]; then
    sudo -n cp -a "$REMOTE_REPO/.e2e_guest_py" "$payload_mount/.e2e_guest_py"
fi
sudo -n sync
sudo -n umount "$payload_mount"
sudo -n chown "$(id -u):$(id -g)" "$E2E_QEMU_PAYLOAD_IMAGE"
REMOTE_PAYLOAD
fi

echo "Starting remote QEMU"
ssh "$SSH_TARGET" "cd '$REMOTE_REPO' && env \
E2E_QEMU_REMOTE_DIR='$E2E_QEMU_REMOTE_DIR' \
E2E_QEMU_IMAGE='$REMOTE_IMAGE' \
E2E_QEMU_KERNEL='$REMOTE_KERNEL' \
E2E_QEMU_ROOTFS='${E2E_QEMU_ROOTFS:-}' \
E2E_QEMU_API_PORT='$E2E_QEMU_API_PORT' \
E2E_QEMU_SSH_PORT='$E2E_QEMU_SSH_PORT' \
E2E_QEMU_GUEST_API_PORT='${E2E_QEMU_GUEST_API_PORT:-8080}' \
E2E_QEMU_GUEST_API_ADDR='${E2E_QEMU_GUEST_API_ADDR:-}' \
E2E_QEMU_MEM='${E2E_QEMU_MEM:-2048}' \
E2E_QEMU_SMP='${E2E_QEMU_SMP:-4}' \
E2E_QEMU_MACHINE='${E2E_QEMU_MACHINE:-virt}' \
E2E_QEMU_CPU='${E2E_QEMU_CPU:-}' \
E2E_QEMU_ACCEL='$E2E_QEMU_ACCEL' \
E2E_QEMU_NET_DEVICE='${E2E_QEMU_NET_DEVICE:-virtio-net-device}' \
E2E_QEMU_DRIVE_IF='${E2E_QEMU_DRIVE_IF:-none}' \
E2E_QEMU_BLOCK_DEVICE='${E2E_QEMU_BLOCK_DEVICE:-virtio-blk-device}' \
E2E_QEMU_HOSTFWD_ADDR='${E2E_QEMU_HOSTFWD_ADDR:-127.0.0.1}' \
E2E_QEMU_USE_SUDO='${E2E_QEMU_USE_SUDO:-0}' \
E2E_QEMU_APPEND='${E2E_QEMU_APPEND:-}' \
E2E_QEMU_GUEST_PYTHON_FLAGS='${E2E_QEMU_GUEST_PYTHON_FLAGS:-}' \
E2E_QEMU_GUEST_PERSISTENCE='$E2E_QEMU_GUEST_PERSISTENCE' \
E2E_QEMU_GUEST_DATABASE_URL='$E2E_QEMU_GUEST_DATABASE_URL' \
E2E_QEMU_PAYLOAD_IMAGE='$REMOTE_PAYLOAD_IMAGE' \
E2E_QEMU_BOOT_TIMEOUT='${E2E_QEMU_BOOT_TIMEOUT:-240}' \
E2E_QEMU_API_TIMEOUT='${E2E_QEMU_API_TIMEOUT:-300}' \
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN='$SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN' \
bash scripts/e2e/qemu-n0-remote.sh"

# Туннель SDN-контроллера
echo "Opening SDN controller tunnel localhost:$E2E_QEMU_API_PORT -> $SSH_TARGET:localhost:$E2E_QEMU_API_PORT"
ssh \
    -N \
    -L "127.0.0.1:${E2E_QEMU_API_PORT}:127.0.0.1:${E2E_QEMU_API_PORT}" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=2 \
    "$SSH_TARGET" >"$LOCAL_LOG_DIR/ssh-tunnel-api.log" 2>&1 &
echo $! >"$TUNNEL_API_PID_FILE"
sleep 2
if ! kill -0 "$(cat "$TUNNEL_API_PID_FILE")" 2>/dev/null; then
    echo "Failed to establish SDN controller SSH tunnel" >&2
    exit 1
fi

E2E_QEMU_WAIT_TIMEOUT=60 "$ROOT_DIR/scripts/e2e/qemu-wait-for-api.sh" "http://127.0.0.1:$E2E_QEMU_API_PORT"

# Запускаем NetOS Agent в госте с реальными backend'ами
GUEST_WORK_DIR="${E2E_QEMU_GUEST_WORK_DIR:-/tmp/nervum-src}"
GUEST_AGENT_PY_PATH="$GUEST_WORK_DIR/src:$GUEST_WORK_DIR/.e2e_guest_py"
echo "Starting NetOS Agent in QEMU guest (port $E2E_QEMU_GUEST_AGENT_PORT)"
ssh \
    -p "$E2E_QEMU_SSH_PORT" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o ConnectTimeout=10 \
    "root@127.0.0.1" \
    "
set -eu
kill \$(cat /tmp/netos-agent.pid 2>/dev/null) 2>/dev/null || true
rm -f /tmp/netos-agent.pid
PYTHONPATH='${GUEST_AGENT_PY_PATH}' \
NETOS_AGENT_OVS_BACKEND=subprocess \
NETOS_AGENT_FIREWALL_BACKEND=nftables \
NETOS_AGENT_HTTP_HOST=0.0.0.0 \
NETOS_AGENT_HTTP_PORT='$E2E_QEMU_GUEST_AGENT_PORT' \
PYTHONUTF8=1 \
nohup python3 -m uvicorn netos_agent.app.main:app \
    --host 0.0.0.0 --port '$E2E_QEMU_GUEST_AGENT_PORT' \
    >/tmp/netos-agent.log 2>&1 &
echo \$! >/tmp/netos-agent.pid
sleep 2
kill -0 \$(cat /tmp/netos-agent.pid) && echo 'NetOS Agent started OK'
" || { echo "WARNING: could not start NetOS Agent; OVS/nftables may not be available"; }

# Туннель агента
echo "Opening agent tunnel localhost:$E2E_DP_AGENT_PORT -> $SSH_TARGET:localhost:$E2E_QEMU_GUEST_AGENT_PORT"
ssh \
    -N \
    -L "127.0.0.1:${E2E_DP_AGENT_PORT}:127.0.0.1:${E2E_QEMU_GUEST_AGENT_PORT}" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=15 \
    -o ServerAliveCountMax=2 \
    "$SSH_TARGET" >"$LOCAL_LOG_DIR/ssh-tunnel-agent.log" 2>&1 &
echo $! >"$TUNNEL_AGENT_PID_FILE"
sleep 2
if ! kill -0 "$(cat "$TUNNEL_AGENT_PID_FILE")" 2>/dev/null; then
    echo "Failed to establish agent SSH tunnel" >&2
    exit 1
fi

# Ждём готовности агента
echo "Waiting for NetOS Agent at http://127.0.0.1:$E2E_DP_AGENT_PORT"
E2E_QEMU_WAIT_TIMEOUT=90 "$ROOT_DIR/scripts/e2e/qemu-wait-for-api.sh" \
    "http://127.0.0.1:$E2E_DP_AGENT_PORT" || {
    echo "WARNING: NetOS Agent did not become ready; DP tests may fail"
}

if [[ ! -x "$PYTEST_BIN" ]]; then
    PYTEST_BIN="pytest"
fi

echo "Running DP dataplane E2E pytest suite"
set +e
E2E_DP_RUN=1 \
E2E_QEMU_RUN=1 \
E2E_DP_AGENT_URL="http://127.0.0.1:$E2E_DP_AGENT_PORT" \
E2E_QEMU_API_URL="http://127.0.0.1:$E2E_QEMU_API_PORT" \
E2E_QEMU_API_PORT="$E2E_QEMU_API_PORT" \
E2E_QEMU_SSH_PORT="$E2E_QEMU_SSH_PORT" \
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN="$SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN" \
"$PYTEST_BIN" tests/e2e_dp -m "e2e and qemu and dp" 2>&1 | tee "$LOCAL_LOG_DIR/pytest.log"
pytest_rc=${PIPESTATUS[0]}
set -e
exit "$pytest_rc"
