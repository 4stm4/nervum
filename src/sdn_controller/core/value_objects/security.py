"""Роли и права northbound API (SDN-030).

Дизайн RBAC — простой и сразу понятный оператору:

* ``admin`` — может всё, включая выдачу/отзыв токенов и удаление узлов;
* ``network_operator`` — управляет сетями, IPAM и apply, но не управляет
  service accounts/tokens и не удаляет узлы;
* ``automation`` — то же, что network_operator, но без чтения секретов
  (списка токенов): автомат регистрируется и работает по своему токену,
  не должен видеть чужие;
* ``viewer`` — только чтение всего, что не секрет.

Permission'ы выбраны мелкими: их легко комбинировать в требования
эндпоинтов, а будущий OIDC mapping будет ложиться на тот же набор.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    """Доступные роли сервисных аккаунтов."""

    ADMIN = "admin"
    NETWORK_OPERATOR = "network_operator"
    AUTOMATION = "automation"
    VIEWER = "viewer"


class Permission(StrEnum):
    """Атомарные права на ресурсы northbound API.

    Имя следует шаблону ``<resource>:<verb>`` — read/write/admin.
    ``admin`` — это «всё, чего read+write не покрывают» (например,
    удаление узлов).
    """

    # ---- networks --------------------------------------------------
    NETWORK_READ = "network:read"
    NETWORK_WRITE = "network:write"
    NETWORK_APPLY = "network:apply"

    # ---- nodes -----------------------------------------------------
    NODE_READ = "node:read"
    NODE_WRITE = "node:write"  # регистрация + heartbeat
    NODE_ADMIN = "node:admin"  # удаление, выпуск токенов enrollment

    # ---- ipam ------------------------------------------------------
    IPAM_READ = "ipam:read"
    IPAM_WRITE = "ipam:write"

    # ---- operations ------------------------------------------------
    OPERATION_READ = "operation:read"

    # ---- topology / drift -----------------------------------------
    TOPOLOGY_READ = "topology:read"
    DRIFT_READ = "drift:read"

    # ---- security (service accounts / tokens) ---------------------
    SERVICE_ACCOUNT_READ = "service_account:read"
    SERVICE_ACCOUNT_WRITE = "service_account:write"
    SERVICE_TOKEN_READ = "service_token:read"  # noqa: S105 — это название права, а не секрет
    SERVICE_TOKEN_WRITE = "service_token:write"  # noqa: S105 — это название права, а не секрет

    # ---- observability --------------------------------------------
    AUDIT_READ = "audit:read"

    # ---- backup / restore ------------------------------------------
    BACKUP_EXPORT = "backup:export"
    BACKUP_IMPORT = "backup:import"
    SNAPSHOT_READ = "snapshot:read"
    SNAPSHOT_WRITE = "snapshot:write"

    # ---- webhooks (SDN-054) ---------------------------------------
    WEBHOOK_READ = "webhook:read"
    WEBHOOK_WRITE = "webhook:write"

    # ---- projects (N0 — multitenancy) ------------------------------
    PROJECT_READ = "project:read"
    PROJECT_WRITE = "project:write"
    PROJECT_ADMIN = "project:admin"  # add/remove members, delete project


# Полный набор — для admin.
_ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)


# Базовые «писательские» возможности для оператора сети.
_NETWORK_OPERATOR_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.NETWORK_READ,
        Permission.NETWORK_WRITE,
        Permission.NETWORK_APPLY,
        Permission.NODE_READ,
        Permission.NODE_WRITE,
        Permission.IPAM_READ,
        Permission.IPAM_WRITE,
        Permission.OPERATION_READ,
        Permission.TOPOLOGY_READ,
        Permission.DRIFT_READ,
        Permission.PROJECT_READ,
        Permission.PROJECT_WRITE,
    }
)


# Automation: те же возможности, что у оператора, кроме чтения секретов.
# Они тоже могут писать сети — это нужно для CI/инфраструктурных пайплайнов,
# которые катят конфигурацию.
_AUTOMATION_PERMISSIONS: frozenset[Permission] = _NETWORK_OPERATOR_PERMISSIONS


# Viewer — только чтение.
_VIEWER_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.NETWORK_READ,
        Permission.NODE_READ,
        Permission.IPAM_READ,
        Permission.OPERATION_READ,
        Permission.TOPOLOGY_READ,
        Permission.DRIFT_READ,
        Permission.PROJECT_READ,
    }
)


# Используем ``MappingProxyType`` для немутабельности — кто-то ещё может
# случайно подсунуть .add() в frozenset, но добавить новую роль в маппинг
# извне не получится.
ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.ADMIN: _ALL_PERMISSIONS,
        Role.NETWORK_OPERATOR: _NETWORK_OPERATOR_PERMISSIONS,
        Role.AUTOMATION: _AUTOMATION_PERMISSIONS,
        Role.VIEWER: _VIEWER_PERMISSIONS,
    }
)


def role_has_permission(role: Role, permission: Permission) -> bool:
    """``True`` если роль ``role`` имеет право ``permission``."""
    return permission in ROLE_PERMISSIONS[role]


__all__ = ["ROLE_PERMISSIONS", "Permission", "Role", "role_has_permission"]
