"""``sdnctl nodes ...`` — управление узлами."""

from __future__ import annotations

import argparse
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json, print_table


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("nodes", help="Управление узлами")
    sub = parser.add_subparsers(dest="nodes_command", required=True)

    p_list = sub.add_parser("list", help="Показать узлы")
    p_list.set_defaults(handler=_list)

    p_register = sub.add_parser("register", help="Зарегистрировать узел в pending-статусе")
    p_register.add_argument("name")
    p_register.add_argument("--mgmt-ip", required=True, help="Адрес агента")
    p_register.add_argument(
        "--label",
        action="append",
        default=[],
        help="key=value метка (можно повторять)",
    )
    p_register.set_defaults(handler=_register)

    p_token = sub.add_parser("enroll-token", help="Выпустить одноразовый enrollment-токен")
    p_token.add_argument("node_id")
    p_token.set_defaults(handler=_enroll_token)

    p_remove = sub.add_parser("remove", help="Удалить узел")
    p_remove.add_argument("node_id")
    p_remove.set_defaults(handler=_remove)


def _parse_labels(raw: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"label must be 'key=value', got: {item!r}")
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip()
    return out


async def _list(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get("/nodes")
    if args.output == "json":
        print_json(body)
        return
    items = body.get("items", [])
    print_table(
        ("ID", "NAME", "MGMT_IP", "STATUS", "LAST_SEEN"),
        [
            (it["id"], it["name"], it["mgmt_ip"], it["status"], it.get("last_seen_at"))
            for it in items
        ],
    )


async def _register(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.post(
        "/nodes",
        json={
            "name": args.name,
            "mgmt_ip": args.mgmt_ip,
            "labels": _parse_labels(args.label),
        },
    )
    if args.output == "json":
        print_json(body)
        return
    node = body["node"]
    print(f"node {node['id']} registered (status={node['status']})")


async def _enroll_token(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.post(f"/nodes/{args.node_id}/enroll-token")
    if args.output == "json":
        print_json(body)
        return
    print(f"token: {body['token']}")
    print(f"expires_at: {body['expires_at']}")


async def _remove(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.delete(f"/nodes/{args.node_id}")
    if args.output == "json":
        print_json(body)
        return
    print(f"node {args.node_id} removal accepted (operation={body['operation_id']})")
