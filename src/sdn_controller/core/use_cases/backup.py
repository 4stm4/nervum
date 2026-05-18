"""Bundle export/import (SDN-034).

Bundle — это JSON-снимок всех «восстановимых» агрегатов: сети + сабнеты,
узлы, сервисные учётки, IPAM-аллокации, аудит-журнал. Это **не**
включает секреты, которые после восстановления нельзя предъявить
клиенту (plaintext'ы enrollment-токенов или service-токенов), и
эфемерные наблюдения (``observed_state`` — обновится при следующем
``apply``).

Импорт идёт в фиксированном порядке зависимости:
1. ``service_accounts`` (учётки не имеют внешних FK);
2. ``nodes``;
3. ``networks`` (вместе с вложенными сабнетами);
4. ``ip_allocations`` (ссылаются на ``subnet_id``);
5. ``audit_events`` (сюда нам важно сохранить историю экспортированной
   системы).

Конфликты (дубликаты по id или name) импорт превращает в
``ConflictError`` — это намеренно консервативно: M11 поддерживает
disaster-recovery поверх пустой БД; merge-import — это уже отдельный
милстоун.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sdn_controller.core.entities import (
    AuditEvent,
    IpAllocation,
    Network,
    Node,
    ServiceAccount,
    Subnet,
)
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.edge_services import (
    DhcpSpec,
    FirewallAction,
    FirewallPolicy,
    FirewallProto,
    FirewallRule,
    NatSpec,
)
from sdn_controller.core.value_objects.enums import NetworkType, NodeStatus
from sdn_controller.core.value_objects.errors import ConflictError, ValidationError
from sdn_controller.core.value_objects.ids import (
    AuditEventId,
    IpAllocationId,
    NetworkId,
    NodeId,
    ServiceAccountId,
    SubnetId,
)
from sdn_controller.core.value_objects.ipam import (
    IpAllocationKind,
    IpRange,
    OwnerRef,
)
from sdn_controller.core.value_objects.security import Role
from sdn_controller.ports.persistence import (
    AuditEventRepository,
    IpAllocationRepository,
    NetworkRepository,
    NodeRepository,
    ServiceAccountRepository,
)

# Версия схемы bundle'а. Меняем при breaking-изменениях формата —
# импорт со старой версией должен либо отказаться, либо иметь
# конвертер (когда мигрируем, опишем в этом же модуле).
BUNDLE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class BundleManifest:
    schema_version: int
    created_at: datetime
    controller_version: str


@dataclass(frozen=True, slots=True)
class Bundle:
    manifest: BundleManifest
    networks: list[Network] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    service_accounts: list[ServiceAccount] = field(default_factory=list)
    ip_allocations: list[IpAllocation] = field(default_factory=list)
    audit_events: list[AuditEvent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


class ExportBundle:
    """Снять снимок всего что нужно восстановить."""

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        nodes: NodeRepository,
        service_accounts: ServiceAccountRepository,
        ip_allocations: IpAllocationRepository,
        audit_events: AuditEventRepository,
        clock: Clock,
        controller_version: str,
    ) -> None:
        self._networks = networks
        self._nodes = nodes
        self._accounts = service_accounts
        self._allocations = ip_allocations
        self._audit = audit_events
        self._clock = clock
        self._version = controller_version

    async def execute(self) -> Bundle:
        networks = await self._networks.list()
        nodes = await self._nodes.list()
        accounts = await self._accounts.list()
        allocations: list[IpAllocation] = []
        for n in networks:
            if n.subnet is not None:
                allocations.extend(await self._allocations.list_for_subnet(n.subnet.id))
        # Лента аудита: до 10 000 последних. Поднимем явный лимит,
        # чтобы bundle оставался разумного размера на крупных кластерах.
        audit = await self._audit.list(limit=10_000)
        return Bundle(
            manifest=BundleManifest(
                schema_version=BUNDLE_SCHEMA_VERSION,
                created_at=self._clock.now(),
                controller_version=self._version,
            ),
            networks=networks,
            nodes=nodes,
            service_accounts=accounts,
            ip_allocations=allocations,
            audit_events=audit,
        )


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImportSummary:
    networks: int
    nodes: int
    service_accounts: int
    ip_allocations: int
    audit_events: int


class ImportBundle:
    """Восстановить снимок поверх **пустой** БД.

    Если встречает существующий объект с тем же id — бросает
    ``ConflictError`` (merge-стратегия выходит за рамки M11).
    """

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        nodes: NodeRepository,
        service_accounts: ServiceAccountRepository,
        ip_allocations: IpAllocationRepository,
        audit_events: AuditEventRepository,
    ) -> None:
        self._networks = networks
        self._nodes = nodes
        self._accounts = service_accounts
        self._allocations = ip_allocations
        self._audit = audit_events

    async def execute(self, bundle: Bundle) -> ImportSummary:
        if bundle.manifest.schema_version != BUNDLE_SCHEMA_VERSION:
            raise ValidationError(
                f"bundle schema_version {bundle.manifest.schema_version} "
                f"is not supported (controller expects {BUNDLE_SCHEMA_VERSION})",
            )

        for account in bundle.service_accounts:
            if await self._accounts.get(account.id) is not None:
                raise ConflictError(f"service_account {account.id} already exists")
            await self._accounts.save(account)

        for node in bundle.nodes:
            if await self._nodes.get(node.id) is not None:
                raise ConflictError(f"node {node.id} already exists")
            await self._nodes.save(node)

        for network in bundle.networks:
            if await self._networks.get(network.id) is not None:
                raise ConflictError(f"network {network.id} already exists")
            await self._networks.save(network)

        for allocation in bundle.ip_allocations:
            if await self._allocations.get(allocation.id) is not None:
                raise ConflictError(f"ip_allocation {allocation.id} already exists")
            await self._allocations.save(allocation)

        for event in bundle.audit_events:
            await self._audit.save(event)

        return ImportSummary(
            networks=len(bundle.networks),
            nodes=len(bundle.nodes),
            service_accounts=len(bundle.service_accounts),
            ip_allocations=len(bundle.ip_allocations),
            audit_events=len(bundle.audit_events),
        )


# ---------------------------------------------------------------------------
# Сериализация ↔ JSON
# ---------------------------------------------------------------------------
#
# bundle.to_dict() / from_dict() лежат на уровне use case'ов, а не в
# HTTP-адаптере, чтобы CLI и фоновые задачи могли работать с тем же
# форматом без дублирования.


def bundle_to_dict(bundle: Bundle) -> dict[str, Any]:
    return {
        "manifest": _manifest_to_dict(bundle.manifest),
        "service_accounts": [_account_to_dict(a) for a in bundle.service_accounts],
        "nodes": [_node_to_dict(n) for n in bundle.nodes],
        "networks": [_network_to_dict(n) for n in bundle.networks],
        "ip_allocations": [_allocation_to_dict(a) for a in bundle.ip_allocations],
        "audit_events": [_audit_to_dict(e) for e in bundle.audit_events],
    }


def bundle_from_dict(raw: dict[str, Any]) -> Bundle:
    manifest_raw = raw.get("manifest")
    if not isinstance(manifest_raw, dict):
        raise ValidationError("bundle: manifest missing or malformed")
    return Bundle(
        manifest=_manifest_from_dict(manifest_raw),
        service_accounts=[_account_from_dict(d) for d in raw.get("service_accounts", [])],
        nodes=[_node_from_dict(d) for d in raw.get("nodes", [])],
        networks=[_network_from_dict(d) for d in raw.get("networks", [])],
        ip_allocations=[_allocation_from_dict(d) for d in raw.get("ip_allocations", [])],
        audit_events=[_audit_from_dict(d) for d in raw.get("audit_events", [])],
    )


# ---- helpers (per-aggregate) ----------------------------------------------


def _manifest_to_dict(manifest: BundleManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "created_at": manifest.created_at.isoformat(),
        "controller_version": manifest.controller_version,
    }


def _manifest_from_dict(raw: dict[str, Any]) -> BundleManifest:
    try:
        return BundleManifest(
            schema_version=int(raw["schema_version"]),
            created_at=datetime.fromisoformat(str(raw["created_at"])),
            controller_version=str(raw["controller_version"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError(f"bundle manifest malformed: {exc}") from exc


def _account_to_dict(a: ServiceAccount) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "role": a.role.value,
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
        "created_by": a.created_by,
        "description": a.description,
        "disabled_at": a.disabled_at.isoformat() if a.disabled_at is not None else None,
        "labels": dict(a.labels),
    }


def _account_from_dict(d: dict[str, Any]) -> ServiceAccount:
    return ServiceAccount(
        id=ServiceAccountId(str(d["id"])),
        name=str(d["name"]),
        role=Role(d["role"]),
        created_at=datetime.fromisoformat(str(d["created_at"])),
        updated_at=datetime.fromisoformat(str(d["updated_at"])),
        created_by=d.get("created_by"),
        description=d.get("description"),
        disabled_at=(
            datetime.fromisoformat(str(d["disabled_at"]))
            if d.get("disabled_at") is not None
            else None
        ),
        labels=dict(d.get("labels") or {}),
    )


def _node_to_dict(n: Node) -> dict[str, Any]:
    return {
        "id": n.id,
        "name": n.name,
        "mgmt_ip": n.mgmt_ip,
        "status": n.status.value,
        "roles": list(n.roles),
        "labels": dict(n.labels),
        "agent_version": n.agent_version,
        "last_seen_at": n.last_seen_at.isoformat() if n.last_seen_at is not None else None,
        "capabilities": _capabilities_to_dict(n.capabilities),
        "tls_thumbprint": n.tls_thumbprint,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
    }


def _node_from_dict(d: dict[str, Any]) -> Node:
    return Node(
        id=NodeId(str(d["id"])),
        name=str(d["name"]),
        mgmt_ip=str(d["mgmt_ip"]),
        status=NodeStatus(d["status"]),
        roles=list(d.get("roles") or []),
        labels=dict(d.get("labels") or {}),
        agent_version=d.get("agent_version"),
        last_seen_at=(
            datetime.fromisoformat(str(d["last_seen_at"]))
            if d.get("last_seen_at") is not None
            else None
        ),
        capabilities=_capabilities_from_dict(d.get("capabilities")),
        tls_thumbprint=d.get("tls_thumbprint"),
        created_at=datetime.fromisoformat(str(d["created_at"])),
        updated_at=datetime.fromisoformat(str(d["updated_at"])),
    )


def _capabilities_to_dict(caps: NodeCapabilities | None) -> dict[str, Any] | None:
    if caps is None:
        return None
    return {
        "ovs_version": caps.ovs_version,
        "kernel": caps.kernel,
        "interfaces": list(caps.interfaces),
        "features": list(caps.features),
    }


def _capabilities_from_dict(raw: Any) -> NodeCapabilities | None:
    if not isinstance(raw, dict):
        return None
    return NodeCapabilities(
        ovs_version=raw.get("ovs_version"),
        kernel=raw.get("kernel"),
        interfaces=tuple(raw.get("interfaces") or ()),
        features=tuple(raw.get("features") or ()),
    )


def _network_to_dict(n: Network) -> dict[str, Any]:
    return {
        "id": n.id,
        "name": n.name,
        "type": n.type.value,
        "created_at": n.created_at.isoformat(),
        "updated_at": n.updated_at.isoformat(),
        "mtu": n.mtu,
        "vlan_id": n.vlan_id,
        "vni": n.vni,
        "subnet": _subnet_to_dict(n.subnet) if n.subnet is not None else None,
        "labels": dict(n.labels),
        "intent_version": n.intent_version,
        "node_ids": list(n.node_ids),
        "nat": _nat_to_dict(n.nat),
        "firewall_policy": _firewall_to_dict(n.firewall_policy),
        "spec_hash": n.spec_hash,
    }


def _network_from_dict(d: dict[str, Any]) -> Network:
    subnet_raw = d.get("subnet")
    return Network(
        id=NetworkId(str(d["id"])),
        name=str(d["name"]),
        type=NetworkType(d["type"]),
        created_at=datetime.fromisoformat(str(d["created_at"])),
        updated_at=datetime.fromisoformat(str(d["updated_at"])),
        mtu=int(d.get("mtu", 1500)),
        vlan_id=d.get("vlan_id"),
        vni=d.get("vni"),
        subnet=_subnet_from_dict(subnet_raw) if subnet_raw is not None else None,
        intent_version=int(d.get("intent_version", 1)),
        labels=dict(d.get("labels") or {}),
        node_ids=tuple(NodeId(s) for s in d.get("node_ids") or ()),
        nat=_nat_from_dict(d.get("nat")),
        firewall_policy=_firewall_from_dict(d.get("firewall_policy")),
        spec_hash=str(d.get("spec_hash", "")),
    )


def _subnet_to_dict(s: Subnet) -> dict[str, Any]:
    return {
        "id": s.id,
        "cidr": s.cidr,
        "gateway": s.gateway,
        "dns_servers": list(s.dns_servers),
        "allocation_pools": [asdict(r) for r in s.allocation_pools],
        "reserved_ranges": [asdict(r) for r in s.reserved_ranges],
        "dhcp": _dhcp_to_dict(s.dhcp),
        "dns_zone": s.dns_zone,
    }


def _subnet_from_dict(d: dict[str, Any]) -> Subnet:
    return Subnet(
        id=SubnetId(str(d["id"])),
        cidr=str(d["cidr"]),
        gateway=d.get("gateway"),
        dns_servers=tuple(d.get("dns_servers") or ()),
        allocation_pools=tuple(
            IpRange(start=str(r["start"]), end=str(r["end"]))
            for r in d.get("allocation_pools") or ()
        ),
        reserved_ranges=tuple(
            IpRange(start=str(r["start"]), end=str(r["end"]))
            for r in d.get("reserved_ranges") or ()
        ),
        dhcp=_dhcp_from_dict(d.get("dhcp")),
        dns_zone=d.get("dns_zone"),
    )


def _dhcp_to_dict(dhcp: DhcpSpec | None) -> dict[str, Any] | None:
    if dhcp is None:
        return None
    return {
        "range_start": dhcp.range_start,
        "range_end": dhcp.range_end,
        "lease_time_seconds": dhcp.lease_time_seconds,
        "domain_name": dhcp.domain_name,
    }


def _dhcp_from_dict(raw: Any) -> DhcpSpec | None:
    if not isinstance(raw, dict):
        return None
    return DhcpSpec(
        range_start=str(raw["range_start"]),
        range_end=str(raw["range_end"]),
        lease_time_seconds=int(raw.get("lease_time_seconds", 3600)),
        domain_name=raw.get("domain_name"),
    )


def _nat_to_dict(nat: NatSpec | None) -> dict[str, Any] | None:
    if nat is None:
        return None
    return {"egress_interface": nat.egress_interface}


def _nat_from_dict(raw: Any) -> NatSpec | None:
    if not isinstance(raw, dict):
        return None
    return NatSpec(egress_interface=str(raw["egress_interface"]))


def _firewall_to_dict(fw: FirewallPolicy | None) -> dict[str, Any] | None:
    if fw is None:
        return None
    return {
        "default_action": fw.default_action.value,
        "rules": [
            {
                "action": r.action.value,
                "proto": r.proto.value,
                "source_cidr": r.source_cidr,
                "destination_cidr": r.destination_cidr,
                "destination_port_start": r.destination_port_start,
                "destination_port_end": r.destination_port_end,
            }
            for r in fw.rules
        ],
    }


def _firewall_from_dict(raw: Any) -> FirewallPolicy | None:
    if not isinstance(raw, dict):
        return None
    return FirewallPolicy(
        default_action=FirewallAction(raw.get("default_action", "drop")),
        rules=tuple(
            FirewallRule(
                action=FirewallAction(r.get("action", "accept")),
                proto=FirewallProto(r.get("proto", "any")),
                source_cidr=r.get("source_cidr"),
                destination_cidr=r.get("destination_cidr"),
                destination_port_start=r.get("destination_port_start"),
                destination_port_end=r.get("destination_port_end"),
            )
            for r in raw.get("rules") or ()
        ),
    )


def _allocation_to_dict(a: IpAllocation) -> dict[str, Any]:
    return {
        "id": a.id,
        "subnet_id": a.subnet_id,
        "ip_address": a.ip_address,
        "owner": {"type": a.owner.type, "id": a.owner.id},
        "kind": a.kind.value,
        "allocated_at": a.allocated_at.isoformat(),
        "label": a.label,
    }


def _allocation_from_dict(d: dict[str, Any]) -> IpAllocation:
    owner_raw = d.get("owner") or {}
    return IpAllocation(
        id=IpAllocationId(str(d["id"])),
        subnet_id=SubnetId(str(d["subnet_id"])),
        ip_address=str(d["ip_address"]),
        owner=OwnerRef(type=str(owner_raw["type"]), id=str(owner_raw["id"])),
        kind=IpAllocationKind(d.get("kind", "dynamic")),
        allocated_at=datetime.fromisoformat(str(d["allocated_at"])),
        label=d.get("label"),
    )


def _audit_to_dict(e: AuditEvent) -> dict[str, Any]:
    return {
        "id": e.id,
        "at": e.at.isoformat(),
        "action": e.action,
        "resource_type": e.resource_type,
        "resource_id": e.resource_id,
        "actor": e.actor,
        "http_status": e.http_status,
        "request_id": e.request_id,
        "payload": dict(e.payload),
    }


def _audit_from_dict(d: dict[str, Any]) -> AuditEvent:
    return AuditEvent(
        id=AuditEventId(str(d["id"])),
        at=datetime.fromisoformat(str(d["at"])),
        action=str(d["action"]),
        resource_type=str(d["resource_type"]),
        resource_id=d.get("resource_id"),
        actor=d.get("actor"),
        http_status=d.get("http_status"),
        request_id=d.get("request_id"),
        payload=dict(d.get("payload") or {}),
    )


__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "Bundle",
    "BundleManifest",
    "ExportBundle",
    "ImportBundle",
    "ImportSummary",
    "bundle_from_dict",
    "bundle_to_dict",
]
