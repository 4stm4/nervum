"""``HttpAgentClient`` — talks to ``netos_agent`` over HTTP.

We deliberately keep the client thin: serialise plans → POST, parse the
response through Pydantic → return controller-side views. Anything richer
(retries, circuit-breaking, mTLS) belongs in middleware around ``httpx``
or in a future M9 hardening pass.

The client is instantiated with two things:

1. an ``httpx.AsyncClient`` — production code passes a real client; tests
   pass one wired to ``TestClient`` via ``httpx.MockTransport`` or the
   ``ASGITransport`` we use in ``test_http_agent_client.py``;
2. an ``AgentEndpoints`` resolver — controller code asks "given a node id,
   what's the base URL?" The default resolver takes a dict; future code
   can derive the URL from the node's mgmt_ip + a configured port.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from sdn_controller.adapters.netos_agent.schemas import (
    ErrorEnvelopeIn,
    NodeStateIn,
    OvsStateIn,
    PlanResultIn,
    SnapshotIn,
    SnapshotRestoreIn,
)
from sdn_controller.core.value_objects.capabilities import NodeCapabilities
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import NodeId
from sdn_controller.ports.agent import (
    DeleteBridgeStep,
    DeleteDhcpScopeStep,
    DeleteDnsZoneStep,
    DeleteFirewallPolicyStep,
    DeleteNatRuleStep,
    DeletePortStep,
    EnsureBridgeStep,
    EnsureDhcpScopeStep,
    EnsureDnsZoneStep,
    EnsureFirewallPolicyStep,
    EnsureNatRuleStep,
    EnsurePortStep,
    EnsureVxlanPortStep,
    OvsStateView,
    Plan,
    PlanResult,
    PlanStep,
    SnapshotRef,
)

_log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Agent URL resolution
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AgentEndpoints:
    """Where do we send requests for a given node?

    Today this is a simple dict; later it'll lookup the node row to derive a
    base URL from ``mgmt_ip``. Wrapping it in a class lets us swap strategies
    without touching the use cases.
    """

    by_node: dict[NodeId, str]

    def __call__(self, node_id: NodeId) -> str:
        try:
            return self.by_node[node_id]
        except KeyError as exc:  # pragma: no cover — defensive
            raise NotFoundError(f"no agent endpoint registered for node {node_id}") from exc


EndpointResolver = Callable[[NodeId], str]


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class AgentTransportError(DomainError):
    """The agent could not be reached, replied non-JSON, or returned 5xx.

    Distinct from ``OvsdbError`` on the agent side: we surface this as a
    transport-level failure so the reconciler can choose between retrying
    against a different agent vs. rolling back.
    """

    code = "agent_transport_error"


_STATUS_TO_EXC: dict[int, type[DomainError]] = {
    400: ValidationError,
    404: NotFoundError,
    409: ConflictError,
    422: ValidationError,
}


def _raise_for_response(response: httpx.Response) -> None:
    """Map an agent HTTP error to a controller-side domain exception.

    Honours the agent's ``{"error": {"code", "message", "details"}}``
    envelope when present; falls back to a generic transport error otherwise.
    """
    if response.is_success:
        return

    code: str | None = None
    method = response.request.method
    path = response.request.url.path
    message = f"agent {method} {path} → {response.status_code}"
    details: dict[str, Any] = {}
    if response.headers.get("content-type", "").startswith("application/json"):
        # The body isn't always our error envelope — e.g. 5xx without a body,
        # or a reverse proxy substituting a generic page. Treat any parse
        # failure as "no structured detail available" and fall through to
        # the generic ``AgentTransportError``.
        try:
            env = ErrorEnvelopeIn.model_validate(response.json())
        except (ValueError, TypeError):
            _log.debug("agent_error_body_not_envelope", status=response.status_code)
        else:
            code = env.error.code
            message = env.error.message
            details = dict(env.error.details)

    exc_cls = _STATUS_TO_EXC.get(response.status_code, AgentTransportError)
    exc = exc_cls(message)
    if code is not None:
        exc.code = code
    if details:
        # Stash details for tests/logging without changing the public exc type.
        exc.args = (message, details)
    raise exc


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class HttpAgentClient:
    def __init__(
        self,
        *,
        http: httpx.AsyncClient,
        endpoints: EndpointResolver,
    ) -> None:
        self._http = http
        self._endpoints = endpoints

    # -- AgentPort ---------------------------------------------------------

    async def get_capabilities(self, node_id: NodeId) -> NodeCapabilities:
        response = await self._get(node_id, "/v1/node/state")
        return NodeStateIn.model_validate(response.json()).to_capabilities()

    async def get_state(self, node_id: NodeId) -> OvsStateView:
        response = await self._get(node_id, "/v1/ovs/state")
        return OvsStateIn.model_validate(response.json()).to_view()

    async def apply_plan(self, node_id: NodeId, plan: Plan) -> PlanResult:
        body = {
            "plan_id": plan.plan_id,
            "steps": [_step_to_wire(step) for step in plan.steps],
        }
        response = await self._post(node_id, "/v1/network/apply", json=body)
        return PlanResultIn.model_validate(response.json()).to_domain()

    async def snapshot(
        self,
        node_id: NodeId,
        *,
        label: str | None = None,
    ) -> SnapshotRef:
        body: dict[str, Any] = {"label": label}
        response = await self._post(node_id, "/v1/ovs/snapshot", json=body)
        return SnapshotIn.model_validate(response.json()).to_ref()

    async def restore(self, node_id: NodeId, snapshot_id: str) -> SnapshotRef:
        response = await self._post(node_id, f"/v1/ovs/restore/{snapshot_id}")
        return SnapshotRestoreIn.model_validate(response.json()).snapshot.to_ref()

    # -- transport ---------------------------------------------------------

    async def _get(self, node_id: NodeId, path: str) -> httpx.Response:
        base = self._endpoints(node_id)
        try:
            response = await self._http.get(f"{base}{path}")
        except httpx.HTTPError as exc:
            raise _transport_failure(exc) from exc
        _raise_for_response(response)
        return response

    async def _post(
        self,
        node_id: NodeId,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        base = self._endpoints(node_id)
        try:
            response = await self._http.post(f"{base}{path}", json=json)
        except httpx.HTTPError as exc:
            raise _transport_failure(exc) from exc
        _raise_for_response(response)
        return response


# ---------------------------------------------------------------------------
# Step → wire format
# ---------------------------------------------------------------------------


def _step_to_wire(step: PlanStep) -> dict[str, Any]:  # noqa: PLR0911, PLR0912 — discriminated dispatch
    """Render a controller-side step dataclass as JSON the agent will accept.

    We list each field by hand rather than calling ``dataclasses.asdict`` so
    the wire layer stays explicit — any new field is a deliberate update.
    """
    match step:
        case EnsureBridgeStep():
            return {
                "action": "ensure_bridge",
                "name": step.name,
                "datapath_type": step.datapath_type,
                "external_ids": dict(step.external_ids),
            }
        case DeleteBridgeStep():
            return {"action": "delete_bridge", "name": step.name}
        case EnsurePortStep():
            payload: dict[str, Any] = {
                "action": "ensure_port",
                "bridge": step.bridge,
                "name": step.name,
                "type": step.type,
                "options": dict(step.options),
                "trunks": list(step.trunks),
                "external_ids": dict(step.external_ids),
            }
            if step.tag is not None:
                payload["tag"] = step.tag
            return payload
        case DeletePortStep():
            return {"action": "delete_port", "bridge": step.bridge, "name": step.name}
        case EnsureVxlanPortStep():
            payload = {
                "action": "ensure_vxlan_port",
                "bridge": step.bridge,
                "name": step.name,
                "vni": step.vni,
                "remote_ip": step.remote_ip,
                "dst_port": step.dst_port,
                "external_ids": dict(step.external_ids),
            }
            if step.local_ip is not None:
                payload["local_ip"] = step.local_ip
            if step.mtu is not None:
                payload["mtu"] = step.mtu
            return payload
        case EnsureDhcpScopeStep():
            return {
                "action": "ensure_dhcp_scope",
                "spec": {
                    "scope_id": step.spec.scope_id,
                    "cidr": step.spec.cidr,
                    "range_start": step.spec.range_start,
                    "range_end": step.spec.range_end,
                    "gateway": step.spec.gateway,
                    "dns_servers": list(step.spec.dns_servers),
                    "lease_time_seconds": step.spec.lease_time_seconds,
                    "domain_name": step.spec.domain_name,
                },
            }
        case DeleteDhcpScopeStep():
            return {"action": "delete_dhcp_scope", "scope_id": step.scope_id}
        case EnsureDnsZoneStep():
            return {
                "action": "ensure_dns_zone",
                "spec": {
                    "zone": step.spec.zone,
                    "soa_email": step.spec.soa_email,
                    "records": [
                        {
                            "name": r.name,
                            "type": r.type,
                            "value": r.value,
                            "ttl_seconds": r.ttl_seconds,
                        }
                        for r in step.spec.records
                    ],
                },
            }
        case DeleteDnsZoneStep():
            return {"action": "delete_dns_zone", "zone": step.zone}
        case EnsureNatRuleStep():
            return {
                "action": "ensure_nat_rule",
                "spec": {
                    "rule_id": step.spec.rule_id,
                    "source_cidr": step.spec.source_cidr,
                    "egress_interface": step.spec.egress_interface,
                },
            }
        case DeleteNatRuleStep():
            return {"action": "delete_nat_rule", "rule_id": step.rule_id}
        case EnsureFirewallPolicyStep():
            return {
                "action": "ensure_firewall_policy",
                "spec": {
                    "policy_id": step.spec.policy_id,
                    "default_action": step.spec.default_action,
                    "rules": [
                        {
                            "action": r.action,
                            "proto": r.proto,
                            "source_cidr": r.source_cidr,
                            "destination_cidr": r.destination_cidr,
                            "destination_port_start": r.destination_port_start,
                            "destination_port_end": r.destination_port_end,
                        }
                        for r in step.spec.rules
                    ],
                },
            }
        case DeleteFirewallPolicyStep():
            return {"action": "delete_firewall_policy", "policy_id": step.policy_id}


def _transport_failure(exc: httpx.HTTPError) -> AgentTransportError:
    return AgentTransportError(f"agent transport failure: {exc!s}")
