"""MirrorConfigurator — генератор OVS-конфигурации для mirror-сессии (N5-02).

Генерирует последовательность ``ovs-vsctl``-команд, которые реализуют
SPAN (локальный порт) или ERSPAN (удалённый IP) зеркалирование.

Пример вывода (SPAN)::

    # Mirror session: mirror_1 — my-mirror
    ovs-vsctl -- --id=@m create Mirror name=my-mirror \
        select-src-port=$(ovs-vsctl get Port lport_1 _uuid) \
        select-dst-port=$(ovs-vsctl get Port lport_1 _uuid) \
        output-port=$(ovs-vsctl get Port lport_2 _uuid)

Пример вывода (ERSPAN)::

    ovs-vsctl add-port br-int erspan0 -- set interface erspan0 \
        type=erspan options:remote_ip=192.168.100.10 options:erspan_idx=1
"""

from __future__ import annotations

from sdn_controller.core.entities.mirror_session import MirrorSession
from sdn_controller.core.value_objects.enums import MirrorDirection


class MirrorConfigurator:
    """Генерирует OVS-команды для port mirroring (N5-02)."""

    def generate_config(self, session: MirrorSession) -> str:
        """Вернуть строку с ovs-vsctl командами для создания зеркала."""
        lines: list[str] = [
            f"# SDN Controller — Mirror Session",
            f"# id={session.id} name={session.name!r}",
            f"# direction={session.direction.value}",
            "",
        ]

        if session.destination_ip is not None:
            lines.extend(self._erspan_lines(session))
        else:
            lines.extend(self._span_lines(session))

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # SPAN (локальный порт назначения)
    # ------------------------------------------------------------------

    def _span_lines(self, session: MirrorSession) -> list[str]:
        src = session.source_port_id
        dst = session.destination_port_id

        select_args = self._select_args(session.direction, src)
        vlan_filter = (
            f"\\\n    select-vlan={session.filter_vlan}"
            if session.filter_vlan is not None
            else ""
        )

        lines = [
            f"ovs-vsctl \\",
            f"    -- --id=@m create Mirror name={session.name!r} \\",
        ]
        lines.extend(f"    {arg} \\" for arg in select_args)
        if vlan_filter:
            lines.append(f"    select-vlan={session.filter_vlan} \\")
        lines.append(
            f"    output-port=$(ovs-vsctl get Port {dst} _uuid)"
        )
        return lines

    # ------------------------------------------------------------------
    # ERSPAN (удалённый IP-коллектор)
    # ------------------------------------------------------------------

    def _erspan_lines(self, session: MirrorSession) -> list[str]:
        src = session.source_port_id
        iface = f"erspan_{session.id}"
        select_args = self._select_args(session.direction, src)
        vlan_part = (
            f" options:erspan_vlan={session.filter_vlan}"
            if session.filter_vlan is not None
            else ""
        )
        lines = [
            f"# Создать ERSPAN-интерфейс",
            f"ovs-vsctl add-port br-int {iface} \\",
            f"    -- set interface {iface} type=erspan \\",
            f"    options:remote_ip={session.destination_ip}{vlan_part}",
            f"",
            f"# Настроить зеркало",
            f"ovs-vsctl \\",
            f"    -- --id=@m create Mirror name={session.name!r} \\",
        ]
        lines.extend(f"    {arg} \\" for arg in select_args)
        lines.append(
            f"    output-port=$(ovs-vsctl get Port {iface} _uuid)"
        )
        return lines

    # ------------------------------------------------------------------
    # Вспомогательные методы
    # ------------------------------------------------------------------

    @staticmethod
    def _select_args(direction: MirrorDirection, port_id: str) -> list[str]:
        """Вернуть флаги select-src/select-dst в зависимости от направления."""
        uuid_expr = f"$(ovs-vsctl get Port {port_id} _uuid)"
        match direction:
            case MirrorDirection.INGRESS:
                return [f"select-src-port={uuid_expr}"]
            case MirrorDirection.EGRESS:
                return [f"select-dst-port={uuid_expr}"]
            case MirrorDirection.BOTH:
                return [
                    f"select-src-port={uuid_expr}",
                    f"select-dst-port={uuid_expr}",
                ]
