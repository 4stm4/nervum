"""``Network.spec_hash`` is canonical, stable, and changes on relevant mutations."""

from __future__ import annotations

from datetime import UTC, datetime

from sdn_controller.core.entities import Network, Subnet, compute_spec_hash
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, SubnetId

_NOW = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _net(**overrides: object) -> Network:
    base: dict[str, object] = {
        "id": NetworkId("net_1"),
        "name": "tenant-a",
        "type": NetworkType.VXLAN,
        "vni": 10100,
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    base.update(overrides)
    return Network(**base)  # type: ignore[arg-type]


def test_spec_hash_is_64_hex_chars() -> None:
    net = _net()

    assert len(net.spec_hash) == 64
    int(net.spec_hash, 16)


def test_spec_hash_is_independent_of_label_order() -> None:
    a = _net(labels={"team": "core", "region": "eu"})
    b = _net(labels={"region": "eu", "team": "core"})

    assert a.spec_hash == b.spec_hash


def test_spec_hash_is_independent_of_node_order() -> None:
    a = _net(node_ids=(NodeId("node_1"), NodeId("node_2")))
    b = _net(node_ids=(NodeId("node_2"), NodeId("node_1")))

    assert a.spec_hash == b.spec_hash


def test_spec_hash_changes_with_mtu() -> None:
    a = _net()
    b = _net(mtu=1450)

    assert a.spec_hash != b.spec_hash


def test_spec_hash_excludes_timestamps_and_intent_version() -> None:
    a = _net()
    b = _net(
        updated_at=datetime(2030, 1, 1, tzinfo=UTC),
        intent_version=42,
    )

    assert a.spec_hash == b.spec_hash


def test_bump_intent_increments_version_and_rehashes() -> None:
    net = _net()
    initial_hash = net.spec_hash
    initial_version = net.intent_version

    later = _NOW.replace(minute=5)
    net.mtu = 1450
    net.bump_intent(now=later)

    assert net.intent_version == initial_version + 1
    assert net.updated_at == later
    assert net.spec_hash != initial_hash


def test_set_nodes_bumps_intent_and_rehashes() -> None:
    net = _net()
    initial_hash = net.spec_hash
    initial_version = net.intent_version

    net.set_nodes(
        (NodeId("node_1"), NodeId("node_2")),
        now=_NOW.replace(minute=5),
    )

    assert net.node_ids == (NodeId("node_1"), NodeId("node_2"))
    assert net.intent_version == initial_version + 1
    assert net.spec_hash != initial_hash


def test_compute_spec_hash_function_matches_entity() -> None:
    net = _net(subnet=Subnet(id=SubnetId("sub_1"), cidr="10.0.0.0/24"))

    # Pure function called externally must agree with the entity's stored hash.
    assert net.spec_hash == compute_spec_hash(net)
