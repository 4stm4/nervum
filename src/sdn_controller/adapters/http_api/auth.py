"""HTTP-аутентификация: Bearer-токены + декларативные требования прав.

Дизайн:

* ``current_principal`` — главная зависимость. Достаёт ``Authorization``,
  отдаёт plaintext в ``AuthenticatePrincipal`` (горячий путь). Если
  ``auth_enabled=False`` (тестовый/демо-режим), возвращает stub-админа —
  тогда никакой токен не нужен, но реальные эндпоинты на это не
  опираются: продакшн всегда включает auth.

* ``require(*permissions)`` — фабрика зависимостей: проверяет, что у
  текущего ``Principal``'а есть все указанные ``Permission``. Если нет —
  бросает ``ForbiddenError``, exception-handler превращает в 403.

Раздельные ``current_principal`` и ``require(...)`` нужны, чтобы
эндпоинты могли получить principal сами (например, для аудита) и при
этом не дублировать логику разбора заголовка.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request

from sdn_controller.adapters.http_api.dependencies import (
    AuthenticatePrincipalDep,
    ContainerDep,
)
from sdn_controller.core.entities import Principal
from sdn_controller.core.value_objects.errors import (
    ForbiddenError,
    UnauthorizedError,
)
from sdn_controller.core.value_objects.ids import ServiceAccountId
from sdn_controller.core.value_objects.security import (
    Permission,
    Role,
    role_has_permission,
)

_BEARER = "bearer "


def _disabled_principal() -> Principal:
    """Stub для ``auth_enabled=False`` — притворяемся, что запрос пришёл
    от полноправного админа. Используется только в тестах и dev-демо."""
    return Principal(
        service_account_id=ServiceAccountId("disabled-auth"),
        name="auth-disabled",
        role=Role.ADMIN,
    )


async def current_principal(
    request: Request,
    container: ContainerDep,
    auth_uc: AuthenticatePrincipalDep,
) -> Principal:
    """Извлечь и проверить Bearer-токен.

    После успешной аутентификации principal кладётся в ``request.state``,
    чтобы middleware'ы (audit, observability) могли увидеть, кто именно
    отработал запрос, не делая повторную аутентификацию.
    """
    if not container.settings.auth_enabled:
        principal = _disabled_principal()
        request.state.principal = principal
        return principal

    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER):
        raise UnauthorizedError("missing or malformed Authorization header")
    plaintext = header[len(_BEARER) :].strip()
    principal = await auth_uc.execute(plaintext)
    request.state.principal = principal
    return principal


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require(*permissions: Permission) -> Callable[[Principal], Awaitable[Principal]]:
    """Фабрика FastAPI-зависимости: возвращает зависимость, которая
    падает с ``ForbiddenError``, если у принципала нет всех прав.

    Использование::

        @router.get(
            "/networks",
            dependencies=[Depends(require(Permission.NETWORK_READ))],
        )
        async def list_networks(...): ...
    """

    async def _check(principal: CurrentPrincipal) -> Principal:
        missing = [p for p in permissions if not role_has_permission(principal.role, p)]
        if missing:
            raise ForbiddenError(
                f"role {principal.role.value!r} lacks permission(s): "
                + ", ".join(p.value for p in missing)
            )
        return principal

    return _check


__all__ = ["CurrentPrincipal", "current_principal", "require"]
