"""Зоны за последние N часов и рыночный bias — данные для часового отчёта.

Это информационный разбор, а не сигнал: зоны строятся по свингам M5,
сила зоны — эвристика (касания + объём + свежесть), а не проверенная
бэктестом закономерность.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .indicators import adx, atr, ema

STRENGTH_ORDER = ("weak", "medium", "strong", "strongest")


@dataclass
class Zone:
    price: float
    side: str  # "sell" — сопротивление сверху, "buy" — поддержка снизу
    touches: int
    volume: float
    score: float
    strength: str
    last_touch: pd.Timestamp


def _swing_points(df: pd.DataFrame, k: int) -> list[tuple[float, pd.Timestamp, float]]:
    """Фракталы: бар выше/ниже k соседей слева и справа."""
    return [(price, when, vol) for _, price, when, vol in swing_points(df, k)]


def swing_points(df: pd.DataFrame, k: int) -> list[tuple[int, float, pd.Timestamp, float]]:
    """Все подтверждённые фракталы: (индекс, цена, время, объём).

    Считается векторно, чтобы зонную стратегию можно было прогонять по каждому
    бару без пересчёта окна с нуля.
    """
    n = len(df)
    if n < 2 * k + 1:
        return []
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    volume = df["tick_volume"].to_numpy(dtype=float)
    times = df["time"].tolist()
    hmax = pd.Series(high).rolling(2 * k + 1, center=True).max().to_numpy()
    lmin = pd.Series(low).rolling(2 * k + 1, center=True).min().to_numpy()
    out: list[tuple[int, float, pd.Timestamp, float]] = []
    for i in range(k, n - k):
        if high[i] >= hmax[i]:
            out.append((i, float(high[i]), times[i], float(volume[i])))
        if low[i] <= lmin[i]:
            out.append((i, float(low[i]), times[i], float(volume[i])))
    return out


def _cluster(points: list[tuple[float, pd.Timestamp, float]], tol: float) -> list[list]:
    points = sorted(points, key=lambda p: p[0])
    groups: list[list[tuple[float, pd.Timestamp, float]]] = []
    for point in points:
        if groups and abs(point[0] - groups[-1][-1][0]) <= tol:
            groups[-1].append(point)
        else:
            groups.append([point])
    return groups


def find_zones(
    df: pd.DataFrame,
    atr_value: float,
    price: float,
    per_side: int = 3,
    fractal: int = 3,
    cluster_atr: float = 0.6,
    min_dist_atr: float = 0.4,
) -> list[Zone]:
    """Строит уровни по свингам окна и ранжирует их по силе."""
    if df.empty or atr_value <= 0:
        return []
    points = _swing_points(df, fractal)
    if not points:
        return []
    return rank_zones(
        points,
        df["time"].iloc[0],
        df["time"].iloc[-1],
        atr_value,
        price,
        per_side=per_side,
        cluster_atr=cluster_atr,
        min_dist_atr=min_dist_atr,
    )


def rank_zones(
    points: list[tuple[float, pd.Timestamp, float]],
    t0: pd.Timestamp,
    t1: pd.Timestamp,
    atr_value: float,
    price: float,
    per_side: int = 3,
    cluster_atr: float = 0.6,
    min_dist_atr: float = 0.4,
) -> list[Zone]:
    """Ранжирует свинги окна в уровни. `points` — (цена, время, объём)."""
    if not points or atr_value <= 0:
        return []

    tol = cluster_atr * atr_value
    groups = _cluster(points, tol)
    span = max((t1 - t0).total_seconds(), 1.0)
    max_volume = max(sum(p[2] for p in g) for g in groups) or 1.0

    raw: list[dict] = []
    for group in groups:
        above = group[0][0] > price
        # Уровень — крайняя точка группы: для сопротивления максимум, для поддержки минимум.
        level = max(p[0] for p in group) if above else min(p[0] for p in group)
        volume = sum(p[2] for p in group)
        last_touch = max(p[1] for p in group)
        recency = (last_touch - t0).total_seconds() / span
        score = (
            0.5 * min(len(group), 5) / 5
            + 0.3 * volume / max_volume
            + 0.2 * max(0.0, min(1.0, recency))
        )
        raw.append(
            {
                "price": level,
                "side": "sell" if above else "buy",
                "touches": len(group),
                "volume": volume,
                "score": score,
                "last_touch": last_touch,
            }
        )

    zones: list[Zone] = []
    # Уровни прямо под ценой — шум: не показываем то, что ближе 0.4 ATR.
    far = [r for r in raw if abs(r["price"] - price) >= min_dist_atr * atr_value]
    if far:
        raw = far
    for side in ("sell", "buy"):
        side_raw = sorted(
            [r for r in raw if r["side"] == side], key=lambda r: r["score"], reverse=True
        )
        # Убираем уровни, которые почти совпали после выбора крайней точки.
        kept: list[dict] = []
        for item in side_raw:
            if all(abs(item["price"] - k["price"]) > tol * 0.5 for k in kept):
                kept.append(item)
        kept = kept[:per_side]
        if not kept:
            continue
        best = kept[0]["score"] or 1.0
        for item in kept:
            ratio = item["score"] / best
            strength = (
                "strongest" if ratio >= 0.85
                else "strong" if ratio >= 0.65
                else "medium" if ratio >= 0.45
                else "weak"
            )
            zones.append(Zone(strength=strength, **item))

    # Сверху вниз для sell, снизу вверх для buy — так читается в сообщении.
    sells = sorted([z for z in zones if z.side == "sell"], key=lambda z: z.price)
    buys = sorted([z for z in zones if z.side == "buy"], key=lambda z: z.price, reverse=True)
    return sells + buys


def _tf_state(df: pd.DataFrame, rule: str) -> tuple[str, float, float, float]:
    close = df.set_index("time")["close"].resample(rule).last().dropna()
    if len(close) < 200:
        return "flat", float("nan"), float("nan"), float(close.iloc[-1]) if len(close) else float("nan")
    fast = float(ema(close, 50).iloc[-1])
    slow = float(ema(close, 200).iloc[-1])
    last = float(close.iloc[-1])
    if last < slow and fast < slow:
        return "down", fast, slow, last
    if last > slow and fast > slow:
        return "up", fast, slow, last
    return "flat", fast, slow, last


def market_bias(df: pd.DataFrame) -> dict:
    """Bias по тренду M5 / M15 / H1."""
    states = {
        "M5": _tf_state(df, "5min"),
        "M15": _tf_state(df, "15min"),
        "H1": _tf_state(df, "1h"),
    }
    votes = {"up": 0, "down": 0, "flat": 0}
    for state, *_ in states.values():
        votes[state] += 1

    if votes["down"] >= 2 and votes["down"] > votes["up"]:
        bias = "SELL"
    elif votes["up"] >= 2 and votes["up"] > votes["down"]:
        bias = "BUY"
    else:
        bias = "FLAT"

    m15 = df.set_index("time")
    m15 = m15.resample("15min").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    adx_m15 = float(adx(m15, 14).iloc[-1]) if len(m15) > 30 else float("nan")

    return {
        "bias": bias,
        "trends": {tf: state[0] for tf, state in states.items()},
        "ema": {tf: {"fast": state[1], "slow": state[2]} for tf, state in states.items()},
        "adx_m15": adx_m15,
        "votes": votes,
    }


def build_report(df: pd.DataFrame, hours: int = 24, per_side: int = 3) -> dict:
    """Собирает всё, что нужно для часового сообщения."""
    last = df["time"].iloc[-1]
    window = df[df["time"] >= last - pd.Timedelta(hours=hours)].reset_index(drop=True)
    price = float(df["close"].iloc[-1])
    atr_value = float(atr(df, 14).iloc[-1])
    bias_info = market_bias(df)
    zones = find_zones(window, atr_value, price, per_side=per_side)

    sells = [z for z in zones if z.side == "sell"]
    buys = [z for z in zones if z.side == "buy"]
    nearest_sell = min(sells, key=lambda z: z.price) if sells else None
    nearest_buy = max(buys, key=lambda z: z.price) if buys else None

    start_price = float(window["open"].iloc[0])
    change = price - start_price
    return {
        "time": last,
        "price": price,
        "hours": hours,
        "high": float(window["high"].max()),
        "low": float(window["low"].min()),
        "change": change,
        "change_pct": 100 * change / start_price if start_price else 0.0,
        "atr": atr_value,
        "adx_m15": bias_info["adx_m15"],
        "bias": bias_info["bias"],
        "trends": bias_info["trends"],
        "ema": bias_info["ema"],
        "zones": zones,
        "nearest_sell": nearest_sell,
        "nearest_buy": nearest_buy,
    }
