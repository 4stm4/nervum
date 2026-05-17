"""Network use cases.

For milestone 1 we only persist *desired state* — there is no agent yet, so
applying changes to live OVS is out of scope. Even so, every mutation goes
through an ``Operation`` so the external API surface stays stable when real
provisioning lands in Milestone 5.
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
from sdn_controller.core.value_objects.ids import IdFactory, NetworkId
from sdn_controller.ports.persistence import NetworkRepository, OperationRepository

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
    created_by: str | None = None


@dataclass(frozen=True, slots=True)
class NetworkCreated:
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

        # Network.__post_init__ enforces invariants; ValidationError bubbles up.
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
        )

        operation = Operation.accept(
            operation_id=self._ids.operation(),
            kind=OperationKind.NETWORK_CREATE,
            resource=ResourceRef(type="network", id=network_id),
            now=now,
            created_by=cmd.created_by,
            message=f"create network {network.name!r}",
        )

        # No agent work yet in milestone 1, so we drive the operation straight
        # through its state machine. Milestone 5 will replace this with a
        # planner/reconciler pipeline.
        for status, message in (
            (OperationStatus.PLANNING, "store desired state"),
            (OperationStatus.RUNNING, "persisting network record"),
            (OperationStatus.VERIFYING, "verifying persisted record"),
            (OperationStatus.SUCCEEDED, "network created"),
        ):
            operation.transition_to(status, now=self._clock.now(), message=message)

        # Persist last so a failed validation does not pollute the repo.
        await self._networks.save(network)
        await self._operations.save(operation)
        return NetworkCreated(network=network, operation=operation)


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
