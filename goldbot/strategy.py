"""Стратегия XAUUSD M5: отскок после перепроданности + дневной откат.

Пять сетапов (настраиваются в config.json):
  * dip_london — резкое падение за N баров в лондонскую сессию → BUY (отскок).
  * dip_late   — то же, но более мягкий порог днём 11:00–15:00.
  * sell_noon  — SELL по времени в 12:00 (10:00 Лондона) — откат перед утренним
                 золотым фиксом; единственный короткий сетап.
  * rsi_ny     — RSI < 30 и разворот вверх в нью-йоркскую сессию → BUY (отскок).
  * stretch_ny — цена ниже EMA50 на N ATR и бычий бар в Нью-Йорке → BUY.

Long-only сетапы имеют устойчивое преимущество на истории 2025-06..2026-11.
Паттерновые короткие сетапы (SELL после роста) на тех же данных были
нестабильны, поэтому по умолчанию отключены (см. config.json → setups.short_*).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import i18n
from .config import ROOT
from .indicators import adx, atr, ema, rsi


@dataclass
class Signal:
    time: pd.Timestamp          # время сигнального бара (время сервера)
    setup: str                  # имя сетапа
    setup_label: str            # человекочитаемое название
    direction: str              # "BUY" | "SELL"
    entry: float
    sl: float
    tp: float
    atr: float
    risk_price: float
    rr: float
    time_exit_bars: int
    be_at_r: float
    confidence: int
    reasons: list[str] = field(default_factory=list)
    reason_items: list[tuple[str, dict]] = field(default_factory=list)
    spread_points: float = 0.0
    bar_index: int | None = None

    def to_dict(self, utc_offset_hours: int = 2) -> dict:
        return {
            "bar_time": self.time.strftime("%Y-%m-%d %H:%M:%S"),
            "bar_time_utc": (self.time - pd.Timedelta(hours=utc_offset_hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "setup": self.setup,
            "setup_label": self.setup_label,
            "direction": self.direction,
            "entry": round(self.entry, 2),
            "sl": round(self.sl, 2),
            "tp": round(self.tp, 2),
            "risk_price": round(self.risk_price, 2),
            "rr": self.rr,
            "time_exit_bars": self.time_exit_bars,
            "be_at_r": self.be_at_r,
            "confidence": self.confidence,
            "spread_points": self.spread_points,
            "reasons": "; ".join(self.reasons),
        }


SETUP_LABELS = {
    "dip_london": "Отскок после падения (Лондон)",
    "dip_late": "Отскок после падения (день Лондона)",
    "rsi_ny": "RSI-разворот (Нью-Йорк)",
    "stretch_ny": "Растяжение от EMA50 (Нью-Йорк)",
    "sell_noon": "Дневной откат (продажа в 12:00)",
    "sell_break": "Пробой вниз (продажа)",
    "swing_high": "Вход у хая по тренду (продажа)",
}


def warmup_bars(cfg: dict) -> int:
    params = cfg["strategy"]
    bars = 200
    for setup in params.get("setups", {}).values():
        bars = max(bars, int(setup.get("drop_bars", 0)), int(setup.get("rsi_period", 0)))
    return bars + 10


def _setup_condition(out: pd.DataFrame, setup: dict) -> pd.Series:
    direction = setup.get("direction", "BUY")
    hour = out["time"].dt.hour
    cond = (hour >= int(setup["session_start_hour"])) & (
        hour < int(setup["session_end_hour"])
    )

    trigger = setup.get("trigger", "rsi")
    if trigger == "drop":
        bars = int(setup["drop_bars"])
        move = (out["close"] - out["close"].shift(bars)) / out["atr"]
        if direction == "BUY":
            cond &= move <= -float(setup["drop_atr"])
        else:
            cond &= move >= float(setup["drop_atr"])
    elif trigger == "rsi":
        level = float(setup["rsi_level"])
        if direction == "BUY":
            cond &= (out["rsi"] < level) & (out["rsi"] > out["rsi"].shift(1))
        else:
            cond &= (out["rsi"] > level) & (out["rsi"] < out["rsi"].shift(1))
    elif trigger == "rsi2":
        level = float(setup["rsi_level"])
        if direction == "BUY":
            cond &= (out["rsi2"] < level) & (out["rsi2"] > out["rsi2"].shift(1))
        else:
            cond &= (out["rsi2"] > level) & (out["rsi2"] < out["rsi2"].shift(1))
    elif trigger == "bb":
        period = int(setup.get("bb_period", 20))
        width = float(setup.get("bb_std", 2.0))
        mid = out["close"].rolling(period).mean()
        sd = out["close"].rolling(period).std(ddof=0)
        if direction == "BUY":
            cond &= (out["close"] < mid - width * sd) & (out["close"] > out["open"])
        else:
            cond &= (out["close"] > mid + width * sd) & (out["close"] < out["open"])
    elif trigger == "stretch":
        period = int(setup.get("ema_period", 50))
        ema_series = out["ema50"] if period == 50 else ema(out["close"], period)
        stretch = (out["close"] - ema_series) / out["atr"]
        limit = float(setup.get("stretch_atr", 2.0))
        if direction == "BUY":
            cond &= (stretch <= -limit) & (out["close"] > out["open"])
        else:
            cond &= (stretch >= limit) & (out["close"] < out["open"])
    elif trigger == "streak":
        bars = int(setup.get("streak_bars", 3))
        if direction == "BUY":
            streak = (out["close"] < out["open"]).astype(int)
        else:
            streak = (out["close"] > out["open"]).astype(int)
        run = streak.groupby((streak == 0).cumsum()).cumsum()
        cond &= run >= bars
        if "rsi_level" in setup:
            level = float(setup["rsi_level"])
            cond &= out["rsi"] < level if direction == "BUY" else out["rsi"] > level
    elif trigger == "trend_pullback":
        if direction == "BUY":
            cond &= (
                (out["close"] > out["ema200"])
                & (out["ema50"] > out["ema200"])
                & (out["low"] <= out["ema20"])
                & (out["close"] > out["ema20"])
                & (out["close"] > out["open"])
            )
        else:
            cond &= (
                (out["close"] < out["ema200"])
                & (out["ema50"] < out["ema200"])
                & (out["high"] >= out["ema20"])
                & (out["close"] < out["ema20"])
                & (out["close"] < out["open"])
            )
    elif trigger == "breakout":
        bars = int(setup.get("breakout_bars", 20))
        buffer_atr = float(setup.get("breakout_buffer_atr", 0.0))
        if direction == "BUY":
            level = out["high"].rolling(bars).max().shift(1)
            cond &= (out["close"] > level + buffer_atr * out["atr"]) & (out["close"] > out["open"])
        else:
            level = out["low"].rolling(bars).min().shift(1)
            cond &= (out["close"] < level - buffer_atr * out["atr"]) & (out["close"] < out["open"])
    elif trigger == "swing_high":
        # Вход у локального хая: цена подошла к максимуму последних N баров
        # и бар закрылся вниз с верхней тенью (отказ от роста).
        look = int(setup.get("sh_lookback", 24))
        zone = float(setup.get("sh_zone_atr", 0.5))
        wick = float(setup.get("sh_wick_atr", 0.3))
        pos = float(setup.get("sh_close_pos", 0.5))
        rally = float(setup.get("sh_rally_atr", 1.0))
        level = out["high"].rolling(look).max().shift(1)
        base = out["low"].rolling(look).min().shift(1)
        rng = (out["high"] - out["low"]).replace(0, np.nan)
        close_pos = (out["close"] - out["low"]) / rng
        body_high = out[["open", "close"]].max(axis=1)
        body_low = out[["open", "close"]].min(axis=1)
        if direction == "SELL":
            cond &= (out["high"] >= level - zone * out["atr"]) & (
                (level - base) >= rally * out["atr"]
            )
            cond &= (
                (out["close"] < out["open"])
                & (close_pos <= pos)
                & ((out["high"] - body_high) >= wick * out["atr"])
            )
        else:
            cond &= (out["low"] <= base + zone * out["atr"]) & (
                (level - base) >= rally * out["atr"]
            )
            cond &= (
                (out["close"] > out["open"])
                & (close_pos >= 1.0 - pos)
                & ((body_low - out["low"]) >= wick * out["atr"])
            )
    elif trigger == "retest_break":
        # Ретест пробитой поддержки: цена отросла к минимуму последних N баров
        # и закрылась обратно под ним — уровень стал сопротивлением.
        look = int(setup.get("rb_lookback", 24))
        zone = float(setup.get("rb_zone_atr", 0.4))
        level = out["low"].rolling(look).min().shift(1)
        if direction == "SELL":
            cond &= (out["high"] >= level - zone * out["atr"]) & (out["close"] < level)
            cond &= out["close"] < out["open"]
        else:
            level = out["high"].rolling(look).max().shift(1)
            cond &= (out["low"] <= level + zone * out["atr"]) & (out["close"] > level)
            cond &= out["close"] > out["open"]
    elif trigger == "pullback_cross":
        # Откат вниз после отскока: цена была выше EMA20, вернулась под неё,
        # RSI на отскоке был высоким, бар медвежий. Тренд — вниз.
        period = int(setup.get("pc_ema", 20))
        ema_series = out["ema20"] if period == 20 else ema(out["close"], period)
        min_rsi = float(setup.get("pc_min_rsi", 50))
        if direction == "SELL":
            cond &= (out["close"] < out["ema200"]) & (out["ema50"] < out["ema200"])
            cond &= (out["close"].shift(1) > ema_series.shift(1)) & (out["close"] < ema_series)
            cond &= (out["rsi"].shift(1) >= min_rsi) & (out["close"] < out["open"])
        else:
            cond &= (out["close"] > out["ema200"]) & (out["ema50"] > out["ema200"])
            cond &= (out["close"].shift(1) < ema_series.shift(1)) & (out["close"] > ema_series)
            cond &= (out["rsi"].shift(1) <= min_rsi) & (out["close"] > out["open"])
    elif trigger == "vwap":
        typical = (out["high"] + out["low"] + out["close"]) / 3.0
        vol = out["tick_volume"].fillna(0).clip(lower=0)
        day = out["time"].dt.date
        cum_pv = (typical * vol).groupby(day).cumsum()
        cum_v = vol.groupby(day).cumsum().replace(0, np.nan)
        vwap = cum_pv / cum_v
        dev = (out["close"] - vwap) / out["atr"]
        limit = float(setup.get("vwap_atr", 1.5))
        if direction == "BUY":
            cond &= (dev <= -limit) & (out["close"] > out["open"])
        else:
            cond &= (dev >= limit) & (out["close"] < out["open"])
    elif trigger == "pin":
        body_low = out[["open", "close"]].min(axis=1)
        body_high = out[["open", "close"]].max(axis=1)
        rng = (out["high"] - out["low"]).replace(0, np.nan)
        lower_wick = body_low - out["low"]
        upper_wick = out["high"] - body_high
        close_pos = (out["close"] - out["low"]) / rng
        wick = float(setup.get("pin_wick_atr", 1.0))
        pos = float(setup.get("pin_close_pos", 0.6))
        if direction == "BUY":
            cond &= (lower_wick >= wick * out["atr"]) & (close_pos >= pos)
        else:
            cond &= (upper_wick >= wick * out["atr"]) & (close_pos <= 1.0 - pos)
        if "rsi_level" in setup:
            level = float(setup["rsi_level"])
            cond &= out["rsi"] < level if direction == "BUY" else out["rsi"] > level
    elif trigger == "fade_break":
        bars = int(setup.get("break_bars", 20))
        if direction == "BUY":
            level = out["low"].rolling(bars).min().shift(1)
            cond &= (out["low"] < level) & (out["close"] > level) & (out["close"] > out["open"])
        else:
            level = out["high"].rolling(bars).max().shift(1)
            cond &= (out["high"] > level) & (out["close"] < level) & (out["close"] < out["open"])
    elif trigger == "rsi_trend":
        level = float(setup["rsi_level"])
        if direction == "BUY":
            cond &= (
                (out["close"] > out["ema200"])
                & (out["rsi"] < level)
                & (out["rsi"] > out["rsi"].shift(1))
            )
        else:
            cond &= (
                (out["close"] < out["ema200"])
                & (out["rsi"] > level)
                & (out["rsi"] < out["rsi"].shift(1))
            )
    elif trigger == "rsi2_trend":
        level = float(setup["rsi_level"])
        if direction == "BUY":
            cond &= (
                (out["close"] > out["ema200"])
                & (out["rsi2"] < level)
                & (out["rsi2"] > out["rsi2"].shift(1))
            )
        else:
            cond &= (
                (out["close"] < out["ema200"])
                & (out["rsi2"] > level)
                & (out["rsi2"] < out["rsi2"].shift(1))
            )
    elif trigger == "keltner":
        period = int(setup.get("kelt_period", 20))
        mult = float(setup.get("kelt_mult", 2.0))
        mid = out["ema20"] if period == 20 else ema(out["close"], period)
        if direction == "BUY":
            cond &= (out["close"] < mid - mult * out["atr"]) & (out["close"] > out["open"])
        else:
            cond &= (out["close"] > mid + mult * out["atr"]) & (out["close"] < out["open"])
    elif trigger == "gap":
        prev_close = out["close"].shift(1)
        gap = (out["open"] - prev_close) / out["atr"]
        new_session = out["time"].diff() > pd.Timedelta(hours=6)
        limit = float(setup.get("gap_atr", 1.0))
        if direction == "BUY":
            cond &= new_session & (gap <= -limit)
        else:
            cond &= new_session & (gap >= limit)
    elif trigger == "pd_fade":
        day = out["time"].dt.date
        daily_low = out.groupby(day)["low"].min()
        daily_high = out.groupby(day)["high"].max()
        prev_low = day.map(daily_low.shift(1))
        prev_high = day.map(daily_high.shift(1))
        tol = float(setup.get("pd_tol_atr", 0.3)) * out["atr"]
        if direction == "BUY":
            cond &= (
                (out["low"] <= prev_low + tol)
                & (out["close"] > prev_low)
                & (out["close"] > out["open"])
            )
        else:
            cond &= (
                (out["high"] >= prev_high - tol)
                & (out["close"] < prev_high)
                & (out["close"] < out["open"])
            )
    elif trigger == "open_fade":
        open_hour = int(setup.get("open_hour", 9))
        bars_after = int(setup.get("fade_bars", 6))
        day = out["time"].dt.date
        in_day = out["time"].dt.hour >= open_hour
        # номер бара внутри дня после часа открытия
        counter = in_day.groupby(day).cumsum()
        session_open = out.loc[in_day].groupby(day)["open"].first()
        open_price = day.map(session_open)
        move = (out["close"] - open_price) / out["atr"]
        limit = float(setup.get("open_atr", 1.0))
        cond &= (counter == bars_after) & (move <= -limit) & (out["close"] > out["open"])
    elif trigger == "spike_fade":
        prev_range = (out["high"] - out["low"]).shift(1)
        prev_bear = out["close"].shift(1) < out["open"].shift(1)
        mid = (out["high"].shift(1) + out["low"].shift(1)) / 2.0
        mult = float(setup.get("spike_atr", 2.0))
        if direction == "BUY":
            cond &= (
                (prev_range >= mult * out["atr"].shift(1))
                & prev_bear
                & (out["close"] > mid)
                & (out["close"] > out["open"])
            )
        else:
            prev_bull = out["close"].shift(1) > out["open"].shift(1)
            cond &= (
                (prev_range >= mult * out["atr"].shift(1))
                & prev_bull
                & (out["close"] < mid)
                & (out["close"] < out["open"])
            )
    elif trigger == "round_level":
        step = float(setup.get("round_step", 10.0))
        tol = float(setup.get("round_tol_atr", 0.3)) * out["atr"]
        if direction == "BUY":
            level = np.floor(out["close"] / step) * step
            cond &= (
                (out["low"] <= level + tol)
                & (out["close"] > level)
                & (out["close"] > out["open"])
            )
        else:
            level = np.ceil(out["close"] / step) * step
            cond &= (
                (out["high"] >= level - tol)
                & (out["close"] < level)
                & (out["close"] < out["open"])
            )
    elif trigger == "asia_fade":
        day = out["time"].dt.date
        asia = (out["time"].dt.hour >= 1) & (out["time"].dt.hour < 8)
        asia_low = out.loc[asia].groupby(day[asia])["low"].min()
        asia_high = out.loc[asia].groupby(day[asia])["high"].max()
        low_level = day.map(asia_low)
        high_level = day.map(asia_high)
        tol = float(setup.get("asia_tol_atr", 0.2)) * out["atr"]
        if direction == "BUY":
            cond &= (
                (out["low"] < low_level - tol)
                & (out["close"] > low_level)
                & (out["close"] > out["open"])
            )
        else:
            cond &= (
                (out["high"] > high_level + tol)
                & (out["close"] < high_level)
                & (out["close"] < out["open"])
            )
    elif trigger == "clock":
        hh = int(setup.get("enter_hour", 12))
        mm = int(setup.get("enter_minute", 0))
        cond &= (out["time"].dt.hour == hh) & (out["time"].dt.minute == mm)
    else:
        raise ValueError(f"Неизвестный trigger: {trigger}")

    # Необязательные фильтры (действуют на любой сетап, если заданы).
    if "max_adx" in setup:
        cond &= out["adx"] <= float(setup["max_adx"])
    if "min_adx" in setup:
        cond &= out["adx"] >= float(setup["min_adx"])
    if "max_ema_dist_atr" in setup:
        dist = (out["close"] - out["ema200"]).abs() / out["atr"]
        cond &= dist <= float(setup["max_ema_dist_atr"])
    if "max_day_range_atr" in setup:
        day = out["time"].dt.date
        hi = out["high"].groupby(day).cummax()
        lo = out["low"].groupby(day).cummin()
        cond &= ((hi - lo) / out["atr"]) <= float(setup["max_day_range_atr"])
    if "max_rsi" in setup:
        cond &= out["rsi"] <= float(setup["max_rsi"])
    if "min_rsi" in setup:
        cond &= out["rsi"] >= float(setup["min_rsi"])

    # Дневное движение: (close / open дня - 1) в %. Открытие дня известно
    # с первого бара — заглядывания вперёд нет. from_hour ограничивает фильтр
    # второй половиной дня, чтобы не резать утренние входы.
    if any(
        key in setup
        for key in (
            "max_day_move",
            "min_day_move",
            "skip_if_day_move_below",
            "skip_if_day_move_above",
        )
    ):
        day = out["time"].dt.date
        day_open = out["open"].groupby(day).transform("first")
        day_move = (out["close"] / day_open - 1.0) * 100.0
        from_hour = int(setup.get("day_move_from_hour", 0))
        after = out["time"].dt.hour >= from_hour
        if "max_day_move" in setup:
            cond &= (day_move <= float(setup["max_day_move"])) & after
        if "min_day_move" in setup:
            cond &= (day_move >= float(setup["min_day_move"])) & after
        if "skip_if_day_move_below" in setup:
            cond &= ~((day_move <= float(setup["skip_if_day_move_below"])) & after)
        if "skip_if_day_move_above" in setup:
            cond &= ~((day_move >= float(setup["skip_if_day_move_above"])) & after)
    if setup.get("trend_filter") == "ema":
        if direction == "BUY":
            cond &= out["ema50"] > out["ema200"]
        else:
            cond &= out["ema50"] < out["ema200"]

    # Фильтр старшего ТФ: тренд по EMA50/EMA200 на M15 (или другом ТФ).
    mtf = int(setup.get("mtf_trend", 0) or 0)
    if mtf:
        prefix = f"m{mtf}"
        fast = out[f"{prefix}_ema50"]
        slow = out[f"{prefix}_ema200"]
        if direction == "BUY":
            cond &= fast > slow
        else:
            cond &= fast < slow
        if setup.get("mtf_close_filter"):
            if direction == "BUY":
                cond &= out["close"] > out[f"{prefix}_ema50"]
            else:
                cond &= out["close"] < out[f"{prefix}_ema50"]
        if "mtf_min_adx" in setup:
            cond &= out[f"{prefix}_adx"] >= float(setup["mtf_min_adx"])
        if "mtf_max_adx" in setup:
            cond &= out[f"{prefix}_adx"] <= float(setup["mtf_max_adx"])

    # Кросс-рыночное подтверждение (серебро, EURUSD): SELL только когда второй
    # рынок движется в ту же сторону. Если данных нет — фильтр пропускается.
    xm = setup.get("xm")
    if xm:
        key = str(xm.get("series", "xag"))
        mode = str(xm.get("mode", "ret1h"))
        if mode == "trend":
            fast_col, slow_col, close_col = f"{key}_ema50", f"{key}_ema200", f"{key}_close"
            if fast_col in out.columns:
                fast, slow, close = out[fast_col], out[slow_col], out[close_col]
                if direction == "BUY":
                    cond &= (fast > slow) & (close > fast)
                else:
                    cond &= (fast < slow) & (close < fast)
        else:
            col = f"{key}_{mode}"
            if col in out.columns:
                th = float(xm.get("th", 0.0))
                cond &= (out[col] >= th) if direction == "BUY" else (out[col] <= -th)

    return cond


def _add_mtf_columns(out: pd.DataFrame, minutes: int) -> None:
    """Добавляет индикаторы старшего ТФ (только по закрытым барам).

    Колонки: m{minutes}_close, m{minutes}_ema20/50/200, m{minutes}_adx.
    Бар M15 попадает в M5-бар только после своего закрытия — заглядывания вперёд нет.
    """
    prefix = f"m{minutes}"
    frame = out.set_index("time")[["open", "high", "low", "close"]]
    agg = (
        frame.resample(f"{minutes}min", label="right", closed="right")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna()
    )
    if agg.empty:
        return
    agg[f"{prefix}_close"] = agg["close"]
    agg[f"{prefix}_ema20"] = ema(agg["close"], 20)
    agg[f"{prefix}_ema50"] = ema(agg["close"], 50)
    agg[f"{prefix}_ema200"] = ema(agg["close"], 200)
    agg[f"{prefix}_adx"] = adx(agg, 14)

    cols = [
        f"{prefix}_close",
        f"{prefix}_ema20",
        f"{prefix}_ema50",
        f"{prefix}_ema200",
        f"{prefix}_adx",
    ]
    merged = pd.merge_asof(
        out[["time"]].sort_values("time"),
        agg[cols].reset_index().sort_values("time"),
        on="time",
        direction="backward",
    )
    for col in cols:
        out[col] = merged[col].to_numpy()


def _add_xmarket_columns(
    out: pd.DataFrame, series: dict[str, pd.DataFrame], minutes: int
) -> None:
    """Добавляет индикаторы кросс-актива (серебро, EURUSD) со старшего ТФ.

    Колонки: {key}_close, {key}_ema20/50/200, {key}_ret1h/2h/3h.
    Бары старшего ТФ попадают в M5-бар только после своего закрытия —
    заглядывания вперёд нет (merge_asof backward, как в _add_mtf_columns).
    """
    for key, raw in series.items():
        if raw is None or raw.empty or "time" not in raw.columns:
            continue
        frame = (
            raw.set_index("time")[["open", "high", "low", "close"]]
            .sort_index()
            .astype(float)
        )
        agg = (
            frame.resample(f"{minutes}min", label="right", closed="right")
            .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
            .dropna()
        )
        if agg.empty:
            continue
        cols = {
            f"{key}_close": agg["close"],
            f"{key}_ema20": ema(agg["close"], 20),
            f"{key}_ema50": ema(agg["close"], 50),
            f"{key}_ema200": ema(agg["close"], 200),
            f"{key}_ret1h": agg["close"].pct_change(4) * 100.0,
            f"{key}_ret2h": agg["close"].pct_change(8) * 100.0,
            f"{key}_ret3h": agg["close"].pct_change(12) * 100.0,
        }
        merged = pd.merge_asof(
            out[["time"]].sort_values("time"),
            pd.DataFrame(cols).reset_index().sort_values("time"),
            on="time",
            direction="backward",
        )
        for col in cols:
            out[col] = merged[col].to_numpy()


def prepare(df: pd.DataFrame, cfg: dict, xmarket: dict[str, pd.DataFrame] | None = None) -> pd.DataFrame:
    """Считает индикаторы и отмечает сетапы на каждом закрытом баре.

    `xmarket` — готовые бары кросс-активов ({"xag": df_m15, ...}) для live-цикла.
    Если не переданы, данные берутся из CSV, указанных в config → cross_market.
    """
    params = cfg["strategy"]
    out = df.copy()

    out["atr"] = atr(out, int(params.get("atr_period", 14)))
    out["rsi"] = rsi(out["close"], int(params.get("rsi_period", 14)))
    out["rsi2"] = rsi(out["close"], 2)
    out["adx"] = adx(out, int(params.get("adx_period", 14)))
    out["ema20"] = ema(out["close"], 20)
    out["ema200"] = ema(out["close"], 200)
    out["ema50"] = ema(out["close"], 50)

    mtf_periods = sorted(
        {
            int(setup["mtf_trend"])
            for setup in params.get("setups", {}).values()
            if setup.get("enabled", True) and setup.get("mtf_trend")
        }
    )
    for minutes in mtf_periods:
        _add_mtf_columns(out, minutes)

    xm_cfg = cfg.get("cross_market") or {}
    xm_needed = any(
        setup.get("enabled", True) and setup.get("xm")
        for setup in params.get("setups", {}).values()
    )
    if xm_cfg.get("enabled") and xm_needed:
        frames: dict[str, pd.DataFrame] = {}
        for key, spec in (xm_cfg.get("series") or {}).items():
            raw = (xmarket or {}).get(key)
            if raw is None:
                path = spec.get("path")
                if path:
                    candidate = Path(path)
                    if not candidate.is_absolute():
                        candidate = ROOT / path
                    if candidate.exists():
                        raw = pd.read_csv(candidate, parse_dates=["time"])
            if raw is not None and len(raw):
                frames[key] = raw
        if frames:
            _add_xmarket_columns(out, frames, int(xm_cfg.get("minutes", 15)))

    max_spread = float(cfg.get("max_spread_points", 30))
    spread_ok = out["spread"].fillna(0) <= max_spread

    out["setup"] = ""
    for name, setup in params.get("setups", {}).items():
        if not setup.get("enabled", True):
            continue
        cond = spread_ok & _setup_condition(out, setup)
        out.loc[cond & (out["setup"] == ""), "setup"] = name

    directions = {
        name: setup.get("direction", "BUY") for name, setup in params.get("setups", {}).items()
    }
    out["long_setup"] = out["setup"].map(lambda s: bool(s) and directions.get(s) == "BUY")
    out["short_setup"] = out["setup"].map(lambda s: bool(s) and directions.get(s) == "SELL")

    return out


def _reasons(
    df: pd.DataFrame, index: int, row: pd.Series, setup_name: str, setup: dict
) -> list[tuple[str, dict]]:
    """Причины входа как (ключ, параметры) — для перевода в i18n.

    Неизвестные/редкие триггеры отдаются через ключ "raw" с русским текстом,
    чтобы остаться читаемыми в любом языке.
    """
    direction = setup.get("direction", "BUY")
    reasons: list[tuple[str, dict]] = []

    def raw(text: str) -> tuple[str, dict]:
        return ("raw", {"text": text})

    trigger = setup.get("trigger", "rsi")
    if trigger == "drop":
        bars = int(setup["drop_bars"])
        move = (float(row["close"]) - float(df["close"].iloc[index - bars])) / float(row["atr"])
        reasons.append(
            ("drop_buy" if direction == "BUY" else "drop_sell", {"bars": bars, "move": move})
        )
        reasons.append(
            (
                "drop_bounce_buy" if direction == "BUY" else "drop_bounce_sell",
                {},
            )
        )
    else:
        side = "вверх" if direction == "BUY" else "вниз"
        if trigger == "rsi2":
            reasons.append(raw(f"RSI(2) = {float(row['rsi2']):.1f} и развернулся {side}"))
        elif trigger == "bb":
            where = "ниже нижней" if direction == "BUY" else "выше верхней"
            reasons.append(raw(f"Цена закрылась {where} полосы Боллинджера (20, 2)"))
            reasons.append(
                raw(
                    "Бычий бар подтверждает разворот"
                    if direction == "BUY"
                    else "Медвежий бар подтверждает разворот"
                )
            )
        elif trigger == "stretch":
            period = int(setup.get("ema_period", 50))
            ema_value = float(row.get(f"ema{period}", row["ema50"]))
            dist = (float(row["close"]) - ema_value) / float(row["atr"])
            reasons.append(
                (
                    "stretch_below" if direction == "BUY" else "stretch_above",
                    {"period": period, "dist": abs(dist)},
                )
            )
        elif trigger == "streak":
            bars = int(setup.get("streak_bars", 3))
            color = "красных" if direction == "BUY" else "зелёных"
            state = "перепроданность" if direction == "BUY" else "перекупленность"
            reasons.append(raw(f"{bars} {color} баров подряд — локальная {state}"))
        elif trigger == "swing_high":
            look = int(setup.get("sh_lookback", 10))
            atr_v = float(row["atr"]) or 1.0
            wick = (float(row["high"]) - max(float(row["open"]), float(row["close"]))) / atr_v
            reasons.append(("swing_high_zone", {"bars": look}))
            reasons.append(("swing_high_reject", {"wick": wick}))
            if "min_rsi" in setup:
                reasons.append(("swing_high_rsi", {"rsi": float(row["rsi"])}))
        elif trigger == "trend_pullback":
            reasons.append(
                raw(
                    "Тренд вверх: EMA50 выше EMA200"
                    if direction == "BUY"
                    else "Тренд вниз: EMA50 ниже EMA200"
                )
            )
            reasons.append(
                raw(
                    "Откат к EMA20 и бычий бар"
                    if direction == "BUY"
                    else "Отскок к EMA20 и медвежий бар"
                )
            )
        elif trigger == "breakout":
            bars = int(setup.get("breakout_bars", 20))
            reasons.append(
                ("breakout_high" if direction == "BUY" else "breakout_low", {"bars": bars})
            )
        elif trigger == "vwap":
            reasons.append(
                raw(
                    f"Цена ушла от дневного VWAP на "
                    f"{float(setup.get('vwap_atr', 1.5)):.1f} ATR"
                    + (" вниз" if direction == "BUY" else " вверх")
                )
            )
        elif trigger == "pin":
            reasons.append(
                raw(
                    "Свеча-пин с длинной нижней тенью — отказ от падения"
                    if direction == "BUY"
                    else "Свеча-пин с длинной верхней тенью — отказ от роста"
                )
            )
        elif trigger == "fade_break":
            bars = int(setup.get("break_bars", 20))
            what = "минимума" if direction == "BUY" else "максимума"
            reasons.append(
                raw(f"Ложный пробой {what} за {bars} баров — возврат внутрь диапазона")
            )
        elif trigger == "rsi_trend":
            reasons.append(
                raw(f"RSI(14) = {float(row['rsi']):.1f} и развернулся {side} в тренде")
            )
        elif trigger == "keltner":
            reasons.append(
                raw(
                    "Цена ниже нижней границы канала Кельтнера — перепроданность"
                    if direction == "BUY"
                    else "Цена выше верхней границы канала Кельтнера — перекупленность"
                )
            )
        elif trigger == "gap":
            reasons.append(
                raw(
                    "Гэп вниз на открытии сессии — исторически часто закрывается"
                    if direction == "BUY"
                    else "Гэп вверх на открытии сессии — исторически часто закрывается"
                )
            )
        elif trigger == "pd_fade":
            reasons.append(
                raw(
                    "Возврат выше минимума прошлого дня"
                    if direction == "BUY"
                    else "Возврат ниже максимума прошлого дня"
                )
            )
        elif trigger == "open_fade":
            reasons.append(
                raw(
                    f"Ранний импульс от открытия сессии перепродан на "
                    f"{float(setup.get('open_atr', 1.0)):.1f} ATR — фейд движения"
                )
            )
        elif trigger == "spike_fade":
            reasons.append(
                raw(
                    "Кульминационная свеча вниз и возврат цены — разворот"
                    if direction == "BUY"
                    else "Кульминационная свеча вверх и возврат цены — разворот"
                )
            )
        elif trigger == "round_level":
            reasons.append(
                raw(f"Отскок от круглого уровня ${float(setup.get('round_step', 10.0)):.0f}")
            )
        elif trigger == "asia_fade":
            reasons.append(raw("Ложный пробой азиатского диапазона — возврат внутрь"))
        elif trigger == "clock":
            hh = int(setup.get("enter_hour", 12))
            mm = int(setup.get("enter_minute", 0))
            reasons.append(("clock", {"hh": hh, "mm": mm}))
        else:
            reasons.append(
                ("rsi_up" if direction == "BUY" else "rsi_down", {"rsi": float(row["rsi"])})
            )

    reasons.append(("atr", {"atr": float(row["atr"])}))

    ema200 = float(row["ema200"])
    if direction == "BUY":
        reasons.append(
            ("ema200_bull", {}) if row["close"] > ema200 else ("ema200_against_buy", {})
        )
    else:
        reasons.append(
            ("ema200_bear", {}) if row["close"] < ema200 else ("ema200_against_sell", {})
        )

    reasons.append(("adx", {"adx": float(row["adx"])}))
    if setup.get("trend_filter") == "ema":
        reasons.append(
            ("trend_filter_up", {}) if direction == "BUY" else ("trend_filter_down", {})
        )
    mtf = int(setup.get("mtf_trend", 0) or 0)
    if mtf:
        prefix = f"m{mtf}"
        fast = float(row[f"{prefix}_ema50"])
        slow = float(row[f"{prefix}_ema200"])
        reasons.append(
            ("mtf_trend_up" if fast > slow else "mtf_trend_down", {"minutes": mtf})
        )
        if "mtf_min_adx" in setup and float(setup.get("mtf_min_adx") or 0) > 0:
            reasons.append(
                ("mtf_adx", {"minutes": mtf, "adx": float(row[f"{prefix}_adx"])})
            )
    xm = setup.get("xm")
    if xm:
        key = str(xm.get("series", "xag"))
        mode = str(xm.get("mode", "ret1h"))
        suffix = "up" if direction == "BUY" else "down"
        if mode == "trend":
            reasons.append((f"xm_{key}_trend_{suffix}", {}))
        else:
            col = f"{key}_{mode}"
            value = abs(float(row[col])) if col in row.index and pd.notna(row.get(col)) else 0.0
            hours = {"ret1h": 1, "ret2h": 2, "ret3h": 3}.get(mode, 1)
            reasons.append(
                (f"xm_{key}_confirm_{suffix}", {"value": value, "hours": hours})
            )
    reasons.append(("session", {"label": setup.get("session_label", "торговая")}))
    return reasons


def _confidence(row: pd.Series, setup: dict) -> int:
    score = 55
    direction = setup.get("direction", "BUY")
    rsi_v = float(row["rsi"])

    if setup.get("trigger") == "drop":
        if direction == "BUY" and rsi_v < 25:
            score += 10
        if direction == "SELL" and rsi_v > 75:
            score += 10
    else:
        if direction == "BUY" and rsi_v < 25:
            score += 10
        if direction == "SELL" and rsi_v > 75:
            score += 10
        turn = abs(float(row["rsi"] - row["rsi_prev"]))
        if turn >= 3:
            score += 10

    bullish = float(row["close"]) > float(row["open"])
    if (direction == "BUY" and bullish) or (direction == "SELL" and not bullish):
        score += 5

    if float(row["adx"]) > 20:
        score += 5

    return int(max(0, min(95, score)))


def signal_at(
    df: pd.DataFrame,
    index: int,
    cfg: dict,
    entry_price: float | None = None,
) -> Signal | None:
    """Проверяет закрытый бар `index` и возвращает сигнал или None."""
    if index < warmup_bars(cfg):
        return None

    row = df.iloc[index].copy()
    setup_name = str(row["setup"])
    if not setup_name:
        return None

    setup = cfg["strategy"]["setups"][setup_name]
    direction = setup.get("direction", "BUY")
    entry = float(entry_price if entry_price is not None else row["close"])
    atr_v = float(row["atr"])
    if atr_v <= 0:
        return None

    lookback = int(setup.get("sl_lookback", 3))
    lo = max(0, index - lookback + 1)
    swing_low = float(df["low"].iloc[lo : index + 1].min())
    swing_high = float(df["high"].iloc[lo : index + 1].max())
    buffer = float(setup.get("sl_buffer_atr", 0.1)) * atr_v
    sl_dist = float(setup["sl_atr"]) * atr_v
    max_sl = float(setup.get("max_sl_atr", setup["sl_atr"] + 0.5)) * atr_v
    rr = float(setup["rr"])

    use_swing = setup.get("sl_use_swing", True)
    sl_cap = bool(setup.get("sl_cap", False))

    if direction == "BUY":
        sl = min(swing_low - buffer, entry - sl_dist) if use_swing else entry - sl_dist
        risk = entry - sl
        if risk <= 0:
            return None
        if risk > max_sl:
            if not sl_cap:
                return None
            risk = max_sl
            sl = entry - risk
        tp = entry + rr * risk
    else:
        sl = max(swing_high + buffer, entry + sl_dist) if use_swing else entry + sl_dist
        risk = sl - entry
        if risk <= 0:
            return None
        if risk > max_sl:
            if not sl_cap:
                return None
            risk = max_sl
            sl = entry + risk
        tp = entry - rr * risk

    row["rsi_prev"] = float(df["rsi"].iloc[index - 1])
    reason_items = _reasons(df, index, row, setup_name, setup)
    return Signal(
        time=row["time"],
        setup=setup_name,
        setup_label=SETUP_LABELS.get(setup_name, setup_name),
        direction=direction,
        entry=round(entry, 2),
        sl=round(sl, 2),
        tp=round(tp, 2),
        atr=atr_v,
        risk_price=risk,
        rr=rr,
        time_exit_bars=int(setup.get("time_exit_bars", 24)),
        be_at_r=float(setup.get("breakeven_at_r", 0.0)),
        confidence=_confidence(row, setup),
        reasons=[i18n.render_reason(item, "ru") for item in reason_items],
        reason_items=reason_items,
        spread_points=float(row.get("spread", 0) or 0),
        bar_index=index,
    )
