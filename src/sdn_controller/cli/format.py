"""Форматирование вывода CLI: таблицы и JSON.

Таблица — дефолт, читаемая в терминале без зависимостей. JSON
переключается флагом ``--output json`` и нужен для пайплайнов (jq,
скрипты). Цветами не злоупотребляем — CLI должен быть пригоден для
``2>&1 | tee log`` без артефактов.
"""

from __future__ import annotations

import json as _json
from collections.abc import Iterable, Sequence
from typing import Any


def print_table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    """Простой текстовый рендер таблицы.

    Считаем ширину колонок по самому длинному значению в каждом
    столбце. Не идеально для длинных id, но операторы привыкли
    обрезать вывод через ``less`` или ``grep`` — лишний padding только
    мешает.
    """
    rows_list = [[_cell(c) for c in row] for row in rows]
    widths = [len(h) for h in headers]
    for row in rows_list:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    template = "  ".join(f"{{:<{w}}}" for w in widths)
    print(template.format(*headers))
    print(template.format(*("-" * w for w in widths)))
    for row in rows_list:
        print(template.format(*row))


def print_json(value: Any) -> None:
    print(_json.dumps(value, indent=2, ensure_ascii=False, default=_json_default))


def _cell(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list | tuple):
        return ",".join(str(v) for v in value) if value else "-"
    return str(value)


def _json_default(value: Any) -> Any:
    """Поддержка datetime/Enum при ``json.dumps``."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"value of type {type(value).__name__} is not JSON-serialisable")


__all__ = ["print_json", "print_table"]
