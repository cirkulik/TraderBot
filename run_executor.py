"""Исполнитель сигналов: отдельный процесс, ставит сделки по журналу.

Запуск (второе окно консоли, анализатор — в первом):
    python run_executor.py
    python run_executor.py --dry-run      :: проверить, ничего не отправляя
    python run_executor.py --once         :: один проход и выход
    python run_executor.py --lot 0.02     :: разовый лот, не меняя config.json
    python run_executor.py --slip 2       :: лимит ухода цены от сигнала в $ (по умолчанию 1)

Счёт: логин и пароль спрашиваются при запуске (Enter — счёт из терминала).
Можно сохранить их в data/account.json или задать флагами:
    python run_executor.py --login 12345678 --server MetaQuotes-Demo
    python run_executor.py --forget        :: забыть сохранённый счёт
"""
from __future__ import annotations

import argparse
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.executor import main


def cli() -> int:
    parser = argparse.ArgumentParser(description="Исполнение сигналов GoldBot на MT5")
    parser.add_argument("--dry-run", action="store_true", help="проверять заявки, но не отправлять")
    parser.add_argument("--once", action="store_true", help="один проход и выход")
    parser.add_argument("--lot", type=float, default=None, help="лот на этот запуск")
    parser.add_argument(
        "--slip",
        type=float,
        default=None,
        help="лимит ухода цены от сигнала в $ (например 2; сейчас 1)",
    )
    parser.add_argument("--login", default=None, help="логин MT5 (иначе спросим)")
    parser.add_argument("--password", default=None, help="пароль MT5 (лучше вводить в консоли)")
    parser.add_argument("--server", default=None, help="сервер MT5, например MetaQuotes-Demo")
    parser.add_argument("--terminal", default=None, help="путь к terminal64.exe, если не находится сам")
    parser.add_argument("--forget", action="store_true", help="удалить сохранённый счёт")
    args = parser.parse_args()
    return main(
        dry_run=args.dry_run,
        once=args.once,
        lot=args.lot,
        slip=args.slip,
        login=args.login,
        password=args.password,
        server=args.server,
        terminal_path=args.terminal,
        forget=args.forget,
    )


if __name__ == "__main__":
    raise SystemExit(cli())
