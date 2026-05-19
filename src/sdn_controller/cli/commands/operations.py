"""``sdnctl operations ...`` — список/просмотр/watch операций."""

from __future__ import annotations

import argparse
import asyncio
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json, print_table

_TERMINAL = {"succeeded", "failed", "cancelled", "rolled_back"}
_POLL_SECONDS = 1.0


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("operations", help="Операции (audit-trail mutations)")
    sub = parser.add_subparsers(dest="operations_command", required=True)

    p_list = sub.add_parser("list", help="Последние операции")
    p_list.add_argument("--limit", type=int, default=20)
    p_list.set_defaults(handler=_list)

    p_show = sub.add_parser("show", help="Одна операция (с событиями)")
    p_show.add_argument("operation_id")
    p_show.set_defaults(handler=_show)

    p_watch = sub.add_parser("watch", help="Поллинг до достижения терминального статуса")
    p_watch.add_argument("operation_id")
    p_watch.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Максимум секунд ожидания (по умолчанию 120)",
    )
    p_watch.set_defaults(handler=_watch)


async def _list(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get("/operations", params={"limit": args.limit})
    if args.output == "json":
        print_json(body)
        return
    print_table(
        ("ID", "KIND", "STATUS", "CREATED_AT"),
        [(it["id"], it["kind"], it["status"], it["created_at"]) for it in body.get("items", [])],
    )


async def _show(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get(f"/operations/{args.operation_id}")
    if args.output == "json":
        print_json(body)
        return
    print(f"operation {body['id']} kind={body['kind']} status={body['status']}")
    print(f"created_at={body['created_at']} updated_at={body['updated_at']}")
    if body.get("error"):
        err = body["error"]
        print(f"error: code={err['code']} message={err['message']}")
    print("\nevents:")
    print_table(
        ("SEQ", "AT", "STATUS", "MESSAGE"),
        [
            (evt["sequence"], evt["at"], evt["status"], evt["message"])
            for evt in body.get("events", [])
        ],
    )


async def _watch(args: argparse.Namespace, client: CliApiClient) -> int:
    """Поллим operation каждую секунду, печатаем новые events."""
    seen_sequence = -1
    deadline = asyncio.get_event_loop().time() + args.timeout
    while True:
        body = await client.get(f"/operations/{args.operation_id}")
        for evt in body.get("events", []):
            if evt["sequence"] > seen_sequence:
                print(f"[{evt['at']}] {evt['status']:>10} | {evt['message']}")
                seen_sequence = evt["sequence"]
        status = body["status"]
        if status in _TERMINAL:
            if body.get("error"):
                err = body["error"]
                print(f"FAILED: code={err['code']} message={err['message']}")
            return 0 if status == "succeeded" else 1
        if asyncio.get_event_loop().time() >= deadline:
            print(f"timeout: operation still {status!r}")
            return 2
        await asyncio.sleep(_POLL_SECONDS)
