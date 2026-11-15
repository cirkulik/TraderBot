"""Запуск бэктеста и проверка устойчивости параметров.

Примеры:
    python run_backtest.py
    python run_backtest.py --refresh
    python run_backtest.py --tune
    python run_backtest.py --start 2026-01-01 --end 2026-11-01
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.backtest import BacktestResult, run_backtest, trades_to_frame
from goldbot.config import REPORTS_DIR, load_config
from goldbot.data import (
    connect,
    ensure_symbol,
    history_cache_path,
    load_history,
    shutdown,
)

# По одному параметру за раз: так видно, где стратегия устойчива, а где ломается.
SWEEPS = [
    ("dip_london", "drop_atr", [2.5, 3.0, 3.5]),
    ("dip_london", "rr", [0.8, 1.0, 1.2]),
    ("dip_london", "sl_atr", [1.0, 1.2, 1.5]),
    ("dip_late", "drop_atr", [2.5, 2.75, 3.0]),
    ("rsi_ny", "rsi_level", [25, 30, 35]),
    ("rsi_ny", "rr", [1.2, 1.5, 2.0]),
    ("rsi_ny", "sl_atr", [0.8, 1.0, 1.2]),
    ("stretch_ny", "stretch_atr", [1.5, 2.0, 2.5]),
    ("stretch_ny", "rr", [0.8, 1.0, 1.2]),
    ("stretch_ny", "sl_atr", [1.0, 1.2, 1.5]),
    ("sell_noon", "sl_atr", [1.2, 1.5, 2.0]),
    ("sell_noon", "rr", [1.0, 1.2, 1.5]),
    ("sell_noon", "time_exit_bars", [6, 9, 12]),
    ("sell_break", "breakout_bars", [5, 7, 9]),
    ("sell_break", "rr", [0.8, 1.0, 1.2]),
    ("sell_break", "sl_atr", [1.0, 1.2, 1.5]),
]


def _fmt_money(value: float) -> str:
    return f"{value:,.2f} $".replace(",", " ")


def print_report(result: BacktestResult, title: str) -> None:
    s = result.stats
    pf = s["profit_factor"]
    pf_text = "∞" if pf == float("inf") else f"{pf:.2f}"

    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)
    print(f"  Сделок:                 {s['trades']}")
    print(f"  Winrate:                {s['winrate']:.1f}%")
    print(f"  Profit factor:          {pf_text}")
    print(f"  Мат. ожидание:          {s['expectancy_r']:+.2f} R на сделку")
    print(f"  Средняя прибыль:        {s['avg_win_r']:+.2f} R")
    print(f"  Средний убыток:         {s['avg_loss_r']:+.2f} R")
    print(f"  Общий P&L:              {_fmt_money(s['total_pnl'])} ({s['return_pct']:+.2f}%)")
    print(f"  Финальный баланс:       {_fmt_money(s['final_balance'])}")
    print(f"  Макс. просадка:         {_fmt_money(s['max_dd'])} ({s['max_dd_pct']:.2f}%)")
    print(f"  Макс. серия убытков:    {s['max_consec_losses']}")
    print(f"  Среднее удержание:      {s['avg_bars']:.1f} баров M5")
    print(f"  Sharpe (на сделку):     {s['sharpe_trade']:.2f}")
    print("  P&L и баланс — справочно при лоте 0.01; на отбор сигналов лот не влияет.")
    print("=" * 64)

    if not result.monthly.empty:
        print("\n  По месяцам:")
        print("  " + "-" * 46)
        print(f"  {'Месяц':<10}{'Сделок':>8}{'P&L, $':>14}{'Winrate':>12}")
        for _, row in result.monthly.iterrows():
            print(
                f"  {row['month']:<10}{int(row['trades']):>8}"
                f"{row['pnl']:>14.2f}{row['winrate']:>11.1f}%"
            )
    print()


def save_result(result: BacktestResult, prefix: str) -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    trades_path = REPORTS_DIR / f"{prefix}_trades.csv"
    equity_path = REPORTS_DIR / f"{prefix}_equity.csv"
    monthly_path = REPORTS_DIR / f"{prefix}_monthly.csv"

    trades_to_frame(result.trades).to_csv(trades_path, index=False, encoding="utf-8-sig")
    result.equity.to_csv(equity_path, index=False, encoding="utf-8-sig")
    result.monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")
    print(f"  Сделки сохранены:  {trades_path}")
    print(f"  Кривая баланса:    {equity_path}")
    print(f"  Месяцы:            {monthly_path}")


def tune(
    df: pd.DataFrame,
    cfg: dict,
    train_frac: float = 0.7,
    entry_mode: str = "close",
    slippage_points: float = 0.0,
) -> None:
    split = int(len(df) * train_frac)
    train = df.iloc[:split].reset_index(drop=True)
    test = df.iloc[split:].reset_index(drop=True)

    print(
        f"\nПроверка параметров: train {train['time'].iloc[0]} .. {train['time'].iloc[-1]}"
        f" ({len(train)} баров), test {test['time'].iloc[0]} .. {test['time'].iloc[-1]}"
        f" ({len(test)} баров)"
    )

    rows = []
    for setup_name, param, values in SWEEPS:
        for value in values:
            setups = {name: dict(sp) for name, sp in cfg["strategy"]["setups"].items()}
            setups[setup_name][param] = value
            cfg_try = {**cfg, "strategy": {**cfg["strategy"], "setups": setups}}
            tr = run_backtest(
                train, cfg_try, entry_mode=entry_mode, slippage_points=slippage_points
            ).stats
            te = run_backtest(
                test, cfg_try, entry_mode=entry_mode, slippage_points=slippage_points
            ).stats
            rows.append(
                {
                    "setup": setup_name,
                    "param": param,
                    "value": value,
                    "train_n": tr["trades"],
                    "train_pf": round(tr["profit_factor"], 2),
                    "train_r": round(tr["expectancy_r"], 2),
                    "test_n": te["trades"],
                    "test_pf": round(te["profit_factor"], 2),
                    "test_r": round(te["expectancy_r"], 2),
                    "test_pnl": round(te["total_pnl"], 1),
                }
            )

    table = pd.DataFrame(rows)
    table["both_ok"] = (table["train_r"] > 0) & (table["test_r"] > 0)
    print("\n" + table.to_string(index=False))
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / "tune_results.csv"
    table.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nТаблица сохранена: {out}")
    print(
        "\nВажно: выбирайте значения, которые дают плюс и на train, и на test, "
        "а не только максимум прибыли (иначе это переобучение)."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Бэктест стратегии XAUUSD M5")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--refresh", action="store_true", help="заново скачать историю")
    parser.add_argument("--start", default=None, help="YYYY-MM-DD (время сервера)")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD (время сервера)")
    parser.add_argument("--tune", action="store_true", help="проверить устойчивость параметров")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument(
        "--realistic",
        action="store_true",
        help="жёсткие условия: вход по open следующего бара, проскальзывание 5 пт, спред 21",
    )
    parser.add_argument(
        "--slippage",
        type=float,
        default=None,
        help="проскальзывание в пунктах (с --realistic по умолчанию 5, иначе 0)",
    )
    parser.add_argument(
        "--spread",
        type=int,
        default=None,
        help="спред в пунктах (с --realistic по умолчанию 21, иначе из конфига)",
    )
    args = parser.parse_args()

    cfg = load_config()
    entry_mode = "next_open" if args.realistic else "close"
    slippage = args.slippage if args.slippage is not None else (5.0 if args.realistic else 0.0)
    spread = args.spread if args.spread is not None else (21 if args.realistic else None)
    if spread is not None:
        cfg["backtest"]["spread_points"] = spread
    symbol = args.symbol or cfg["symbol"]
    tf = cfg["timeframe"]

    cache = history_cache_path(symbol, tf)
    if cache.exists() and not args.refresh:
        # Можно запускать бэктест без запущенного MT5, если история уже скачана.
        df = load_history(symbol, tf)
    else:
        connect()
        try:
            ensure_symbol(symbol)
            df = load_history(symbol, tf, refresh=args.refresh)
        finally:
            shutdown()

    if args.start:
        df = df[df["time"] >= pd.Timestamp(args.start)]
    if args.end:
        df = df[df["time"] < pd.Timestamp(args.end)]
    df = df.reset_index(drop=True)

    if len(df) < 500:
        print("Недостаточно истории для бэктеста.")
        return 1

    print(
        f"Данные: {symbol} {tf}, {len(df)} баров, "
        f"{df['time'].iloc[0]} .. {df['time'].iloc[-1]} (время сервера)"
    )

    if args.tune:
        tune(df, cfg, args.train_frac, entry_mode=entry_mode, slippage_points=slippage)
        return 0

    result = run_backtest(
        df, cfg, entry_mode=entry_mode, slippage_points=slippage
    )
    parts = []
    if entry_mode != "close":
        parts.append("вход open(i+1)")
    if slippage:
        parts.append(f"проскальзывание {slippage:g} пт")
    if parts:
        parts.append(f"спред {cfg['backtest']['spread_points']} пт")
    mode_note = (" | " + ", ".join(parts)) if parts else ""
    title = (
        f"БЭКТЕСТ {symbol} {tf} | {df['time'].iloc[0]:%d.%m.%Y} - "
        f"{df['time'].iloc[-1]:%d.%m.%Y}{mode_note}"
    )
    print_report(result, title)
    save_result(result, "backtest_realistic" if entry_mode != "close" else "backtest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
