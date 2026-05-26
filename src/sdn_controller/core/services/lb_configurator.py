"""LbConfigurator — генерация конфигурации HAProxy для LoadBalancer (N4-06).

Генерирует haproxy.cfg на основе LoadBalancer, LbListener, LbPool, LbMember
и HealthMonitor. В MVP конфиг сохраняется в applied_config; агент применяет
его в N5+.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdn_controller.core.entities.health_monitor import HealthMonitor
    from sdn_controller.core.entities.load_balancer import (
        LbListener,
        LbMember,
        LbPool,
        LoadBalancer,
    )


class LbConfigurator:
    """Генерирует haproxy.cfg для LoadBalancer (N4-06)."""

    def generate(
        self,
        lb: "LoadBalancer",
        listeners: list["LbListener"],
        pools: list["LbPool"],
        members: dict[str, list["LbMember"]],
        monitors: dict[str, "HealthMonitor"],
        *,
        now: datetime | None = None,
    ) -> str:
        """Формирует полный haproxy.cfg.

        ``members``  — словарь pool_id → список LbMember.
        ``monitors`` — словарь pool_id → HealthMonitor (опционально).
        """
        ts = (now or datetime.utcnow()).isoformat()
        lines: list[str] = [
            f"# SDN Controller — haproxy.cfg",
            f"# lb={lb.id} name={lb.name!r} generated={ts}",
            "",
            "global",
            "    log /dev/log local0",
            "    maxconn 50000",
            "    user haproxy",
            "    group haproxy",
            "    daemon",
            "",
            "defaults",
            "    log     global",
            "    mode    http",
            "    option  httplog",
            "    option  dontlognull",
            "    timeout connect 5s",
            "    timeout client  50s",
            "    timeout server  50s",
            "",
        ]

        # frontend sections (один на listener)
        for listener in listeners:
            pool_id = listener.default_pool_id
            backend_name = f"pool_{pool_id}" if pool_id else "default_backend"
            lines += [
                f"frontend {listener.id}",
                f"    bind {lb.vip_address}:{listener.protocol_port}",
                f"    mode {listener.protocol.value}",
                f"    default_backend {backend_name}",
                "",
            ]

        # backend sections (один на pool)
        for pool in pools:
            pool_members = members.get(pool.id, [])
            monitor = monitors.get(pool.id)
            algo_map = {
                "round_robin": "roundrobin",
                "least_connections": "leastconn",
                "source_ip": "source",
            }
            algo = algo_map.get(pool.lb_algorithm.value, "roundrobin")
            lines += [f"backend pool_{pool.id}",
                      f"    mode {pool.protocol.value}",
                      f"    balance {algo}"]
            if pool.session_persistence.value == "source_ip":
                lines.append("    stick-table type ip size 100k expire 30m")
                lines.append("    stick on src")
            if monitor:
                lines += self._health_check_lines(monitor)
            for member in pool_members:
                state = "" if member.admin_state_up else " disabled"
                lines.append(
                    f"    server {member.id} {member.address}:{member.protocol_port}"
                    f" weight {member.weight}{state} check"
                )
            lines.append("")

        return "\n".join(lines)

    def _health_check_lines(self, monitor: "HealthMonitor") -> list[str]:
        lines: list[str] = [
            f"    option httpchk {monitor.http_method} {monitor.url_path}",
            f"    timeout check {monitor.timeout}s",
        ]
        return lines
