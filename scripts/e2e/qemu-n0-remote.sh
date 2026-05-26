#!/usr/bin/env bash
set -Eeuo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
E2E_QEMU_MEM_WAS_SET="${E2E_QEMU_MEM+x}"
E2E_QEMU_CPU_WAS_SET="${E2E_QEMU_CPU:+x}"
E2E_QEMU_GUEST_API_PORT_WAS_SET="${E2E_QEMU_GUEST_API_PORT+x}"
E2E_QEMU_GUEST_API_ADDR_WAS_SET="${E2E_QEMU_GUEST_API_ADDR+x}"
E2E_QEMU_NET_DEVICE_WAS_SET="${E2E_QEMU_NET_DEVICE+x}"
E2E_QEMU_DRIVE_IF_WAS_SET="${E2E_QEMU_DRIVE_IF+x}"
E2E_QEMU_HOSTFWD_ADDR_WAS_SET="${E2E_QEMU_HOSTFWD_ADDR+x}"
E2E_QEMU_USE_SUDO_WAS_SET="${E2E_QEMU_USE_SUDO+x}"
E2E_QEMU_APPEND_WAS_SET="${E2E_QEMU_APPEND+x}"
E2E_QEMU_REMOTE_DIR="${E2E_QEMU_REMOTE_DIR:-/tmp/nervum-e2e-qemu}"
E2E_QEMU_IMAGE="${E2E_QEMU_IMAGE:-/mnt/build-ssd/litainer-build/litainer/qemu-virt.img}"
E2E_QEMU_KERNEL="${E2E_QEMU_KERNEL:-}"
E2E_QEMU_ROOTFS="${E2E_QEMU_ROOTFS:-}"
E2E_QEMU_API_PORT="${E2E_QEMU_API_PORT:-18080}"
E2E_QEMU_SSH_PORT="${E2E_QEMU_SSH_PORT:-10022}"
E2E_QEMU_GUEST_API_PORT="${E2E_QEMU_GUEST_API_PORT:-8080}"
E2E_QEMU_GUEST_API_ADDR="${E2E_QEMU_GUEST_API_ADDR:-}"
E2E_QEMU_MEM="${E2E_QEMU_MEM:-2048}"
E2E_QEMU_SMP="${E2E_QEMU_SMP:-4}"
E2E_QEMU_MACHINE="${E2E_QEMU_MACHINE:-virt}"
E2E_QEMU_CPU="${E2E_QEMU_CPU:-cortex-a72}"
E2E_QEMU_ACCEL="${E2E_QEMU_ACCEL:-auto}"
E2E_QEMU_NET_DEVICE="${E2E_QEMU_NET_DEVICE:-virtio-net-device}"
E2E_QEMU_DRIVE_IF="${E2E_QEMU_DRIVE_IF:-none}"
E2E_QEMU_BLOCK_DEVICE="${E2E_QEMU_BLOCK_DEVICE:-virtio-blk-device}"
E2E_QEMU_HOSTFWD_ADDR="${E2E_QEMU_HOSTFWD_ADDR:-127.0.0.1}"
E2E_QEMU_USE_SUDO="${E2E_QEMU_USE_SUDO:-0}"
E2E_QEMU_BOOT_TIMEOUT="${E2E_QEMU_BOOT_TIMEOUT:-240}"
E2E_QEMU_API_TIMEOUT="${E2E_QEMU_API_TIMEOUT:-300}"
E2E_QEMU_CONSOLE_TIMEOUT="${E2E_QEMU_CONSOLE_TIMEOUT:-90}"
E2E_QEMU_SHARE_REPO="${E2E_QEMU_SHARE_REPO:-1}"
E2E_QEMU_REPO_MOUNT_TAG="${E2E_QEMU_REPO_MOUNT_TAG:-nervumrepo}"
E2E_QEMU_GUEST_REPO_DIR="${E2E_QEMU_GUEST_REPO_DIR:-/mnt/nervum}"
E2E_QEMU_GUEST_WORK_DIR="${E2E_QEMU_GUEST_WORK_DIR:-/tmp/nervum-src}"
E2E_QEMU_GUEST_PYTHON_FLAGS="${E2E_QEMU_GUEST_PYTHON_FLAGS:-}"
E2E_QEMU_PAYLOAD_IMAGE="${E2E_QEMU_PAYLOAD_IMAGE:-}"
E2E_QEMU_PAYLOAD_DEVICE="${E2E_QEMU_PAYLOAD_DEVICE:-/dev/sda}"
E2E_QEMU_PAYLOAD_BUS="${E2E_QEMU_PAYLOAD_BUS:-usb}"
E2E_QEMU_PAYLOAD_BLOCK_DEVICE="${E2E_QEMU_PAYLOAD_BLOCK_DEVICE:-virtio-blk-pci}"
E2E_QEMU_GUEST_PAYLOAD_DIR="${E2E_QEMU_GUEST_PAYLOAD_DIR:-/mnt/nervum-payload}"
E2E_QEMU_LOG_MOUNT_TAG="${E2E_QEMU_LOG_MOUNT_TAG:-nervumlogs}"
E2E_QEMU_GUEST_LOG_DIR="${E2E_QEMU_GUEST_LOG_DIR:-/mnt/nervum-logs}"
E2E_QEMU_GUEST_PERSISTENCE="${E2E_QEMU_GUEST_PERSISTENCE:-memory}"
E2E_QEMU_GUEST_DATABASE_URL="${E2E_QEMU_GUEST_DATABASE_URL:-sqlite+aiosqlite:////tmp/nervum-e2e-n0/sdn_controller.db}"
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN="${SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN:-e2e-admin-token}"

if [[ "$E2E_QEMU_IMAGE" == */raspi-zero2w.qcow2 ]]; then
    E2E_QEMU_KERNEL="${E2E_QEMU_KERNEL:-/mnt/build-ssd/litainer-build/litainer/temp/mainline_linux/arch/arm64/boot/Image}"
    [[ -z "$E2E_QEMU_MEM_WAS_SET" ]] && E2E_QEMU_MEM="1024"
    [[ -z "$E2E_QEMU_CPU_WAS_SET" ]] && E2E_QEMU_CPU="cortex-a53"
    [[ -z "$E2E_QEMU_NET_DEVICE_WAS_SET" ]] && E2E_QEMU_NET_DEVICE="virtio-net-pci"
    [[ -z "$E2E_QEMU_DRIVE_IF_WAS_SET" ]] && E2E_QEMU_DRIVE_IF="virtio"
    [[ -z "$E2E_QEMU_HOSTFWD_ADDR_WAS_SET" ]] && E2E_QEMU_HOSTFWD_ADDR="0.0.0.0"
    [[ -z "$E2E_QEMU_USE_SUDO_WAS_SET" ]] && E2E_QEMU_USE_SUDO="1"
    [[ -z "$E2E_QEMU_APPEND_WAS_SET" ]] && E2E_QEMU_APPEND="console=ttyAMA0 root=/dev/vda2 rootfstype=ext4 rw rootwait"
fi

if [[ "$E2E_QEMU_IMAGE" == */qemu-virt.img ]]; then
    E2E_QEMU_KERNEL="${E2E_QEMU_KERNEL:-/mnt/build-ssd/litainer-build/litainer/temp/mainline_linux/arch/arm64/boot/Image}"
    [[ -z "$E2E_QEMU_MEM_WAS_SET" ]] && E2E_QEMU_MEM="1024"
    if [[ "$E2E_QEMU_ACCEL" == "auto" && -e /dev/kvm ]]; then
        E2E_QEMU_ACCEL="kvm"
    fi
    if [[ "$E2E_QEMU_ACCEL" == "kvm" && -z "$E2E_QEMU_CPU_WAS_SET" ]]; then
        E2E_QEMU_CPU="host"
    elif [[ -z "$E2E_QEMU_CPU_WAS_SET" ]]; then
        E2E_QEMU_CPU="cortex-a72"
    fi
    [[ -z "$E2E_QEMU_GUEST_API_PORT_WAS_SET" ]] && E2E_QEMU_GUEST_API_PORT="8090"
    [[ -z "$E2E_QEMU_NET_DEVICE_WAS_SET" ]] && E2E_QEMU_NET_DEVICE="virtio-net-pci"
    [[ -z "$E2E_QEMU_DRIVE_IF_WAS_SET" ]] && E2E_QEMU_DRIVE_IF="virtio"
    [[ -z "$E2E_QEMU_HOSTFWD_ADDR_WAS_SET" ]] && E2E_QEMU_HOSTFWD_ADDR="127.0.0.1"
    [[ -z "$E2E_QEMU_APPEND_WAS_SET" ]] && E2E_QEMU_APPEND="console=ttyAMA0 root=/dev/vda2 rootfstype=ext4 rw rootwait"
fi

RUN_DIR="$E2E_QEMU_REMOTE_DIR/run"
LOG_DIR="$E2E_QEMU_REMOTE_DIR/logs"
PID_FILE="$RUN_DIR/qemu.pid"
SERIAL_LOG="$LOG_DIR/serial.log"
SERIAL_SOCK="$RUN_DIR/serial.sock"
QEMU_LOG="$LOG_DIR/qemu.log"
ENV_FILE="$E2E_QEMU_REMOTE_DIR/qemu-n0.env"

die() {
    echo "ERROR: $*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "$1 is required on this host"
}

discover_file() {
    local explicit="$1"
    shift
    if [[ -n "$explicit" && -f "$explicit" ]]; then
        printf "%s\n" "$explicit"
        return 0
    fi

    local root pattern
    for root in "$@"; do
        [[ -d "$root" ]] || continue
        while IFS= read -r candidate; do
            [[ -f "$candidate" ]] || continue
            printf "%s\n" "$candidate"
            return 0
        done < <(
            find "$root" -maxdepth 4 -type f \( \
                -name 'qemu-virt.img' -o \
                -name 'netos.qcow2' -o \
                -name 'netos.img' -o \
                -name '*.qcow2' -o \
                -name '*.img' \
            \) 2>/dev/null | sort
        )
    done
    return 1
}

disk_format_for() {
    case "$1" in
        *.qcow2) printf "qcow2" ;;
        *) printf "raw" ;;
    esac
}

make_overlay() {
    local source="$1"
    local overlay="$2"
    local source_format="$3"
    if command -v qemu-img >/dev/null 2>&1; then
        qemu-img create -f qcow2 -F "$source_format" -b "$source" "$overlay" >/dev/null
        printf "%s\n" "$overlay"
        return 0
    fi
    echo "qemu-img not found; booting directly from $source" >&2
    printf "%s\n" "$source"
}

guest_ssh() {
    ssh \
        -p "$E2E_QEMU_SSH_PORT" \
        -o BatchMode=yes \
        -o ConnectTimeout=5 \
        -o StrictHostKeyChecking=no \
        -o UserKnownHostsFile="$RUN_DIR/known_hosts" \
        root@127.0.0.1 "$@"
}

wait_for_guest_ssh() {
    local deadline=$((SECONDS + E2E_QEMU_BOOT_TIMEOUT))
    while (( SECONDS < deadline )); do
        if guest_ssh "true" >/dev/null 2>&1; then
            echo "Guest SSH is ready on localhost:$E2E_QEMU_SSH_PORT"
            return 0
        fi
        sleep 3
    done
    return 1
}

console_run() {
    local command="$1"
    python3 - "$SERIAL_SOCK" "$E2E_QEMU_CONSOLE_TIMEOUT" "$command" <<'PY'
from __future__ import annotations

import os
import selectors
import socket
import sys
import time
import uuid

sock_path = sys.argv[1]
timeout = float(sys.argv[2])
command = sys.argv[3]
deadline = time.monotonic() + timeout


def remaining() -> float:
    return max(0.1, deadline - time.monotonic())


client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
while True:
    try:
        client.connect(sock_path)
        break
    except OSError:
        if time.monotonic() >= deadline:
            print(f"Timed out connecting to serial console {sock_path}", file=sys.stderr)
            raise SystemExit(1)
        time.sleep(1)

client.setblocking(False)
selector = selectors.DefaultSelector()
selector.register(client, selectors.EVENT_READ)
buffer = bytearray()


def send(data: str) -> None:
    client.sendall(data.encode())


def read_some(seconds: float = 0.5) -> bytes:
    events = selector.select(seconds)
    if not events:
        return b""
    try:
        return client.recv(65536)
    except BlockingIOError:
        return b""


send("\n")
logged_in = False
while time.monotonic() < deadline:
    data = read_some(0.5)
    if data:
        buffer.extend(data)
    view = bytes(buffer[-4096:]).lower()
    if b"login:" in view:
        send("root\n")
        buffer.clear()
        continue
    if b"password:" in view:
        send("\n")
        buffer.clear()
        continue
    if b"# " in view or view.rstrip().endswith(b"#"):
        logged_in = True
        break
    if not data:
        send("\n")

if not logged_in:
    print("Timed out waiting for root shell on serial console", file=sys.stderr)
    raise SystemExit(1)

token = "__E2E_CONSOLE_DONE_%s__" % uuid.uuid4().hex
payload = "\n%s\nrc=$?\nprintf '\\n%s:%%s\\n' \"$rc\"\n" % (command, token)
send(payload)

output = bytearray()
while time.monotonic() < deadline:
    data = read_some(min(1.0, remaining()))
    if data:
        output.extend(data)
        text = output.decode(errors="replace")
        marker = f"{token}:"
        if marker in text:
            after_marker = text.split(marker, 1)[1]
            if "\n" not in after_marker:
                continue
            tail = after_marker.splitlines()[0].strip()
            try:
                rc = int(tail)
            except ValueError:
                rc = 1
            sys.stdout.write(text)
            raise SystemExit(rc)

sys.stdout.write(output.decode(errors="replace"))
print(f"Timed out waiting for serial command sentinel {token}", file=sys.stderr)
raise SystemExit(1)
PY
}

start_nervum_in_guest() {
    local guest_env
    guest_env="SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN='$SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN' SDN_AUTH_ENABLED=true SDN_HTTP_HOST=0.0.0.0 SDN_HTTP_PORT='$E2E_QEMU_GUEST_API_PORT' SDN_LOG_FORMAT=console SDN_LOG_LEVEL=INFO SDN_OTEL_ENABLED=false"

    guest_ssh "mkdir -p /tmp/nervum-e2e-n0; kill \$(cat /tmp/nervum-e2e-n0/nervum.pid 2>/dev/null) 2>/dev/null || true; rm -f /tmp/nervum-e2e-n0/nervum.pid" >/dev/null 2>&1 || true

    for unit in sdn-controller.service nervum.service; do
        if guest_ssh "command -v systemctl >/dev/null 2>&1 && systemctl list-unit-files '$unit' | grep -q '^$unit'"; then
            if guest_ssh "systemctl set-environment $guest_env && systemctl restart '$unit'"; then
                echo "Nervum startup mode: systemd:$unit"
                return 0
            fi
        fi
    done

    if guest_ssh "command -v service >/dev/null 2>&1 && test -x /etc/init.d/sdn-controller"; then
        guest_ssh "$guest_env service sdn-controller restart"
        echo "Nervum startup mode: init.d:sdn-controller"
        return 0
    fi

    if guest_ssh "command -v supervisorctl >/dev/null 2>&1 && supervisorctl status sdn-controller >/dev/null 2>&1"; then
        guest_ssh "$guest_env supervisorctl restart sdn-controller"
        echo "Nervum startup mode: supervisord:sdn-controller"
        return 0
    fi

    local manual
    manual="$(guest_ssh "if command -v sdn-controller >/dev/null 2>&1; then echo sdn-controller; elif python3 -c 'import sdn_controller.app.main' >/dev/null 2>&1; then echo python-module; elif command -v uvicorn >/dev/null 2>&1; then echo uvicorn; fi" 2>/dev/null | tail -n 1)"
    case "$manual" in
        sdn-controller)
            guest_ssh "cd /tmp/nervum-e2e-n0 && nohup sh -c \"$guest_env exec sdn-controller\" >nervum.log 2>&1 & echo \$! >nervum.pid"
            echo "Nervum startup mode: manual:sdn-controller"
            ;;
        python-module)
            guest_ssh "cd /tmp/nervum-e2e-n0 && nohup sh -c \"$guest_env exec python3 -m sdn_controller.app.main\" >nervum.log 2>&1 & echo \$! >nervum.pid"
            echo "Nervum startup mode: manual:python-module"
            ;;
        uvicorn)
            guest_ssh "cd /tmp/nervum-e2e-n0 && nohup sh -c \"$guest_env exec uvicorn sdn_controller.app.main:app --host 0.0.0.0 --port '$E2E_QEMU_GUEST_API_PORT'\" >nervum.log 2>&1 & echo \$! >nervum.pid"
            echo "Nervum startup mode: manual:uvicorn"
            ;;
        *)
            echo "Nervum startup mode: not-found"
            return 1
            ;;
    esac
}

start_nervum_via_console() {
    [[ "$E2E_QEMU_SHARE_REPO" == "1" ]] || return 1

    local command
    command="
set -eu
mkdir -p /tmp/nervum-e2e-n0 '$E2E_QEMU_GUEST_REPO_DIR' '$E2E_QEMU_GUEST_LOG_DIR' '$E2E_QEMU_GUEST_PAYLOAD_DIR'
kill \$(cat /tmp/nervum-e2e-n0/nervum.pid 2>/dev/null) 2>/dev/null || true
rm -f /tmp/nervum-e2e-n0/nervum.pid
PAYLOAD_SOURCE='$E2E_QEMU_GUEST_REPO_DIR'
if [ -b '$E2E_QEMU_PAYLOAD_DEVICE' ]; then
    mountpoint -q '$E2E_QEMU_GUEST_PAYLOAD_DIR' || mount -o ro '$E2E_QEMU_PAYLOAD_DEVICE' '$E2E_QEMU_GUEST_PAYLOAD_DIR'
    PAYLOAD_SOURCE='$E2E_QEMU_GUEST_PAYLOAD_DIR'
else
    mountpoint -q '$E2E_QEMU_GUEST_REPO_DIR' || mount -t 9p -o trans=virtio,version=9p2000.L '$E2E_QEMU_REPO_MOUNT_TAG' '$E2E_QEMU_GUEST_REPO_DIR'
fi
mountpoint -q '$E2E_QEMU_GUEST_LOG_DIR' || mount -t 9p -o trans=virtio,version=9p2000.L '$E2E_QEMU_LOG_MOUNT_TAG' '$E2E_QEMU_GUEST_LOG_DIR'
rm -rf '$E2E_QEMU_GUEST_WORK_DIR'
mkdir -p '$E2E_QEMU_GUEST_WORK_DIR'
if [ -d \"\$PAYLOAD_SOURCE/src\" ]; then
    cp -a \"\$PAYLOAD_SOURCE/src\" '$E2E_QEMU_GUEST_WORK_DIR/src'
    [ ! -f \"\$PAYLOAD_SOURCE/alembic.ini\" ] || cp -a \"\$PAYLOAD_SOURCE/alembic.ini\" '$E2E_QEMU_GUEST_WORK_DIR/alembic.ini'
    [ ! -d \"\$PAYLOAD_SOURCE/.e2e_guest_py\" ] || cp -a \"\$PAYLOAD_SOURCE/.e2e_guest_py\" '$E2E_QEMU_GUEST_WORK_DIR/.e2e_guest_py'
elif [ -f \"\$PAYLOAD_SOURCE/.e2e_guest_payload.tar\" ]; then
    tar -xf \"\$PAYLOAD_SOURCE/.e2e_guest_payload.tar\" -C '$E2E_QEMU_GUEST_WORK_DIR'
else
    echo \"payload source lacks src/: \$PAYLOAD_SOURCE\"
    exit 1
fi
cd '$E2E_QEMU_GUEST_WORK_DIR'
export PYTHONPATH='$E2E_QEMU_GUEST_WORK_DIR/src:$E2E_QEMU_GUEST_WORK_DIR/.e2e_guest_py:/opt/testum/.python:'\"\${PYTHONPATH:-}\"
export SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN='$SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN'
export SDN_AUTH_ENABLED=true
export SDN_HTTP_HOST=0.0.0.0
export SDN_HTTP_PORT='$E2E_QEMU_GUEST_API_PORT'
export SDN_LOG_FORMAT=console
export SDN_LOG_LEVEL=INFO
export SDN_OTEL_ENABLED=false
export SDN_PERSISTENCE='$E2E_QEMU_GUEST_PERSISTENCE'
export SDN_DATABASE_URL='$E2E_QEMU_GUEST_DATABASE_URL'
export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
/etc/init.d/S97nervum stop 2>/dev/null || true
/etc/init.d/S98testum stop 2>/dev/null || true
    for pid in \$(ps w | awk '/sdn-controller|uvicorn sdn_controller\\.app\\.main|python3 -m sdn_controller\\.app\\.main/ && !/awk/ {print \$1}'); do
        kill \"\$pid\" 2>/dev/null || true
    done
    sleep 1
    for pid in \$(ps w | awk '/sdn-controller|uvicorn sdn_controller\\.app\\.main|python3 -m sdn_controller\\.app\\.main/ && !/awk/ {print \$1}'); do
        kill -9 \"\$pid\" 2>/dev/null || true
    done
    rm -f /tmp/nervum-e2e-n0/sdn_controller.db /tmp/nervum-e2e-n0/sdn_controller.db-* '$E2E_QEMU_GUEST_LOG_DIR/alembic.log'
    if [ \"\$SDN_PERSISTENCE\" = sqlite ]; then
        if [ -f alembic.ini ]; then
            LC_ALL=C tr -cd '\11\12\15\40-\176' < alembic.ini > /tmp/nervum-e2e-n0/alembic.ini
            python3 -m alembic -c /tmp/nervum-e2e-n0/alembic.ini upgrade head > '$E2E_QEMU_GUEST_LOG_DIR/alembic.log' 2>&1 || {
                cat '$E2E_QEMU_GUEST_LOG_DIR/alembic.log'
                exit 1
            }
        else
            echo \"alembic.ini missing from E2E payload\"
            exit 1
        fi
    fi
    nohup python3 $E2E_QEMU_GUEST_PYTHON_FLAGS -m uvicorn sdn_controller.app.main:app --host 0.0.0.0 --port '$E2E_QEMU_GUEST_API_PORT' >'$E2E_QEMU_GUEST_LOG_DIR/nervum.log' 2>&1 &
echo \$! >/tmp/nervum-e2e-n0/nervum.pid
sleep 2
cat '$E2E_QEMU_GUEST_LOG_DIR/nervum.log' || true
kill -0 \$(cat /tmp/nervum-e2e-n0/nervum.pid)
"
    console_run "$command" || return 1
    echo "Nervum startup mode: serial-console:uvicorn-9p"
}

require_command qemu-system-aarch64
require_command ssh
require_command python3
if [[ "$E2E_QEMU_USE_SUDO" == "1" ]]; then
    sudo -n true || die "E2E_QEMU_USE_SUDO=1 requires passwordless sudo for qemu-system-aarch64"
fi

bash "$REPO_DIR/scripts/e2e/qemu-cleanup.sh" --remote >/dev/null 2>&1 || true
mkdir -p "$RUN_DIR" "$LOG_DIR"
: >"$SERIAL_LOG"
: >"$QEMU_LOG"

mode=""
image_path=""
drive_path=""
drive_format=""

if [[ -n "$E2E_QEMU_KERNEL" ]]; then
    [[ -f "$E2E_QEMU_KERNEL" ]] || die "E2E_QEMU_KERNEL does not exist: $E2E_QEMU_KERNEL"
    if [[ -n "$E2E_QEMU_ROOTFS" ]]; then
        [[ -f "$E2E_QEMU_ROOTFS" ]] || die "E2E_QEMU_ROOTFS does not exist: $E2E_QEMU_ROOTFS"
        image_path="$E2E_QEMU_ROOTFS"
        mode="kernel-rootfs"
    else
        image_path="$(discover_file "$E2E_QEMU_IMAGE" \
            "$REPO_DIR/build" "$REPO_DIR/output" "$REPO_DIR/dist" "$REPO_DIR/images" \
            "$REPO_DIR/artifacts" "$REPO_DIR/netos" "$E2E_QEMU_REMOTE_DIR/artifacts" \
            "/mnt/build-ssd/litainer-build/litainer" || true)"
        [[ -n "$image_path" ]] || die "E2E_QEMU_KERNEL was set, but no disk image was found. Set E2E_QEMU_IMAGE=/path/disk.img or E2E_QEMU_ROOTFS=/path/rootfs.ext4"
        mode="kernel-image"
    fi
else
    image_path="$(discover_file "$E2E_QEMU_IMAGE" \
        "$REPO_DIR/build" "$REPO_DIR/output" "$REPO_DIR/dist" "$REPO_DIR/images" \
        "$REPO_DIR/artifacts" "$REPO_DIR/netos" "$E2E_QEMU_REMOTE_DIR/artifacts" \
        "/mnt/build-ssd/litainer-build/litainer" || true)"
    [[ -n "$image_path" ]] || die "No QEMU image found. Set E2E_QEMU_IMAGE=/path/netos.qcow2 or E2E_QEMU_KERNEL=/path/Image plus E2E_QEMU_ROOTFS=/path/rootfs.ext4. Default remote image checked: /mnt/build-ssd/litainer-build/litainer/qemu-virt.img"
    mode="image"
fi

drive_format="$(disk_format_for "$image_path")"
drive_path="$(make_overlay "$image_path" "$RUN_DIR/disk-overlay.qcow2" "$drive_format")"
if [[ "$drive_path" == "$RUN_DIR/disk-overlay.qcow2" ]]; then
    drive_format="qcow2"
fi

qemu_bin=(qemu-system-aarch64)
if [[ "$E2E_QEMU_USE_SUDO" == "1" ]]; then
    qemu_bin=(sudo -n qemu-system-aarch64)
fi

api_guest_forward=":$E2E_QEMU_GUEST_API_PORT"
if [[ -n "$E2E_QEMU_GUEST_API_ADDR" ]]; then
    api_guest_forward="$E2E_QEMU_GUEST_API_ADDR:$E2E_QEMU_GUEST_API_PORT"
fi

qemu_cmd=(
    "${qemu_bin[@]}"
    -M "$E2E_QEMU_MACHINE"
    -cpu "$E2E_QEMU_CPU"
    -m "$E2E_QEMU_MEM"
    -smp "$E2E_QEMU_SMP"
    -display none
    -chardev "socket,id=serial0,path=$SERIAL_SOCK,server=on,wait=off,logfile=$SERIAL_LOG,signal=off"
    -serial "chardev:serial0"
    -monitor "unix:$RUN_DIR/qemu-monitor.sock,server,nowait"
    -netdev "user,id=net0,hostfwd=tcp:$E2E_QEMU_HOSTFWD_ADDR:$E2E_QEMU_SSH_PORT-:22,hostfwd=tcp:$E2E_QEMU_HOSTFWD_ADDR:$E2E_QEMU_API_PORT-$api_guest_forward"
    -device "$E2E_QEMU_NET_DEVICE,netdev=net0"
    -daemonize
    -pidfile "$PID_FILE"
    -D "$QEMU_LOG"
)

if [[ "$E2E_QEMU_ACCEL" != "auto" && -n "$E2E_QEMU_ACCEL" ]]; then
    qemu_cmd+=(-accel "$E2E_QEMU_ACCEL")
fi

if [[ "$E2E_QEMU_SHARE_REPO" == "1" ]]; then
    qemu_cmd+=(
        -fsdev "local,id=repo,path=$REPO_DIR,security_model=none,readonly=on"
        -device "virtio-9p-pci,fsdev=repo,mount_tag=$E2E_QEMU_REPO_MOUNT_TAG"
        -fsdev "local,id=e2elogs,path=$LOG_DIR,security_model=none"
        -device "virtio-9p-pci,fsdev=e2elogs,mount_tag=$E2E_QEMU_LOG_MOUNT_TAG"
    )
fi

if [[ "$E2E_QEMU_DRIVE_IF" == "none" ]]; then
    qemu_cmd+=(
        -drive "file=$drive_path,if=none,id=hd0,format=$drive_format"
        -device "$E2E_QEMU_BLOCK_DEVICE,drive=hd0"
    )
else
    qemu_cmd+=(-drive "file=$drive_path,format=$drive_format,if=$E2E_QEMU_DRIVE_IF")
fi

if [[ -n "$E2E_QEMU_PAYLOAD_IMAGE" ]]; then
    [[ -f "$E2E_QEMU_PAYLOAD_IMAGE" ]] || die "E2E_QEMU_PAYLOAD_IMAGE does not exist: $E2E_QEMU_PAYLOAD_IMAGE"
    if [[ "$E2E_QEMU_PAYLOAD_BUS" == "usb" ]]; then
        qemu_cmd+=(
            -device "qemu-xhci,id=xhci0"
            -drive "file=$E2E_QEMU_PAYLOAD_IMAGE,format=raw,if=none,id=payload0,readonly=on"
            -device "usb-storage,drive=payload0"
        )
    else
        qemu_cmd+=(
            -drive "file=$E2E_QEMU_PAYLOAD_IMAGE,format=raw,if=none,id=payload0,readonly=on"
            -device "$E2E_QEMU_PAYLOAD_BLOCK_DEVICE,drive=payload0"
        )
    fi
fi

if [[ "$mode" == "kernel-rootfs" || "$mode" == "kernel-image" ]]; then
    qemu_cmd+=(
        -kernel "$E2E_QEMU_KERNEL"
        -append "${E2E_QEMU_APPEND:-console=ttyAMA0 root=/dev/vda rw rootwait}"
    )
elif [[ -n "${E2E_QEMU_BIOS:-}" ]]; then
    [[ -f "$E2E_QEMU_BIOS" ]] || die "E2E_QEMU_BIOS does not exist: $E2E_QEMU_BIOS"
    qemu_cmd+=(-bios "$E2E_QEMU_BIOS")
else
    for bios in /usr/share/qemu-efi-aarch64/QEMU_EFI.fd /usr/share/AAVMF/AAVMF_CODE.fd /usr/share/edk2/aarch64/QEMU_EFI.fd; do
        if [[ -f "$bios" ]]; then
            qemu_cmd+=(-bios "$bios")
            break
        fi
    done
fi

echo "Starting QEMU mode=$mode image=$image_path accel=$E2E_QEMU_ACCEL cpu=$E2E_QEMU_CPU"
"${qemu_cmd[@]}"
if [[ "$E2E_QEMU_USE_SUDO" == "1" ]]; then
    sudo -n chown "$(id -u):$(id -g)" "$PID_FILE" "$QEMU_LOG" "$SERIAL_LOG" "$SERIAL_SOCK" 2>/dev/null || true
fi
cp "$PID_FILE" "$LOG_DIR/qemu.pid"

cat >"$ENV_FILE" <<EOF
E2E_QEMU_API_URL=http://127.0.0.1:$E2E_QEMU_API_PORT
E2E_QEMU_API_PORT=$E2E_QEMU_API_PORT
E2E_QEMU_SSH_PORT=$E2E_QEMU_SSH_PORT
E2E_QEMU_REMOTE_DIR=$E2E_QEMU_REMOTE_DIR
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN=$SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN
EOF

if E2E_QEMU_WAIT_TIMEOUT=20 "$REPO_DIR/scripts/e2e/qemu-wait-for-api.sh" "http://127.0.0.1:$E2E_QEMU_API_PORT"; then
    echo "Nervum startup mode: already-running-in-image"
else
    if wait_for_guest_ssh; then
        start_nervum_in_guest || start_nervum_via_console || true
    else
        echo "Guest SSH was not reachable before timeout; trying serial console startup"
        start_nervum_via_console || true
    fi
    E2E_QEMU_WAIT_TIMEOUT="$E2E_QEMU_API_TIMEOUT" "$REPO_DIR/scripts/e2e/qemu-wait-for-api.sh" "http://127.0.0.1:$E2E_QEMU_API_PORT"
fi

echo "QEMU/Nervum ready. Env written to $ENV_FILE"
