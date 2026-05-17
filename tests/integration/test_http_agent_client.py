"""Controller ↔ Agent contract test.

We instantiate a real ``netos_agent`` FastAPI app, point an
``httpx.AsyncClient`` at it via ``ASGITransport`` (no socket, no port), and
drive ``HttpAgentClient`` against it. If the controller's view of the agent's
wire format ever drifts, these tests fail with a clear Pydantic parse error.

Why ASGITransport instead of ``TestClient``? ``TestClient`` is sync;
``HttpAgentClient`` is async. ASGITransport gives us an in-process async
HTTP roundtrip with zero networking — fast and deterministic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from netos_agent.adapters.http_api import create_app as create_agent_app
from netos_agent.app.config import Settings as AgentSettings
from netos_agent.app.container import build_container as build_agent_container
from sdn_controller.adapters.netos_agent import AgentEndpoints, HttpAgentClient
from sdn_controller.core.value_objects.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from sdn_controller.core.value_objects.ids import NodeId
from sdn_controller.ports.agent import (
    EnsureBridgeStep,
    EnsurePortStep,
    EnsureVxlanPortStep,
    Plan,
)

_NODE = NodeId("node_test")


@pytest.fixture
async def client(tmp_path: Path) -> AsyncIterator[HttpAgentClient]:
    settings = AgentSettings(
        ovs_backend="fake",
        snapshots_dir=str(tmp_path / "snapshots"),
        log_level="WARNING",
        log_format="console",
    )
    agent_app = create_agent_app(build_agent_container(settings))
    transport = httpx.ASGITransport(app=agent_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as http:
        yield HttpAgentClient(
            http=http,
            endpoints=AgentEndpoints(by_node={_NODE: ""}),
        )


# ---------------------------------------------------------------------------
# Read paths
# ---------------------------------------------------------------------------


async def test_get_capabilities_returns_ovs_version(client: HttpAgentClient) -> None:
    caps = await client.get_capabilities(_NODE)

    assert caps.ovs_version  # FakeOvsdb advertises a fake version


async def test_get_state_on_empty_agent(client: HttpAgentClient) -> None:
    state = await client.get_state(_NODE)

    assert state.bridges == ()
    assert state.state_hash  # 64-hex chars even on empty


# ---------------------------------------------------------------------------
# Apply plan — drift / VLAN / VXLAN / external_ids end-to-end
# ---------------------------------------------------------------------------


async def test_apply_plan_creates_bridge_and_port(client: HttpAgentClient) -> None:
    plan = Plan(
        plan_id="plan_1",
        steps=(
            EnsureBridgeStep(name="br-int"),
            EnsurePortStep(bridge="br-int", name="patch-tun", type="patch"),
        ),
    )

    result = await client.apply_plan(_NODE, plan)

    assert result.ok is True
    assert [(s.action, s.ok, s.changed) for s in result.steps] == [
        ("ensure_bridge", True, True),
        ("ensure_port", True, True),
    ]


async def test_apply_plan_is_idempotent_through_http(client: HttpAgentClient) -> None:
    plan = Plan(
        plan_id="plan_idem",
        steps=(EnsureBridgeStep(name="br-int"),),
    )

    first = await client.apply_plan(_NODE, plan)
    second = await client.apply_plan(_NODE, plan)

    assert first.steps[0].changed is True
    assert second.steps[0].changed is False


async def test_vxlan_with_all_options_round_trips(client: HttpAgentClient) -> None:
    plan = Plan(
        plan_id="plan_vxlan",
        steps=(
            EnsureBridgeStep(name="br-tun"),
            EnsureVxlanPortStep(
                bridge="br-tun",
                name="vxlan-10100",
                vni=10100,
                remote_ip="10.0.0.2",
                local_ip="10.0.0.1",
                dst_port=8472,
                mtu=1450,
                external_ids={"owner": "sdn"},
            ),
        ),
    )

    result = await client.apply_plan(_NODE, plan)
    assert result.ok is True

    state = await client.get_state(_NODE)
    bridge = state.find_bridge("br-tun")
    assert bridge is not None
    port = next(p for p in bridge.ports if p.name == "vxlan-10100")
    iface = port.interfaces[0]
    assert iface.type == "vxlan"
    assert iface.options["remote_ip"] == "10.0.0.2"
    assert iface.options["local_ip"] == "10.0.0.1"
    assert iface.options["dst_port"] == "8472"
    assert iface.options["mtu_request"] == "1450"
    assert port.external_ids == {"owner": "sdn"}


async def test_external_ids_on_bridge_round_trip(client: HttpAgentClient) -> None:
    plan = Plan(
        plan_id="plan_xids",
        steps=(
            EnsureBridgeStep(
                name="br-tenant",
                external_ids={"owner": "sdn-controller", "network_id": "net_42"},
            ),
        ),
    )
    await client.apply_plan(_NODE, plan)

    state = await client.get_state(_NODE)
    bridge = state.find_bridge("br-tenant")
    assert bridge is not None
    assert bridge.external_ids == {"owner": "sdn-controller", "network_id": "net_42"}


async def test_apply_plan_partial_failure_surfaces_per_step(client: HttpAgentClient) -> None:
    plan = Plan(
        plan_id="plan_partial",
        steps=(
            EnsurePortStep(bridge="missing-bridge", name="p1"),  # fails
            EnsureBridgeStep(name="br-good"),  # succeeds
        ),
    )

    result = await client.apply_plan(_NODE, plan)

    assert result.ok is False
    assert result.steps[0].ok is False
    assert result.steps[0].details["code"] == "not_found"
    assert result.steps[1].ok is True


# ---------------------------------------------------------------------------
# Snapshot / restore
# ---------------------------------------------------------------------------


async def test_snapshot_and_restore_round_trip(client: HttpAgentClient) -> None:
    await client.apply_plan(
        _NODE,
        Plan(plan_id="plan_pre", steps=(EnsureBridgeStep(name="br-stable"),)),
    )
    snap = await client.snapshot(_NODE, label="pre-mutation")
    pre_hash = (await client.get_state(_NODE)).state_hash

    await client.apply_plan(
        _NODE,
        Plan(plan_id="plan_mutate", steps=(EnsureBridgeStep(name="br-extra"),)),
    )
    assert (await client.get_state(_NODE)).state_hash != pre_hash

    restored = await client.restore(_NODE, snap.id)
    assert restored.state_hash == pre_hash


# ---------------------------------------------------------------------------
# Error envelope mapping
# ---------------------------------------------------------------------------


async def test_restore_unknown_snapshot_maps_to_not_found(client: HttpAgentClient) -> None:
    with pytest.raises(NotFoundError):
        await client.restore(_NODE, "snap_missing")


async def test_malformed_plan_maps_to_validation_error(client: HttpAgentClient) -> None:
    # Pydantic VLAN range validation runs in the agent; the controller sees a 422.
    plan = Plan(
        plan_id="plan_bad",
        steps=(EnsurePortStep(bridge="br-x", name="p", tag=99999),),
    )
    with pytest.raises(ValidationError):
        await client.apply_plan(_NODE, plan)


async def test_no_endpoint_for_node_raises_not_found(tmp_path: Path) -> None:
    settings = AgentSettings(
        ovs_backend="fake",
        snapshots_dir=str(tmp_path / "snapshots"),
        log_level="WARNING",
        log_format="console",
    )
    agent_app = create_agent_app(build_agent_container(settings))
    transport = httpx.ASGITransport(app=agent_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://agent") as http:
        bad_client = HttpAgentClient(
            http=http,
            endpoints=AgentEndpoints(by_node={}),  # empty
        )
        with pytest.raises(NotFoundError, match="no agent endpoint"):
            await bad_client.get_state(NodeId("node_unknown"))


# Pulled out so the file doesn't fail to import even if the test is skipped.
_ = ConflictError  # keep import for future tests
