# QEMU E2E N0 Stand

This stand runs the real NetOS/Nervum image on `rpi4-codex` through QEMU and
then runs pytest from the local machine against the forwarded HTTP API.

## Prerequisites

On `rpi4-codex`:

- SSH access from the local machine.
- `qemu-system-aarch64`.
- `/dev/kvm` is preferred for `qemu-virt.img`; the launcher auto-selects
  `E2E_QEMU_ACCEL=kvm` and `-cpu host` when KVM is available.
- Enough free disk space under `E2E_QEMU_REMOTE_DIR`.
- Guest SSH access as `root` is optional but required if the API does not
  start automatically inside the image.

On the local machine:

- Project dev dependencies installed so local `pytest` can run.
- `ssh`; `rsync` is preferred but the script has a tar fallback.

## Required Image

The default remote image is:

```sh
/mnt/build-ssd/litainer-build/litainer/qemu-virt.img
```

The Raspberry Pi Zero 2W artifact on `rpi4-codex` can be used with the
mainline `virt` kernel:

```sh
E2E_QEMU_IMAGE=/mnt/build-ssd/litainer-build/litainer/raspi-zero2w.qcow2 \
E2E_QEMU_KERNEL=/mnt/build-ssd/litainer-build/litainer/temp/mainline_linux/arch/arm64/boot/Image \
E2E_QEMU_SSH_PORT=12222 \
E2E_QEMU_MEM=2048 \
E2E_QEMU_SMP=4 \
make e2e-qemu-n0
```

`temp/rpi_linux/arch/arm64/boot/Image` is Raspberry-Pi specific and was observed
to wait forever for `/dev/vda2` under QEMU `virt`; use the mainline kernel for
this stand.

Override it when needed:

```sh
E2E_QEMU_IMAGE=/path/netos.qcow2 make e2e-qemu-n0
```

Kernel/rootfs mode is also supported:

```sh
E2E_QEMU_KERNEL=/path/Image \
E2E_QEMU_ROOTFS=/path/rootfs.ext4 \
make e2e-qemu-n0
```

The remote launcher also searches these directories in the synced repository:
`build/`, `output/`, `dist/`, `images/`, `artifacts/`, and `netos/`.

If a Raspberry Pi specific image does not boot on QEMU `virt`, build or point
the stand at a generic aarch64 `virt` kernel/rootfs or qcow2/img where the
Nervum API and DB run inside the guest. N0 does not require OVS/nftables
dataplane.

## Commands

```sh
make e2e-qemu-n0
```

Useful overrides:

```sh
E2E_QEMU_HOST=rpi4-codex
E2E_QEMU_USER=
E2E_QEMU_REMOTE_DIR=/tmp/nervum-e2e-qemu
E2E_QEMU_API_PORT=18080
E2E_QEMU_SSH_PORT=10022
E2E_QEMU_ACCEL=auto
E2E_QEMU_GUEST_API_PORT=8090
E2E_QEMU_GUEST_API_ADDR=
E2E_QEMU_GUEST_PERSISTENCE=memory
E2E_QEMU_PREPARE_GUEST_PAYLOAD=1
E2E_QEMU_PAYLOAD_IMAGE=/tmp/nervum-e2e-qemu/payload.ext4
```

Set `E2E_QEMU_ACCEL=tcg` to force software emulation. On the Raspberry Pi
builder this is much slower and can make Python startup take several minutes.

Keep the VM and SSH tunnel for debugging:

```sh
E2E_QEMU_KEEP_VM=1 make e2e-qemu-n0
```

Collect logs:

```sh
make e2e-qemu-n0-logs
```

Stop QEMU and remove the tunnel:

```sh
make e2e-qemu-n0-clean
```

## Guest Access

From `rpi4-codex`:

```sh
ssh -p ${E2E_QEMU_SSH_PORT:-10022} root@127.0.0.1
```

The local script opens an API tunnel:

```sh
http://127.0.0.1:${E2E_QEMU_API_PORT:-18080}
```

## Nervum Startup

The remote script first waits for the API in case the image starts Nervum
itself. If it does not, and guest SSH is reachable, it tries:

- `systemd`: `sdn-controller.service`, then `nervum.service`.
- `init.d`: `/etc/init.d/sdn-controller`.
- `supervisord`: `sdn-controller`.
- Manual commands: `sdn-controller`, `python3 -m sdn_controller.app.main`,
  then `uvicorn sdn_controller.app.main:app --host 0.0.0.0 --port 8080`.

If guest SSH is not usable, the launcher falls back to the serial console. It
mounts a small readonly payload disk prepared on `rpi4-codex` with `src/` and
pure-Python dependency shims, starts `uvicorn` from inside the VM, and keeps
`nervum.log` on the host through a log share. This path is used by the current
images when dropbear accepts TCP but closes root SSH sessions. Before starting
the fallback, the launcher stops `/etc/init.d/S97nervum` if present so a stale
image-installed controller cannot race with the synced repository version.
The serial fallback defaults to `E2E_QEMU_GUEST_PERSISTENCE=memory` for the
current image because its guest Python lacks the `greenlet` extension required
by SQLAlchemy async SQLite. Set `E2E_QEMU_GUEST_PERSISTENCE=sqlite` after
building an image with `greenlet`; the launcher will then run Alembic migrations
against an isolated SQLite database under `/tmp/nervum-e2e-n0/` inside the guest
before starting the API.

The bootstrap admin token is passed as:

```sh
SDN_AUTH_BOOTSTRAP_ADMIN_TOKEN=e2e-admin-token
```

Override it if the image expects another value.

## Logs

Local logs are collected under:

```text
.e2e/qemu-n0/logs/
  qemu.log
  serial.log
  nervum.log
  alembic.log
  journal.log
  pytest.log
  guest-ip.txt
  qemu.pid
```

Remote logs live under:

```text
${E2E_QEMU_REMOTE_DIR:-/tmp/nervum-e2e-qemu}/logs/
```

On failure, `qemu-n0.sh` always attempts log collection before cleanup.

## N0 Coverage

- `test_project_crud_real_qemu`: creates two projects, lists them as admin,
  and verifies stable `id`/`name`/`slug` semantics.
- `test_project_isolation_for_networks_real_qemu`: creates two project-scoped
  networks with the same name, checks admin project filtering, and xfails only
  the project-scoped credential/RBAC subcase with
  `N0-03 project-scoped credentials/API missing` if runtime enforcement is not
  implemented.
- `test_project_id_in_operations_audit_outbox_real_qemu`: creates a
  project-scoped network and verifies `project_id` in the response envelope,
  audit payload, and outbox v2 event. If `/api/v1/events` is absent, the test
  can use the isolated guest DB helper via SSH and `sqlite3`.
- `test_legacy_null_project_id_behavior_real_qemu`: creates a legacy
  `project_id = NULL` network and verifies admin visibility. Project-scoped
  hiding is xfailed if runtime support is not implemented.
- `test_deprecation_sunset_headers_real_qemu`: verifies current routes do not
  emit deprecation headers and xfails deprecated route header checks with
  `N0-05 deprecated routes not implemented` when no deprecated route contract
  exists.
Leave `E2E_QEMU_USER` empty when `rpi4-codex` is an SSH config alias that
already selects the remote user.
