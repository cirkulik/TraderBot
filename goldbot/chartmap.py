"""Карта уровней для графика MT5: зоны и сильные точки из отчётов 30/60.

Python считает уровни и пишет простой текстовый файл в общую папку терминала
(`Common\\Files\\GoldBot\\levels.txt`). Индикатор `GoldBotLevels.mq5` читает
этот файл и рисует на графике полосы зон и пунктирные линии точек.

Торговли нет: скрипт только читает котировки и пишет файл. Скальпер и
исполнитель сигналов не затрагиваются.
"""
from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from .config import ROOT
from .data import ensure_symbol, get_account_info, get_closed_rates, get_tick
from .hourly import strong_points
from .indicators import atr
from .zones import find_zones

STRENGTH_RANK = {"weak": 0, "medium": 1, "strong": 2, "strongest": 3}


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def strength_from_touches(touches: int) -> str:
    """Та же градация, что у скальпера: 2 — слабая, 3–4 — средняя, 5+ — сильная."""
    if touches >= 8:
        return "strongest"
    if touches >= 5:
        return "strong"
    if touches >= 3:
        return "medium"
    return "weak"


@dataclass
class MapLevel:
    kind: str  # zone / point
    side: str  # buy / sell
    price: float
    strength: str
    touches: int
    half_width: float = 0.0


class ChartMap:
    def __init__(self, cfg: dict, cfg_path: str | Path | None = None) -> None:
        self.cfg = cfg
        m = cfg.get("map") or {}

        self.enabled = bool(m.get("enabled", True))
        self.symbol = str(m.get("symbol") or cfg.get("symbol") or "XAUUSD")
        self.interval_seconds = max(10, int(m.get("interval_seconds", 60)))
        self.history_bars = max(500, int(m.get("history_bars", 3000)))
        self.hours = max(1, int(m.get("hours", 24)))
        self.per_side = max(1, int(m.get("per_side", 4)))
        self.max_zones_per_side = max(1, int(m.get("max_zones_per_side", 4)))
        self.max_points_per_side = max(1, int(m.get("max_points_per_side", 4)))
        self.max_distance = max(0.5, float(m.get("max_distance", 20.0)))
        self.zone_half_width = max(0.01, float(m.get("zone_half_width", 0.15)))
        self.min_touches_zones = max(1, int(m.get("min_touches_zones", 2)))
        self.min_touches_points = max(1, int(m.get("min_touches_points", 2)))
        self.point_zone_gap = max(0.0, float(m.get("point_zone_gap", 0.50)))
        self.merge_points = float(m.get("merge_points", 100))
        self.touch_tol_points = float(m.get("touch_tol_points", 15))
        self.touch_gap_points = float(m.get("touch_gap_points", 50))
        self.intervals = [int(value) for value in (m.get("intervals") or [30, 60])]

        self.log_path = ROOT / str(m.get("log_path", "data/map.log"))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path = self._resolve_output(m.get("output_path"))

        self.info = ensure_symbol(self.symbol)
        self.point = float(self.info.point) or 0.01
        self.digits = int(self.info.digits or 2)
        self.merge = self.merge_points * self.point
        self.account = get_account_info()
        self._last_signature: tuple | None = None

    # --- служебное ---------------------------------------------------------
    def _resolve_output(self, value) -> Path:
        if value:
            path = Path(str(value))
            return path if path.is_absolute() else ROOT / path
        info = mt5.terminal_info()
        if info is not None and getattr(info, "data_path", ""):
            common = Path(info.data_path).parent / "Common" / "Files"
        else:
            appdata = os.environ.get("APPDATA")
            common = (
                Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"
                if appdata
                else ROOT / "data"
            )
        return common / "GoldBot" / "levels.txt"

    def log(self, message: str) -> None:
        line = f"[{_now_utc()}] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def startup(self) -> None:
        account = self.account
        self.log("=" * 62)
        self.log("  GOLDBOT MAP — зоны и сильные точки на графике MT5")
        self.log("=" * 62)
        if account is not None:
            self.log(
                f"  счёт {account.login} ({account.server}), баланс "
                f"{account.balance:.2f} {account.currency}"
            )
        self.log(f"  обновление каждые {self.interval_seconds} с")
        self.log(f"  уровни: зоны {self.hours} ч + точки M30/H1")
        self.log(f"  файл для индикатора: {self.output_path}")
        self.log("-" * 62)

    # --- уровни ------------------------------------------------------------
    def _pick_zones(
        self, items: list[MapLevel], price: float
    ) -> list[MapLevel]:
        picked: list[MapLevel] = []
        for side in ("sell", "buy"):
            side_items = sorted(
                [item for item in items if item.side == side],
                key=lambda item: abs(item.price - price),
            )
            kept: list[MapLevel] = []
            for item in side_items:
                if any(abs(item.price - other.price) < self.merge for other in kept):
                    continue
                kept.append(item)
                if len(kept) >= self.max_zones_per_side:
                    break
            picked.extend(kept)
        return picked

    def _pick_points(
        self, items: list[MapLevel], zones: list[MapLevel], price: float
    ) -> list[MapLevel]:
        picked: list[MapLevel] = []
        for side in ("sell", "buy"):
            side_items = sorted(
                [item for item in items if item.side == side],
                key=lambda item: (-item.touches, abs(item.price - price)),
            )
            kept: list[MapLevel] = []
            for item in side_items:
                if abs(item.price - price) > self.max_distance:
                    continue
                if any(abs(item.price - other.price) < self.merge for other in kept):
                    continue
                if any(
                    abs(item.price - zone.price)
                    <= zone.half_width + self.point_zone_gap
                    for zone in zones
                ):
                    continue
                kept.append(item)
                if len(kept) >= self.max_points_per_side:
                    break
            picked.extend(kept)
        return picked

    def build(self) -> tuple[list[MapLevel], float, float]:
        df = get_closed_rates(self.symbol, "M5", self.history_bars)
        if df.empty or len(df) < 60:
            raise RuntimeError("мало данных M5")
        tick = get_tick(self.symbol)
        price = float(tick.bid if tick is not None and tick.bid > 0 else df["close"].iloc[-1])
        atr_value = float(atr(df, 14).iloc[-1])
        if not math.isfinite(atr_value) or atr_value <= 0:
            raise RuntimeError("ATR не посчитан")

        last = df["time"].iloc[-1]
        window = df[
            df["time"] >= last - pd.Timedelta(hours=self.hours)
        ].reset_index(drop=True)
        zones = find_zones(window, atr_value, price, per_side=self.per_side)

        zone_items = [
            MapLevel(
                kind="zone",
                side=zone.side,
                price=round(float(zone.price), self.digits),
                strength=str(zone.strength),
                touches=int(zone.touches),
                half_width=self.zone_half_width,
            )
            for zone in zones
            if abs(float(zone.price) - price) <= self.max_distance
            and int(zone.touches) >= self.min_touches_zones
        ]
        zones = self._pick_zones(zone_items, price)

        tol = self.touch_tol_points * self.point
        gap = self.touch_gap_points * self.point
        raw_points: list[MapLevel] = []
        for interval in self.intervals:
            for item in strong_points(
                df,
                interval,
                tol,
                gap,
                merge=self.merge,
                min_touches=self.min_touches_points,
                limit=self.max_points_per_side * 2,
            ):
                level_price = float(item["price"])
                if abs(level_price - price) > self.max_distance:
                    continue
                raw_points.append(
                    MapLevel(
                        kind="point",
                        side="sell" if level_price > price else "buy",
                        price=round(level_price, self.digits),
                        strength=strength_from_touches(int(item["touches"])),
                        touches=int(item["touches"]),
                    )
                )
        points = self._pick_points(raw_points, zones, price)
        return zones + points, price, atr_value

    def write(self, levels: list[MapLevel], price: float) -> None:
        path = self.output_path
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# goldbot map v1",
            f"S|{self.symbol}|{price:.{self.digits}f}|{int(time.time())}",
        ]
        for item in sorted(levels, key=lambda level: level.price, reverse=True):
            kind = "Z" if item.kind == "zone" else "P"
            lines.append(
                f"{kind}|{item.side}|{item.price:.{self.digits}f}|"
                f"{item.half_width:.2f}|{item.strength}|{item.touches}"
            )
        text = "\n".join(lines) + "\n"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(text.encode("ascii"))
        os.replace(tmp, path)

    def run_once(self, print_levels: bool = False) -> None:
        levels, price, atr_value = self.build()
        self.write(levels, price)
        signature = tuple(
            (item.kind, item.side, item.price, item.strength, item.touches)
            for item in sorted(levels, key=lambda level: level.price)
        )
        if signature != self._last_signature:
            self._last_signature = signature
            zones = sum(1 for item in levels if item.kind == "zone")
            points = len(levels) - zones
            self.log(
                f"карта: {zones} зон, {points} точек, цена {price:.2f}, "
                f"ATR {atr_value:.2f} -> {self.output_path}"
            )
        if print_levels:
            for item in sorted(levels, key=lambda level: level.price, reverse=True):
                print(
                    f"  {item.kind:5} {item.side:4} {item.price:8.2f} "
                    f"{item.strength:9} {item.touches:2} кас."
                )

    def run(self) -> None:
        self.startup()
        self.log("цикл запущен")
        try:
            while True:
                started = time.time()
                try:
                    self.run_once()
                except Exception as exc:  # noqa: BLE001 - процесс не должен падать
                    self.log(f"ОШИБКА: {type(exc).__name__}: {exc}")
                elapsed = time.time() - started
                time.sleep(max(1.0, self.interval_seconds - elapsed))
        except KeyboardInterrupt:
            self.log("остановлен пользователем")
