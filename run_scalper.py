"""Скальпер: активная торговля лимитками на зонах и сильных точках.

Запуск (отдельное окно, рядом с анализатором и исполнителем сигналов):
    python run_scalper.py --once --dry-run   # один проход, ничего не отправляя
    python run_scalper.py --loop             # рабочий цикл
    python run_scalper.py --loop --no-telegram
    python run_scalper.py --once --force     # даже если scalper.enabled = false

Счёт: берётся из data/account.json или из терминала MT5. Можно задать явно:
    python run_scalper.py --loop --login 12345678 --server MetaQuotes-Demo

По умолчанию процесс работает только на демо-счёте. Реальный счёт требует
`scalper.allow_live = true` в config.json. Лот фиксированный (0.01), свой
magic (770002) — сделки исполнителя сигналов (770001) не трогаются.
"""
from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.config import load_config
from goldbot.data import connect, forget_account
from goldbot.scalper import Scalper


def cli() -> int:
    parser = argparse.ArgumentParser(
        description="Скальпер GoldBot: лимитки на зонах и сильных точках"
    )
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--loop", action="store_true", help="работать постоянно")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="только показывать, что бот хотел бы сделать",
    )
    parser.add_argument(
        "--no-telegram", action="store_true", help="не присылать уведомления админу"
    )
    parser.add_argument("--force", action="store_true", help="игнорировать enabled=false")
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument("--login", default=None, help="логин MT5")
    parser.add_argument("--password", default=None, help="пароль MT5")
    parser.add_argument("--server", default=None, help="сервер MT5")
    parser.add_argument("--terminal", default=None, help="путь к terminal64.exe")
    parser.add_argument(
        "--forget", action="store_true", help="забыть сохранённый счёт MT5"
    )
    args = parser.parse_args()

    if args.once and args.loop:
        parser.error("нельзя одновременно --once и --loop")
    if not args.once and not args.loop:
        parser.error("укажите --once или --loop")

    cfg = load_config(args.config)
    sc = cfg.get("scalper") or {}
    dry_run = bool(args.dry_run or sc.get("dry_run", True))

    if not sc.get("enabled") and not dry_run and not args.force:
        print("Скальпер выключен: config.json → scalper.enabled = false")
        print("Проверить без ордеров: python run_scalper.py --once --dry-run")
        print("Включить: поставьте scalper.enabled = true и перезапустите.")
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
        print(f"[scalper] MT5: {exc}")
        return 2

    try:
        scalper = Scalper(
            cfg,
            dry_run=dry_run,
            no_telegram=args.no_telegram,
            cfg_path=args.config,
        )
    except Exception as exc:  # noqa: BLE001 - например, реальный счёт без allow_live
        print(f"[scalper] {exc}")
        return 3

    if args.once:
        scalper.startup()
        scalper.tick(force_refresh=True)
        return 0

    scalper.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
