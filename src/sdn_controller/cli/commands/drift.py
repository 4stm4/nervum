"""``sdnctl drift scan`` — отчёт о структурном дрейфе."""

from __future__ import annotations

import argparse
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json, print_table


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("drift", help="Drift detection")
    sub = parser.add_subparsers(dest="drift_command", required=True)
    p_scan = sub.add_parser("scan", help="Сравнить desired vs cached observed")
    p_scan.set_defaults(handler=_scan)


async def _scan(args: argparse.Namespace, client: CliApiClient) -> int:
    body = await client.get("/drift")
    if args.output == "json":
        print_json(body)
    else:
        stale = body.get("stale_nodes") or []
        if stale:
            print(f"# stale nodes (без observed state): {', '.join(stale)}\n")
        items = body.get("items") or []
        if not items:
            print("no drift")
        else:
            print_table(
                ("NETWORK", "NODE", "KIND", "DESCRIPTION"),
                [(it["network_id"], it["node_id"], it["kind"], it["description"]) for it in items],
            )
    # Exit-код: 0 если всё сошлось, 1 если есть items или stale_nodes — это
    # удобно для CI: ``sdnctl drift scan && echo clean`` работает как ожидаешь.
    return 0 if not body.get("items") and not body.get("stale_nodes") else 1
