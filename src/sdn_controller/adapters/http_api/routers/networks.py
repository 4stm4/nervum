"""Network endpoints (Milestone 1 read/create surface)."""

from __future__ import annotations

from fastapi import APIRouter, status

from sdn_controller.adapters.http_api.dependencies import (
    CreateNetworkDep,
    GetNetworkDep,
    ListNetworksDep,
)
from sdn_controller.adapters.http_api.schemas import (
    NetworkCreateRequest,
    NetworkCreateResponse,
    NetworkListResponse,
    NetworkOut,
    SubnetIn,
    operation_envelope,
)
from sdn_controller.core.use_cases.networks import (
    CreateNetworkCommand,
    SubnetSpec,
)
from sdn_controller.core.value_objects.ids import NetworkId

router = APIRouter(prefix="/networks", tags=["networks"])


@router.get("", response_model=NetworkListResponse, summary="List networks")
async def list_networks(use_case: ListNetworksDep) -> NetworkListResponse:
    networks = await use_case.execute()
    return NetworkListResponse(items=[NetworkOut.from_domain(n) for n in networks])


@router.post(
    "",
    response_model=NetworkCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a network (returns operation envelope)",
)
async def create_network(
    payload: NetworkCreateRequest,
    use_case: CreateNetworkDep,
) -> NetworkCreateResponse:
    subnet = _to_subnet_spec(payload.subnet)
    result = await use_case.execute(
        CreateNetworkCommand(
            name=payload.name,
            type=payload.type,
            mtu=payload.mtu,
            vlan_id=payload.vlan_id,
            vni=payload.vni,
            subnet=subnet,
            labels=dict(payload.labels),
        )
    )
    return NetworkCreateResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation),
    )


@router.get("/{network_id}", response_model=NetworkOut, summary="Get a network")
async def get_network(network_id: str, use_case: GetNetworkDep) -> NetworkOut:
    network = await use_case.execute(NetworkId(network_id))
    return NetworkOut.from_domain(network)


def _to_subnet_spec(subnet: SubnetIn | None) -> SubnetSpec | None:
    if subnet is None:
        return None
    return SubnetSpec(cidr=subnet.cidr, gateway=subnet.gateway)
