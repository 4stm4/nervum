"""End-to-end CLI: argparse → ``CliApiClient`` через ASGI → FastAPI.

Эти тесты заменяют только транспорт ``httpx`` на ``ASGITransport``,
а всё остальное (парсер, форматирование, error handling) гоняется как
в проде. Покрываем основные команды одной-двумя ассертами на каждую —
сюда же приземлится защита от смены контрактов API.
"""

from __future__ import annotations

import json
import sys

import httpx
import pytest
from fastapi import FastAPI

from sdn_controller.adapters.http_api import create_app
from sdn_controller.adapters.netos_agent import FakeAgent
from sdn_controller.app.config import Settings
from sdn_controller.app.container import build_container
from sdn_controller.cli.client import CliApiClient, CliApiError, CliSettings
from sdn_controller.cli.main import build_parser
from tests.conftest import CountingIdFactory, FrozenClock, SequentialTokenFactory


@pytest.fixture
def shared_agent(clock: FrozenClock) -> FakeAgent:
    return FakeAgent(clock=clock)


@pytest.fixture
def app(
    clock: FrozenClock,
    ids: CountingIdFactory,
    token_factory: SequentialTokenFactory,
    shared_agent: FakeAgent,
) -> FastAPI:
    settings = Settings(
        persistence="memory",
        log_level="WARNING",
        log_format="console",
        auth_enabled=False,
    )
    container = build_container(
        settings,
        clock=clock,
        ids=ids,
        token_factory=token_factory,
        agent=shared_agent,
    )
    return create_app(container)


_HTTP_SERVER_ERROR_THRESHOLD = 500


async def _run(
    app: FastAPI,
    argv: list[str],
    capsys: pytest.CaptureFixture[str] | None = None,
) -> int:
    """Прогнать CLI-команду через ASGI-транспорт.

    Повторяет error-handling из ``main._run_async``: ловим
    ``CliApiError`` и возвращаем 1 (4xx) / 2 (transport или 5xx).
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = CliSettings(url="http://controller", token=None)
    transport = httpx.ASGITransport(app=app)
    async with CliApiClient(settings, transport=transport) as client:
        try:
            rc = await args.handler(args, client)
        except CliApiError as exc:
            print(f"error: {exc}", file=sys.stderr)
            if exc.http_status is None:
                return 2
            return 1 if exc.http_status < _HTTP_SERVER_ERROR_THRESHOLD else 2
    return int(rc) if isinstance(rc, int) else 0


# ---------------------------------------------------------------------------
# nodes
# ---------------------------------------------------------------------------


async def test_cli_nodes_register_and_list_json(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = await _run(
        app,
        ["--output", "json", "nodes", "register", "node-a", "--mgmt-ip", "10.0.0.1"],
    )
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["node"]["name"] == "node-a"

    rc = await _run(app, ["--output", "json", "nodes", "list"])
    assert rc == 0
    listing = json.loads(capsys.readouterr().out)
    assert any(n["name"] == "node-a" for n in listing["items"])


async def test_cli_nodes_list_table_default(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(app, ["nodes", "register", "node-a", "--mgmt-ip", "10.0.0.1"])
    capsys.readouterr()  # очистим
    await _run(app, ["nodes", "list"])
    out = capsys.readouterr().out
    assert "NAME" in out
    assert "node-a" in out


# ---------------------------------------------------------------------------
# networks
# ---------------------------------------------------------------------------


async def test_cli_network_create_and_apply_by_name(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(app, ["nodes", "register", "node-a", "--mgmt-ip", "10.0.0.1"])
    # Извлечём id зарегистрированного узла, чтобы дать его создающей сети.
    capsys.readouterr()
    rc = await _run(app, ["--output", "json", "nodes", "list"])
    assert rc == 0
    nodes = json.loads(capsys.readouterr().out)["items"]
    node_id = next(n["id"] for n in nodes if n["name"] == "node-a")

    rc = await _run(
        app,
        ["networks", "create", "prod", "--type", "vxlan", "--vni", "10100", "--node", node_id],
    )
    assert rc == 0
    capsys.readouterr()

    # apply по *имени* (нашему resolver'у пришлось пройти 404→list lookup)
    rc = await _run(app, ["networks", "apply", "prod"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "succeeded" in out


# ---------------------------------------------------------------------------
# topology, drift
# ---------------------------------------------------------------------------


async def test_cli_topology_empty(app: FastAPI, capsys: pytest.CaptureFixture[str]) -> None:
    rc = await _run(app, ["--output", "json", "topology"])
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["nodes"] == []
    assert body["networks"] == []


async def test_cli_drift_returns_zero_when_clean(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = await _run(app, ["drift", "scan"])
    assert rc == 0
    assert "no drift" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# audit + backup
# ---------------------------------------------------------------------------


async def test_cli_audit_list_after_mutation(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    await _run(app, ["nodes", "register", "node-a", "--mgmt-ip", "10.0.0.1"])
    capsys.readouterr()
    rc = await _run(app, ["--output", "json", "audit", "list", "--action", "node.register"])
    assert rc == 0
    items = json.loads(capsys.readouterr().out)["items"]
    assert any(it["action"] == "node.register" for it in items)


async def test_cli_backup_export_outputs_json(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = await _run(app, ["backup", "export"])
    assert rc == 0
    out = capsys.readouterr().out
    body = json.loads(out)
    assert body["manifest"]["schema_version"] == 1


# ---------------------------------------------------------------------------
# error handling
# ---------------------------------------------------------------------------


async def test_cli_returns_exit_code_1_on_404(
    app: FastAPI,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = await _run(app, ["nodes", "remove", "node_missing"])
    assert rc == 1
    assert "error" in capsys.readouterr().err
