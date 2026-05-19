"""HTTP-клиент CLI поверх ``httpx``.

CLI работает поверх public northbound API контроллера, то есть тех же
ручек, что и любой другой клиент: Bearer-токен в ``Authorization``,
``X-Request-Id`` принимается обратно для корреляции с логами.

Адрес контроллера и токен берутся из (в порядке приоритета):

1. флагов командной строки ``--url`` / ``--token``;
2. переменных окружения ``SDN_CONTROLLER_URL`` / ``SDN_TOKEN``;
3. дефолта ``http://127.0.0.1:8080``.

Токен **обязателен**, если контроллер запущен с включённым auth — без
него любой запрос вернёт 401. Для dev-инсталляций (``auth_enabled=False``)
токен можно опустить.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

from sdn_controller import __version__

_DEFAULT_URL = "http://127.0.0.1:8080"
_API_PREFIX = "/api/v1"
_HTTP_ERROR_THRESHOLD = 400
_HTTP_NO_CONTENT = 204


@dataclass(frozen=True, slots=True)
class CliSettings:
    """Резолвленные настройки CLI на момент запуска команды."""

    url: str
    token: str | None

    @classmethod
    def resolve(cls, *, url: str | None, token: str | None) -> CliSettings:
        resolved_url = url or os.environ.get("SDN_CONTROLLER_URL") or _DEFAULT_URL
        resolved_token = token or os.environ.get("SDN_TOKEN") or None
        return cls(url=resolved_url.rstrip("/"), token=resolved_token)


class CliApiError(Exception):
    """Сетевая или серверная ошибка, поднятая в CLI.

    Стандартизованный конверт ``{"error":{"code","message","details"}}``
    — выдаёт сервер; мы здесь прокидываем ``code`` для красивого
    рендера и ``http_status`` для exit-кода (1 для 4xx, 2 для 5xx и
    транспортных проблем).
    """

    def __init__(self, message: str, *, http_status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


class CliApiClient:
    """Тонкий async-обёртка над ``httpx`` для CLI-команд.

    Нельзя дёргать руками — экземпляр живёт в ``async with``-блоке,
    чтобы коннекшен-пул закрывался даже при ошибках.
    """

    def __init__(self, settings: CliSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        headers = {"User-Agent": f"sdnctl/{__version__}"}
        if settings.token:
            headers["Authorization"] = f"Bearer {settings.token}"
        self._client = httpx.AsyncClient(
            base_url=settings.url,
            headers=headers,
            timeout=30.0,
            transport=transport,
        )

    async def __aenter__(self) -> CliApiClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    # -- HTTP ---------------------------------------------------------

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=params)

    async def post(
        self,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        return await self._request("POST", path, json=json, params=params)

    async def patch(self, path: str, *, json: Any = None) -> Any:
        return await self._request("PATCH", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        # Команды CLI передают относительные пути (``/networks``, ``/topology``).
        # Абсолютный путь с ``/api/`` или ``/metrics`` считаем уже полным.
        if path.startswith("/api/") or path.startswith("/metrics"):
            url = path
        else:
            normalised = path if path.startswith("/") else f"/{path}"
            url = f"{_API_PREFIX}{normalised}"
        try:
            response = await self._client.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            raise CliApiError(
                f"transport error: {exc}",
                http_status=None,
                code="transport_error",
            ) from exc
        if response.status_code >= _HTTP_ERROR_THRESHOLD:
            _raise_for_envelope(response)
        if response.status_code == _HTTP_NO_CONTENT or not response.content:
            return None
        return response.json()


def _raise_for_envelope(response: httpx.Response) -> None:
    """Распарсить error envelope, если он есть."""
    code: str | None = None
    message = response.text
    try:
        body = response.json()
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            err = body["error"]
            code = err.get("code")
            message = err.get("message") or message
    except (ValueError, KeyError):
        pass
    raise CliApiError(message, http_status=response.status_code, code=code)


__all__ = ["CliApiClient", "CliApiError", "CliSettings"]
