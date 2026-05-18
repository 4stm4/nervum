"""Network endpoints — desired-state CRUD + the M5 apply trigger."""

from __future__ import annotations

from fastapi import APIRouter, status

from sdn_controller.adapters.http_api.dependencies import (
    ApplyNetworkDep,
    AssignNetworkNodesDep,
    CreateNetworkDep,
    GetNetworkDep,
    ListNetworksDep,
    UpdateNetworkDep,
)
from sdn_controller.adapters.http_api.schemas import (
    NetworkApplyResponse,
    NetworkAssignNodesRequest,
    NetworkCreateRequest,
    NetworkCreateResponse,
    NetworkListResponse,
    NetworkOut,
    NetworkUpdateRequest,
    NetworkUpdateResponse,
    SubnetIn,
    operation_envelope,
)
from sdn_controller.core.use_cases.networks import (
    AssignNodesCommand,
    CreateNetworkCommand,
    SubnetSpec,
    UpdateNetworkCommand,
)
from sdn_controller.core.value_objects.ids import NetworkId, NodeId

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
    result = await use_case.execute(
        CreateNetworkCommand(
            name=payload.name,
            type=payload.type,
            mtu=payload.mtu,
            vlan_id=payload.vlan_id,
            vni=payload.vni,
            subnet=_to_subnet_spec(payload.subnet),
            labels=dict(payload.labels),
            node_ids=tuple(NodeId(n) for n in payload.node_ids),
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


@router.patch(
    "/{network_id}",
    response_model=NetworkUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Partial update of a network's spec (bumps intent_version + spec_hash)",
)
async def update_network(
    network_id: str,
    payload: NetworkUpdateRequest,
    use_case: UpdateNetworkDep,
) -> NetworkUpdateResponse:
    result = await use_case.execute(
        NetworkId(network_id),
        UpdateNetworkCommand(
            mtu=payload.mtu,
            subnet=_to_subnet_spec(payload.subnet),
            labels=dict(payload.labels) if payload.labels is not None else None,
        ),
    )
    return NetworkUpdateResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation),
    )


@router.post(
    "/{network_id}/nodes",
    response_model=NetworkUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replace the network's node membership",
)
async def assign_nodes(
    network_id: str,
    payload: NetworkAssignNodesRequest,
    use_case: AssignNetworkNodesDep,
) -> NetworkUpdateResponse:
    result = await use_case.execute(
        NetworkId(network_id),
        AssignNodesCommand(node_ids=tuple(NodeId(n) for n in payload.node_ids)),
    )
    return NetworkUpdateResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation),
    )


@router.post(
    "/{network_id}/apply",
    response_model=NetworkApplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Apply (observe → diff → push → verify) across the network's nodes",
)
async def apply_network(
    network_id: str,
    use_case: ApplyNetworkDep,
) -> NetworkApplyResponse:
    result = await use_case.execute(NetworkId(network_id))
    return NetworkApplyResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation),
    )


def _to_subnet_spec(subnet: SubnetIn | None) -> SubnetSpec | None:
    if subnet is None:
        return None
    return SubnetSpec(cidr=subnet.cidr, gateway=subnet.gateway)
