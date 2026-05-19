"""``sdnctl networks ...``."""

from __future__ import annotations

import argparse
from typing import Any

from sdn_controller.cli.client import CliApiClient, CliApiError
from sdn_controller.cli.format import print_json, print_table


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("networks", help="Управление сетями")
    sub = parser.add_subparsers(dest="networks_command", required=True)

    p_list = sub.add_parser("list", help="Показать сети")
    p_list.set_defaults(handler=_list)

    p_show = sub.add_parser("show", help="Показать одну сеть (по id или имени)")
    p_show.add_argument("identifier")
    p_show.set_defaults(handler=_show)

    p_create = sub.add_parser("create", help="Создать сеть")
    p_create.add_argument("name")
    p_create.add_argument("--type", required=True, choices=("flat", "vlan", "vxlan"))
    p_create.add_argument("--mtu", type=int, default=1500)
    p_create.add_argument("--vlan-id", type=int)
    p_create.add_argument("--vni", type=int)
    p_create.add_argument("--cidr", help="Создать subnet с этим CIDR")
    p_create.add_argument("--gateway", help="Шлюз сабнета")
    p_create.add_argument(
        "--node",
        action="append",
        default=[],
        help="Привязать сеть к этому node_id (можно повторять)",
    )
    p_create.set_defaults(handler=_create)

    p_apply = sub.add_parser("apply", help="Применить сеть (observe → diff → push → verify)")
    p_apply.add_argument("identifier", help="id или имя сети")
    p_apply.set_defaults(handler=_apply)

    p_assign = sub.add_parser("assign-nodes", help="Заменить список узлов сети")
    p_assign.add_argument("identifier", help="id или имя сети")
    p_assign.add_argument("--node", action="append", required=True, default=[])
    p_assign.set_defaults(handler=_assign)


async def _list(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get("/networks")
    if args.output == "json":
        print_json(body)
        return
    print_table(
        ("ID", "NAME", "TYPE", "VLAN/VNI", "MTU", "INTENT", "NODES"),
        [
            (
                it["id"],
                it["name"],
                it["type"],
                it.get("vni") or it.get("vlan_id") or "-",
                it["mtu"],
                it["intent_version"],
                len(it.get("node_ids") or []),
            )
            for it in body.get("items", [])
        ],
    )


_HTTP_NOT_FOUND = 404


async def _resolve(client: CliApiClient, identifier: str) -> str:
    """Принимает либо id, либо имя; возвращает id."""
    # Пробуем сначала как id — если 404, идём искать по имени.
    try:
        body = await client.get(f"/networks/{identifier}")
        return str(body["id"])
    except CliApiError as exc:
        if exc.http_status != _HTTP_NOT_FOUND:
            raise
    listing = await client.get("/networks")
    for net in listing.get("items", []):
        if net["name"] == identifier:
            return str(net["id"])
    raise CliApiError(
        f"network {identifier!r} not found",
        http_status=_HTTP_NOT_FOUND,
        code="not_found",
    )


async def _show(args: argparse.Namespace, client: CliApiClient) -> None:
    network_id = await _resolve(client, args.identifier)
    body = await client.get(f"/networks/{network_id}")
    if args.output == "json":
        print_json(body)
        return
    print_table(
        ("FIELD", "VALUE"),
        [
            ("id", body["id"]),
            ("name", body["name"]),
            ("type", body["type"]),
            ("mtu", body["mtu"]),
            ("vlan_id", body.get("vlan_id")),
            ("vni", body.get("vni")),
            ("intent_version", body["intent_version"]),
            ("spec_hash", body["spec_hash"]),
            ("node_ids", body.get("node_ids")),
            ("subnet_cidr", (body.get("subnet") or {}).get("cidr")),
        ],
    )


async def _create(args: argparse.Namespace, client: CliApiClient) -> None:
    payload: dict[str, Any] = {
        "name": args.name,
        "type": args.type,
        "mtu": args.mtu,
        "node_ids": args.node,
    }
    if args.vlan_id is not None:
        payload["vlan_id"] = args.vlan_id
    if args.vni is not None:
        payload["vni"] = args.vni
    if args.cidr is not None:
        payload["subnet"] = {"cidr": args.cidr, "gateway": args.gateway}

    body = await client.post("/networks", json=payload)
    if args.output == "json":
        print_json(body)
        return
    net = body["network"]
    print(f"network {net['id']} created (operation={body['operation']['operation_id']})")


async def _apply(args: argparse.Namespace, client: CliApiClient) -> None:
    network_id = await _resolve(client, args.identifier)
    body = await client.post(f"/networks/{network_id}/apply")
    if args.output == "json":
        print_json(body)
        return
    op = body["operation"]
    print(f"apply on {network_id}: operation={op['operation_id']} status={op['status']}")


async def _assign(args: argparse.Namespace, client: CliApiClient) -> None:
    network_id = await _resolve(client, args.identifier)
    body = await client.post(
        f"/networks/{network_id}/nodes",
        json={"node_ids": args.node},
    )
    if args.output == "json":
        print_json(body)
        return
    op = body["operation"]
    print(
        f"assigned {len(args.node)} node(s) to {network_id} "
        f"(operation={op['operation_id']} status={op['status']})"
    )
