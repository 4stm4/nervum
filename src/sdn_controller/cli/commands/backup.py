"""``sdnctl backup ...`` — export/import bundle'а."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sdn_controller.cli.client import CliApiClient
from sdn_controller.cli.format import print_json


def register(subparsers: argparse._SubParsersAction[Any]) -> None:
    parser = subparsers.add_parser("backup", help="Backup и restore состояния контроллера")
    sub = parser.add_subparsers(dest="backup_command", required=True)

    p_export = sub.add_parser("export", help="Снять bundle")
    p_export.add_argument(
        "--output-file",
        "-f",
        type=Path,
        help="Записать в файл; без флага — в stdout как JSON",
    )
    p_export.set_defaults(handler=_export)

    p_import = sub.add_parser("import", help="Восстановить из bundle'а (только в пустую БД)")
    p_import.add_argument("path", type=Path)
    p_import.set_defaults(handler=_import)


async def _export(args: argparse.Namespace, client: CliApiClient) -> None:
    body = await client.get("/backup/export")
    text = json.dumps(body, indent=2, ensure_ascii=False)
    if args.output_file is not None:
        args.output_file.write_text(text, encoding="utf-8")
        print(f"exported bundle to {args.output_file}", file=sys.stderr)
    else:
        # output=json/--output-file=- эквивалентны
        sys.stdout.write(text)
        sys.stdout.write("\n")


async def _import(args: argparse.Namespace, client: CliApiClient) -> None:
    text = args.path.read_text(encoding="utf-8")
    bundle = json.loads(text)
    body = await client.post("/backup/import", json=bundle)
    if args.output == "json":
        print_json(body)
        return
    print(
        f"imported: networks={body['networks']} nodes={body['nodes']} "
        f"service_accounts={body['service_accounts']} "
        f"ip_allocations={body['ip_allocations']} audit_events={body['audit_events']}"
    )
