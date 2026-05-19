"""``sdnctl topology`` — снимок графа узлы/сети/мосты/рёбра."""

from __future__ import annotations

import argparse
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json, print_table


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("topology", help="Снимок топологии")
    parser.set_defaults(handler=_show)


async def _show(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get("/topology")
    if args.output == "json":
        print_json(body)
        return
    print("# nodes")
    print_table(
        ("ID", "NAME", "STATUS", "OBSERVED_AT"),
        [(n["id"], n["name"], n["status"], n.get("observed_at")) for n in body.get("nodes", [])],
    )
    print("\n# networks")
    print_table(
        ("ID", "NAME", "TYPE", "VLAN/VNI", "NODES"),
        [
            (
                n["id"],
                n["name"],
                n["type"],
                n.get("vni") or n.get("vlan_id") or "-",
                len(n.get("node_ids") or []),
            )
            for n in body.get("networks", [])
        ],
    )
    print("\n# bridges (observed)")
    print_table(
        ("NODE", "BRIDGE", "NETWORK"),
        [(b["node_id"], b["name"], b.get("network_id")) for b in body.get("bridges", [])],
    )
    print("\n# edges")
    print_table(
        ("KIND", "SOURCE", "TARGET", "NETWORK"),
        [(e["kind"], e["source"], e["target"], e.get("network_id")) for e in body.get("edges", [])],
    )
