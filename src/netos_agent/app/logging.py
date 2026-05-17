"""Structlog setup — same processor chain as the controller for parity."""

from __future__ import annotations

import logging
import sys
from typing import Literal

import structlog
from structlog.types import Processor


def configure_logging(
    *,
    level: str = "INFO",
    fmt: Literal["json", "console"] = "json",
) -> None:
    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]
    renderer: Processor = (
        structlog.processors.JSONRenderer()
        if fmt == "json"
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[*shared_processors, structlog.processors.format_exc_info, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        cache_logger_on_first_use=True,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(processors=[*shared_processors, renderer])
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
