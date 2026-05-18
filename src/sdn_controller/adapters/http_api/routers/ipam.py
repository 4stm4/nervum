"""``/subnets`` and ``/ipam`` endpoints (Milestone 6).

* Subnet upsert lives under the parent network (``/networks/{id}/subnet``).
* Read endpoints are top-level so operators can list all subnets without
  walking the network catalog.
* Allocations live under their subnet for create + list; release is by
  allocation id at the top level so the caller doesn't need to remember
  which subnet the IP came from.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    AllocateIpDep,
    GetAllocationDep,
    GetSubnetDep,
    ListAllocationsDep,
    ListSubnetsDep,
    ReleaseIpDep,
    ReserveIpDep,
    UpsertSubnetDep,
)
from sdn_controller.adapters.http_api.schemas import (
    AllocateIpRequest,
    DhcpSpecIO,
    IpAllocationListResponse,
    IpAllocationOut,
    IpRangeIO,
    NetworkOut,
    OwnerRefIO,
    SubnetListResponse,
    SubnetOutFull,
    SubnetUpsertRequest,
)
from sdn_controller.core.entities import IpAllocation
from sdn_controller.core.use_cases.ipam import (
    AllocateIpCommand,
    ReserveIpCommand,
    SubnetWithNetwork,
    UpsertSubnetCommand,
)
from sdn_controller.core.value_objects.edge_services import DhcpSpec
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import (
    IpAllocationId,
    NetworkId,
    SubnetId,
)
from sdn_controller.core.value_objects.ipam import IpRange, OwnerRef
from sdn_controller.core.value_objects.security import Permission

# ---------------------------------------------------------------------------
# Subnet endpoints
# ---------------------------------------------------------------------------

subnets_router = APIRouter(
    prefix="/subnets",
    tags=["ipam"],
    dependencies=[Depends(require(Permission.IPAM_READ))],
)
network_subnet_router = APIRouter(
    prefix="/networks",
    tags=["ipam"],
    dependencies=[Depends(require(Permission.IPAM_WRITE))],
)
allocations_router = APIRouter(
    prefix="/allocations",
    tags=["ipam"],
    dependencies=[Depends(require(Permission.IPAM_READ))],
)


@subnets_router.get("", response_model=SubnetListResponse, summary="List all subnets")
async def list_subnets(use_case: ListSubnetsDep) -> SubnetListResponse:
    items = await use_case.execute()
    return SubnetListResponse(items=[_subnet_out(s) for s in items])


@subnets_router.get(
    "/{subnet_id}",
    response_model=SubnetOutFull,
    summary="Get a subnet",
)
async def get_subnet(subnet_id: str, use_case: GetSubnetDep) -> SubnetOutFull:
    bundle = await use_case.execute(SubnetId(subnet_id))
    return _subnet_out(bundle)


@network_subnet_router.post(
    "/{network_id}/subnet",
    response_model=NetworkOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create or replace a subnet on a network",
)
async def upsert_subnet(
    network_id: str,
    payload: SubnetUpsertRequest,
    use_case: UpsertSubnetDep,
) -> NetworkOut:
    network = await use_case.execute(
        NetworkId(network_id),
        UpsertSubnetCommand(
            cidr=payload.cidr,
            gateway=payload.gateway,
            dns_servers=tuple(payload.dns_servers),
            allocation_pools=tuple(_range(r) for r in payload.allocation_pools),
            reserved_ranges=tuple(_range(r) for r in payload.reserved_ranges),
            dhcp=_to_dhcp_spec(payload.dhcp),
            dns_zone=payload.dns_zone,
        ),
    )
    return NetworkOut.from_domain(network)


# ---------------------------------------------------------------------------
# Allocation endpoints
# ---------------------------------------------------------------------------


@subnets_router.get(
    "/{subnet_id}/allocations",
    response_model=IpAllocationListResponse,
    summary="List allocations within a subnet",
)
async def list_allocations(
    subnet_id: str,
    use_case: ListAllocationsDep,
) -> IpAllocationListResponse:
    items = await use_case.execute(SubnetId(subnet_id))
    return IpAllocationListResponse(items=[_allocation_out(a) for a in items])


@subnets_router.post(
    "/{subnet_id}/allocations",
    response_model=IpAllocationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Allocate (dynamic) or reserve (pinned) an IP from a subnet",
    dependencies=[Depends(require(Permission.IPAM_WRITE))],
)
async def create_allocation(
    subnet_id: str,
    payload: AllocateIpRequest,
    allocate: AllocateIpDep,
    reserve: ReserveIpDep,
) -> IpAllocationOut:
    owner = OwnerRef(type=payload.owner.type, id=payload.owner.id)
    if payload.kind == "reservation":
        if not payload.ip_address:
            raise ValidationError("reservation requires ip_address")
        allocation = await reserve.execute(
            SubnetId(subnet_id),
            ReserveIpCommand(ip_address=payload.ip_address, owner=owner, label=payload.label),
        )
    else:
        allocation = await allocate.execute(
            SubnetId(subnet_id),
            AllocateIpCommand(owner=owner, label=payload.label),
        )
    return _allocation_out(allocation)


@allocations_router.get(
    "/{allocation_id}",
    response_model=IpAllocationOut,
    summary="Get an allocation",
)
async def get_allocation(
    allocation_id: str,
    use_case: GetAllocationDep,
) -> IpAllocationOut:
    allocation = await use_case.execute(IpAllocationId(allocation_id))
    return _allocation_out(allocation)


@allocations_router.delete(
    "/{allocation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Release an allocation (idempotent)",
    dependencies=[Depends(require(Permission.IPAM_WRITE))],
)
async def release_allocation(
    allocation_id: str,
    use_case: ReleaseIpDep,
) -> None:
    await use_case.execute(IpAllocationId(allocation_id))


# ---------------------------------------------------------------------------
# DTO helpers
# ---------------------------------------------------------------------------


def _range(rng: IpRangeIO) -> IpRange:
    return IpRange(start=rng.start, end=rng.end)


def _to_dhcp_spec(dhcp: DhcpSpecIO | None) -> DhcpSpec | None:
    if dhcp is None:
        return None
    return DhcpSpec(
        range_start=dhcp.range_start,
        range_end=dhcp.range_end,
        lease_time_seconds=dhcp.lease_time_seconds,
        domain_name=dhcp.domain_name,
    )


def _subnet_out(bundle: SubnetWithNetwork) -> SubnetOutFull:
    s = bundle.subnet
    return SubnetOutFull(
        id=s.id,
        network_id=bundle.network.id,
        cidr=s.cidr,
        gateway=s.gateway,
        dns_servers=list(s.dns_servers),
        allocation_pools=[IpRangeIO(start=r.start, end=r.end) for r in s.allocation_pools],
        reserved_ranges=[IpRangeIO(start=r.start, end=r.end) for r in s.reserved_ranges],
        dhcp=DhcpSpecIO.from_domain(s.dhcp) if s.dhcp is not None else None,
        dns_zone=s.dns_zone,
    )


def _allocation_out(a: IpAllocation) -> IpAllocationOut:
    return IpAllocationOut(
        id=a.id,
        subnet_id=a.subnet_id,
        ip_address=a.ip_address,
        owner=OwnerRefIO(type=a.owner.type, id=a.owner.id),
        kind=a.kind.value,
        allocated_at=a.allocated_at,
        label=a.label,
    )
