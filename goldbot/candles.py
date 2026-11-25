"""Свечи старших ТФ и касания их хая/лоу — для блока «сильные точки» в отчёте.

M5-бары сворачиваются в M15 / M30 / H1, затем для каждой свечи считается,
сколько раз цена подходила к её хаю и лоу и отходила обратно. Близкие уровни
складываются в одну точку: чем больше касаний, тем сильнее зона.
"""
from __future__ import annotations

import pandas as pd

NAMES = {5: "M5", 15: "M15", 30: "M30", 60: "H1"}

# интервал отчёта -> (ТФ в минутах, сколько свечей показывать максимум)
# 15 мин — до 4, если все четыре активные; иначе останутся 2–3.
DISPLAY: dict[int, tuple[int, int]] = {
    5: (5, 1),
    15: (15, 4),
    30: (30, 6),
    60: (60, 1),
}


def tf_name(minutes: int) -> str:
    return NAMES.get(minutes, f"M{minutes}")


def display_count(interval: int) -> int:
    """Сколько свечей показывать в блоке для этого интервала отчёта."""
    return DISPLAY.get(interval, DISPLAY[60])[1]


def aggregate(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Сворачивает M5 в свечи нужного ТФ. Незакрытая последняя свеча отбрасывается."""
    if minutes <= 5:
        return df[["time", "open", "high", "low", "close"]].reset_index(drop=True)

    candles = (
        df.set_index("time")
        .resample(f"{minutes}min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna(subset=["open"])
        .reset_index()
    )
    last_bar = df["time"].iloc[-1]
    closed = candles["time"] + pd.Timedelta(minutes=minutes) <= last_bar + pd.Timedelta(minutes=5)
    return candles[closed].reset_index(drop=True)


def _touches(bars: pd.DataFrame, level: float, tol: float, gap: float) -> int:
    """Сколько раз цена подходила к уровню и уходила от него."""
    count = 0
    ready = True
    for _, bar in bars.iterrows():
        touched = bar["low"] <= level + tol and bar["high"] >= level - tol
        if touched and ready:
            count += 1
            ready = False
        elif bar["close"] > level + gap or bar["close"] < level - gap:
            ready = True
    return count


def touch_rows(
    df: pd.DataFrame, interval: int, tol: float, gap: float, lookback: int = 6
) -> list[dict]:
    """Последние свечи ТФ отчёта и число касаний их хая/лоу."""
    minutes = interval if interval in NAMES else 60
    candles = aggregate(df, minutes).tail(lookback)
    rows: list[dict] = []
    for _, candle in candles.iterrows():
        after = df[df["time"] >= candle["time"]]
        rows.append(
            {
                "time": candle["time"],
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "hi": _touches(after, float(candle["high"]), tol, gap),
                "lo": _touches(after, float(candle["low"]), tol, gap),
            }
        )
    return rows


def active_rows(rows: list[dict], min_touches: int = 2, limit: int = 6) -> list[dict]:
    """Оставляет свечи, к которым реально возвращались. Минимум — последняя."""
    active = [row for row in rows if max(row["hi"], row["lo"]) >= min_touches]
    if not active and rows:
        active = rows[-1:]
    return active[-limit:]


def levels(rows: list[dict], price: float, cluster: float) -> list[dict]:
    """Собирает хаи и лоу в точки: близкие цены складываются в одну."""
    points: list[tuple[float, int]] = []
    for row in rows:
        points.append((row["high"], row["hi"]))
        points.append((row["low"], row["lo"]))
    points.sort(key=lambda item: item[0])

    clusters: list[dict] = []
    for value, touches in points:
        if clusters and value - clusters[-1]["price"] <= cluster:
            item = clusters[-1]
            item["touches"] += touches
            item["candles"] += 1
            item["price"] = (item["price"] + value) / 2
        else:
            clusters.append({"price": value, "touches": touches, "candles": 1})

    for item in clusters:
        item["side"] = "sell" if item["price"] > price else "buy"
    clusters.sort(key=lambda item: item["touches"], reverse=True)
    return clusters
