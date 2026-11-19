"""Запуск live-режима: сигналы XAUUSD M5 в консоль.

Примеры:
    python run_live.py            # ждать сигналы постоянно
    python run_live.py --once     # проверить текущий закрытый бар и выйти
    python run_live.py --no-telegram   # сигналы только в журнал, без Telegram
    python run_live.py --terminal "C:\\Program Files\\MetaTrader 5\\terminal64.exe"
"""
from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.config import load_config
from goldbot.live import _log_error, run_live


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-сигналы XAUUSD M5")
    parser.add_argument("--once", action="store_true", help="одна проверка и выход")
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument(
        "--terminal",
        default=None,
        help="путь к terminal64.exe, если терминал не находится автоматически",
    )
    parser.add_argument(
        "--no-telegram",
        "--no-tg",
        dest="no_telegram",
        action="store_true",
        help="не отправлять сигналы в Telegram (только журнал для исполнителя)",
    )
    args = parser.parse_args()

    cfg = load_config(args.config)
    try:
        run_live(
            cfg,
            once=args.once,
            terminal_path=args.terminal,
            telegram=not args.no_telegram,
        )
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - фиксируем причину остановки
        _log_error(exc, "fatal")
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
