"""Network use cases — desired-state CRUD.

Creation, update, and assignment of nodes to a network. Each mutation runs
through an ``Operation`` so the external API surface is uniform; the actual
provisioning side (apply state to agents) lives in
``sdn_controller.core.use_cases.reconcile``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sdn_controller.core.entities import (
    Network,
    Operation,
    ResourceRef,
    Subnet,
)
from sdn_controller.core.services.clock import Clock
from sdn_controller.core.value_objects.edge_services import (
    FirewallPolicy,
    NatSpec,
)
from sdn_controller.core.value_objects.enums import (
    NetworkType,
    OperationKind,
    OperationStatus,
)
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import IdFactory, NetworkId, NodeId
from sdn_controller.ports.persistence import (
    NetworkRepository,
    NodeRepository,
    OperationRepository,
)

# ---------------------------------------------------------------------------
# Commands & results
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SubnetSpec:
    cidr: str
    gateway: str | None = None


@dataclass(frozen=True, slots=True)
class CreateNetworkCommand:
    name: str
    type: NetworkType
    mtu: int = 1500
    vlan_id: int | None = None
    vni: int | None = None
    subnet: SubnetSpec | None = None
    labels: dict[str, str] = field(default_factory=dict)
    node_ids: tuple[NodeId, ...] = ()
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkCreated:
    network: Network
    operation: Operation


@dataclass(frozen=True, slots=True)
class UpdateNetworkCommand:
    """Partial-update spec. ``None`` fields mean "leave alone"."""

    mtu: int | None = None
    subnet: SubnetSpec | None = None
    labels: dict[str, str] | None = None
    nat: NatSpec | None = None
    firewall_policy: FirewallPolicy | None = None
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class AssignNodesCommand:
    node_ids: tuple[NodeId, ...]
    updated_by: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkUpdated:
    network: Network
    operation: Operation


# ---------------------------------------------------------------------------
# Use cases
# ---------------------------------------------------------------------------


class CreateNetwork:
    """Persist a new desired-state network record."""

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        operations: OperationRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._networks = networks
        self._operations = operations
        self._clock = clock
        self._ids = ids

    async def execute(self, cmd: CreateNetworkCommand) -> NetworkCreated:
        if not cmd.name or not cmd.name.strip():
            raise ValidationError("network name must be non-empty")

        existing = await self._networks.get_by_name(cmd.name)
        if existing is not None:
            raise ConflictError(f"network with name {cmd.name!r} already exists")

        now = self._clock.now()
        network_id = self._ids.network()
        subnet = (
            Subnet(id=self._ids.subnet(), cidr=cmd.subnet.cidr, gateway=cmd.subnet.gateway)
            if cmd.subnet is not None
            else None
        )

        network = Network(
            id=network_id,
            name=cmd.name.strip(),
            type=cmd.type,
            created_at=now,
            updated_at=now,
            mtu=cmd.mtu,
            vlan_id=cmd.vlan_id,
            vni=cmd.vni,
            subnet=subnet,
            labels=dict(cmd.labels),
            node_ids=tuple(cmd.node_ids),
        )

        operation = Operation.accept(
            operation_id=self._ids.operation(),
            kind=OperationKind.NETWORK_CREATE,
            resource=ResourceRef(type="network", id=network_id),
            now=now,
            created_by=cmd.created_by,
            message=f"create network {network.name!r}",
        )
        # Creating intent is a synchronous record — actual realization to
        # agents goes through ``ApplyNetwork`` (Milestone 5 reconciler).
        for status, message in (
            (OperationStatus.PLANNING, "store desired state"),
            (OperationStatus.RUNNING, "persisting network record"),
            (OperationStatus.VERIFYING, "verifying persisted record"),
            (OperationStatus.SUCCEEDED, "network created"),
        ):
            operation.transition_to(status, now=self._clock.now(), message=message)

        await self._networks.save(network)
        await self._operations.save(operation)
        return NetworkCreated(network=network, operation=operation)


class UpdateNetwork:
    """Apply a partial update to a network's spec.

    Bumps ``intent_version`` + ``spec_hash`` only if something actually
    changed — if every field is ``None`` we return the existing record with
    a succeeded operation but no version bump.
    """

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        operations: OperationRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._networks = networks
        self._operations = operations
        self._clock = clock
        self._ids = ids

    async def execute(self, network_id: NetworkId, cmd: UpdateNetworkCommand) -> NetworkUpdated:
        network = await self._networks.get(network_id)
        if network is None:
            raise NotFoundError(f"network {network_id} not found")

        now = self._clock.now()
        operation = Operation.accept(
            operation_id=self._ids.operation(),
            kind=OperationKind.NETWORK_UPDATE,
            resource=ResourceRef(type="network", id=network_id),
            now=now,
            created_by=cmd.updated_by,
            message=f"update network {network.name!r}",
        )

        changed = self._apply_update(network, cmd)
        if changed:
            network.bump_intent(now=now)

        for status, message in (
            (OperationStatus.PLANNING, "validating update"),
            (OperationStatus.RUNNING, "persisting update"),
            (OperationStatus.VERIFYING, "verifying persisted record"),
            (
                OperationStatus.SUCCEEDED,
                f"network updated (intent_version={network.intent_version})"
                if changed
                else "network unchanged (no-op)",
            ),
        ):
            operation.transition_to(status, now=self._clock.now(), message=message)

        await self._networks.save(network)
        await self._operations.save(operation)
        return NetworkUpdated(network=network, operation=operation)

    def _apply_update(self, network: Network, cmd: UpdateNetworkCommand) -> bool:
        """Mutate ``network`` in place, return whether anything changed."""
        changed = False
        if cmd.mtu is not None and cmd.mtu != network.mtu:
            network.mtu = cmd.mtu
            changed = True
        if cmd.subnet is not None:
            new_subnet = Subnet(
                id=network.subnet.id if network.subnet else self._ids.subnet(),
                cidr=cmd.subnet.cidr,
                gateway=cmd.subnet.gateway,
            )
            if network.subnet is None or (
                network.subnet.cidr != new_subnet.cidr
                or network.subnet.gateway != new_subnet.gateway
            ):
                network.subnet = new_subnet
                changed = True
        if cmd.labels is not None and dict(cmd.labels) != dict(network.labels):
            network.labels = dict(cmd.labels)
            changed = True
        if cmd.nat is not None and cmd.nat != network.nat:
            network.nat = cmd.nat
            changed = True
        if cmd.firewall_policy is not None and cmd.firewall_policy != network.firewall_policy:
            network.firewall_policy = cmd.firewall_policy
            changed = True
        # Re-validate after mutation so invariants don't decay across updates.
        if changed:
            network._validate()
        return changed


class AssignNetworkToNodes:
    """Replace a network's ``node_ids`` list and bump intent.

    Validates that every supplied node exists; doesn't apply state to agents
    (that's ``ApplyNetwork``'s job — operators typically call this then
    apply).
    """

    def __init__(
        self,
        *,
        networks: NetworkRepository,
        nodes: NodeRepository,
        operations: OperationRepository,
        clock: Clock,
        ids: IdFactory,
    ) -> None:
        self._networks = networks
        self._nodes = nodes
        self._operations = operations
        self._clock = clock
        self._ids = ids

    async def execute(self, network_id: NetworkId, cmd: AssignNodesCommand) -> NetworkUpdated:
        network = await self._networks.get(network_id)
        if network is None:
            raise NotFoundError(f"network {network_id} not found")

        for node_id in cmd.node_ids:
            if await self._nodes.get(node_id) is None:
                raise NotFoundError(f"node {node_id} not found")

        now = self._clock.now()
        operation = Operation.accept(
            operation_id=self._ids.operation(),
            kind=OperationKind.NETWORK_UPDATE,
            resource=ResourceRef(type="network", id=network_id),
            now=now,
            created_by=cmd.updated_by,
            message=f"assign nodes to network {network.name!r}: {list(cmd.node_ids)}",
        )

        same = tuple(network.node_ids) == tuple(cmd.node_ids)
        if not same:
            network.set_nodes(tuple(cmd.node_ids), now=now)

        for status, message in (
            (OperationStatus.PLANNING, "validating node membership"),
            (OperationStatus.RUNNING, "persisting node membership"),
            (OperationStatus.VERIFYING, "verifying persisted record"),
            (
                OperationStatus.SUCCEEDED,
                "membership updated" if not same else "membership unchanged",
            ),
        ):
            operation.transition_to(status, now=self._clock.now(), message=message)

        await self._networks.save(network)
        await self._operations.save(operation)
        return NetworkUpdated(network=network, operation=operation)


class ListNetworks:
    def __init__(self, *, networks: NetworkRepository) -> None:
        self._networks = networks

    async def execute(self) -> list[Network]:
        return await self._networks.list()


class GetNetwork:
    def __init__(self, *, networks: NetworkRepository) -> None:
        self._networks = networks

    async def execute(self, network_id: NetworkId) -> Network:
        network = await self._networks.get(network_id)
        if network is None:
            raise NotFoundError(f"network {network_id} not found")
        return network
