"""Интервалы отправки анализа: 30 и 60 минут.

В базе у каждого получателя лежит строка вида `30,60` — какие отправления
он хочет получать. Пустое значение читается как значение по умолчанию
(60 минут), чтобы старые записи продолжали работать как раньше.

Пяти- и пятнадцатиминутные отчёты отключены: остались только M30 и H1.
"""
from __future__ import annotations

SUPPORTED = (30, 60)
DEFAULT = 60

ALL_ALIASES = ("all", "все", "всё", "hamisi", "hamısı")


def normalize(values) -> list[int]:
    """Оставляет поддерживаемые интервалы, по убыванию, без повторов."""
    out: list[int] = []
    for value in values:
        try:
            number = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if number in SUPPORTED and number not in out:
            out.append(number)
    return sorted(out, reverse=True)


def parse(text, default: int = DEFAULT) -> list[int]:
    """Разбирает `30,60` / `30 60` / `every 30 60` / `all`. Пусто — default."""
    raw = str(text or "").strip().lower().replace(",", " ").replace(";", " ")
    if raw.startswith("every "):
        raw = raw[6:].strip()
    if not raw:
        return [default]
    if raw in ALL_ALIASES:
        return list(SUPPORTED)
    return normalize(raw.split()) or [default]


def pack(values) -> str:
    """Строка для базы: `30,60`."""
    return ",".join(str(value) for value in normalize(values))


def label(values, lang: str = "ru") -> str:
    """Человеческий вид: `30, 60 мин` или `30, 60 dəq`."""
    items = sorted(normalize(values))
    if not items:
        return "—"
    suffix = "мин" if lang == "ru" else "dəq"
    return f"{', '.join(str(value) for value in items)} {suffix}"
