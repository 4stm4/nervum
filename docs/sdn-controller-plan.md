# SDN Controller — Development Plan

This document is the canonical development plan for the standalone SDN Controller.
It is intentionally **platform-agnostic** on the northbound side: any external management
platform can drive it through HTTP API / CLI / UI.

> **Главный принцип:**
> API принимает *намерение* → SDN Controller хранит *desired state* →
> Reconciler приводит *actual state* к *desired state* → Agent применяет
> изменения локально на node.

---

## 0. Target architecture

```text
External Management Platform
        │
        │ REST API / OpenAPI
        ▼
┌──────────────────────────┐
│      SDN Controller      │
│ desired state            │
│ topology                 │
│ reconciler               │
│ IPAM / DHCP / DNS / NAT  │
│ audit / operations       │
└───────────┬──────────────┘
            │ gRPC/HTTPS/mTLS
            ▼
┌──────────────────────────┐
│      NetOS Agent         │
│ network / vm / storage   │
│ stat / system modules    │
└───────────┬──────────────┘
            │ local
            ▼
┌──────────────────────────┐
│ OVSDB + OVS + nftables   │
│ Kea/CoreDNS/dnsmasq      │
└──────────────────────────┘
```

The controller never issues raw shell commands. Each mutation produces an **operation**
that is planned, applied through the agent, verified, and recorded in audit.

---

## 1. Ports & adapters layout

```text
sdn-controller/
├─ src/sdn_controller/
│  ├─ core/                 # pure domain, no I/O
│  │  ├─ entities/
│  │  ├─ value_objects/
│  │  ├─ services/
│  │  ├─ policies/
│  │  └─ use_cases/
│  │
│  ├─ ports/                # Protocol interfaces only
│  │  ├─ api/
│  │  ├─ agent/
│  │  ├─ persistence/
│  │  ├─ ovs/
│  │  ├─ dhcp/
│  │  ├─ dns/
│  │  ├─ firewall/
│  │  ├─ jobs/
│  │  ├─ events/
│  │  └─ auth/
│  │
│  ├─ adapters/             # concrete implementations
│  │  ├─ http_api/          # FastAPI
│  │  ├─ memory/            # in-memory repos (tests & quick dev)
│  │  ├─ sql/               # SQLAlchemy adapter (SQLite for MVP, Postgres later)
│  │  ├─ netos_agent/       # gRPC/HTTPS client
│  │  ├─ ovsdb/
│  │  ├─ kea/
│  │  ├─ coredns/
│  │  ├─ dnsmasq/
│  │  ├─ nftables/
│  │  ├─ pyjobkit/
│  │  └─ auth_static/
│  │
│  ├─ app/
│  │  ├─ config.py
│  │  ├─ container.py
│  │  ├─ logging.py
│  │  └─ main.py
│  │
│  └─ migrations/
│
├─ proto/
├─ openapi/
├─ tests/
├─ docs/
├─ docker/
├─ systemd/
└─ README.md
```

**Hard rule:** `core/` must not import FastAPI, SQLAlchemy, OVSDB, Kea, CoreDNS,
nftables, HTTP, gRPC, or systemd. It depends only on its own value objects and
the abstract ports.

---

## 2. Domain entities

### Base entities

```text
Node              NodeCapability     NodeInterface
Network           Segment            Subnet
IpPool            IpAllocation
Bridge            Port               Attachment
Gateway           DhcpScope          DnsZone        DnsRecord
NatRule           FirewallPolicy     SecurityGroup
DesiredState      ActualState
Operation         Plan               PlanStep
DriftFinding      AuditEvent
```

### Network shape

```text
Network
├─ Segment (flat / vlan / vxlan, vlan_id, vni, mtu, attachments)
├─ Subnet  (cidr, gateway, dhcp, dns)
└─ Edge    (nat, firewall, gateway_node)
```

### Example desired state

```json
{
  "name": "prod-network",
  "type": "vxlan",
  "vni": 10100,
  "mtu": 1450,
  "subnet": {
    "cidr": "10.100.0.0/24",
    "gateway": "10.100.0.1",
    "dhcp": { "enabled": true, "range_start": "10.100.0.100", "range_end": "10.100.0.200" },
    "dns":  { "enabled": true, "zone": "prod.internal" }
  },
  "nat":   { "enabled": true, "egress_interface": "wan0" },
  "nodes": ["node-1", "node-2", "node-3"]
}
```

---

## 3. Northbound API

### Endpoints

```text
GET    /api/v1/health
GET    /api/v1/version

GET    /api/v1/nodes
POST   /api/v1/nodes/enroll-token
GET    /api/v1/nodes/{node_id}
GET    /api/v1/nodes/{node_id}/state
GET    /api/v1/nodes/{node_id}/capabilities

GET    /api/v1/networks
POST   /api/v1/networks
GET    /api/v1/networks/{network_id}
PATCH  /api/v1/networks/{network_id}
DELETE /api/v1/networks/{network_id}
POST   /api/v1/networks/{network_id}/apply

GET    /api/v1/subnets
POST   /api/v1/subnets
GET    /api/v1/ipam/allocations
POST   /api/v1/ipam/reservations

GET    /api/v1/dhcp/scopes
GET    /api/v1/dns/zones
GET    /api/v1/nat/rules
GET    /api/v1/firewall/policies

GET    /api/v1/topology
GET    /api/v1/drift
POST   /api/v1/drift/scan

GET    /api/v1/operations
GET    /api/v1/operations/{operation_id}
GET    /api/v1/operations/{operation_id}/events
POST   /api/v1/operations/{operation_id}/cancel

GET    /api/v1/audit/events
GET    /api/v1/backup/export
POST   /api/v1/backup/import
```

### Asynchronous operations

Every mutating endpoint returns an operation envelope:

```json
{
  "operation_id": "op_01HX...",
  "status": "accepted",
  "resource": { "type": "network", "id": "net_01HX..." },
  "links": {
    "self":   "/api/v1/operations/op_01HX...",
    "events": "/api/v1/operations/op_01HX.../events"
  }
}
```

Operation status machine:

```text
accepted → planning → running → verifying → succeeded
                                          ↘ failed
                                          ↘ rolled_back
                              ↘ cancelled
```

---

## 4. Southbound Agent API

### REST (v1)

```text
GET  /healthz
GET  /readyz

POST /v1/enroll
GET  /v1/node/info
GET  /v1/node/capabilities
GET  /v1/node/state

GET  /v1/network/state
POST /v1/network/apply
POST /v1/network/rollback

GET  /v1/ovs/state
POST /v1/ovs/snapshot
POST /v1/ovs/restore

GET  /v1/system/stats
GET  /v1/system/logs
```

### gRPC (later)

```text
AgentService.Enroll()
AgentService.GetCapabilities()
AgentService.GetState()
AgentService.ApplyPlan()
AgentService.RollbackPlan()
AgentService.WatchState()
AgentService.Heartbeat()
```

The agent receives **plans**, never shell commands:

```json
{
  "plan_id": "plan_01HX",
  "steps": [
    { "action": "ensure_bridge", "bridge": { "name": "br-tenant", "datapath_type": "system" } },
    { "action": "ensure_vxlan_port",
      "port": { "bridge": "br-tenant", "name": "vxlan-10100-node2", "vni": 10100, "remote_ip": "10.10.0.2" } }
  ]
}
```

---

## 5. Core ports

### Repositories

```python
class NodeRepository(Protocol):
    async def get(self, node_id: NodeId) -> Node | None: ...
    async def list(self) -> list[Node]: ...
    async def save(self, node: Node) -> None: ...
    async def update_status(self, node_id: NodeId, status: NodeStatus) -> None: ...

class NetworkRepository(Protocol):
    async def get(self, network_id: NetworkId) -> Network | None: ...
    async def save(self, network: Network) -> None: ...
    async def list_by_owner(self, owner_id: OwnerId) -> list[Network]: ...

class OperationRepository(Protocol):
    async def create(self, operation: Operation) -> None: ...
    async def update_status(self, operation_id: OperationId, status: OperationStatus) -> None: ...
    async def append_event(self, operation_id: OperationId, event: OperationEvent) -> None: ...
```

### Agent / device ports

```python
class AgentPort(Protocol):
    async def get_capabilities(self, node_id: NodeId) -> NodeCapability: ...
    async def get_state(self, node_id: NodeId) -> ActualState: ...
    async def apply_plan(self, node_id: NodeId, plan: Plan) -> PlanResult: ...
    async def rollback(self, node_id: NodeId, rollback_ref: RollbackRef) -> None: ...

class OvsPort(Protocol):
    async def get_state(self) -> OvsState: ...
    async def ensure_bridge(self, spec: BridgeSpec) -> None: ...
    async def ensure_port(self, spec: PortSpec) -> None: ...
    async def ensure_vlan(self, spec: VlanSpec) -> None: ...
    async def ensure_vxlan(self, spec: VxlanSpec) -> None: ...
    async def snapshot(self) -> SnapshotRef: ...
    async def restore(self, snapshot_ref: SnapshotRef) -> None: ...
```

### Edge services

```python
class DhcpPort(Protocol):     ...
class DnsPort(Protocol):      ...
class FirewallPort(Protocol): ...
class JobPort(Protocol):      ...
```

---

## 6. Milestones

| # | Milestone                                      | Goal                                          |
|---|------------------------------------------------|-----------------------------------------------|
| 1 | Application skeleton                           | Runnable controller, ports & adapters         |
| 2 | Node inventory & enrollment                    | Controller sees nodes safely                  |
| 3 | NetOS Agent                                    | Local executor on node                        |
| 4 | OVS / OVSDB adapter                            | Manage local OVS through adapter              |
| 5 | Desired state, planner, reconciler             | Real SDN behaviour, not a command panel       |
| 6 | IPAM                                           | Controller owns addressing                    |
| 7 | DHCP / DNS / NAT / firewall                    | Edge node serves tenant networks              |
| 8 | Topology & drift                               | See and correct the real picture              |
| 9 | Security (auth, mTLS, RBAC)                    | No "remote root via API"                      |
| 10| Observability (metrics, logs, audit)           | Understand what is happening                  |
| 11| Backup / restore                               | Recoverable after failure                     |
| 12| CLI                                            | Operate without UI                            |
| 13| Production readiness + testum contract         | HA, probes, outbox, webhooks, snapshot reconciliation |

### Milestone 1 — application skeleton

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-001  | Repo & port/adapter layout, `/health`, baseline tooling|
| SDN-002  | SQLAlchemy adapter + SQLite (MVP); Alembic migrations  |
| SDN-003  | OpenAPI contract                                       |
| SDN-004  | Operation model (accepted → … → succeeded/failed)      |

> **Persistence choice for MVP.** SQLite via SQLAlchemy is the default backend:
> one file, no extra service, full SQL semantics, Alembic for migrations.
> PostgreSQL is the planned upgrade path (same SQLAlchemy adapter, different
> URL) once we hit any of: multi-writer concurrency, multi-tenant scale, audit
> retention with rich queries, or HA needs. The repository ports do not change.

### Milestone 2 — node inventory & enrollment

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-005  | `Node` entity (id, mgmt_ip, status, roles, labels, capabilities) |
| SDN-006  | One-shot enrollment tokens                             |
| SDN-007  | Agent heartbeat → online / stale / offline             |

### Milestone 3 — NetOS Agent

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-008  | Agent skeleton (`/healthz`, `/readyz`, `/v1/node/...`) |
| SDN-009  | OVS state collector (bridges, ports, ifaces, hash)     |
| SDN-010  | `ApplyPlan` endpoint (idempotent, structured results)  |
| SDN-011  | Snapshot / rollback                                    |

### Milestone 4 — OVS / OVSDB adapter

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-012  | OVSDB adapter (bridge/port/external_ids/snapshot)      |
| SDN-013  | VLAN support (access/trunk/native/allowed)             |
| SDN-014  | VXLAN support (vni, remote_ip, dst_port, mtu, mesh)    |

### Milestone 5 — desired state, planner, reconciler

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-015  | Versioned desired state (`intent_version`, `spec_hash`)|
| SDN-016  | Actual state storage (`actual_hash`, `observed_at`)    |
| SDN-017  | Diff engine (desired vs actual → plan)                 |
| SDN-018  | Planner (global → per-node → per-service)              |
| SDN-019  | Reconciler loop (observe → diff → plan → apply → verify → audit) |

### Milestone 6 — IPAM

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-020  | Prefix model (cidr, gateway, reserved, pools)          |
| SDN-021  | Dynamic allocation, reservation, release, owner_ref    |

### Milestone 7 — DHCP / DNS / NAT / firewall

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-022  | DHCP adapter (dnsmasq first, Kea later)                |
| SDN-023  | DNS adapter (CoreDNS zone files)                       |
| SDN-024  | NAT adapter (nftables, atomic apply)                   |
| SDN-025  | Firewall policy adapter (default deny, isolation, sets)|

### Milestone 8 — topology & drift

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-026  | `GET /topology` (nodes, bridges, ports, networks, edges)|
| SDN-027  | Drift detection (bridge/port/vlan/vni/scope/record)    |

### Milestone 9 — security

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-028  | Northbound auth (Bearer/service token, OIDC later)     |
| SDN-029  | Agent mTLS (cert pinning, rotation)                    |
| SDN-030  | RBAC (admin / network_operator / viewer / automation)  |

### Milestone 10 — observability

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-031  | Prometheus metrics                                     |
| SDN-032  | Structured logs (operation_id, node_id propagation)    |
| SDN-033  | Immutable audit events                                 |

### Milestone 11 — backup / restore

| Task     | Description                                            |
|----------|--------------------------------------------------------|
| SDN-034  | Controller export/import bundles                       |
| SDN-035  | Node snapshots (OVSDB, nftables, dhcp, dns, agent cfg) |

### Milestone 12 — CLI

```bash
sdnctl nodes list
sdnctl nodes enroll-token
sdnctl networks create prod --type vxlan --vni 10100 --cidr 10.100.0.0/24
sdnctl networks apply prod
sdnctl topology
sdnctl drift scan
sdnctl operations watch op_01HX
```

### Milestone 13 — production readiness + testum integration contract

Закрывает разрыв между «работает у меня» и «работает в проде», и
готовит nervum к интеграции с внешним platform-orchestrator'ом
(`testum`): он ходит к нам по REST, мы шлём ему webhook'и об
operations и audit-событиях, после downtime он догоняет нас через
snapshot/export. CLI остаётся для людей и emergency.

| Task     | Description                                                              |
|----------|--------------------------------------------------------------------------|
| SDN-036  | HTTPS из коробки (TLS-listener в uvicorn, HSTS, опц. HTTP→HTTPS redirect)|
| SDN-037  | HA-friendly per-network operation lock (Postgres advisory / sqlite-row) |
| SDN-038  | Background reconciler loop + heartbeat reaper                            |
| SDN-039  | Health probes `/livez` vs `/readyz` (БД-ping, готовность bg-tasks)       |
| SDN-040  | Retention `operations`/`audit_events` + archive port (noop/file/S3)      |
| SDN-041  | OpenTelemetry export (OTLP) для HTTP / БД / agent-вызовов                |
| SDN-042  | Token-bucket rate-limit per principal на mutating endpoint'ы             |
| SDN-043  | SecretStore port + adapters (env / file / Vault); bootstrap-токен оттуда |
| SDN-054  | Webhook subscriptions (CRUD), HMAC-SHA256 подпись, retry + dead-letter   |
| SDN-055  | Transactional outbox + bg-dispatcher (monotonic event_id, at-least-once) |
| SDN-056  | Correlation IDs: `X-Source-Task-Id` → `actor=testum:<task_id>`           |
| SDN-057  | `GET /api/v1/export/snapshot?since_event_id=N` для reconciliation        |
| SDN-058  | `docs/integrations/testum.md` — границы, контракт, sequence-диаграммы    |

**Roles after M13**

* `testum` — глобальная platform-плоскость: пользователи, RBAC платформы,
  SSH-provisioning узлов, GitOps, web UI, backups, host-snapshots.
* `nervum` — специализированный SDN control-plane: declarative
  networks, IPAM, planner / reconciler / drift / operations.
* **stop-list**: testum никогда не правит OVS / nftables / dnsmasq /
  CoreDNS напрямую через SSH — только через REST API nervum'а.

---

## 7. Implementation order

1.  Application skeleton
2.  PostgreSQL + migrations
3.  OpenAPI + operation model
4.  Node inventory
5.  Enrollment
6.  NetOS Agent skeleton
7.  OVS state collector
8.  OVS apply plan
9.  VLAN
10. VXLAN
11. Desired / actual state
12. Diff engine
13. Planner
14. Reconciler
15. IPAM
16. DHCP
17. DNS
18. NAT
19. Firewall
20. Topology
21. Drift
22. Auth / RBAC
23. mTLS
24. Metrics / logs / audit
25. Backup / restore
26. CLI
27. HA

---

## 8. Minimal production-ready definition

A first serious release is ready when all of the following hold:

- SDN Controller is a standalone service with ports & adapters
- REST API with OpenAPI + asynchronous operations
- Node enrollment + NetOS Agent
- OVSDB / OVS management through the agent (VLAN and VXLAN)
- Versioned desired & actual state with a reconciler
- IPAM, DHCP, DNS, NAT, firewall isolation
- Topology API + drift detection
- Audit log, Prometheus metrics
- Backup / restore

End-to-end smoke scenario:

```text
1. Enroll 3 NetOS nodes.
2. Create a VXLAN network.
3. Attach a subnet.
4. Enable DHCP / DNS / NAT.
5. Attach a VM tap port.
6. Verify connectivity.
7. Show topology.
8. Detect drift.
9. Fix drift through reconcile.
10. Roll back a failed change.
```

---

## 9. Tagline

> We are building a **declarative SDN controller** — versioned desired state,
> port/adapter architecture, northbound REST API, southbound NetOS Agent API,
> reconciler mechanics, and IPAM / DHCP / DNS / NAT / firewall integration —
> not a wrapper around `ovs-vsctl`.
