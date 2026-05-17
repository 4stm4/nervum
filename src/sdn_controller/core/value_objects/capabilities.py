"""``NodeCapabilities`` value object.

Capabilities are facts about a node reported by the agent (OVS version,
kernel, interfaces, optional feature flags). They are part of the node
aggregate — we expose the same type from the agent port so the protocol can
share it without forcing the core to depend on adapter code.

We deliberately use ``tuple`` instead of ``list``/``frozenset`` for the
collection fields so:

* the value object stays hashable and ``frozen=True``,
* JSON round-trips through the SQL adapter preserve element order,
* tests can compare two ``NodeCapabilities`` for structural equality.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeCapabilities:
    ovs_version: str | None = None
    kernel: str | None = None
    interfaces: tuple[str, ...] = ()
    features: tuple[str, ...] = ()

    def supports(self, feature: str) -> bool:
        return feature in self.features
