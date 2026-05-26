"""CronScheduler — планировщик cron-расписаний (N5-01).

Проверяет, «наступило ли» время для заданного 5-польного cron-выражения.
Поддерживает стандартный POSIX-синтаксис:

  * — любое значение
  */N — каждые N единиц
  A   — конкретное значение
  A-B — диапазон A..B (включительно)

Cron-поля (слева направо): минута час день_месяца месяц день_недели (0=воскресенье).

Пример::

    # каждый день в 2:00
    is_due("0 2 * * *", datetime(2026,5,26,2,0))  # True
    is_due("0 2 * * *", datetime(2026,5,26,3,0))  # False
"""

from __future__ import annotations

from datetime import datetime

from sdn_controller.core.value_objects.errors import ValidationError


class CronScheduler:
    """Сервис проверки cron-расписаний."""

    @staticmethod
    def is_due(cron_expr: str, now: datetime) -> bool:
        """Вернуть True, если ``now`` попадает в окно cron-выражения."""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValidationError(
                f"cron_expr должен содержать 5 полей: {cron_expr!r}"
            )
        minute_field, hour_field, dom_field, month_field, dow_field = parts

        # Cron: день_недели 0=воскресенье; Python weekday(): 0=понедельник
        # Конвертация: cron_dow = (python_weekday + 1) % 7
        cron_dow = (now.weekday() + 1) % 7

        return (
            _matches(minute_field, now.minute, 0, 59)
            and _matches(hour_field, now.hour, 0, 23)
            and _matches(dom_field, now.day, 1, 31)
            and _matches(month_field, now.month, 1, 12)
            and _matches(dow_field, cron_dow, 0, 6)
        )

    @staticmethod
    def validate(cron_expr: str) -> None:
        """Проверить синтаксис без вычисления — бросает ValidationError."""
        parts = cron_expr.strip().split()
        if len(parts) != 5:
            raise ValidationError(
                f"cron_expr должен содержать 5 полей: {cron_expr!r}"
            )
        ranges = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 6)]
        names = ["minute", "hour", "day", "month", "dow"]
        for field, (lo, hi), name in zip(parts, ranges, names):
            try:
                _matches(field, lo, lo, hi)
            except (ValueError, ValidationError) as exc:
                raise ValidationError(
                    f"cron field {name!r}: {exc}"
                ) from exc


def _matches(field: str, value: int, lo: int, hi: int) -> bool:
    """Проверить, соответствует ли ``value`` полю cron.

    Поддерживаемые форматы: ``*``, ``*/N``, ``A``, ``A-B``.
    """
    if field == "*":
        return True

    if field.startswith("*/"):
        step_str = field[2:]
        if not step_str.isdigit():
            raise ValidationError(f"некорректный шаг: {field!r}")
        step = int(step_str)
        if step <= 0:
            raise ValidationError(f"шаг должен быть > 0: {field!r}")
        return (value - lo) % step == 0

    if "-" in field:
        parts = field.split("-", 1)
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            raise ValidationError(f"некорректный диапазон: {field!r}")
        start, end = int(parts[0]), int(parts[1])
        if not (lo <= start <= hi and lo <= end <= hi):
            raise ValidationError(
                f"диапазон {field!r} выходит за [{lo},{hi}]"
            )
        return start <= value <= end

    if field.isdigit():
        specific = int(field)
        if not (lo <= specific <= hi):
            raise ValidationError(
                f"значение {field!r} выходит за [{lo},{hi}]"
            )
        return specific == value

    raise ValidationError(f"нераспознанный формат cron-поля: {field!r}")
