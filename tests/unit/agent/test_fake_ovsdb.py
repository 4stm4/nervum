"""Behaviour of the in-memory ``FakeOvsdb`` adapter."""

from __future__ import annotations

import pytest

from netos_agent.adapters.ovsdb_fake import FakeOvsdb
from netos_agent.core.value_objects.errors import NotFoundError


async def test_ensure_bridge_is_idempotent() -> None:
    db = FakeOvsdb()

    assert await db.ensure_bridge(name="br-a") is True  # first time: changed
    assert await db.ensure_bridge(name="br-a") is False  # second time: noop


async def test_ensure_bridge_changes_datapath_type() -> None:
    db = FakeOvsdb()
    await db.ensure_bridge(name="br-a", datapath_type="system")

    assert await db.ensure_bridge(name="br-a", datapath_type="netdev") is True
    assert await db.ensure_bridge(name="br-a", datapath_type="netdev") is False


async def test_delete_bridge() -> None:
    db = FakeOvsdb()
    await db.ensure_bridge(name="br-a")

    assert await db.delete_bridge(name="br-a") is True
    assert await db.delete_bridge(name="br-a") is False


async def test_ensure_port_requires_bridge() -> None:
    db = FakeOvsdb()

    with pytest.raises(NotFoundError):
        await db.ensure_port(bridge="br-a", name="p1")


async def test_ensure_port_round_trip() -> None:
    db = FakeOvsdb()
    await db.ensure_bridge(name="br-a")

    assert await db.ensure_port(bridge="br-a", name="p1") is True
    assert await db.ensure_port(bridge="br-a", name="p1") is False  # noop

    state = await db.get_state()
    bridge = state.find_bridge("br-a")
    assert bridge is not None
    assert [p.name for p in bridge.ports] == ["p1"]


async def test_vxlan_port_encodes_options() -> None:
    db = FakeOvsdb()
    await db.ensure_bridge(name="br-tun")

    assert (
        await db.ensure_vxlan_port(
            bridge="br-tun", name="vxlan-10100", vni=10100, remote_ip="10.0.0.2"
        )
        is True
    )

    state = await db.get_state()
    bridge = state.find_bridge("br-tun")
    assert bridge is not None
    iface = bridge.ports[0].interfaces[0]
    assert iface.type == "vxlan"
    assert iface.options["key"] == "10100"
    assert iface.options["remote_ip"] == "10.0.0.2"
    assert iface.options["dst_port"] == "4789"


async def test_dump_and_restore_round_trip() -> None:
    src = FakeOvsdb()
    await src.ensure_bridge(name="br-a")
    await src.ensure_port(bridge="br-a", name="p1", type="internal")
    payload = await src.dump()

    dst = FakeOvsdb()
    await dst.restore(payload)

    assert (await dst.get_state()).hash == (await src.get_state()).hash
