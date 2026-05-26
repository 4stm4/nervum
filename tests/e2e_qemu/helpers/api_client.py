"""Small HTTP client wrapper for real QEMU E2E tests."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        request_headers = dict(headers or {})
        if self.token:
            request_headers.setdefault("Authorization", f"Bearer {self.token}")
        return self._client.request(
            method,
            path,
            json=json,
            headers=request_headers,
            params=params,
        )

    def get(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("PATCH", path, **kwargs)

    def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        return self.request("DELETE", path, **kwargs)

    def create_project(self, *, name: str, slug: str) -> dict[str, Any]:
        response = self.post("/api/v1/projects", json={"name": name, "slug": slug})
        assert response.status_code == 201, response.text
        return dict(response.json())

    def create_service_account(self, *, name: str, role: str) -> dict[str, Any]:
        response = self.post("/api/v1/service-accounts", json={"name": name, "role": role})
        assert response.status_code == 201, response.text
        return dict(response.json())

    def issue_token(self, account_id: str) -> str:
        response = self.post(f"/api/v1/service-accounts/{account_id}/tokens", json={})
        assert response.status_code == 201, response.text
        return str(response.json()["plaintext"])

    def add_project_member(
        self,
        *,
        project_id: str,
        service_account_id: str,
        role: str,
    ) -> dict[str, Any]:
        response = self.put_member(project_id, service_account_id, role)
        assert response.status_code == 200, response.text
        return dict(response.json())

    def put_member(self, project_id: str, service_account_id: str, role: str) -> httpx.Response:
        return self.request(
            "PUT",
            f"/api/v1/projects/{project_id}/members/{service_account_id}",
            json={"service_account_id": service_account_id, "role": role},
        )
