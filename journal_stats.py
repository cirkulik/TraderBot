"""Статистика по журналу live-сигналов (data/signals.csv)."""
from __future__ import annotations

import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.config import ROOT, load_config


def main() -> int:
    cfg = load_config()
    path = ROOT / cfg["live"].get("journal_path", "data/signals.csv")
    if not path.exists():
        print(f"Журнал не найден: {path}")
        print("Запустите бота: python run_live.py")
        return 1

    df = pd.read_csv(path, encoding="utf-8-sig")
    if df.empty:
        print("Журнал пуст — сигналов ещё не было.")
        return 0

    open_count = int((df["status"] == "OPEN").sum())
    closed = df[df["status"] == "CLOSED"].copy()
    print(f"Всего сигналов: {len(df)}   открытых: {open_count}   закрытых: {len(closed)}")

    if closed.empty:
        print("Закрытых сделок пока нет.")
        return 0

    closed["result_r"] = pd.to_numeric(closed["result_r"], errors="coerce")
    closed["result_points"] = pd.to_numeric(closed["result_points"], errors="coerce")
    wins = closed[closed["result_r"] > 0]
    losses = closed[closed["result_r"] < 0]
    gross_profit = float(wins["result_r"].sum())
    gross_loss = float(-losses["result_r"].sum())
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    pf_text = "∞" if pf == float("inf") else f"{pf:.2f}"

    print()
    print("=" * 56)
    print("  СТАТИСТИКА ЖУРНАЛА (в R, без привязки к лоту)")
    print("=" * 56)
    print(f"  Winrate:            {(closed['result_r'] > 0).mean() * 100:.1f}%")
    print(f"  Profit factor:      {pf_text}")
    print(f"  Мат. ожидание:      {closed['result_r'].mean():+.2f} R")
    print(f"  Итог:               {closed['result_r'].sum():+.2f} R")
    print(f"  Движение цены:      {closed['result_points'].sum():+.2f} $")
    print(f"  Средняя сделка:     {closed['result_r'].mean():+.2f} R")
    print(f"  Лучшая:             {closed['result_r'].max():+.2f} R")
    print(f"  Худшая:             {closed['result_r'].min():+.2f} R")
    print("=" * 56)

    if "setup" in closed.columns:
        print("\n  По сетапам:")
        for setup, group in closed.groupby("setup"):
            print(
                f"    {setup:<24} N={len(group):>3}  "
                f"win={(group['result_r'] > 0).mean() * 100:>5.1f}%  "
                f"R={group['result_r'].sum():>+7.2f}  "
                f"avgR={group['result_r'].mean():>+.2f}"
            )

    if "exit_reason" in closed.columns:
        print("\n  По типу выхода:")
        for reason, group in closed.groupby("exit_reason"):
            print(
                f"    {reason:<6} N={len(group):>3}  "
                f"R={group['result_r'].sum():>+7.2f}  "
                f"avgR={group['result_r'].mean():>+.2f}"
            )

    if "gap_to_tp" in closed.columns:
        gap = pd.to_numeric(closed["gap_to_tp"], errors="coerce").dropna()
        if len(gap):
            print("\n  Недолёт до TP (по максимуму цены за сделку):")
            print(f"    в среднем:          {gap.mean():+.2f} $ (минус = прокол)")
            short = gap[gap > 0]
            if len(short):
                print(f"    не дошло до TP:     {len(short)} из {len(gap)} сделок")
                for x in (0.1, 0.25, 0.5, 1.0):
                    n = int((short <= x).sum())
                    print(
                        f"      недолёт <= ${x:<5} {n:>3} "
                        f"({n / len(gap) * 100:4.1f}% всех сделок)"
                    )
            pierced = gap[gap <= 0]
            if len(pierced):
                print(
                    f"    TP с проколом:      {len(pierced)} шт, "
                    f"средний прокол {-pierced.mean():.2f} $"
                )

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
