"""``sdnctl`` — entrypoint (M12).

CLI собирается на ``argparse`` без дополнительных зависимостей.
Каждая верхнеуровневая команда живёт в ``sdn_controller.cli.commands.*``
и регистрирует свои подкоманды через ``register(subparsers)``.

Все команды async, потому что используют один async ``CliApiClient``;
``asyncio.run`` живёт ровно один раз на запуск процесса.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from sdn_controller import __version__
from sdn_controller.cli.client import CliApiClient, CliApiError, CliSettings
from sdn_controller.cli.commands import (
    audit,
    backup,
    drift,
    networks,
    nodes,
    operations,
    snapshots,
    topology,
)

if TYPE_CHECKING:
    Handler = Callable[[argparse.Namespace, CliApiClient], Awaitable[int | None]]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdnctl",
        description="CLI клиент к SDN Controller.",
    )
    parser.add_argument("--version", action="version", version=f"sdnctl {__version__}")
    parser.add_argument(
        "--url",
        help="Адрес контроллера (по умолчанию $SDN_CONTROLLER_URL или http://127.0.0.1:8080)",
    )
    parser.add_argument(
        "--token",
        help="Bearer-токен (по умолчанию $SDN_TOKEN)",
    )
    parser.add_argument(
        "--output",
        "-o",
        choices=("table", "json"),
        default="table",
        help="Формат вывода (по умолчанию table)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="команда")

    nodes.register(subparsers)
    networks.register(subparsers)
    operations.register(subparsers)
    topology.register(subparsers)
    drift.register(subparsers)
    audit.register(subparsers)
    backup.register(subparsers)
    snapshots.register(subparsers)

    return parser


_HTTP_SERVER_ERROR_THRESHOLD = 500


async def _run_async(args: argparse.Namespace) -> int:
    settings = CliSettings.resolve(
        url=getattr(args, "url", None),
        token=getattr(args, "token", None),
    )
    handler: Handler = args.handler
    async with CliApiClient(settings) as client:
        try:
            result = await handler(args, client)
        except CliApiError as exc:
            print(f"error: {exc}", file=sys.stderr)
            if exc.code:
                print(f"code: {exc.code}", file=sys.stderr)
            if exc.http_status is None:
                return 2
            return 1 if exc.http_status < _HTTP_SERVER_ERROR_THRESHOLD else 2
    return int(result) if isinstance(result, int) else 0


def run(argv: list[str] | None = None) -> int:
    """Entry point, на который ссылается ``sdnctl`` в pyproject."""
    parser = build_parser()
    args = parser.parse_args(argv)
    return asyncio.run(_run_async(args))


if __name__ == "__main__":  # pragma: no cover — обычный shebang-путь
    raise SystemExit(run())


__all__ = ["build_parser", "run"]
