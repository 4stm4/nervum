"""Unit-тесты для ``Role`` + ``Permission`` + ``ROLE_PERMISSIONS``.

Проверяем границу ролей: что admin может всё, что viewer не может ничего
писать, что network_operator/automation идентичны для писательских прав
и что в каталоге прав нет «дыр» (каждой роли — известный набор).
"""

from __future__ import annotations

import pytest

from sdn_controller.core.value_objects.security import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    role_has_permission,
)


def test_admin_has_every_permission() -> None:
    for p in Permission:
        assert role_has_permission(Role.ADMIN, p), p


def test_viewer_cannot_write() -> None:
    write_perms = {
        Permission.NETWORK_WRITE,
        Permission.NETWORK_APPLY,
        Permission.NODE_WRITE,
        Permission.NODE_ADMIN,
        Permission.IPAM_WRITE,
        Permission.SERVICE_ACCOUNT_WRITE,
        Permission.SERVICE_TOKEN_WRITE,
    }
    for p in write_perms:
        assert not role_has_permission(Role.VIEWER, p), p


def test_network_operator_and_automation_share_baseline() -> None:
    assert ROLE_PERMISSIONS[Role.NETWORK_OPERATOR] == ROLE_PERMISSIONS[Role.AUTOMATION]


def test_no_role_can_manage_service_accounts_except_admin() -> None:
    for role in (Role.NETWORK_OPERATOR, Role.AUTOMATION, Role.VIEWER):
        assert not role_has_permission(role, Permission.SERVICE_ACCOUNT_WRITE)
        assert not role_has_permission(role, Permission.SERVICE_TOKEN_WRITE)


def test_every_role_is_in_catalog() -> None:
    for role in Role:
        assert role in ROLE_PERMISSIONS


@pytest.mark.parametrize(
    "role,permission",
    [
        (Role.NETWORK_OPERATOR, Permission.NETWORK_READ),
        (Role.NETWORK_OPERATOR, Permission.NETWORK_WRITE),
        (Role.NETWORK_OPERATOR, Permission.NETWORK_APPLY),
        (Role.AUTOMATION, Permission.IPAM_WRITE),
        (Role.VIEWER, Permission.OPERATION_READ),
        (Role.VIEWER, Permission.DRIFT_READ),
    ],
)
def test_expected_permissions_present(role: Role, permission: Permission) -> None:
    assert role_has_permission(role, permission)
