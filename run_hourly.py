"""Часовой отчёт в Telegram: зоны за 24 часа, bias, картинка.

Примеры:
    python run_hourly.py --once                     # один отчёт, отправить
    python run_hourly.py --once --no-telegram       # только в консоль + картинка
    python run_hourly.py --loop                     # каждый час, в :01
    python run_hourly.py --loop --minute 5
    python run_hourly.py --once --csv data/XAUUSD_M5.csv

Это информационный разбор уровней, не торговый сигнал.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from goldbot.config import ROOT, load_config
from goldbot.hourly import send_report

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


def run_once(cfg: dict, args: argparse.Namespace) -> None:
    df = load_data(cfg, Path(args.csv) if args.csv else None)
    if df.empty:
        print("[hourly] нет данных")
        return
    lang = args.lang or (cfg.get("telegram", {}) or {}).get("default_lang", "ru")
    send_report(
        cfg,
        df,
        lang=lang,
        hours=args.hours,
        telegram=not args.no_telegram,
        chart_dir=args.chart_dir,
    )


def seconds_until(minute: int) -> float:
    now = datetime.now()
    target = now.replace(minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(hours=1)
    return (target - now).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description="Часовой отчёт XAUUSD M5")
    parser.add_argument("--once", action="store_true", help="один отчёт и выход")
    parser.add_argument("--loop", action="store_true", help="отправлять каждый час")
    parser.add_argument("--minute", type=int, default=1, help="минута часа для отправки")
    parser.add_argument("--hours", type=int, default=24, help="окно анализа, часов")
    parser.add_argument("--lang", default=None, help="ru или az (по умолчанию из конфига)")
    parser.add_argument("--csv", default=None, help="взять данные из CSV вместо MT5")
    parser.add_argument("--chart-dir", default=None, help="куда сохранять картинки")
    parser.add_argument("--no-telegram", action="store_true", help="не отправлять в Telegram")
    parser.add_argument("--config", default=None, help="путь к config.json")
    args = parser.parse_args()

    if not args.once and not args.loop:
        args.once = True

    cfg = load_config(args.config)

    if args.once:
        run_once(cfg, args)
        return

    print(f"[hourly] цикл запущен, отправка в :{args.minute:02d} каждого часа")
    while True:
        wait = seconds_until(args.minute)
        print(f"[hourly] следующая отправка через {wait / 60:.1f} мин")
        while wait > 0:
            time.sleep(min(wait, 30))
            wait -= 30
        try:
            run_once(cfg, args)
        except Exception as exc:  # noqa: BLE001 - цикл не должен падать
            print(f"[hourly] ошибка: {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
