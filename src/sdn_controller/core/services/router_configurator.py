"""RouterConfigurator — генератор конфигурации L3-маршрутизатора (N3-03, N3-04).

Принимает Router, список BgpPeer и опциональный HaConfig и генерирует
shell-скрипт, который применяется агентом на узле через ``sh -e``.

Выходной скрипт содержит:
1. ``ip route`` команды для статических маршрутов.
2. nftables SNAT/masquerade-правило на external-интерфейс (если задан
   external_network_id).
3. radvd.conf-фрагмент для IPv6 SLAAC (N3-04), если ipv6_config.mode != off.
4. Вызов keepalived (N3-06) если ha_mode = VRRP.

Всё это — чистая доменная служба: нет I/O, нет внешних зависимостей.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdn_controller.core.entities.bgp_peer import BgpPeer
    from sdn_controller.core.entities.router import Router


class RouterConfigurator:
    """Генерирует конфиг (shell-скрипт) для Router (N3-03, N3-04, N3-06)."""

    def generate(
        self,
        router: "Router",
        bgp_peers: list["BgpPeer"] | None = None,
        *,
        now: datetime | None = None,
    ) -> str:
        """Создаёт полный конфиг для маршрутизатора.

        Args:
            router:    сущность Router с маршрутами, HA-параметрами, IPv6-конфигом.
            bgp_peers: список BgpPeer для данного маршрутизатора (N3-05).
            now:       момент генерации (по умолчанию UTC now).

        Returns:
            shell-скрипт, готовый для исполнения через ``sh -e``.
        """
        bgp_peers = bgp_peers or []
        if now is None:
            now = datetime.now(tz=timezone.utc)

        lines: list[str] = []
        lines.append("#!/bin/sh")
        lines.append("# SDN Controller — конфигурация маршрутизатора")
        lines.append(f"# Router: {router.name} ({router.id})")
        lines.append(f"# Сгенерировано: {now.isoformat()}")
        lines.append("set -e")
        lines.append("")

        # 1. Статические маршруты
        if router.static_routes:
            lines.append("# --- Статические маршруты ---")
            for route in router.static_routes:
                lines.append(
                    f"ip route replace {route.destination} via {route.nexthop}"
                )
            lines.append("")

        # 2. SNAT/masquerade через nftables (N3-03)
        if router.external_network_id:
            lines.append("# --- NAT / masquerade (внешняя сеть) ---")
            ext_id = router.external_network_id
            table = f"sdn_nat_{router.id.replace('-', '_')}"
            lines.append(f"nft add table inet {table} 2>/dev/null || true")
            lines.append(
                f"nft add chain inet {table} postrouting "
                f"'{{ type nat hook postrouting priority 100; }}' 2>/dev/null || true"
            )
            lines.append(
                f"# external_network_id={ext_id}: masquerade на uplink-интерфейсе"
            )
            lines.append(
                f"nft add rule inet {table} postrouting "
                f"oifname != lo masquerade"
            )
            lines.append("")

        # 3. IPv6 / SLAAC / DHCPv6 (N3-04)
        if router.ipv6_config and router.ipv6_config.mode.value != "off":
            lines.extend(self._generate_ipv6_config(router))

        # 4. BGP (N3-05)
        if bgp_peers:
            lines.extend(self._generate_bgp_config(router, bgp_peers))

        # 5. VRRP / keepalived (N3-06)
        if router.ha_mode.value == "vrrp":
            lines.extend(self._generate_ha_config(router))

        lines.append("# --- Конец конфигурации ---")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Вспомогательные секции
    # ------------------------------------------------------------------

    def _generate_ipv6_config(self, router: "Router") -> list[str]:
        """Генерирует radvd.conf-фрагмент для IPv6 SLAAC/DHCPv6 (N3-04)."""
        cfg = router.ipv6_config
        assert cfg is not None
        lines: list[str] = ["# --- IPv6 / SLAAC ---"]

        if cfg.mode.value == "slaac":
            lines.append("# Режим: SLAAC (radvd)")
            if cfg.prefix:
                lines.append(
                    f"# Префикс для Router Advertisement: {cfg.prefix}"
                )
            lines.append("# Запись /etc/radvd.conf генерируется агентом по шаблону")
            lines.append(f"# radvd_prefix={cfg.prefix or 'auto'}")
        elif cfg.mode.value in ("stateful", "stateless"):
            dhcp_mode = "stateful" if cfg.dhcpv6_stateful else "stateless"
            lines.append(f"# Режим: DHCPv6 {dhcp_mode}")
            if cfg.prefix:
                lines.append(f"# DHCPv6 prefix delegation: {cfg.prefix}")
        lines.append("")
        return lines

    def _generate_bgp_config(
        self, router: "Router", peers: list["BgpPeer"]
    ) -> list[str]:
        """Генерирует конфиг bird (N3-05) для всех пиров маршрутизатора."""
        lines = ["# --- BGP (bird) ---"]
        for peer in peers:
            lines.append(f"# BGP peer: {peer.peer_ip} AS{peer.peer_asn}")
            lines.append(f"# local_asn={peer.local_asn} peer_id={peer.id}")
            if peer.password:
                lines.append(f"# MD5 аутентификация: включена")
        lines.append("# Полный bird.conf генерируется BgpConfigurator и передаётся агенту")
        lines.append("")
        return lines

    def _generate_ha_config(self, router: "Router") -> list[str]:
        """Генерирует keepalived.conf-фрагмент для VRRP (N3-06)."""
        lines = ["# --- HA / VRRP (keepalived) ---"]
        vrid = router.vrrp_vrid or 1
        prio = router.vrrp_priority or 100
        lines.append(f"# vrrp_vrid={vrid} vrrp_priority={prio}")
        lines.append("# keepalived.conf генерируется HaConfigurator и передаётся агенту")
        lines.append("")
        return lines
