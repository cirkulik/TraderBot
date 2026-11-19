"""Бэктест стратегии на исторических данных M5."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .strategy import prepare, signal_at, warmup_bars


@dataclass
class Trade:
    signal_time: pd.Timestamp
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: str
    setup: str
    entry: float
    sl: float
    tp: float
    exit_price: float
    exit_reason: str
    risk_price: float
    r_multiple: float
    pnl_usd: float
    bars_held: int
    confidence: int
    balance_after: float


@dataclass
class BacktestResult:
    trades: list[Trade]
    initial_balance: float
    final_balance: float
    equity: pd.DataFrame
    stats: dict
    monthly: pd.DataFrame


def simulate(
    df: pd.DataFrame,
    cfg: dict,
    contract_size: float = 100.0,
    point: float = 0.01,
    collect_trades: bool = True,
    entry_mode: str = "close",
    slippage_points: float = 0.0,
) -> BacktestResult:
    """Симуляция: сигнал на закрытии бара, вход по цене закрытия, учёт спреда.

    `entry_mode`:
      * "close"     — идеальное исполнение: вход по цене сигнального бара;
      * "next_open" — реалистичное: вход по open следующего бара (задержка 5 мин),
                      SL/TP пересчитываются от фактической цены входа.

    `slippage_points` — проскальзывание в пунктах, всегда против сделки.
    """
    params = cfg["strategy"]
    bt = cfg["backtest"]
    spread = float(bt["spread_points"]) * point
    # Лот — только справочный множитель для пересчёта R в доллары в отчёте.
    # На отбор сигналов он не влияет.
    lot = float(bt.get("reference_lot", 0.01))
    commission = float(bt.get("commission_per_lot", 0.0)) * lot
    initial_balance = float(bt["initial_balance"])

    cooldown = int(params["cooldown_bars"])
    max_per_day = int(params["max_signals_per_day"])
    max_risk_price = float(cfg.get("max_risk_price", 0.0) or 0.0)
    trail_atr = float(params.get("trail_atr", 0.0) or 0.0)
    trail_start_r = float(params.get("trail_start_r", 1.0) or 1.0)

    warm = warmup_bars(cfg)
    n = len(df)

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    atr_arr = df["atr"].to_numpy(dtype=float)
    long_setup = df["long_setup"].to_numpy(dtype=bool)
    short_setup = df["short_setup"].to_numpy(dtype=bool)
    times = df["time"]
    dates = df["time"].dt.date.to_numpy()

    trades: list[Trade] = []
    equity_rows: list[tuple[pd.Timestamp, float]] = [(times.iloc[min(warm, n - 1)], initial_balance)]
    balance = initial_balance
    daily_counts: dict = {}
    cooldown_until = 0
    pos: dict | None = None

    def close_position(i: int, exit_price: float, reason: str) -> None:
        nonlocal balance, pos, cooldown_until
        assert pos is not None
        direction = pos["dir"]
        gross = (exit_price - pos["entry"]) * direction
        net = gross - spread
        r_mult = net / pos["risk"]
        pnl = net * contract_size * lot - commission
        balance += pnl

        if reason == "SL" and pos["be"] and abs(exit_price - pos["entry"]) < 1e-9:
            reason = "BE"
        elif reason == "SL" and pos.get("trailed"):
            reason = "TRAIL"

        trades.append(
            Trade(
                signal_time=pos["signal_time"],
                entry_time=pos["entry_time"],
                exit_time=times.iloc[i],
                direction="BUY" if direction == 1 else "SELL",
                setup=pos["setup"],
                entry=round(pos["entry"], 2),
                sl=round(pos["initial_sl"], 2),
                tp=round(pos["tp"], 2),
                exit_price=round(exit_price, 2),
                exit_reason=reason,
                risk_price=round(pos["risk"], 4),
                r_multiple=round(r_mult, 4),
                pnl_usd=round(pnl, 4),
                bars_held=i - pos["entry_i"],
                confidence=pos["confidence"],
                balance_after=round(balance, 2),
            )
        )
        equity_rows.append((times.iloc[i], balance))
        cooldown_until = i + cooldown
        pos = None

    for i in range(warm, n):
        if pos is not None:
            held = i - pos["entry_i"]
            exit_price = None
            reason = ""

            if pos["dir"] == 1:
                pos["max_high"] = max(pos["max_high"], h[i])
                if o[i] <= pos["sl"]:
                    exit_price, reason = o[i], "SL"
                elif low[i] <= pos["sl"]:
                    exit_price, reason = pos["sl"], "SL"
                elif o[i] >= pos["tp"]:
                    exit_price, reason = o[i], "TP"
                elif h[i] >= pos["tp"]:
                    exit_price, reason = pos["tp"], "TP"
                elif held >= pos["time_exit"]:
                    exit_price, reason = c[i], "TIME"
                else:
                    if (
                        pos["be_r"] > 0
                        and not pos["be"]
                        and h[i] >= pos["entry"] + pos["be_r"] * pos["risk"]
                    ):
                        pos["sl"] = max(pos["sl"], pos["entry"])
                        pos["be"] = True
                    if (
                        trail_atr > 0
                        and h[i] >= pos["entry"] + trail_start_r * pos["risk"]
                    ):
                        new_sl = pos["max_high"] - trail_atr * atr_arr[i]
                        if new_sl > pos["sl"]:
                            pos["sl"] = new_sl
                            pos["trailed"] = True
                            if pos["sl"] >= pos["entry"]:
                                pos["be"] = True
            else:
                pos["min_low"] = min(pos["min_low"], low[i])
                if o[i] >= pos["sl"]:
                    exit_price, reason = o[i], "SL"
                elif h[i] >= pos["sl"]:
                    exit_price, reason = pos["sl"], "SL"
                elif o[i] <= pos["tp"]:
                    exit_price, reason = o[i], "TP"
                elif low[i] <= pos["tp"]:
                    exit_price, reason = pos["tp"], "TP"
                elif held >= pos["time_exit"]:
                    exit_price, reason = c[i], "TIME"
                else:
                    if (
                        pos["be_r"] > 0
                        and not pos["be"]
                        and low[i] <= pos["entry"] - pos["be_r"] * pos["risk"]
                    ):
                        pos["sl"] = min(pos["sl"], pos["entry"])
                        pos["be"] = True
                    if (
                        trail_atr > 0
                        and low[i] <= pos["entry"] - trail_start_r * pos["risk"]
                    ):
                        new_sl = pos["min_low"] + trail_atr * atr_arr[i]
                        if new_sl < pos["sl"]:
                            pos["sl"] = new_sl
                            pos["trailed"] = True
                            if pos["sl"] <= pos["entry"]:
                                pos["be"] = True

            if exit_price is not None:
                close_position(i, float(exit_price), reason)
                continue

        if pos is not None:
            continue
        if i < cooldown_until:
            continue

        day = dates[i]
        if daily_counts.get(day, 0) >= max_per_day:
            continue
        if not (long_setup[i] or short_setup[i]):
            continue

        setup_name = str(df["setup"].iloc[i])
        direction = cfg["strategy"]["setups"][setup_name].get("direction", "BUY")
        if entry_mode == "next_open":
            if i + 1 >= n:
                continue
            entry_px = float(o[i + 1])
        else:
            entry_px = float(c[i])
        if slippage_points:
            entry_px += slippage_points * point * (1 if direction == "BUY" else -1)

        sig = signal_at(df, i, cfg, entry_price=entry_px)
        if sig is None:
            continue
        if max_risk_price > 0 and sig.risk_price > max_risk_price:
            continue
        daily_counts[day] = daily_counts.get(day, 0) + 1
        pos = {
            "dir": 1 if sig.direction == "BUY" else -1,
            "entry": sig.entry,
            "sl": sig.sl,
            "initial_sl": sig.sl,
            "tp": sig.tp,
            "risk": sig.risk_price,
            "entry_i": i,
            "signal_time": sig.time,
            "entry_time": times.iloc[i + 1] if entry_mode == "next_open" else times.iloc[i],
            "confidence": sig.confidence,
            "setup": sig.setup,
            "be": False,
            "be_r": sig.be_at_r,
            "time_exit": sig.time_exit_bars,
            "trailed": False,
            "max_high": sig.entry,
            "min_low": sig.entry,
        }

    if pos is not None:
        close_position(n - 1, float(c[n - 1]), "END")

    equity = pd.DataFrame(equity_rows, columns=["time", "balance"])
    stats = compute_stats(trades, initial_balance, equity)
    monthly = monthly_table(trades)
    return BacktestResult(
        trades=trades,
        initial_balance=initial_balance,
        final_balance=balance,
        equity=equity,
        stats=stats,
        monthly=monthly,
    )


def run_backtest(
    df: pd.DataFrame,
    cfg: dict,
    contract_size: float = 100.0,
    point: float = 0.01,
    entry_mode: str = "close",
    slippage_points: float = 0.0,
    xmarket: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    prepared = prepare(df, cfg, xmarket=xmarket)
    return simulate(
        prepared,
        cfg,
        contract_size=contract_size,
        point=point,
        entry_mode=entry_mode,
        slippage_points=slippage_points,
    )


def compute_stats(
    trades: list[Trade], initial_balance: float, equity: pd.DataFrame
) -> dict:
    if not trades:
        return {
            "trades": 0,
            "winrate": 0.0,
            "profit_factor": 0.0,
            "expectancy_r": 0.0,
            "total_pnl": 0.0,
            "return_pct": 0.0,
            "max_dd": 0.0,
            "max_dd_pct": 0.0,
            "avg_r": 0.0,
            "avg_win_r": 0.0,
            "avg_loss_r": 0.0,
            "max_consec_losses": 0,
            "avg_bars": 0.0,
            "sharpe_trade": 0.0,
            "final_balance": initial_balance,
        }

    pnl = np.array([t.pnl_usd for t in trades], dtype=float)
    r = np.array([t.r_multiple for t in trades], dtype=float)
    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())

    max_consec = 0
    cur = 0
    for value in pnl:
        if value < 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    balances = equity["balance"].to_numpy(dtype=float)
    peak = np.maximum.accumulate(balances)
    dd = peak - balances
    max_dd = float(dd.max()) if len(dd) else 0.0
    max_dd_pct = float((dd / np.where(peak == 0, 1, peak)).max() * 100) if len(dd) else 0.0

    std = float(r.std(ddof=1)) if len(r) > 1 else 0.0
    sharpe = float(r.mean() / std * np.sqrt(len(r))) if std > 0 else 0.0

    return {
        "trades": len(trades),
        "winrate": float(len(wins) / len(trades) * 100),
        "profit_factor": float(gross_profit / gross_loss) if gross_loss > 0 else float("inf"),
        "expectancy_r": float(r.mean()),
        "total_pnl": float(pnl.sum()),
        "return_pct": float(pnl.sum() / initial_balance * 100),
        "max_dd": max_dd,
        "max_dd_pct": max_dd_pct,
        "avg_r": float(r.mean()),
        "avg_win_r": float(r[r > 0].mean()) if len(wins) else 0.0,
        "avg_loss_r": float(r[r < 0].mean()) if len(losses) else 0.0,
        "max_consec_losses": max_consec,
        "avg_bars": float(np.mean([t.bars_held for t in trades])),
        "sharpe_trade": sharpe,
        "final_balance": float(initial_balance + pnl.sum()),
    }


def monthly_table(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame(columns=["month", "trades", "pnl", "winrate"])
    df = pd.DataFrame(
        {
            "month": [t.exit_time.strftime("%Y-%m") for t in trades],
            "pnl": [t.pnl_usd for t in trades],
            "win": [1 if t.pnl_usd > 0 else 0 for t in trades],
        }
    )
    grouped = df.groupby("month").agg(
        trades=("pnl", "size"), pnl=("pnl", "sum"), winrate=("win", "mean")
    )
    grouped["winrate"] = grouped["winrate"] * 100
    return grouped.reset_index()


def trades_to_frame(trades: list[Trade]) -> pd.DataFrame:
    return pd.DataFrame([asdict(t) for t in trades])
