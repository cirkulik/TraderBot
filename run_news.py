"""Ежедневные новости ForexFactory в каналы Telegram.

Запуск (отдельное окно, рядом с остальными процессами):
    python run_news.py --once --print          # показать текст, ничего не отправлять
    python run_news.py --once --print --date 2026-11-25
    python run_news.py --once                  # отправить и закрепить
    python run_news.py --loop                  # каждый день в 11:00 МСК
    start_news.bat                             # то же в отдельном окне

Скрипт не торгует: читает календарь ForexFactory и пишет в каналы, которые
подключены к боту. Бот должен быть администратором канала с правом
«Закреплять сообщения».
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.config import load_config
from goldbot.news import NewsDigest


def cli() -> int:
    parser = argparse.ArgumentParser(
        description="GoldBot News: новости ForexFactory в каналы Telegram"
    )
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--loop", action="store_true", help="работать постоянно")
    parser.add_argument(
        "--print", action="store_true", help="показать текст, не отправлять"
    )
    parser.add_argument(
        "--date",
        default=None,
        help="дата в формате YYYY-MM-DD (по умолчанию сегодня)",
    )
    parser.add_argument(
        "--no-pin", action="store_true", help="не закреплять сообщение"
    )
    parser.add_argument(
        "--list-channels",
        action="store_true",
        help="показать каналы, которые найдены у бота",
    )
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument(
        "--force", action="store_true", help="игнорировать news.enabled = false"
    )
    args = parser.parse_args()

    if args.once and args.loop:
        parser.error("нельзя одновременно --once и --loop")
    if not args.once and not args.loop and not args.print and not args.list_channels:
        parser.error("укажите --once, --loop, --print или --list-channels")

    cfg = load_config(args.config)
    news_cfg = cfg.get("news") or {}
    if not news_cfg.get("enabled", True) and not args.force:
        print("Новости выключены: config.json → news.enabled = false")
        print("Включить: поставьте news.enabled = true и перезапустите.")
        return 1

    digest = NewsDigest(cfg, cfg_path=args.config)

    if args.list_channels:
        if not digest.token:
            print("Нет TELEGRAM_TOKEN — каналы не определить.")
            return 2
        channels = digest.channels()
        if not channels:
            print("Подключённых каналов не найдено.")
            return 3
        for chat_id in channels:
            print(chat_id)
        return 0

    if args.date:
        try:
            day = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            parser.error("--date должен быть в формате YYYY-MM-DD")
    else:
        day = None

    if args.loop:
        digest.run()
        return 0

    try:
        digest.startup()
        sent, total = digest.send_daily(
            day=day,
            pin=False if args.no_pin else None,
            print_only=args.print,
        )
    except Exception as exc:  # noqa: BLE001 - показываем причину
        print(f"[news] ОШИБКА: {type(exc).__name__}: {exc}")
        return 4

    if not args.print:
        print(f"[news] отправлено {sent} из {total}")
        if not sent:
            return 5
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
