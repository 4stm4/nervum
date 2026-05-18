"""FastAPI dependency providers.

We resolve the singleton ``Container`` from ``app.state`` and expose narrow
``Annotated`` shortcuts. Handlers depend on a single use case each, never on
the whole container — that keeps the public signature honest.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from sdn_controller.app.container import Container
from sdn_controller.core.use_cases.enrollment import (
    EnrollAgent,
    IssueEnrollmentToken,
    RecordHeartbeat,
)
from sdn_controller.core.use_cases.ipam import (
    AllocateIp,
    GetAllocation,
    GetSubnet,
    ListAllocations,
    ListSubnets,
    ReleaseIp,
    ReserveIp,
    UpsertSubnet,
)
from sdn_controller.core.use_cases.networks import (
    AssignNetworkToNodes,
    CreateNetwork,
    GetNetwork,
    ListNetworks,
    UpdateNetwork,
)
from sdn_controller.core.use_cases.nodes import (
    GetNode,
    ListNodes,
    RegisterNode,
    RemoveNode,
)
from sdn_controller.core.use_cases.operations import GetOperation, ListOperations
from sdn_controller.core.use_cases.reconcile import ApplyNetwork
from sdn_controller.core.use_cases.topology import GetTopology, ScanDrift


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


def _create_network(c: ContainerDep) -> CreateNetwork:
    return c.create_network


def _list_networks(c: ContainerDep) -> ListNetworks:
    return c.list_networks


def _get_network(c: ContainerDep) -> GetNetwork:
    return c.get_network


def _list_nodes(c: ContainerDep) -> ListNodes:
    return c.list_nodes


def _get_node(c: ContainerDep) -> GetNode:
    return c.get_node


def _register_node(c: ContainerDep) -> RegisterNode:
    return c.register_node


def _remove_node(c: ContainerDep) -> RemoveNode:
    return c.remove_node


def _issue_enrollment_token(c: ContainerDep) -> IssueEnrollmentToken:
    return c.issue_enrollment_token


def _enroll_agent(c: ContainerDep) -> EnrollAgent:
    return c.enroll_agent


def _record_heartbeat(c: ContainerDep) -> RecordHeartbeat:
    return c.record_heartbeat


def _list_operations(c: ContainerDep) -> ListOperations:
    return c.list_operations


def _get_operation(c: ContainerDep) -> GetOperation:
    return c.get_operation


def _update_network(c: ContainerDep) -> UpdateNetwork:
    return c.update_network


def _assign_network_nodes(c: ContainerDep) -> AssignNetworkToNodes:
    return c.assign_network_to_nodes


def _apply_network(c: ContainerDep) -> ApplyNetwork:
    return c.apply_network


def _upsert_subnet(c: ContainerDep) -> UpsertSubnet:
    return c.upsert_subnet


def _list_subnets(c: ContainerDep) -> ListSubnets:
    return c.list_subnets


def _get_subnet(c: ContainerDep) -> GetSubnet:
    return c.get_subnet


def _allocate_ip(c: ContainerDep) -> AllocateIp:
    return c.allocate_ip


def _reserve_ip(c: ContainerDep) -> ReserveIp:
    return c.reserve_ip


def _release_ip(c: ContainerDep) -> ReleaseIp:
    return c.release_ip


def _list_allocations(c: ContainerDep) -> ListAllocations:
    return c.list_allocations


def _get_allocation(c: ContainerDep) -> GetAllocation:
    return c.get_allocation


def _get_topology(c: ContainerDep) -> GetTopology:
    return c.get_topology


def _scan_drift(c: ContainerDep) -> ScanDrift:
    return c.scan_drift


CreateNetworkDep = Annotated[CreateNetwork, Depends(_create_network)]
ListNetworksDep = Annotated[ListNetworks, Depends(_list_networks)]
GetNetworkDep = Annotated[GetNetwork, Depends(_get_network)]
UpdateNetworkDep = Annotated[UpdateNetwork, Depends(_update_network)]
AssignNetworkNodesDep = Annotated[AssignNetworkToNodes, Depends(_assign_network_nodes)]
ApplyNetworkDep = Annotated[ApplyNetwork, Depends(_apply_network)]
ListNodesDep = Annotated[ListNodes, Depends(_list_nodes)]
GetNodeDep = Annotated[GetNode, Depends(_get_node)]
RegisterNodeDep = Annotated[RegisterNode, Depends(_register_node)]
RemoveNodeDep = Annotated[RemoveNode, Depends(_remove_node)]
IssueEnrollmentTokenDep = Annotated[IssueEnrollmentToken, Depends(_issue_enrollment_token)]
EnrollAgentDep = Annotated[EnrollAgent, Depends(_enroll_agent)]
RecordHeartbeatDep = Annotated[RecordHeartbeat, Depends(_record_heartbeat)]
ListOperationsDep = Annotated[ListOperations, Depends(_list_operations)]
GetOperationDep = Annotated[GetOperation, Depends(_get_operation)]
UpsertSubnetDep = Annotated[UpsertSubnet, Depends(_upsert_subnet)]
ListSubnetsDep = Annotated[ListSubnets, Depends(_list_subnets)]
GetSubnetDep = Annotated[GetSubnet, Depends(_get_subnet)]
AllocateIpDep = Annotated[AllocateIp, Depends(_allocate_ip)]
ReserveIpDep = Annotated[ReserveIp, Depends(_reserve_ip)]
ReleaseIpDep = Annotated[ReleaseIp, Depends(_release_ip)]
ListAllocationsDep = Annotated[ListAllocations, Depends(_list_allocations)]
GetAllocationDep = Annotated[GetAllocation, Depends(_get_allocation)]
GetTopologyDep = Annotated[GetTopology, Depends(_get_topology)]
ScanDriftDep = Annotated[ScanDrift, Depends(_scan_drift)]
