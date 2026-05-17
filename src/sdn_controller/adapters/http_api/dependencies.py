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
from sdn_controller.core.use_cases.networks import CreateNetwork, GetNetwork, ListNetworks
from sdn_controller.core.use_cases.nodes import (
    GetNode,
    ListNodes,
    RegisterNode,
    RemoveNode,
)
from sdn_controller.core.use_cases.operations import GetOperation, ListOperations


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


CreateNetworkDep = Annotated[CreateNetwork, Depends(_create_network)]
ListNetworksDep = Annotated[ListNetworks, Depends(_list_networks)]
GetNetworkDep = Annotated[GetNetwork, Depends(_get_network)]
ListNodesDep = Annotated[ListNodes, Depends(_list_nodes)]
GetNodeDep = Annotated[GetNode, Depends(_get_node)]
RegisterNodeDep = Annotated[RegisterNode, Depends(_register_node)]
RemoveNodeDep = Annotated[RemoveNode, Depends(_remove_node)]
IssueEnrollmentTokenDep = Annotated[IssueEnrollmentToken, Depends(_issue_enrollment_token)]
EnrollAgentDep = Annotated[EnrollAgent, Depends(_enroll_agent)]
RecordHeartbeatDep = Annotated[RecordHeartbeat, Depends(_record_heartbeat)]
ListOperationsDep = Annotated[ListOperations, Depends(_list_operations)]
GetOperationDep = Annotated[GetOperation, Depends(_get_operation)]
