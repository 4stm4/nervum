"""In-memory DHCP backend used by tests and ``ovs_backend=fake`` deployments."""

from netos_agent.adapters.dhcp_fake.adapter import FakeDhcp

__all__ = ["FakeDhcp"]
