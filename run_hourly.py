"""Периодический отчёт в Telegram: зоны за 24 часа, bias, картинка.

Примеры:
    python run_hourly.py --once                     # один отчёт, отправить всем
    python run_hourly.py --once --no-telegram       # только в консоль + картинка
    python run_hourly.py --loop                     # расписание из конфига
    python run_hourly.py --loop --intervals 5,15,30,60
    python run_hourly.py --loop --intervals 15 --minute 5   # :05, :20, :35, :50
    python run_hourly.py --once --daily             # итоги дня в консоль
    python run_hourly.py --once --csv data/XAUUSD_M5.csv

Расписание: поддерживаются 5, 15, 30 и 60 минут. Цикл просыпается на самом
коротком интервале, а дальше смотрит, какие интервалы наступили в этот слот.
Кому что уходит — настраивается по каналам: /channel <id> 5 30.

Отдельно раз в сутки (по умолчанию 01:00 МСК, `daily`) уходит разбор сильных
зон за день — всем каналам, независимо от их интервалов.

Это информационный разбор уровней, не торговый сигнал.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from goldbot import intervals
from goldbot.config import ROOT, load_config
from goldbot.hourly import send_daily, send_report

BARS = 5000
CSV_PATH = ROOT / "data" / "XAUUSD_M5.csv"
COLUMNS = ["time", "open", "high", "low", "close", "tick_volume"]


def load_from_mt5(cfg: dict) -> pd.DataFrame:
    from goldbot.data import connect, get_closed_rates, shutdown

    connect()
    try:
        df = get_closed_rates(cfg.get("symbol", "XAUUSD"), "M5", BARS)
    finally:
        shutdown()
    return df[COLUMNS].reset_index(drop=True)


def load_from_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["time"])
    return df[COLUMNS].sort_values("time").reset_index(drop=True)


def load_data(cfg: dict, csv_path: Path | None) -> pd.DataFrame:
    if csv_path is not None:
        return load_from_csv(csv_path)
    try:
        df = load_from_mt5(cfg)
        print(f"[data] MT5: {len(df)} баров до {df['time'].iloc[-1]}")
        return df
    except Exception as exc:  # noqa: BLE001 - без терминала работаем по CSV
        print(f"[data] MT5 недоступен ({exc}) — беру CSV")
        return load_from_csv(CSV_PATH)


def run_once(cfg: dict, args: argparse.Namespace, due: set[int] | None = None) -> None:
    df = load_data(cfg, Path(args.csv) if args.csv else None)
    if df.empty:
        print("[hourly] нет данных")
        return
    hourly_cfg = cfg.get("hourly", {}) or {}
    lang = (
        args.lang
        or hourly_cfg.get("lang")
        or (cfg.get("telegram", {}) or {}).get("default_lang", "ru")
    )
    hours = args.hours if args.hours is not None else int(hourly_cfg.get("hours", 24))
    to = args.to or str(hourly_cfg.get("to", "all"))
    send_report(
        cfg,
        df,
        lang=lang,
        hours=hours,
        telegram=not args.no_telegram,
        chart_dir=args.chart_dir,
        with_chart=bool(hourly_cfg.get("chart", True)),
        to=to,
        due=due,
    )


def run_daily(cfg: dict, args: argparse.Namespace) -> None:
    """Итоги дня: сильные зоны за сутки, всем каналам."""
    df = load_data(cfg, Path(args.csv) if args.csv else None)
    if df.empty:
        print("[daily] нет данных")
        return
    daily_cfg = cfg.get("daily", {}) or {}
    hourly_cfg = cfg.get("hourly", {}) or {}
    lang = (
        args.lang
        or hourly_cfg.get("lang")
        or (cfg.get("telegram", {}) or {}).get("default_lang", "ru")
    )
    hours = args.hours if args.hours is not None else int(daily_cfg.get("hours", 24))
    send_daily(
        cfg,
        df,
        lang=lang,
        hours=hours,
        telegram=not args.no_telegram,
        chart_dir=args.chart_dir,
        with_chart=bool(daily_cfg.get("chart", True)),
    )


def due_intervals(slot: datetime, minute: int, slots: list[int]) -> list[int]:
    """Какие интервалы наступают в этот слот (сдвиг `minute` внутри часа)."""
    since_midnight = slot.hour * 60 + slot.minute
    return [step for step in slots if (since_midnight - minute) % step == 0]


def next_slot(now: datetime, minute: int, step: int) -> datetime:
    """Ближайший слот расписания, кратный `step` минутам, со сдвигом `minute`."""
    slot = now.replace(minute=0, second=0, microsecond=0) + timedelta(
        minutes=minute % step
    )
    while slot <= now:
        slot += timedelta(minutes=step)
    return slot


def parse_hhmm(text: str) -> tuple[int, int]:
    hour, _, minute = str(text or "01:00").partition(":")
    return int(hour) % 24, int(minute) % 60


def next_daily(now: datetime, time_text: str, utc_offset: float) -> datetime:
    """Ближайшее HH:MM в поясе UTC±offset, в локальном времени машины."""
    hour, minute = parse_hhmm(time_text)
    tz = timezone(timedelta(hours=utc_offset))
    current = now.astimezone(tz)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target.astimezone(now.tzinfo).replace(tzinfo=None)


def main() -> None:
    parser = argparse.ArgumentParser(description="Периодический отчёт XAUUSD M5")
    parser.add_argument("--once", action="store_true", help="один отчёт и выход")
    parser.add_argument("--loop", action="store_true", help="отправлять по расписанию")
    parser.add_argument(
        "--daily", action="store_true", help="итоги дня вместо обычного отчёта (с --once)"
    )
    parser.add_argument("--minute", type=int, default=None, help="сдвиг слота внутри часа, минут")
    parser.add_argument(
        "--intervals",
        default=None,
        help="интервалы через запятую: 5,15,30,60 (по умолчанию из hourly.intervals)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="устаревшее: один интервал; используйте --intervals",
    )
    parser.add_argument("--hours", type=int, default=None, help="окно анализа, часов")
    parser.add_argument("--lang", default=None, help="ru или az (по умолчанию из конфига)")
    parser.add_argument(
        "--to",
        default=None,
        choices=("all", "admin"),
        help="all — каждому подписчику на его языке, admin — только в чат админа",
    )
    parser.add_argument("--csv", default=None, help="взять данные из CSV вместо MT5")
    parser.add_argument("--chart-dir", default=None, help="куда сохранять картинки")
    parser.add_argument("--no-telegram", action="store_true", help="не отправлять в Telegram")
    parser.add_argument("--config", default=None, help="путь к config.json")
    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    cfg = load_config(args.config)
    hourly_cfg = cfg.get("hourly", {}) or {}
    if args.minute is None:
        args.minute = int(hourly_cfg.get("minute", 1))

    if args.once:
        if args.daily:
            run_daily(cfg, args)
        else:
            run_once(cfg, args)
        return

    if args.interval is not None:
        slots = intervals.normalize([args.interval])
    elif args.intervals:
        slots = intervals.normalize(str(args.intervals).replace(",", " ").split())
    else:
        slots = intervals.normalize(hourly_cfg.get("intervals") or [])
    if not slots:
        parser.error("поддерживаются интервалы 5, 15, 30, 60")

    step = min(slots)
    minute = args.minute % step
    print(
        f"[hourly] цикл запущен: интервалы {intervals.label(slots)}, "
        f"шаг {step} мин, сдвиг :{minute:02d}"
    )

    daily_cfg = cfg.get("daily", {}) or {}
    daily_on = bool(daily_cfg.get("enabled", True))
    daily_time = str(daily_cfg.get("time", "01:00"))
    daily_offset = float(daily_cfg.get("utc_offset", 3))
    next_daily_at: datetime | None = None
    if daily_on:
        next_daily_at = next_daily(
            datetime.now().astimezone(), daily_time, daily_offset
        )
        print(
            f"[hourly] итоги дня: {daily_time} (UTC{daily_offset:+g}) "
            f"— следующий {next_daily_at:%d.%m %H:%M} по времени машины"
        )

    while True:
        slot = next_slot(datetime.now(), minute, step)
        wake = min([slot] + ([next_daily_at] if next_daily_at else []))
        while True:
            left = (wake - datetime.now()).total_seconds()
            if left <= 0:
                break
            time.sleep(min(left, 30))
        now = datetime.now()

        if next_daily_at is not None and now >= next_daily_at:
            print(f"[hourly] {now:%d.%m %H:%M} — итоги дня")
            try:
                run_daily(cfg, args)
            except Exception as exc:  # noqa: BLE001 - цикл не должен падать
                print(f"[hourly] ошибка итогов дня: {exc}", file=sys.stderr)
            next_daily_at = next_daily(
                datetime.now().astimezone(), daily_time, daily_offset
            )

        if now >= slot:
            due = due_intervals(slot, minute, slots)
            if due:
                print(
                    f"[hourly] {slot:%d.%m %H:%M} — интервалы: {', '.join(map(str, due))}"
                )
                try:
                    run_once(cfg, args, set(due))
                except Exception as exc:  # noqa: BLE001 - цикл не должен падать
                    print(f"[hourly] ошибка: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
