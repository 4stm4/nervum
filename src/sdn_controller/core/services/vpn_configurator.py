"""VpnConfigurator — генерация WireGuard / IPsec конфигурации (N5-05).

Для WireGuard генерирует ``wg0.conf`` (формат wg-quick):

    [Interface]
    PrivateKey = <local_private_key_placeholder>
    Address = <local_endpoint>/32
    ListenPort = 51820

    [Peer]
    PublicKey = <peer_public_key>
    AllowedIPs = 10.0.0.0/24
    Endpoint = 192.168.1.1:51820
    PersistentKeepalive = 25

Для IPsec генерирует ``ipsec.conf`` + ``ipsec.secrets`` (Strongswan-формат).
"""

from __future__ import annotations

from sdn_controller.core.entities.vpn_tunnel import VpnPeer, VpnTunnel
from sdn_controller.core.value_objects.enums import VpnProtocol


class VpnConfigurator:
    """Генератор конфигов VPN-туннелей (N5-05)."""

    def generate_config(self, tunnel: VpnTunnel, peers: list[VpnPeer]) -> str:
        """Вернуть текстовый конфиг в зависимости от протокола туннеля."""
        match tunnel.protocol:
            case VpnProtocol.WIREGUARD:
                return self._wireguard(tunnel, peers)
            case VpnProtocol.IPSEC:
                return self._ipsec(tunnel)

    # ------------------------------------------------------------------
    # WireGuard
    # ------------------------------------------------------------------

    def _wireguard(self, tunnel: VpnTunnel, peers: list[VpnPeer]) -> str:
        lines: list[str] = [
            "# SDN Controller — WireGuard конфиг",
            f"# tunnel={tunnel.id} name={tunnel.name!r}",
            "",
            "[Interface]",
            "# PrivateKey задаётся оператором при деплое",
            "PrivateKey = <REPLACE_WITH_PRIVATE_KEY>",
            f"Address = {tunnel.local_endpoint}/32",
            f"ListenPort = {tunnel.listen_port}",
        ]

        for peer in peers:
            lines.append("")
            lines.append("[Peer]")
            lines.append(f"PublicKey = {peer.public_key}")
            if peer.endpoint:
                lines.append(f"Endpoint = {peer.endpoint}")
            if peer.allowed_ips:
                lines.append(f"AllowedIPs = {', '.join(peer.allowed_ips)}")
            else:
                lines.append("AllowedIPs = 0.0.0.0/0")
            if tunnel.preshared_key:
                lines.append(f"PresharedKey = {tunnel.preshared_key}")
            if peer.persistent_keepalive > 0:
                lines.append(f"PersistentKeepalive = {peer.persistent_keepalive}")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # IPsec (Strongswan)
    # ------------------------------------------------------------------

    def _ipsec(self, tunnel: VpnTunnel) -> str:
        conn_name = tunnel.name.replace(" ", "_").lower()
        conf_lines = [
            "# SDN Controller — IPsec конфиг (Strongswan)",
            f"# tunnel={tunnel.id} name={tunnel.name!r}",
            "",
            "config setup",
            "    charondebug=\"ike 2, knl 2, cfg 2\"",
            "",
            f"conn {conn_name}",
            "    keyexchange=ikev2",
            "    authby=psk",
            f"    left={tunnel.local_endpoint}",
            f"    leftid={tunnel.local_endpoint}",
            f"    right={tunnel.remote_endpoint}",
            f"    rightid={tunnel.remote_endpoint}",
            "    auto=start",
            "    ike=aes256-sha256-modp2048!",
            "    esp=aes256-sha256!",
            "    dpdaction=restart",
            "    dpddelay=30s",
        ]
        secrets_lines = [
            "# IPsec secrets (ipsec.secrets)",
            f"{tunnel.local_endpoint} {tunnel.remote_endpoint} : PSK "
            f'"{tunnel.preshared_key or "<REPLACE_WITH_PSK>"}"',
        ]
        return "\n".join(conf_lines) + "\n\n" + "\n".join(secrets_lines)
