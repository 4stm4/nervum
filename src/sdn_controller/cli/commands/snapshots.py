"""``sdnctl snapshots ...`` — каталог снапшотов узлов."""

from __future__ import annotations

import argparse
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json, print_table


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("snapshots", help="Снапшоты узлов")
    sub = parser.add_subparsers(dest="snapshots_command", required=True)

    p_list = sub.add_parser("list", help="Снапшоты конкретного узла")
    p_list.add_argument("node_id")
    p_list.set_defaults(handler=_list)

    p_take = sub.add_parser("take", help="Сделать новый снапшот")
    p_take.add_argument("node_id")
    p_take.add_argument("--label")
    p_take.set_defaults(handler=_take)

    p_restore = sub.add_parser("restore", help="Восстановить узел из снапшота")
    p_restore.add_argument("snapshot_id")
    p_restore.set_defaults(handler=_restore)


async def _list(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get(f"/nodes/{args.node_id}/snapshots")
    if args.output == "json":
        print_json(body)
        return
    print_table(
        ("ID", "AGENT_ID", "CREATED_AT", "LABEL"),
        [
            (it["id"], it["agent_snapshot_id"], it["created_at"], it.get("label") or "-")
            for it in body.get("items", [])
        ],
    )


async def _take(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.post(
        f"/nodes/{args.node_id}/snapshots",
        json={"label": args.label} if args.label is not None else {},
    )
    if args.output == "json":
        print_json(body)
        return
    print(f"snapshot {body['id']} created (state_hash={body['state_hash'][:12]}…)")


async def _restore(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.post(f"/node-snapshots/{args.snapshot_id}/restore")
    if args.output == "json":
        print_json(body)
        return
    snap = body["snapshot"]
    print(f"node {snap['node_id']} restored from snapshot {snap['id']}")
