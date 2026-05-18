"""Real firewall + NAT backend backed by ``nftables``."""

from netos_agent.adapters.firewall_nftables.adapter import NftablesFirewall

__all__ = ["NftablesFirewall"]
