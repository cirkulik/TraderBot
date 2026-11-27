"""Карта уровней для графика MT5: зоны и сильные точки из отчётов 30/60.

Запуск (отдельное окно, рядом с анализатором и скальпером):
    python run_map.py --once --print      # один проход и показать уровни
    python run_map.py --loop              # рабочий цикл, файл раз в минуту
    start_map.bat                         # то же в отдельном окне

Скрипт ничего не торгует: он только читает котировки и пишет
`Common\\Files\\GoldBot\\levels.txt`. Индикатор `GoldBotLevels.mq5` рисует
этот файл на графике.
"""
from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.chartmap import ChartMap
from goldbot.config import load_config
from goldbot.data import connect, forget_account


def cli() -> int:
    parser = argparse.ArgumentParser(
        description="GoldBot Map: зоны и сильные точки на графике MT5"
    )
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--loop", action="store_true", help="работать постоянно")
    parser.add_argument(
        "--print", action="store_true", help="показать уровни в консоли"
    )
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument("--login", default=None, help="логин MT5")
    parser.add_argument("--password", default=None, help="пароль MT5")
    parser.add_argument("--server", default=None, help="сервер MT5")
    parser.add_argument("--terminal", default=None, help="путь к terminal64.exe")
    parser.add_argument(
        "--forget", action="store_true", help="забыть сохранённый счёт MT5"
    )
    parser.add_argument(
        "--force", action="store_true", help="игнорировать map.enabled = false"
    )
    args = parser.parse_args()

    if args.once and args.loop:
        parser.error("нельзя одновременно --once и --loop")
    if not args.once and not args.loop:
        parser.error("укажите --once или --loop")

    cfg = load_config(args.config)
    m = cfg.get("map") or {}
    if not m.get("enabled", True) and not args.force:
        print("Карта выключена: config.json → map.enabled = false")
        print("Включить: поставьте map.enabled = true и перезапустите.")
        return 1

    if args.forget:
        removed = forget_account()
        print("Сохранённый счёт забыт." if removed else "Файл data/account.json не найден.")

    terminal = args.terminal or (cfg.get("execution") or {}).get("terminal_path") or None
    try:
        connect(
            path=terminal,
            login=args.login,
            password=args.password,
            server=args.server,
        )
    except Exception as exc:  # noqa: BLE001 - показываем причину и выходим
        print(f"[map] MT5: {exc}")
        return 2

    try:
        chart_map = ChartMap(cfg, cfg_path=args.config)
    except Exception as exc:  # noqa: BLE001
        print(f"[map] {exc}")
        return 3

    if args.once:
        chart_map.startup()
        chart_map.run_once(print_levels=args.print)
        return 0

    chart_map.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
