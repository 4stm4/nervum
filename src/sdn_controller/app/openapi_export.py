"""Dump the OpenAPI schema to stdout.

Used by ``make openapi`` to refresh ``openapi/sdn-controller.generated.json``
so the contract can be reviewed in PRs.
"""

from __future__ import annotations

import json
import sys

from sdn_controller.adapters.http_api import create_app
from sdn_controller.app.config import load_settings
from sdn_controller.app.container import build_container


def main() -> None:
    settings = load_settings()
    app = create_app(build_container(settings))
    json.dump(app.openapi(), sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")


if __name__ == "__main__":  # pragma: no cover
    main()
