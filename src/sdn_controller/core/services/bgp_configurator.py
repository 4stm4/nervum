"""BgpConfigurator — генератор bird.conf для BGP-пиров (N3-05).

Принимает маршрутизатор и список его BGP-пиров; возвращает строку с
конфигурацией bird2 (BIRD Internet Routing Daemon), которую агент
записывает в ``/etc/bird/bird.conf`` и перезапускает bird.

Служба — чистый доменный объект: нет I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdn_controller.core.entities.bgp_peer import BgpPeer
    from sdn_controller.core.entities.router import Router


class BgpConfigurator:
    """Генерирует bird2.conf для набора BGP-пиров (N3-05)."""

    def generate(
        self,
        router: "Router",
        peers: list["BgpPeer"],
        *,
        router_id_ip: str = "127.0.0.1",
        now: datetime | None = None,
    ) -> str:
        """Создаёт bird2.conf для маршрутизатора.

        Args:
            router:       сущность Router.
            peers:        список BgpPeer данного маршрутизатора.
            router_id_ip: IP-адрес, используемый как router-id в bird.
                          Обычно это IP внешнего интерфейса; агент подставляет
                          реальный IP — здесь используется placeholder.
            now:          момент генерации.

        Returns:
            Строка конфига bird2 в формате, пригодном для записи в файл.
        """
        if now is None:
            now = datetime.now(tz=timezone.utc)

        lines: list[str] = []
        lines.append(f"# SDN Controller — bird2 конфиг")
        lines.append(f"# Router: {router.name} ({router.id})")
        lines.append(f"# Сгенерировано: {now.isoformat()}")
        lines.append("")
        lines.append(f"router id {router_id_ip};")
        lines.append("")
        lines.append("log syslog all;")
        lines.append("")
        lines.append("protocol device {}")
        lines.append("")
        lines.append("protocol direct {")
        lines.append("    ipv4;")
        lines.append("    ipv6;")
        lines.append("}")
        lines.append("")
        lines.append("protocol kernel {")
        lines.append("    ipv4 { export all; };")
        lines.append("    learn;")
        lines.append("}")
        lines.append("")

        for peer in peers:
            lines.extend(self._peer_block(peer))

        return "\n".join(lines)

    def _peer_block(self, peer: "BgpPeer") -> list[str]:
        """Генерирует блок ``protocol bgp`` для одного пира."""
        proto_name = f"bgp_{peer.id.replace('-', '_')}"
        lines = [
            f"protocol bgp {proto_name} {{",
            f"    local as {peer.local_asn};",
            f"    neighbor {peer.peer_ip} as {peer.peer_asn};",
        ]
        if peer.password:
            lines.append(f'    password "{peer.password}";')
        lines += [
            "    ipv4 {",
            "        import all;",
            "        export all;",
            "    };",
            "}",
            "",
        ]
        return lines
