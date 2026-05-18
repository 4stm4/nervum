"""Real DHCP backend backed by ``dnsmasq``.

Not unit-tested — exercising it requires ``dnsmasq`` on the host. The
shape is the same as ``FakeDhcp`` so the dispatcher swap is invisible.
"""

from netos_agent.adapters.dhcp_dnsmasq.adapter import DnsmasqDhcp

__all__ = ["DnsmasqDhcp"]
