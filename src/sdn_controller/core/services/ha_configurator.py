"""HaConfigurator — генератор keepalived.conf для VRRP HA (N3-06).

Принимает Router с ha_mode=VRRP и генерирует конфиг keepalived,
который агент записывает в ``/etc/keepalived/keepalived.conf``.

Служба — чистый доменный объект: нет I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdn_controller.core.entities.router import Router

from sdn_controller.core.value_objects.enums import HaMode
from sdn_controller.core.value_objects.errors import ValidationError


class HaConfigurator:
    """Генерирует keepalived.conf для VRRP (N3-06)."""

    def generate(
        self,
        router: "Router",
        *,
        interface: str = "eth0",
        virtual_ip: str = "0.0.0.0",
        now: datetime | None = None,
    ) -> str:
        """Создаёт keepalived.conf для маршрутизатора.

        Args:
            router:     сущность Router с ha_mode=VRRP.
            interface:  имя сетевого интерфейса (агент подставляет реальное).
            virtual_ip: виртуальный IP (VIP) VRRP-группы.
            now:        момент генерации.

        Returns:
            Строка конфига keepalived, пригодная для записи в файл.

        Raises:
            ValidationError: если ha_mode != VRRP.
        """
        if router.ha_mode != HaMode.VRRP:
            raise ValidationError(
                f"HaConfigurator.generate() вызван для маршрутизатора с ha_mode={router.ha_mode!r}; "
                "ожидается ha_mode=vrrp"
            )
        if now is None:
            now = datetime.now(tz=timezone.utc)

        vrid = router.vrrp_vrid or 1
        priority = router.vrrp_priority or 100
        state = "MASTER" if priority >= 100 else "BACKUP"

        lines: list[str] = [
            f"# SDN Controller — keepalived конфиг",
            f"# Router: {router.name} ({router.id})",
            f"# Сгенерировано: {now.isoformat()}",
            "",
            "global_defs {",
            f"    router_id {router.id}",
            "}",
            "",
            "vrrp_instance SDN_HA {",
            f"    state {state}",
            f"    interface {interface}",
            f"    virtual_router_id {vrid}",
            f"    priority {priority}",
            "    advert_int 1",
            "    authentication {",
            "        auth_type PASS",
            f"        auth_pass sdn{vrid:03d}",
            "    }",
            "    virtual_ipaddress {",
            f"        {virtual_ip}",
            "    }",
            "}",
        ]
        return "\n".join(lines)
