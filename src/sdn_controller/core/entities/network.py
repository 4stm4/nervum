"""Network aggregate.

The aggregate captures *intent* — what the operator wants the network to look
like across the fleet. Two columns track every change:

* ``intent_version`` — monotonic counter the controller bumps on every spec
  change. The reconciler uses it to detect "this is a new revision, replan".
* ``spec_hash`` — SHA-256 over the canonical form of the spec (name, type,
  VLAN/VNI, MTU, subnet, labels, node_ids). Stable across timestamp churn,
  so two readers (e.g. a follower replica) always agree on equality.

``node_ids`` is the membership list: which nodes the network is realized on.
The planner walks this list to build per-node plans.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network

from sdn_controller.core.value_objects.edge_services import (
    DhcpSpec,
    FirewallPolicy,
    NatSpec,
)
from sdn_controller.core.value_objects.enums import NetworkType
from sdn_controller.core.value_objects.errors import ValidationError
from sdn_controller.core.value_objects.ids import NetworkId, NodeId, ProjectId, SubnetId
from sdn_controller.core.value_objects.ipam import IpRange

# Reasonable VLAN/VNI bounds — enforced here so adapters can trust the entity.
_VLAN_MIN = 1
_VLAN_MAX = 4094
_VNI_MIN = 1
_VNI_MAX = 16_777_215  # 2^24 - 1
_MTU_MIN = 576
_MTU_MAX = 9216


@dataclass(slots=True)
class Subnet:
    """L3 prefix attached to a network.

    Beyond the obvious ``cidr``/``gateway``, M6 adds:

    * ``dns_servers`` — handed to DHCP/DNS at provisioning time;
    * ``allocation_pools`` — ranges the dynamic allocator may carve from
      (empty means "the whole CIDR minus the gateway and reserved ranges");
    * ``reserved_ranges`` — ranges the allocator never touches (gateway,
      DHCP servers, infrastructure addresses).

    All fields are validated together: pools and reserved ranges must lie
    inside the CIDR, pools must not overlap each other, the gateway must
    not sit inside any pool.
    """

    id: SubnetId
    cidr: str
    gateway: str | None = None
    dns_servers: tuple[str, ...] = ()
    allocation_pools: tuple[IpRange, ...] = ()
    reserved_ranges: tuple[IpRange, ...] = ()
    # M7: edge-service intent attached to the subnet.
    dhcp: DhcpSpec | None = None
    dns_zone: str | None = None

    def __post_init__(self) -> None:  # noqa: PLR0912 — IPAM validation is naturally branchy
        try:
            net: IPv4Network | IPv6Network = ip_network(self.cidr, strict=True)
        except ValueError as exc:
            raise ValidationError(f"invalid cidr: {self.cidr}: {exc}") from exc

        if self.gateway is not None:
            try:
                gw = ip_address(self.gateway)
            except ValueError as exc:
                raise ValidationError(f"invalid gateway: {self.gateway}: {exc}") from exc
            if gw not in net:
                raise ValidationError(
                    f"gateway {self.gateway} is not inside subnet {self.cidr}",
                )

        for ds in self.dns_servers:
            try:
                ip_address(ds)
            except ValueError as exc:
                raise ValidationError(f"invalid dns server: {ds}: {exc}") from exc

        for rng in self.allocation_pools:
            self._require_inside_cidr(rng, net=net, label="allocation pool")
        for rng in self.reserved_ranges:
            self._require_inside_cidr(rng, net=net, label="reserved range")

        # Allocation pools must not overlap each other.
        for i, a in enumerate(self.allocation_pools):
            for b in self.allocation_pools[i + 1 :]:
                if a.overlaps(b):
                    raise ValidationError(
                        f"allocation pools overlap: {a.start}-{a.end} and {b.start}-{b.end}",
                    )

        # Gateway must not fall inside any pool — pools are where the
        # allocator hands out IPs, the gateway is permanent infrastructure.
        if self.gateway is not None:
            for pool in self.allocation_pools:
                if pool.contains(self.gateway):
                    raise ValidationError(
                        f"gateway {self.gateway} lies inside allocation pool "
                        f"{pool.start}-{pool.end}",
                    )

    @staticmethod
    def _require_inside_cidr(
        rng: IpRange,
        *,
        net: IPv4Network | IPv6Network,
        label: str,
    ) -> None:
        start = ip_address(rng.start)
        end = ip_address(rng.end)
        if start not in net or end not in net:
            raise ValidationError(
                f"{label} {rng.start}-{rng.end} is not inside subnet {net}",
            )


@dataclass(slots=True)
class Network:
    id: NetworkId
    name: str
    type: NetworkType
    created_at: datetime
    updated_at: datetime
    mtu: int = 1500
    vlan_id: int | None = None
    vni: int | None = None
    subnet: Subnet | None = None
    intent_version: int = 1
    labels: dict[str, str] = field(default_factory=dict)
    # M5: which nodes this network is realized on. Empty tuple means "not
    # yet attached to any node" (created but inert).
    node_ids: tuple[NodeId, ...] = ()
    # M7: edge-service intent attached to the network as a whole.
    nat: NatSpec | None = None
    firewall_policy: FirewallPolicy | None = None
    # SHA-256 over the canonical spec; recomputed on every mutation.
    spec_hash: str = ""
    # N0: multitenancy — optional project scope.
    project_id: ProjectId | None = None

    def __post_init__(self) -> None:
        self._validate()
        if not self.spec_hash:
            self.spec_hash = compute_spec_hash(self)

    # -- invariants --------------------------------------------------------

    def _validate(self) -> None:  # noqa: PLR0912 — flat dispatch over network type
        if not self.name or not self.name.strip():
            raise ValidationError("network name must be non-empty")

        if not (_MTU_MIN <= self.mtu <= _MTU_MAX):
            raise ValidationError(f"mtu {self.mtu} out of range [{_MTU_MIN}, {_MTU_MAX}]")

        match self.type:
            case NetworkType.FLAT:
                if self.vlan_id is not None or self.vni is not None:
                    raise ValidationError("flat network must not set vlan_id or vni")
            case NetworkType.VLAN:
                if self.vlan_id is None:
                    raise ValidationError("vlan network requires vlan_id")
                if self.vni is not None:
                    raise ValidationError("vlan network must not set vni")
                if not (_VLAN_MIN <= self.vlan_id <= _VLAN_MAX):
                    raise ValidationError(
                        f"vlan_id {self.vlan_id} out of range [{_VLAN_MIN}, {_VLAN_MAX}]"
                    )
            case NetworkType.VXLAN:
                if self.vni is None:
                    raise ValidationError("vxlan network requires vni")
                if self.vlan_id is not None:
                    raise ValidationError("vxlan network must not set vlan_id")
                if not (_VNI_MIN <= self.vni <= _VNI_MAX):
                    raise ValidationError(f"vni {self.vni} out of range [{_VNI_MIN}, {_VNI_MAX}]")

        # node_ids must be unique
        if len(set(self.node_ids)) != len(self.node_ids):
            raise ValidationError("network node_ids contain duplicates")

    # -- behaviour ---------------------------------------------------------

    def bump_intent(self, *, now: datetime) -> None:
        """Record a new desired-state revision.

        Call this after mutating any spec-relevant field. Recomputes the
        spec hash and increments ``intent_version`` atomically with the
        ``updated_at`` bump so observers see the trio change together.
        """
        self.intent_version += 1
        self.updated_at = now
        self.spec_hash = compute_spec_hash(self)

    def set_nodes(self, node_ids: tuple[NodeId, ...], *, now: datetime) -> None:
        """Replace the network's node membership and bump intent."""
        if len(set(node_ids)) != len(node_ids):
            raise ValidationError("network node_ids contain duplicates")
        self.node_ids = tuple(node_ids)
        self.bump_intent(now=now)


def compute_spec_hash(network: Network) -> str:
    """Canonical hash over the spec (everything but timestamps + intent_version).

    Pure function: lives outside the entity so use cases and adapters can
    compute it without holding the aggregate (e.g. to verify a stored hash
    matches what was loaded).
    """
    canonical = {
        "name": network.name,
        "type": network.type.value,
        "mtu": network.mtu,
        "vlan_id": network.vlan_id,
        "vni": network.vni,
        "subnet": _subnet_to_canonical(network.subnet),
        "labels": dict(sorted(network.labels.items())),
        "node_ids": sorted(network.node_ids),
        "nat": (
            {"egress_interface": network.nat.egress_interface} if network.nat is not None else None
        ),
        "firewall_policy": _firewall_to_canonical(network.firewall_policy),
        "project_id": network.project_id,
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _subnet_to_canonical(subnet: Subnet | None) -> dict[str, object] | None:
    if subnet is None:
        return None
    return {
        "cidr": subnet.cidr,
        "gateway": subnet.gateway,
        "dns_zone": subnet.dns_zone,
        "dhcp": (
            {
                "range_start": subnet.dhcp.range_start,
                "range_end": subnet.dhcp.range_end,
                "lease_time_seconds": subnet.dhcp.lease_time_seconds,
                "domain_name": subnet.dhcp.domain_name,
            }
            if subnet.dhcp is not None
            else None
        ),
    }


def _firewall_to_canonical(fw: FirewallPolicy | None) -> dict[str, object] | None:
    if fw is None:
        return None
    return {
        "default_action": fw.default_action.value,
        "rules": [
            {
                "action": r.action.value,
                "proto": r.proto.value,
                "source_cidr": r.source_cidr,
                "destination_cidr": r.destination_cidr,
                "destination_port_start": r.destination_port_start,
                "destination_port_end": r.destination_port_end,
            }
            for r in fw.rules
        ],
    }
