"""Unit tests for N1 entities and use cases.

Covers: LogicalPort, SecurityGroup, AddressPool, ServiceObject, QosPolicy,
Node maintenance mode.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.adapters.memory import (
    InMemoryAddressPoolRepository,
    InMemoryLogicalPortRepository,
    InMemoryNetworkRepository,
    InMemoryNodeRepository,
    InMemoryOutboxRepository,
    InMemoryQosPolicyRepository,
    InMemorySecurityGroupMemberRepository,
    InMemorySecurityGroupRepository,
    InMemoryServiceObjectRepository,
)
from sdn_controller.core.entities import (
    AddressPool,
    LogicalPort,
    QosPolicy,
    SecurityGroup,
    SecurityGroupMember,
    ServiceObject,
)
from sdn_controller.core.entities.node import Node
from sdn_controller.core.services.event_publisher import EventPublisher
from sdn_controller.core.use_cases.n1 import (
    AddSecurityGroupMember,
    AttachLogicalPort,
    CreateAddressPool,
    CreateAddressPoolCommand,
    CreateLogicalPort,
    CreateLogicalPortCommand,
    CreateQosPolicy,
    CreateQosPolicyCommand,
    CreateSecurityGroup,
    CreateSecurityGroupCommand,
    CreateServiceObject,
    CreateServiceObjectCommand,
    DeleteAddressPool,
    DeleteLogicalPort,
    DeleteQosPolicy,
    DeleteSecurityGroup,
    DeleteServiceObject,
    DetachLogicalPort,
    EnterMaintenanceMode,
    ExitMaintenanceMode,
    GetLogicalPort,
    ListLogicalPorts,
    ListSecurityGroupMembers,
    RemoveSecurityGroupMember,
    UpdateAddressPool,
    UpdateAddressPoolCommand,
    UpdateLogicalPort,
    UpdateLogicalPortCommand,
    UpdateQosPolicy,
    UpdateQosPolicyCommand,
    UpdateSecurityGroup,
    UpdateSecurityGroupCommand,
    UpdateServiceObject,
    UpdateServiceObjectCommand,
)
from sdn_controller.core.value_objects.enums import LogicalPortStatus, NodeStatus
from sdn_controller.core.value_objects.errors import NotFoundError, ValidationError
from sdn_controller.core.value_objects.ids import (
    AddressPoolId,
    LogicalPortId,
    NetworkId,
    NodeId,
    ProjectId,
    QosPolicyId,
    SecurityGroupId,
    ServiceObjectId,
)

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeClock:
    def now(self) -> datetime:
        return _NOW


def _make_node(node_id: str = "node_1") -> Node:
    return Node(
        id=NodeId(node_id),
        name="edge-1",
        mgmt_ip="10.0.0.1",
        created_at=_NOW,
        updated_at=_NOW,
        status=NodeStatus.ONLINE,
        last_seen_at=_NOW,
    )


def _make_network_repo() -> InMemoryNetworkRepository:
    return InMemoryNetworkRepository()


def _make_outbox() -> InMemoryOutboxRepository:
    return InMemoryOutboxRepository()


class _FakeIds:
    _lport = 0
    _sg = 0
    _apool = 0
    _svcobj = 0
    _qos = 0

    def logical_port(self) -> LogicalPortId:
        self._lport += 1
        return LogicalPortId(f"lport_{self._lport}")

    def security_group(self) -> SecurityGroupId:
        self._sg += 1
        return SecurityGroupId(f"sg_{self._sg}")

    def address_pool(self) -> AddressPoolId:
        self._apool += 1
        return AddressPoolId(f"apool_{self._apool}")

    def service_object(self) -> ServiceObjectId:
        self._svcobj += 1
        return ServiceObjectId(f"svcobj_{self._svcobj}")

    def qos_policy(self) -> QosPolicyId:
        self._qos += 1
        return QosPolicyId(f"qos_{self._qos}")

    def outbox_event(self) -> str:
        return "outbox_1"

    def node(self) -> NodeId:
        return NodeId("node_1")


# ---------------------------------------------------------------------------
# LogicalPort entity
# ---------------------------------------------------------------------------


def test_logical_port_initial_status_pending() -> None:
    port = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="eth0",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:ee",
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert port.status is LogicalPortStatus.PENDING
    assert port.vif_id is None


def test_logical_port_attach_transitions_to_active() -> None:
    port = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="eth0",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:ee",
        created_at=_NOW,
        updated_at=_NOW,
    )
    port.attach(vif_id="vif-99", now=_NOW)
    assert port.status is LogicalPortStatus.ACTIVE
    assert port.vif_id == "vif-99"


def test_logical_port_detach_transitions_to_detached() -> None:
    port = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="eth0",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:ee",
        created_at=_NOW,
        updated_at=_NOW,
        status=LogicalPortStatus.ACTIVE,
        vif_id="vif-99",
    )
    port.detach(now=_NOW)
    assert port.status is LogicalPortStatus.DETACHED
    assert port.vif_id is None


def test_logical_port_invalid_mac_raises() -> None:
    with pytest.raises(ValidationError, match="mac_address"):
        LogicalPort(
            id=LogicalPortId("lport_1"),
            name="eth0",
            node_id=NodeId("n1"),
            network_id=NetworkId("net1"),
            mac_address="bad-mac",
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_logical_port_update_name_and_labels() -> None:
    port = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="old-name",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:ee",
        created_at=_NOW,
        updated_at=_NOW,
    )
    port.update(name="new-name", labels={"k": "v"}, now=_NOW)
    assert port.name == "new-name"
    assert port.labels == {"k": "v"}


# ---------------------------------------------------------------------------
# AddressPool entity
# ---------------------------------------------------------------------------


def test_address_pool_valid_cidr() -> None:
    pool = AddressPool(
        id=AddressPoolId("apool_1"),
        name="prod-pool",
        cidrs=("10.0.0.0/24", "192.168.0.0/16"),
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert len(pool.cidrs) == 2


def test_address_pool_invalid_cidr_raises() -> None:
    with pytest.raises(ValidationError, match="cidr"):
        AddressPool(
            id=AddressPoolId("apool_1"),
            name="bad",
            cidrs=("not-a-cidr",),
            created_at=_NOW,
            updated_at=_NOW,
        )


# ---------------------------------------------------------------------------
# ServiceObject entity
# ---------------------------------------------------------------------------


def test_service_object_tcp_with_ports() -> None:
    obj = ServiceObject(
        id=ServiceObjectId("svcobj_1"),
        name="http",
        protocol="tcp",
        ports=("80", "443", "8000-8080"),
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert obj.protocol == "tcp"
    assert "80" in obj.ports


def test_service_object_icmp_no_ports() -> None:
    obj = ServiceObject(
        id=ServiceObjectId("svcobj_1"),
        name="ping",
        protocol="icmp",
        ports=(),
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert obj.protocol == "icmp"


def test_service_object_icmp_with_ports_raises() -> None:
    with pytest.raises(ValidationError, match="ports.*icmp|icmp.*ports"):
        ServiceObject(
            id=ServiceObjectId("svcobj_1"),
            name="ping",
            protocol="icmp",
            ports=("80",),
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_service_object_invalid_port_range_raises() -> None:
    with pytest.raises(ValidationError):
        ServiceObject(
            id=ServiceObjectId("svcobj_1"),
            name="bad",
            protocol="tcp",
            ports=("9000-8000",),  # lo > hi
            created_at=_NOW,
            updated_at=_NOW,
        )


# ---------------------------------------------------------------------------
# QosPolicy entity
# ---------------------------------------------------------------------------


def test_qos_policy_valid() -> None:
    q = QosPolicy(
        id=QosPolicyId("qos_1"),
        name="gold",
        ingress_kbps=10000,
        egress_kbps=5000,
        burst_kb=1000,
        dscp=46,
        created_at=_NOW,
        updated_at=_NOW,
    )
    assert q.dscp == 46


def test_qos_policy_dscp_out_of_range_raises() -> None:
    with pytest.raises(ValidationError, match="dscp"):
        QosPolicy(
            id=QosPolicyId("qos_1"),
            name="bad",
            dscp=64,
            created_at=_NOW,
            updated_at=_NOW,
        )


def test_qos_policy_negative_kbps_raises() -> None:
    with pytest.raises(ValidationError):
        QosPolicy(
            id=QosPolicyId("qos_1"),
            name="bad",
            ingress_kbps=-1,
            created_at=_NOW,
            updated_at=_NOW,
        )


# ---------------------------------------------------------------------------
# Node maintenance mode
# ---------------------------------------------------------------------------


def test_node_enter_maintenance() -> None:
    node = _make_node()
    assert node.maintenance is False
    node.enter_maintenance(now=_NOW)
    assert node.maintenance is True
    assert node.maintenance_at == _NOW


def test_node_exit_maintenance() -> None:
    node = _make_node()
    node.enter_maintenance(now=_NOW)
    node.exit_maintenance(now=_NOW)
    assert node.maintenance is False
    assert node.maintenance_at is None


# ---------------------------------------------------------------------------
# CreateLogicalPort use case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_logical_port_requires_valid_node() -> None:
    ports = InMemoryLogicalPortRepository()
    nodes = InMemoryNodeRepository()
    networks = _make_network_repo()
    outbox = _make_outbox()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=_FakeIds())  # type: ignore[arg-type]
    uc = CreateLogicalPort(
        ports=ports,
        nodes=nodes,
        networks=networks,
        clock=_FakeClock(),
        ids=_FakeIds(),  # type: ignore[arg-type]
        events=events,
    )

    with pytest.raises(NotFoundError, match="node"):
        await uc.execute(
            CreateLogicalPortCommand(
                name="eth0",
                node_id=NodeId("ghost"),
                network_id=NetworkId("net_1"),
            )
        )


@pytest.mark.anyio
async def test_create_logical_port_happy_path() -> None:
    ports = InMemoryLogicalPortRepository()
    nodes = InMemoryNodeRepository()
    node = _make_node()
    await nodes.save(node)

    networks = _make_network_repo()
    from sdn_controller.core.entities.network import Network
    from sdn_controller.core.value_objects.enums import NetworkType

    net = Network(
        id=NetworkId("net_1"),
        name="default",
        type=NetworkType.FLAT,
        created_at=_NOW,
        updated_at=_NOW,
    )
    await networks.save(net)

    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]
    uc = CreateLogicalPort(
        ports=ports,
        nodes=nodes,
        networks=networks,
        clock=_FakeClock(),
        ids=ids,  # type: ignore[arg-type]
        events=events,
    )

    port = await uc.execute(
        CreateLogicalPortCommand(
            name="eth0",
            node_id=NodeId("node_1"),
            network_id=NetworkId("net_1"),
            mac_address="02:aa:bb:cc:dd:ee",
        )
    )

    assert port.id == LogicalPortId("lport_1")
    assert port.name == "eth0"
    assert port.status is LogicalPortStatus.PENDING
    # outbox must have received an event
    events_list = await outbox.list_undelivered(limit=10)
    assert any(e.event_type == "logical_port.created" for e in events_list)


@pytest.mark.anyio
async def test_attach_detach_logical_port() -> None:
    ports = InMemoryLogicalPortRepository()
    port = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="eth0",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:ee",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await ports.save(port)
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    attach = AttachLogicalPort(ports=ports, clock=_FakeClock(), events=events)
    p = await attach.execute(LogicalPortId("lport_1"), vif_id="vif-42")
    assert p.status is LogicalPortStatus.ACTIVE
    assert p.vif_id == "vif-42"

    detach = DetachLogicalPort(ports=ports, clock=_FakeClock(), events=events)
    p2 = await detach.execute(LogicalPortId("lport_1"))
    assert p2.status is LogicalPortStatus.DETACHED
    assert p2.vif_id is None


@pytest.mark.anyio
async def test_delete_logical_port() -> None:
    ports = InMemoryLogicalPortRepository()
    port = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="eth0",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:ee",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await ports.save(port)
    outbox = _make_outbox()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=_FakeIds())  # type: ignore[arg-type]

    uc = DeleteLogicalPort(ports=ports, events=events)
    await uc.execute(LogicalPortId("lport_1"))
    assert await ports.get(LogicalPortId("lport_1")) is None


# ---------------------------------------------------------------------------
# SecurityGroup use case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_security_group_and_add_member() -> None:
    groups = InMemorySecurityGroupRepository()
    members = InMemorySecurityGroupMemberRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    create_uc = CreateSecurityGroup(
        groups=groups, clock=_FakeClock(), ids=ids, events=events  # type: ignore[arg-type]
    )
    sg = await create_uc.execute(
        CreateSecurityGroupCommand(name="web-sg", description="web servers")
    )
    assert sg.name == "web-sg"

    add_uc = AddSecurityGroupMember(
        groups=groups, members=members, clock=_FakeClock()
    )
    await add_uc.execute(
        sg_id=sg.id, member_type="logical_port", member_value="lport_99"
    )

    list_uc = ListSecurityGroupMembers(members=members)
    ms = await list_uc.execute(sg.id)
    assert len(ms) == 1
    assert ms[0].member_type == "logical_port"


@pytest.mark.anyio
async def test_remove_security_group_member() -> None:
    groups = InMemorySecurityGroupRepository()
    members = InMemorySecurityGroupMemberRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    create_uc = CreateSecurityGroup(
        groups=groups, clock=_FakeClock(), ids=ids, events=events  # type: ignore[arg-type]
    )
    sg = await create_uc.execute(CreateSecurityGroupCommand(name="sg1"))

    add_uc = AddSecurityGroupMember(groups=groups, members=members, clock=_FakeClock())
    await add_uc.execute(sg_id=sg.id, member_type="cidr", member_value="10.0.0.0/8")

    remove_uc = RemoveSecurityGroupMember(groups=groups, members=members)
    await remove_uc.execute(
        sg_id=sg.id, member_type="cidr", member_value="10.0.0.0/8"
    )

    list_uc = ListSecurityGroupMembers(members=members)
    ms = await list_uc.execute(sg.id)
    assert ms == []


@pytest.mark.anyio
async def test_delete_security_group_cascades_members() -> None:
    groups = InMemorySecurityGroupRepository()
    members = InMemorySecurityGroupMemberRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    sg = SecurityGroup(
        id=SecurityGroupId("sg_1"),
        name="sg1",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await groups.save(sg)
    m = SecurityGroupMember(
        sg_id=SecurityGroupId("sg_1"),
        member_type="logical_port",
        member_value="lport_1",
        created_at=_NOW,
    )
    await members.add(m)

    delete_uc = DeleteSecurityGroup(groups=groups, members=members, events=events)
    await delete_uc.execute(SecurityGroupId("sg_1"))

    assert await groups.get(SecurityGroupId("sg_1")) is None
    remaining = await members.list_for_group(SecurityGroupId("sg_1"))
    assert remaining == []


# ---------------------------------------------------------------------------
# AddressPool use case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_and_update_address_pool() -> None:
    pools = InMemoryAddressPoolRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    create_uc = CreateAddressPool(pools=pools, clock=_FakeClock(), ids=ids, events=events)  # type: ignore[arg-type]
    pool = await create_uc.execute(
        CreateAddressPoolCommand(name="prod", cidrs=["10.0.0.0/8"])
    )
    assert pool.name == "prod"
    assert "10.0.0.0/8" in pool.cidrs

    update_uc = UpdateAddressPool(pools=pools, clock=_FakeClock(), events=events)
    updated = await update_uc.execute(
        UpdateAddressPoolCommand(pool_id=pool.id, cidrs=("192.168.0.0/16",))
    )
    assert "192.168.0.0/16" in updated.cidrs


@pytest.mark.anyio
async def test_delete_address_pool() -> None:
    pools = InMemoryAddressPoolRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    pool = AddressPool(
        id=AddressPoolId("apool_1"),
        name="pool1",
        cidrs=("10.0.0.0/24",),
        created_at=_NOW,
        updated_at=_NOW,
    )
    await pools.save(pool)

    delete_uc = DeleteAddressPool(pools=pools, events=events)
    await delete_uc.execute(AddressPoolId("apool_1"))
    # Verify gone
    from sdn_controller.core.use_cases.n1 import GetAddressPool

    get_uc = GetAddressPool(pools=pools)
    with pytest.raises(NotFoundError):
        await get_uc.execute(AddressPoolId("apool_1"))


# ---------------------------------------------------------------------------
# ServiceObject use case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_and_update_service_object() -> None:
    objects = InMemoryServiceObjectRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    create_uc = CreateServiceObject(objects=objects, clock=_FakeClock(), ids=ids, events=events)  # type: ignore[arg-type]
    obj = await create_uc.execute(
        CreateServiceObjectCommand(name="http", protocol="tcp", ports=["80", "443"])
    )
    assert obj.protocol == "tcp"
    assert "80" in obj.ports

    update_uc = UpdateServiceObject(objects=objects, clock=_FakeClock(), events=events)
    updated = await update_uc.execute(
        UpdateServiceObjectCommand(obj_id=obj.id, ports=("8080",))
    )
    assert "8080" in updated.ports


# ---------------------------------------------------------------------------
# QosPolicy use case
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_and_update_qos_policy() -> None:
    policies = InMemoryQosPolicyRepository()
    outbox = _make_outbox()
    ids = _FakeIds()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=ids)  # type: ignore[arg-type]

    create_uc = CreateQosPolicy(policies=policies, clock=_FakeClock(), ids=ids, events=events)  # type: ignore[arg-type]
    qos = await create_uc.execute(
        CreateQosPolicyCommand(name="gold", ingress_kbps=10000, egress_kbps=5000)
    )
    assert qos.ingress_kbps == 10000

    update_uc = UpdateQosPolicy(policies=policies, clock=_FakeClock(), events=events)
    updated = await update_uc.execute(
        UpdateQosPolicyCommand(policy_id=qos.id, dscp=46)
    )
    assert updated.dscp == 46


@pytest.mark.anyio
async def test_delete_qos_policy() -> None:
    policies = InMemoryQosPolicyRepository()
    outbox = _make_outbox()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=_FakeIds())  # type: ignore[arg-type]

    qos = QosPolicy(
        id=QosPolicyId("qos_1"),
        name="bronze",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await policies.save(qos)

    delete_uc = DeleteQosPolicy(policies=policies, events=events)
    await delete_uc.execute(QosPolicyId("qos_1"))

    from sdn_controller.core.use_cases.n1 import GetQosPolicy

    get_uc = GetQosPolicy(policies=policies)
    with pytest.raises(NotFoundError):
        await get_uc.execute(QosPolicyId("qos_1"))


# ---------------------------------------------------------------------------
# Node maintenance use cases
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_enter_and_exit_maintenance_mode() -> None:
    nodes = InMemoryNodeRepository()
    node = _make_node()
    await nodes.save(node)

    outbox = _make_outbox()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=_FakeIds())  # type: ignore[arg-type]

    enter_uc = EnterMaintenanceMode(nodes=nodes, clock=_FakeClock(), events=events)
    await enter_uc.execute(NodeId("node_1"))

    saved = await nodes.get(NodeId("node_1"))
    assert saved is not None
    assert saved.maintenance is True

    exit_uc = ExitMaintenanceMode(nodes=nodes, clock=_FakeClock(), events=events)
    await exit_uc.execute(NodeId("node_1"))

    saved2 = await nodes.get(NodeId("node_1"))
    assert saved2 is not None
    assert saved2.maintenance is False


@pytest.mark.anyio
async def test_enter_maintenance_unknown_node_raises() -> None:
    nodes = InMemoryNodeRepository()
    outbox = _make_outbox()
    events = EventPublisher(outbox=outbox, clock=_FakeClock(), ids=_FakeIds())  # type: ignore[arg-type]

    uc = EnterMaintenanceMode(nodes=nodes, clock=_FakeClock(), events=events)
    with pytest.raises(NotFoundError):
        await uc.execute(NodeId("ghost"))


# ---------------------------------------------------------------------------
# List / filter
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_logical_ports_filter_by_node() -> None:
    ports = InMemoryLogicalPortRepository()
    p1 = LogicalPort(
        id=LogicalPortId("lport_1"),
        name="p1",
        node_id=NodeId("n1"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:01",
        created_at=_NOW,
        updated_at=_NOW,
    )
    p2 = LogicalPort(
        id=LogicalPortId("lport_2"),
        name="p2",
        node_id=NodeId("n2"),
        network_id=NetworkId("net1"),
        mac_address="02:aa:bb:cc:dd:02",
        created_at=_NOW,
        updated_at=_NOW,
    )
    await ports.save(p1)
    await ports.save(p2)

    uc = ListLogicalPorts(ports=ports)
    result = await uc.execute(node_id=NodeId("n1"))
    assert len(result) == 1
    assert result[0].id == LogicalPortId("lport_1")
