"""``sdnctl audit list`` — лента аудита."""

from __future__ import annotations

import argparse
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json, print_table


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("audit", help="Лента аудита")
    sub = parser.add_subparsers(dest="audit_command", required=True)

    p_list = sub.add_parser("list", help="Последние audit-события")
    p_list.add_argument("--actor")
    p_list.add_argument("--action")
    p_list.add_argument("--resource-type")
    p_list.add_argument("--resource-id")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(handler=_list)


async def _list(args: argparse.Namespace, client: CliApiClient) -> None:
    params: dict[str, Any] = {"limit": args.limit}
    for key in ("actor", "action"):
        value = getattr(args, key)
        if value is not None:
            params[key] = value
    if args.resource_type is not None:
        params["resource_type"] = args.resource_type
    if args.resource_id is not None:
        params["resource_id"] = args.resource_id

    body = await client.get("/audit-events", params=params)
    if args.output == "json":
        print_json(body)
        return
    print_table(
        ("AT", "ACTOR", "ACTION", "RESOURCE", "STATUS"),
        [
            (
                it["at"],
                it.get("actor") or "-",
                it["action"],
                f"{it['resource_type']}:{it.get('resource_id') or '-'}",
                it.get("http_status") or "-",
            )
            for it in body.get("items", [])
        ],
    )
