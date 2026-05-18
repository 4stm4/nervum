"""Round-trip bundle: export → JSON → import в чистую БД, всё сходится.

Самый ценный тест M11: показывает, что disaster-recovery flow честно
переносит все агрегаты, на которые мы рассчитываем.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from sdn_controller.adapters.memory import (
    InMemoryAuditEventRepository,
    InMemoryIpAllocationRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryServiceAccountRepository,
)
from sdn_controller.core.entities import (
    AuditEvent,
    IpAllocation,
    Network,
    Node,
    ServiceAccount,
    Subnet,
)
from sdn_controller.core.use_cases.backup import (
    BUNDLE_SCHEMA_VERSION,
    Bundle,
    BundleManifest,
    ExportBundle,
    ImportBundle,
    bundle_from_dict,
    bundle_to_dict,
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
from tests.conftest import FrozenClock

_NOW = datetime(2026, 5, 18, 12, 0, 0, tzinfo=UTC)


def _node(node_id: str) -> Node:
    return Node(
        id=NodeId(node_id),
        name=node_id,
        mgmt_ip="10.0.0.1",
        status=NodeStatus.ONLINE,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _network(name: str, *, with_subnet: bool = False) -> Network:
    subnet = None
    if with_subnet:
        subnet = Subnet(
            id=SubnetId(f"sub_{name}"),
            cidr="10.0.0.0/24",
            allocation_pools=(IpRange(start="10.0.0.10", end="10.0.0.250"),),
        )
    return Network(
        id=NetworkId(f"net_{name}"),
        name=name,
        type=NetworkType.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
        subnet=subnet,
    )


def _allocation(network_subnet: SubnetId, ip: str) -> IpAllocation:
    return IpAllocation(
        id=IpAllocationId(f"ipa_{ip}"),
        subnet_id=network_subnet,
        ip_address=ip,
        owner=OwnerRef(type="vm", id="vm-1"),
        kind=IpAllocationKind.DYNAMIC,
        allocated_at=_NOW,
    )


def _account(name: str) -> ServiceAccount:
    return ServiceAccount(
        id=ServiceAccountId(f"sa_{name}"),
        name=name,
        role=Role.AUTOMATION,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _audit(id_: str, action: str) -> AuditEvent:
    return AuditEvent(
        id=AuditEventId(id_),
        at=_NOW,
        action=action,
        resource_type="network",
    )


@pytest.fixture
async def populated_source(
    clock: FrozenClock,
) -> tuple[
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryServiceAccountRepository,
    InMemoryIpAllocationRepository,
    InMemoryAuditEventRepository,
]:
    """Заполнено разными агрегатами; используется как «source кластер»."""
    networks = InMemoryNetworkRepository()
    nodes = InMemoryNodeRepository()
    accounts = InMemoryServiceAccountRepository()
    allocations = InMemoryIpAllocationRepository()
    audit = InMemoryAuditEventRepository()

    await nodes.save(_node("node_a"))
    n = _network("prod", with_subnet=True)
    await networks.save(n)
    assert n.subnet is not None
    await allocations.save(_allocation(n.subnet.id, "10.0.0.42"))
    await accounts.save(_account("ci"))
    await audit.save(_audit("audit_1", "network.create"))
    return networks, nodes, accounts, allocations, audit


async def test_export_serializes_to_json(
    populated_source: tuple[
        InMemoryNetworkRepository,
        InMemoryNodeRepository,
        InMemoryServiceAccountRepository,
        InMemoryIpAllocationRepository,
        InMemoryAuditEventRepository,
    ],
    clock: FrozenClock,
) -> None:
    networks, nodes, accounts, allocations, audit = populated_source
    bundle = await ExportBundle(
        networks=networks,
        nodes=nodes,
        service_accounts=accounts,
        ip_allocations=allocations,
        audit_events=audit,
        clock=clock,
        controller_version="0.1.0",
    ).execute()

    raw = bundle_to_dict(bundle)
    # JSON-сериализация должна проходить без TypeError'ов на datetime/Enum.
    json_payload = json.dumps(raw)
    assert "net_prod" in json_payload
    assert raw["manifest"]["schema_version"] == BUNDLE_SCHEMA_VERSION
    assert len(raw["networks"]) == 1
    assert len(raw["ip_allocations"]) == 1


async def test_export_then_import_into_empty_yields_same_content(
    populated_source: tuple[
        InMemoryNetworkRepository,
        InMemoryNodeRepository,
        InMemoryServiceAccountRepository,
        InMemoryIpAllocationRepository,
        InMemoryAuditEventRepository,
    ],
    clock: FrozenClock,
) -> None:
    networks, nodes, accounts, allocations, audit = populated_source
    bundle = await ExportBundle(
        networks=networks,
        nodes=nodes,
        service_accounts=accounts,
        ip_allocations=allocations,
        audit_events=audit,
        clock=clock,
        controller_version="0.1.0",
    ).execute()

    raw = bundle_to_dict(bundle)
    reloaded = bundle_from_dict(raw)

    # Импортируем в пустые целевые репо.
    target_networks = InMemoryNetworkRepository()
    target_nodes = InMemoryNodeRepository()
    target_accounts = InMemoryServiceAccountRepository()
    target_allocations = InMemoryIpAllocationRepository()
    target_audit = InMemoryAuditEventRepository()
    summary = await ImportBundle(
        networks=target_networks,
        nodes=target_nodes,
        service_accounts=target_accounts,
        ip_allocations=target_allocations,
        audit_events=target_audit,
    ).execute(reloaded)

    assert summary.networks == 1
    assert summary.nodes == 1
    assert summary.service_accounts == 1
    assert summary.ip_allocations == 1
    assert summary.audit_events == 1

    # Целевая БД теперь видит те же id.
    assert (await target_networks.get(NetworkId("net_prod"))) is not None
    assert (await target_nodes.get(NodeId("node_a"))) is not None
    assert (await target_accounts.get(ServiceAccountId("sa_ci"))) is not None


async def test_import_rejects_unknown_schema_version() -> None:
    target_networks = InMemoryNetworkRepository()
    target_nodes = InMemoryNodeRepository()
    target_accounts = InMemoryServiceAccountRepository()
    target_allocations = InMemoryIpAllocationRepository()
    target_audit = InMemoryAuditEventRepository()

    future_bundle = Bundle(
        manifest=BundleManifest(
            schema_version=BUNDLE_SCHEMA_VERSION + 1,
            created_at=_NOW,
            controller_version="0.9.9",
        ),
    )
    with pytest.raises(ValidationError):
        await ImportBundle(
            networks=target_networks,
            nodes=target_nodes,
            service_accounts=target_accounts,
            ip_allocations=target_allocations,
            audit_events=target_audit,
        ).execute(future_bundle)


async def test_import_into_non_empty_raises_conflict(
    populated_source: tuple[
        InMemoryNetworkRepository,
        InMemoryNodeRepository,
        InMemoryServiceAccountRepository,
        InMemoryIpAllocationRepository,
        InMemoryAuditEventRepository,
    ],
    clock: FrozenClock,
) -> None:
    networks, nodes, accounts, allocations, audit = populated_source
    bundle = await ExportBundle(
        networks=networks,
        nodes=nodes,
        service_accounts=accounts,
        ip_allocations=allocations,
        audit_events=audit,
        clock=clock,
        controller_version="0.1.0",
    ).execute()

    # Импорт в ту же БД должен упасть на первом же повторе.
    with pytest.raises(ConflictError):
        await ImportBundle(
            networks=networks,
            nodes=nodes,
            service_accounts=accounts,
            ip_allocations=allocations,
            audit_events=audit,
        ).execute(bundle)
