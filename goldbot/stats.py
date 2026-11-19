"""Статистика сетапов за последние дни — для карточек сигналов в Telegram.

Считается так же, как в run_monitor:
  * соло — сколько сделок дал бы сетап сам по себе;
  * в портфеле — сколько из них реально случилось (открыта одна позиция);
  * вытеснено = соло − в портфеле (сигнал не взяли, потому что была открыта
    другая сделка).
"""
from __future__ import annotations

import copy

import pandas as pd

from .backtest import run_backtest, trades_to_frame

DEFAULT_WINDOWS = (7, 14)
WARMUP_DAYS = 10  # запас истории, чтобы индикаторы успели сойтись


def _solo_trades(
    warm: pd.DataFrame,
    cfg: dict,
    name: str,
    setup: dict,
    xmarket: dict[str, pd.DataFrame] | None = None,
) -> pd.DataFrame:
    c = copy.deepcopy(cfg)
    c["strategy"] = {**cfg["strategy"], "setups": {name: setup}}
    return trades_to_frame(run_backtest(warm, c, xmarket=xmarket).trades)


def setup_stats(
    df: pd.DataFrame,
    cfg: dict,
    windows: tuple[int, ...] = DEFAULT_WINDOWS,
    last_bar: pd.Timestamp | None = None,
    xmarket: dict[str, pd.DataFrame] | None = None,
) -> dict[str, dict[str, dict]]:
    """Возвращает {"7": {setup: {"n", "wr", "displaced"}}, "14": {...}}.

    `df` — бары M5; желательно с запасом больше самого длинного окна.
    """
    empty = {str(d): {} for d in windows}
    if df is None or df.empty:
        return empty

    last = pd.Timestamp(last_bar) if last_bar is not None else pd.Timestamp(df["time"].iloc[-1])
    longest = max(windows)
    start = last - pd.Timedelta(days=longest)
    warm = df[df["time"] >= start - pd.Timedelta(days=WARMUP_DAYS)].reset_index(drop=True)
    if warm.empty:
        return empty

    setups = {
        name: setup
        for name, setup in cfg["strategy"]["setups"].items()
        if setup.get("enabled", True)
    }
    port_cfg = copy.deepcopy(cfg)
    port_cfg["strategy"] = {**cfg["strategy"], "setups": setups}
    port = trades_to_frame(run_backtest(warm, port_cfg, xmarket=xmarket).trades)
    if port.empty or "signal_time" not in port.columns:
        port = pd.DataFrame(columns=["setup", "signal_time", "r_multiple"])

    out: dict[str, dict[str, dict]] = {str(d): {} for d in windows}
    for name, setup in setups.items():
        solo = _solo_trades(warm, cfg, name, setup, xmarket=xmarket)
        if solo.empty or "signal_time" not in solo.columns:
            solo = pd.DataFrame(columns=["signal_time", "r_multiple"])
        for days in windows:
            since = last - pd.Timedelta(days=days)
            s = solo[solo["signal_time"] >= since]
            p = port[(port["setup"] == name) & (port["signal_time"] >= since)]
            n = int(len(s))
            wins = int((s["r_multiple"] > 0).sum()) if n else 0
            out[str(days)][name] = {
                "n": n,
                "wr": round(100 * wins / n, 1) if n else 0.0,
                "displaced": max(0, n - int(len(p))),
            }
    return out
