"""Проверка сетапов на текущем окне и сравнение с прошлой проверкой.

Запуск:
    python run_monitor.py                # последние 30 дней
    python run_monitor.py --days 90      # последние 90 дней
    python run_monitor.py --refresh      # докачать историю из MT5

Что показывает:
  * по каждому включённому сетапу — сделки, winrate, profit factor, R, просадку;
  * сколько сигналов реально ушло в журнал за окно;
  * что изменилось с прошлой проверки (новые сделки и добавленный R);
  * какие сетапы ушли в минус и сколько сделок для этого нужно.

История проверок хранится в data/monitor_last.json (перезаписывается
каждым запуском).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.backtest import run_backtest, trades_to_frame
from goldbot.config import DATA_DIR, ROOT, load_config
from goldbot.data import (
    connect,
    ensure_symbol,
    get_closed_rates,
    history_cache_path,
    load_history,
    shutdown,
)

STATE_PATH = DATA_DIR / "monitor_last.json"
MIN_TRADES = 10          # меньше — статистики нет
WARN_TRADES = 5          # для сравнения с прошлой проверкой


def refresh_history(cfg: dict) -> pd.DataFrame:
    """Докачивает свежие бары к кешу, не теряя старую историю."""
    symbol, tf = cfg["symbol"], cfg["timeframe"]
    cache = history_cache_path(symbol, tf)
    old = load_history(symbol, tf) if cache.exists() else pd.DataFrame()
    connect()
    try:
        ensure_symbol(symbol)
        fresh = get_closed_rates(symbol, tf, 3000)
    finally:
        shutdown()
    if old.empty:
        fresh.to_csv(cache, index=False)
        return fresh
    new = fresh[fresh["time"] > old["time"].max()]
    if new.empty:
        return old
    merged = (
        pd.concat([old, new], ignore_index=True)
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )
    merged.to_csv(cache, index=False)
    print(f"История обновлена: +{len(new)} баров, последний {merged['time'].iloc[-1]}")
    return merged


def dd_r(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    eq = trades["r_multiple"].cumsum()
    return float((eq.cummax() - eq).max())


def one(df: pd.DataFrame, cfg: dict, name: str, setup: dict) -> dict:
    cfg_try = {**cfg, "strategy": {**cfg["strategy"], "setups": {name: setup}}}
    res = run_backtest(df, cfg_try)
    t = trades_to_frame(res.trades)
    wins = int((t["r_multiple"] > 0).sum()) if len(t) else 0
    pf = res.stats["profit_factor"] if len(t) else 0.0
    return {
        "n": len(t),
        "wins": wins,
        "wr": round(100 * wins / len(t), 1) if len(t) else 0.0,
        "pf": None if pf == float("inf") else round(pf, 2),
        "r": round(float(t["r_multiple"].sum()), 1) if len(t) else 0.0,
        "dd": round(dd_r(t), 1),
    }


def journal_counts(cfg: dict, start_utc: pd.Timestamp) -> dict[str, int]:
    path = ROOT / cfg["live"].get("journal_path", "data/signals.csv")
    if not path.exists():
        return {}
    try:
        j = pd.read_csv(path)
    except Exception:  # noqa: BLE001 - битый журнал не должен ломать проверку
        return {}
    if "created_utc" not in j.columns or j.empty:
        return {}
    j["created_utc"] = pd.to_datetime(j["created_utc"], errors="coerce", utc=True)
    j = j.dropna(subset=["created_utc"])
    sub = j[j["created_utc"].dt.tz_localize(None) >= start_utc]
    return sub["setup"].value_counts().to_dict()


def executor_results(cfg: dict) -> dict[str, dict]:
    path = ROOT / cfg["execution"].get("state_path", "data/executor.csv")
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return {}
    if "status" not in df.columns:
        return {}
    closed = df[df["status"] == "CLOSED"].copy()
    if closed.empty:
        return {}
    closed["result_r"] = pd.to_numeric(closed["result_r"], errors="coerce")
    out: dict[str, dict] = {}
    for setup, group in closed.groupby("setup"):
        out[str(setup)] = {
            "n": int(len(group)),
            "r": round(float(group["result_r"].sum()), 1),
        }
    return out


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def status(row: dict, delta_r: float | None, new_trades: int | None) -> str:
    if row["n"] < MIN_TRADES:
        return "мало сделок"
    if row["r"] < 0:
        return "В МИНУСЕ"
    if delta_r is not None and new_trades and new_trades >= WARN_TRADES and delta_r < 0:
        return "ухудшился"
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка сетапов GoldBot")
    parser.add_argument("--days", type=int, default=30, help="окно в днях (по умолчанию 30)")
    parser.add_argument("--refresh", action="store_true", help="докачать историю из MT5")
    parser.add_argument("--no-save", action="store_true", help="не сохранять снимок")
    args = parser.parse_args()

    cfg = load_config()
    df = refresh_history(cfg) if args.refresh else load_history(
        cfg["symbol"], cfg["timeframe"]
    )
    if df.empty:
        print("Нет истории: запустите с --refresh")
        return 1

    last_bar = df["time"].iloc[-1]
    now_utc = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    offset_h = int(round((last_bar - now_utc).total_seconds() / 3600))
    start_server = last_bar - pd.Timedelta(days=args.days)
    start_utc = start_server - pd.Timedelta(hours=offset_h)
    window = df[df["time"] >= start_server].reset_index(drop=True)

    setups = {
        k: v for k, v in cfg["strategy"]["setups"].items() if v.get("enabled", True)
    }
    base_cfg = {**cfg, "strategy": {**cfg["strategy"], "setups": setups}}
    base = run_backtest(window, base_cfg)
    bt = trades_to_frame(base.trades)

    journal = journal_counts(cfg, start_utc)
    live = executor_results(cfg)
    state = load_state()
    prev = state.get("setups", {})
    prev_days = state.get("days")

    print()
    print("=" * 96)
    print(f"  GOLDBOT — проверка сетапов за {args.days} дней")
    print(f"  окно: {start_server:%d.%m %H:%M} .. {last_bar:%d.%m %H:%M} "
          f"(сервер), {len(window)} баров")
    if prev_days and prev_days != args.days:
        print(f"  прошлая проверка была за {prev_days} дней — сравнение только по R")
    print("=" * 96)
    print(f"  портфель: {len(bt)} сделок, WR {base.stats['winrate']:.1f}%, "
          f"PF {base.stats['profit_factor']:.2f}, "
          f"{bt['r_multiple'].sum():+.1f} R, просадка {dd_r(bt):.1f} R")
    if journal:
        total = sum(journal.values())
        print(f"  сигналов в журнале за окно: {total}")
    print()

    header = (
        f"  {'сетап':<20}{'сделок':>7}{'WR':>7}{'PF':>7}{'R':>8}{'DD':>7}"
        f"{'сигн.':>7}   {'с прошлой проверки':<26}{'статус'}"
    )
    print(header)
    print("  " + "-" * 92)

    rows: dict[str, dict] = {}
    problems: list[str] = []
    for name, setup in setups.items():
        row = one(window, cfg, name, setup)
        rows[name] = row
        old = prev.get(name)
        delta_text = "—"
        delta_r = None
        new_trades = None
        if old and prev_days == args.days:
            new_trades = row["n"] - int(old.get("n", 0))
            delta_r = round(row["r"] - float(old.get("r", 0.0)), 1)
            delta_text = f"+{new_trades} сд., {delta_r:+.1f} R"
        state_text = status(row, delta_r, new_trades)
        if state_text in ("В МИНУСЕ", "ухудшился"):
            problems.append(f"{name}: {state_text.lower()} ({row['r']:+.1f} R, "
                            f"{row['n']} сделок)")
        live_part = live.get(name)
        live_text = f"{live_part['n']} / {live_part['r']:+.1f}R" if live_part else "—"
        pf_text = "—" if row["pf"] is None else f"{row['pf']:.2f}"
        print(
            f"  {name:<20}{row['n']:>7}{row['wr']:>6.1f}%{pf_text:>7}"
            f"{row['r']:>+8.1f}{row['dd']:>7.1f}{journal.get(name, 0):>7}"
            f"   {delta_text:<26}{state_text}"
        )
        if live_part:
            print(f"  {'':<20}факт. сделки исполнителя: {live_text}")

    print("  " + "-" * 92)
    if problems:
        print("\n  Требуют решения:")
        for line in problems:
            print(f"    - {line}")
        print("  Отключать сетап стоит, если он в минусе и за 30, и за 90 дней "
              "(проверьте --days 90).")
    else:
        print("\n  Всё в плюсе: сетапы можно не трогать.")

    if not args.no_save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(
            json.dumps(
                {
                    "checked_at": now_utc.isoformat(timespec="seconds"),
                    "days": args.days,
                    "last_bar": str(last_bar),
                    "portfolio": {
                        "n": len(bt),
                        "wr": round(base.stats["winrate"], 1),
                        "pf": round(base.stats["profit_factor"], 2),
                        "r": round(float(bt["r_multiple"].sum()), 1),
                    },
                    "setups": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n  Снимок сохранён: {STATE_PATH}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
