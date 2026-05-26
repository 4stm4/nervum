"""BondConfigurator — генерация конфигурации агрегации каналов (N4-04).

Формирует фрагмент netplan YAML или ifupdown-стиля для LACP /
active-backup bonding. Результат сохраняется в GatewayBond.applied_config.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sdn_controller.core.entities.gateway_bond import GatewayBond


class BondConfigurator:
    """Генерирует netplan-конфиг для GatewayBond (N4-04)."""

    def generate(self, bond: "GatewayBond", *, now: datetime | None = None) -> str:
        """Возвращает netplan YAML-фрагмент для bond-интерфейса."""
        ts = (now or datetime.utcnow()).isoformat()
        mode_map = {
            "active_backup": "active-backup",
            "lacp": "802.3ad",
            "none": "active-backup",
        }
        bond_mode = mode_map.get(bond.mode.value, "active-backup")
        members_yaml = "\n".join(f"              - {m}" for m in bond.members)

        return (
            f"# SDN Controller — netplan bond config\n"
            f"# bond={bond.id} node={bond.node_id} generated={ts}\n"
            f"network:\n"
            f"  version: 2\n"
            f"  bonds:\n"
            f"    {bond.bond_name}:\n"
            f"      interfaces:\n"
            f"{members_yaml}\n"
            f"      parameters:\n"
            f"        mode: {bond_mode}\n"
            f"        lacp-rate: fast\n"
            f"        mii-monitor-interval: 100\n"
            f"      mtu: {bond.mtu}\n"
        )
