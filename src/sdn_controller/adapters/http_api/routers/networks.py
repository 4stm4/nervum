"""Network endpoints — desired-state CRUD + the M5 apply trigger."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status

from sdn_controller.adapters.http_api.auth import require
from sdn_controller.adapters.http_api.dependencies import (
    ApplyNetworkDep,
    AssignNetworkNodesDep,
    CreateNetworkDep,
    GetNetworkDep,
    ListNetworksDep,
    UpdateNetworkDep,
)
from sdn_controller.adapters.http_api.schemas import (
    FirewallPolicyIO,
    NatSpecIO,
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
from sdn_controller.core.value_objects.edge_services import (
    FirewallAction,
    FirewallPolicy,
    FirewallProto,
    FirewallRule,
    NatSpec,
)
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, ProjectId
from sdn_controller.core.value_objects.security import Permission

router = APIRouter(prefix="/networks", tags=["networks"])


@router.get(
    "",
    response_model=NetworkListResponse,
    summary="List networks",
    dependencies=[Depends(require(Permission.NETWORK_READ))],
)
async def list_networks(
    use_case: ListNetworksDep,
    project_id: str | None = Query(default=None, min_length=1),
) -> NetworkListResponse:
    networks = await use_case.execute(project_id=ProjectId(project_id) if project_id else None)
    return NetworkListResponse(items=[NetworkOut.from_domain(n) for n in networks])

@router.post(
    "",
    response_model=NetworkCreateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Create a network (returns operation envelope)",
    dependencies=[Depends(require(Permission.NETWORK_WRITE))],
)
async def create_network(
    request: Request,
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
            project_id=ProjectId(payload.project_id) if payload.project_id else None,
            subnet=_to_subnet_spec(payload.subnet),
            labels=dict(payload.labels),
            node_ids=tuple(NodeId(n) for n in payload.node_ids),
            created_by=_actor_from_request(request),
        )
    )
    request.state.operation_id = result.operation.id
    return NetworkCreateResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation, project_id=result.network.project_id),
    )


@router.get(
    "/{network_id}",
    response_model=NetworkOut,
    summary="Get a network",
    dependencies=[Depends(require(Permission.NETWORK_READ))],
)
async def get_network(network_id: str, use_case: GetNetworkDep) -> NetworkOut:
    network = await use_case.execute(NetworkId(network_id))
    return NetworkOut.from_domain(network)


@router.patch(
    "/{network_id}",
    response_model=NetworkUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Partial update of a network's spec (bumps intent_version + spec_hash)",
    dependencies=[Depends(require(Permission.NETWORK_WRITE))],
)
async def update_network(
    request: Request,
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
            nat=_to_nat_spec(payload.nat),
            firewall_policy=_to_firewall_policy(payload.firewall_policy),
            updated_by=_actor_from_request(request),
        ),
    )
    request.state.operation_id = result.operation.id
    return NetworkUpdateResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation, project_id=result.network.project_id),
    )


@router.post(
    "/{network_id}/nodes",
    response_model=NetworkUpdateResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Replace the network's node membership",
    dependencies=[Depends(require(Permission.NETWORK_WRITE))],
)
async def assign_nodes(
    request: Request,
    network_id: str,
    payload: NetworkAssignNodesRequest,
    use_case: AssignNetworkNodesDep,
) -> NetworkUpdateResponse:
    result = await use_case.execute(
        NetworkId(network_id),
        AssignNodesCommand(
            node_ids=tuple(NodeId(n) for n in payload.node_ids),
            updated_by=_actor_from_request(request),
        ),
    )
    request.state.operation_id = result.operation.id
    return NetworkUpdateResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation, project_id=result.network.project_id),
    )


@router.post(
    "/{network_id}/apply",
    response_model=NetworkApplyResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Apply (observe → diff → push → verify) across the network's nodes",
    dependencies=[Depends(require(Permission.NETWORK_APPLY))],
)
async def apply_network(
    request: Request,
    network_id: str,
    use_case: ApplyNetworkDep,
) -> NetworkApplyResponse:
    result = await use_case.execute(
        NetworkId(network_id),
        requested_by=_actor_from_request(request),
    )
    request.state.operation_id = result.operation.id
    return NetworkApplyResponse(
        network=NetworkOut.from_domain(result.network),
        operation=operation_envelope(result.operation, project_id=result.network.project_id),
    )


def _actor_from_request(request: Request) -> str | None:
    """Сложить ``created_by`` для operation'а из principal + source_task_id."""
    principal = getattr(request.state, "principal", None)
    source_task_id = getattr(request.state, "source_task_id", None)
    parts: list[str] = []
    if principal is not None:
        parts.append(f"sa:{principal.name}")
    if source_task_id is not None:
        parts.append(f"testum:{source_task_id}")
    return "+".join(parts) if parts else None


def _to_subnet_spec(subnet: SubnetIn | None) -> SubnetSpec | None:
    if subnet is None:
        return None
    return SubnetSpec(cidr=subnet.cidr, gateway=subnet.gateway)


def _to_nat_spec(nat: NatSpecIO | None) -> NatSpec | None:
    if nat is None:
        return None
    return NatSpec(egress_interface=nat.egress_interface)


def _to_firewall_policy(fw: FirewallPolicyIO | None) -> FirewallPolicy | None:
    if fw is None:
        return None
    return FirewallPolicy(
        default_action=FirewallAction(fw.default_action),
        rules=tuple(
            FirewallRule(
                action=FirewallAction(r.action),
                proto=FirewallProto(r.proto),
                source_cidr=r.source_cidr,
                destination_cidr=r.destination_cidr,
                destination_port_start=r.destination_port_start,
                destination_port_end=r.destination_port_end,
            )
            for r in fw.rules
        ),
    )
