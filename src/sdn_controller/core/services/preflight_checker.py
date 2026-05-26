"""PreflightChecker — валидация ресурсов перед применением (N4-02).

Проверяет Router и Network до вызова apply, чтобы выявить ошибки
конфигурации до генерации скриптов и отправки на агент.
"""

from __future__ import annotations

from dataclasses import dataclass

from sdn_controller.core.entities.router import Router
from sdn_controller.core.value_objects.enums import HaMode, Ipv6Mode


@dataclass(frozen=True)
class PreflightIssue:
    """Одна проблема, обнаруженная при предварительной проверке."""

    severity: str    # "error" | "warning"
    code: str        # машиночитаемый код
    message: str     # описание для человека


class PreflightChecker:
    """Проверяет конфигурацию Router перед ApplyRouter (N4-02).

    Возвращает список ``PreflightIssue``. Пустой список — всё в порядке.
    Use case PreflightRouter поднимает исключение, если есть ERROR-уровень.
    """

    def check_router(self, router: Router) -> list[PreflightIssue]:
        """Запускает все проверки маршрутизатора."""
        issues: list[PreflightIssue] = []
        self._check_external_network(router, issues)
        self._check_routes(router, issues)
        self._check_ha(router, issues)
        self._check_ipv6(router, issues)
        self._check_admin_state(router, issues)
        return issues

    def _check_external_network(self, router: Router, issues: list[PreflightIssue]) -> None:
        if router.external_network_id is None and router.static_routes:
            issues.append(PreflightIssue(
                severity="warning",
                code="NO_EXTERNAL_NETWORK",
                message="маршрутизатор имеет статические маршруты, но не подключён к внешней сети",
            ))

    def _check_routes(self, router: Router, issues: list[PreflightIssue]) -> None:
        seen: set[str] = set()
        for route in router.static_routes:
            if route.destination in seen:
                issues.append(PreflightIssue(
                    severity="error",
                    code="DUPLICATE_ROUTE",
                    message=f"дублирующийся маршрут: {route.destination}",
                ))
            seen.add(route.destination)

    def _check_ha(self, router: Router, issues: list[PreflightIssue]) -> None:
        if router.ha_mode == HaMode.VRRP:
            if router.vrrp_priority is None:
                issues.append(PreflightIssue(
                    severity="error",
                    code="VRRP_NO_PRIORITY",
                    message="ha_mode=vrrp требует vrrp_priority",
                ))
            if router.vrrp_vrid is None:
                issues.append(PreflightIssue(
                    severity="error",
                    code="VRRP_NO_VRID",
                    message="ha_mode=vrrp требует vrrp_vrid",
                ))

    def _check_ipv6(self, router: Router, issues: list[PreflightIssue]) -> None:
        cfg = router.ipv6_config
        if cfg is None or cfg.mode == Ipv6Mode.OFF:
            return
        if not cfg.prefix:
            issues.append(PreflightIssue(
                severity="error",
                code="IPV6_NO_PREFIX",
                message=f"ipv6_mode={cfg.mode} требует ipv6_prefix",
            ))

    def _check_admin_state(self, router: Router, issues: list[PreflightIssue]) -> None:
        if not router.admin_state_up:
            issues.append(PreflightIssue(
                severity="warning",
                code="ADMIN_DOWN",
                message="маршрутизатор административно выключен (admin_state_up=False)",
            ))
