"""IPAM value objects: ``IpRange``, ``OwnerRef``, ``IpAllocationKind``.

These are small, immutable shapes the entities + use cases share. They live
here (not in ``entities/``) because none of them carry identity — they are
characterised by their values alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import ip_address

from sdn_controller.core.value_objects.errors import ValidationError

# ---------------------------------------------------------------------------
# IpRange — inclusive [start, end] segment of one address family
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IpRange:
    """Closed-interval range of IP addresses (``start`` and ``end`` included).

    Why a tuple of strings instead of a CIDR? Pools and reservations
    routinely don't align on prefix boundaries (e.g. ``10.0.0.100 to
    10.0.0.200`` inside a ``/24``). CIDR pinning would force operators
    into awkward aggregations.
    """

    start: str
    end: str

    def __post_init__(self) -> None:
        try:
            start = ip_address(self.start)
            end = ip_address(self.end)
        except ValueError as exc:
            raise ValidationError(f"invalid ip range {self.start}-{self.end}: {exc}") from exc
        if type(start) is not type(end):
            raise ValidationError(f"ip range {self.start}-{self.end} mixes address families")
        if int(start) > int(end):
            raise ValidationError(f"ip range {self.start}-{self.end} has start > end")

    def contains(self, address: str) -> bool:
        try:
            target = ip_address(address)
        except ValueError:
            return False
        start = ip_address(self.start)
        if type(start) is not type(target):
            return False
        return int(start) <= int(target) <= int(ip_address(self.end))

    def overlaps(self, other: IpRange) -> bool:
        a_start, a_end = ip_address(self.start), ip_address(self.end)
        b_start, b_end = ip_address(other.start), ip_address(other.end)
        if type(a_start) is not type(b_start):
            return False
        return int(a_start) <= int(b_end) and int(b_start) <= int(a_end)

    def iter_addresses(self) -> list[str]:
        """Materialise every address in the range as a string.

        Cheap for tests and for IPv4 pools up to ``/16``. The allocator uses
        a generator-based traversal instead, so the production hot path
        doesn't pay this cost.
        """
        start = ip_address(self.start)
        end = ip_address(self.end)
        cls = type(start)
        return [str(cls(i)) for i in range(int(start), int(end) + 1)]


# ---------------------------------------------------------------------------
# OwnerRef — opaque pointer to whoever holds an allocation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerRef:
    """Identifies the consumer of an IP allocation.

    Common ``type`` values today: ``"vm-port"``, ``"router-interface"``,
    ``"dhcp"``, ``"manual"``. Kept as a free-form string so the controller
    can carry references to entities it doesn't itself model yet.
    """

    type: str
    id: str


# ---------------------------------------------------------------------------
# IpAllocationKind — provenance label
# ---------------------------------------------------------------------------


class IpAllocationKind(StrEnum):
    """How was this allocation produced?

    Both kinds occupy the address pool identically — the distinction is
    audit-only. ``RESERVATION`` is a *pinned* IP that the caller chose;
    ``DYNAMIC`` was assigned by ``next_available_ip``.
    """

    DYNAMIC = "dynamic"
    RESERVATION = "reservation"
