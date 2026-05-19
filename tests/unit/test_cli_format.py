"""Unit-тесты ``print_table`` и ``print_json``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sdn_controller.cli.format import print_json, print_table


def test_table_aligns_columns(capsys: pytest.CaptureFixture[str]) -> None:
    print_table(
        ("ID", "NAME"),
        [("node_1", "a"), ("very-long-id", "node-name")],
    )
    out = capsys.readouterr().out.splitlines()
    # Все строки должны быть одной длины (паддинг до ширины широких).
    widths = {len(line) for line in out}
    assert len(widths) == 1


def test_table_renders_none_as_dash(capsys: pytest.CaptureFixture[str]) -> None:
    print_table(("ID", "STATUS"), [("a", None)])
    out = capsys.readouterr().out
    assert " - " in out or out.endswith("-\n")


def test_table_renders_list(capsys: pytest.CaptureFixture[str]) -> None:
    print_table(("X",), [(["a", "b"],)])
    assert "a,b" in capsys.readouterr().out


def test_json_default_serializes_datetime(capsys: pytest.CaptureFixture[str]) -> None:
    print_json({"at": datetime(2026, 5, 19, 0, 0, tzinfo=UTC)})
    out = capsys.readouterr().out
    assert "2026-05-19" in out
