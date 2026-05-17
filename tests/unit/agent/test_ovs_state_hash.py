"""Stable hash + canonical encoding for ``OvsState``."""

from __future__ import annotations

from netos_agent.core.entities import (
    BridgeState,
    InterfaceState,
    OvsState,
    PortState,
)


def _state() -> OvsState:
    return OvsState(
        ovs_version="3.2.1",
        bridges=(
            BridgeState(
                name="br-int",
                datapath_type="system",
                ports=(
                    PortState(
                        name="patch-tun",
                        interfaces=(
                            InterfaceState(
                                name="patch-tun",
                                type="patch",
                                options={"peer": "patch-int"},
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )


def test_hash_is_64_hex_chars() -> None:
    h = _state().hash
    assert len(h) == 64
    int(h, 16)  # all hex


def test_hash_independent_of_bridge_order() -> None:
    s1 = OvsState(
        ovs_version="3.2.0",
        bridges=(
            BridgeState(name="br-a"),
            BridgeState(name="br-b"),
        ),
    )
    s2 = OvsState(
        ovs_version="3.2.0",
        bridges=(
            BridgeState(name="br-b"),
            BridgeState(name="br-a"),
        ),
    )

    assert s1.hash == s2.hash


def test_hash_independent_of_option_dict_order() -> None:
    a = InterfaceState(name="i", type="vxlan", options={"key": "10", "remote_ip": "10.0.0.1"})
    b = InterfaceState(name="i", type="vxlan", options={"remote_ip": "10.0.0.1", "key": "10"})

    s1 = OvsState(bridges=(BridgeState(name="br", ports=(PortState(name="p", interfaces=(a,)),)),))
    s2 = OvsState(bridges=(BridgeState(name="br", ports=(PortState(name="p", interfaces=(b,)),)),))

    assert s1.hash == s2.hash


def test_hash_changes_when_value_changes() -> None:
    s1 = OvsState(ovs_version="3.2.0", bridges=(BridgeState(name="br-a"),))
    s2 = OvsState(ovs_version="3.2.0", bridges=(BridgeState(name="br-b"),))

    assert s1.hash != s2.hash


def test_find_bridge() -> None:
    s = _state()

    assert s.find_bridge("br-int") is not None
    assert s.find_bridge("does-not-exist") is None
