"""Скальпер: лимитные ордера на зонах и сильных точках, быстрый цикл.

Отдельный процесс рядом с анализатором и исполнителем сигналов. Сам считает
уровни (зоны M5 + сильные точки M30/H1, включая формирующуюся свечу), ставит
Buy Limit / Sell Limit, ведёт позиции (безубыток, трейлинг, перенос TP, выход
по времени) и пишет всё в свой журнал. Чужие сделки не трогает: у процесса
свой magic.

По умолчанию работает только на демо-счёте: для реального счёта нужно явно
разрешить `scalper.allow_live = true`. Включается отдельно
(`scalper.enabled`), по умолчанию — dry-run.
"""
from __future__ import annotations

import csv
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from . import candles as candles_mod
from .config import DEFAULT_CONFIG_PATH, ROOT
from .data import (
    ensure_symbol,
    get_account_info,
    get_closed_rates,
    get_rates,
    get_tick,
    server_utc_offset_hours,
)
from .indicators import atr
from .zones import find_zones, market_bias

JOURNAL_COLUMNS = [
    "ts_utc",
    "event",
    "side",
    "source",
    "level",
    "price",
    "sl",
    "tp",
    "ticket",
    "lot",
    "reason",
    "score",
    "note",
]

STRENGTH_RANK = {"weak": 0, "medium": 1, "strong": 2, "strongest": 3}
STRENGTH_SCORE = {"weak": 0.0, "medium": 4.0, "strong": 8.0, "strongest": 12.0}

# Коды, при которых имеет смысл повторить отправку (цена/связь/заполнение).
RETRY_CODES = {
    10004,  # requote
    10006,  # rejected
    10007,  # canceled
    10020,  # price changed
    10021,  # price off
    10024,  # too many requests
    10026,  # server disables at
    10027,  # client disables at
}

MARKET_CLOSED_CODES = {10017, 10018, 10019}


@dataclass
class Level:
    """Уровень, от которого скальпер готов поставить лимитку."""

    price: float
    side: str  # buy — поддержка снизу, sell — сопротивление сверху
    source: str  # zone / point / both
    touches: int
    strength: str
    live: bool = False
    score: float = 0.0


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _strength_from_touches(touches: int) -> str:
    if touches >= 8:
        return "strongest"
    if touches >= 5:
        return "strong"
    if touches >= 3:
        return "medium"
    return "weak"


def _touches(bars: pd.DataFrame, level: float, tol: float, gap: float) -> int:
    """Сколько раз цена подходила к уровню и уходила от него."""
    if bars.empty:
        return 0
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    count = 0
    ready = True
    for index in range(len(high)):
        touched = low[index] <= level + tol and high[index] >= level - tol
        if touched and ready:
            count += 1
            ready = False
        elif close[index] > level + gap or close[index] < level - gap:
            ready = True
    return count


def _aggregate_live(df: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Свечи ТФ, включая текущую формирующуюся (в отличие от candles.aggregate)."""
    if df.empty:
        return df
    return (
        df.set_index("time")
        .resample(f"{minutes}min", label="left", closed="left")
        .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
        .dropna(subset=["open"])
        .reset_index()
    )


def _rows_for_candles(
    df: pd.DataFrame, candles: pd.DataFrame, tol: float, gap: float
) -> list[dict]:
    """Строки «хай/лоу свечи + число касаний» для candles.levels."""
    rows: list[dict] = []
    for _, candle in candles.iterrows():
        after = df[df["time"] >= candle["time"]]
        high = float(candle["high"])
        low = float(candle["low"])
        rows.append(
            {
                "time": candle["time"],
                "high": high,
                "low": low,
                "hi": _touches(after, high, tol, gap),
                "lo": _touches(after, low, tol, gap),
            }
        )
    return rows


def _merge_levels(levels: list[Level], merge: float, price: float) -> list[Level]:
    """Сливает близкие уровни (зона + точка на одной цене — один уровень)."""
    if not levels:
        return []
    ordered = sorted(levels, key=lambda item: item.price)
    groups: list[list[Level]] = []
    for level in ordered:
        if groups and level.price - groups[-1][-1].price <= merge:
            groups[-1].append(level)
        else:
            groups.append([level])

    merged: list[Level] = []
    for group in groups:
        weights = [max(1, item.touches) for item in group]
        total = sum(weights)
        level_price = sum(item.price * weight for item, weight in zip(group, weights)) / total
        touches = max(item.touches for item in group)
        best = max(group, key=lambda item: STRENGTH_RANK.get(item.strength, 0))
        sources = {item.source for item in group}
        merged.append(
            Level(
                price=round(level_price, 2),
                side="sell" if level_price > price else "buy",
                source="both" if len(sources) > 1 else group[0].source,
                touches=touches,
                strength=best.strength,
                live=all(item.live for item in group),
            )
        )
    return merged


class Scalper:
    def __init__(
        self,
        cfg: dict,
        dry_run: bool = False,
        no_telegram: bool = False,
        cfg_path: str | Path | None = None,
    ) -> None:
        self.cfg = cfg
        sc = cfg.get("scalper") or {}
        self.cfg_path = Path(cfg_path) if cfg_path else DEFAULT_CONFIG_PATH

        self.dry_run = bool(dry_run or sc.get("dry_run", True))
        self.no_telegram = no_telegram
        self.symbol = str(sc.get("symbol") or cfg.get("symbol") or "XAUUSD")
        self.magic = int(sc.get("magic", 770002))
        self.lot = float(sc.get("lot", 0.01))

        self.refresh_seconds = max(10, int(sc.get("refresh_seconds", 15)))
        self.poll_seconds = max(0.2, float(sc.get("poll_seconds", 0.25)))
        self.history_bars = max(500, int(sc.get("history_bars", 3000)))
        self.order_ttl = max(1, int(sc.get("order_ttl_minutes", 60)))
        self.max_positions = max(1, int(sc.get("max_positions", 2)))
        self.max_pending_per_side = max(1, int(sc.get("max_pending_per_side", 3)))
        self.max_pending_total = max(1, int(sc.get("max_pending_total", 6)))
        self.pending_min_gap = float(sc.get("pending_min_gap", 1.0))
        self.cancel_pending_on_fill = str(
            sc.get("cancel_pending_on_fill", "all")
        ).lower()
        if self.cancel_pending_on_fill not in ("all", "side", "none"):
            self.cancel_pending_on_fill = "all"
        self.max_levels_per_side = max(1, int(sc.get("max_levels_per_side", 2)))

        self.level_min_distance = float(sc.get("level_min_distance", 0.10))
        self.level_max_distance = float(sc.get("level_max_distance", 30.0))
        self.level_move_tolerance = float(sc.get("level_move_tolerance", 0.50))
        self.pending_cancel_guard = float(sc.get("pending_cancel_guard", 0.50))
        self.sl_buffer = float(sc.get("sl_buffer", 0.30))
        self.sl_atr_buffer = float(sc.get("sl_atr_buffer", 0.15))
        self.min_risk = float(sc.get("min_risk", 0.50))
        self.max_risk = float(sc.get("max_risk", 5.0))
        self.tp_r = float(sc.get("tp_r", 1.5))
        self.tp_min_r = float(sc.get("tp_min_r", 1.0))
        self.tp_max_r = float(sc.get("tp_max_r", 3.0))
        self.tp_extend_at_r = float(sc.get("tp_extend_at_r", 0.5))
        self.be_r = float(sc.get("be_r", 0.7))
        self.be_buffer = float(sc.get("be_buffer", 0.05))
        self.trail_at_r = float(sc.get("trail_at_r", 1.0))
        self.trail_atr = float(sc.get("trail_atr", 0.8))
        self.max_trade_minutes = float(sc.get("max_trade_minutes", 20))
        self.max_spread_points = float(sc.get("max_spread_points", 35))

        self.min_score = float(sc.get("min_score", 35))
        self.min_touches = int(sc.get("min_touches", 3))
        self.live_min_touches = int(sc.get("live_min_touches", 2))
        self.point_candles = int(sc.get("point_candles", 6))
        self.merge_points = float(sc.get("merge_points", 100))
        self.touch_tol_points = float(sc.get("touch_tol_points", 15))
        self.touch_gap_points = float(sc.get("touch_gap_points", 50))

        self.zone_hours = int(sc.get("zone_hours", 24))
        self.zone_per_side = int(sc.get("zone_per_side", 4))
        self.zone_cluster_atr = float(sc.get("zone_cluster_atr", 0.15))
        self.zone_min_dist_atr = float(sc.get("zone_min_dist_atr", 0.05))
        self.zone_min_touches = int(sc.get("zone_min_touches", 2))
        self.zone_min_strength = str(sc.get("zone_min_strength", "strong"))

        self.bias_filter = bool(sc.get("bias_filter", True))
        self.skip_hours = {int(hour) for hour in (sc.get("skip_hours") or [])}
        self.skip_weekend = bool(sc.get("skip_weekend", True))
        self.max_margin_use = float(sc.get("max_margin_use", 0.80))
        self.deviation = int(sc.get("deviation_points", 20))
        self.allow_live = bool(sc.get("allow_live", False))
        self.notify_trades = bool(sc.get("notify_trades", True))

        self.state_path = ROOT / str(sc.get("state_path", "data/scalper_state.json"))
        self.journal_path = ROOT / str(sc.get("journal_path", "data/scalper.csv"))
        self.log_path = ROOT / str(sc.get("log_path", "data/scalper.log"))
        for path in (self.state_path, self.journal_path, self.log_path):
            path.parent.mkdir(parents=True, exist_ok=True)

        self.info = ensure_symbol(self.symbol)
        self.point = float(self.info.point) or 0.01
        self.digits = int(self.info.digits or 2)
        self.stops_level = int(getattr(self.info, "trade_stops_level", 0) or 0)
        self.contract = float(getattr(self.info, "trade_contract_size", 100.0) or 100.0)
        self.server_offset = 2
        try:
            offset = server_utc_offset_hours(self.symbol)
            if -12 <= offset <= 14:
                self.server_offset = offset
        except Exception:  # noqa: BLE001
            pass

        self.account = get_account_info()
        self._check_account()

        self.state = self._load_state()
        self.levels: list[Level] = []
        self.atr = 0.0
        self.price = 0.0
        self.bias = "FLAT"
        self.adx = 0.0
        self.last_refresh = 0.0
        self._known_positions: set[int] = set()
        self._last_place_log = 0.0
        self._last_skip_log = 0.0
        self._dry_orders: dict[str, list[tuple[float, float]]] = {}
        self._bot = self._make_bot()

    # --- служебное ---------------------------------------------------------
    def log(self, message: str) -> None:
        line = f"[{_now_utc()}] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def journal(self, event: str, **fields) -> None:
        row = {column: "" for column in JOURNAL_COLUMNS}
        row["ts_utc"] = _now_utc()
        row["event"] = event
        extra: list[str] = []
        for key, value in fields.items():
            if key in row:
                row[key] = value
            else:
                extra.append(f"{key}={value}")
        if extra:
            row["note"] = " ".join(part for part in (str(row["note"]), *extra) if part)
        new_file = not self.journal_path.exists() or self.journal_path.stat().st_size == 0
        try:
            with self.journal_path.open("a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=JOURNAL_COLUMNS)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
        except OSError as exc:
            self.log(f"журнал не записан: {exc}")

    def _make_bot(self):
        if self.no_telegram or not self.notify_trades:
            return None
        try:
            from .telegram import TelegramBot

            bot = TelegramBot.from_config(self.cfg)
            if bot.enabled and bot.admin_chat_id:
                return bot
        except Exception as exc:  # noqa: BLE001 - уведомления не должны ронять торговлю
            self.log(f"Telegram: {exc}")
        return None

    def notify(self, text: str) -> None:
        if self._bot is None:
            return
        try:
            self._bot.send(self._bot.admin_chat_id, text)
        except Exception as exc:  # noqa: BLE001
            self.log(f"Telegram: {exc}")

    def _check_account(self) -> None:
        account = self.account
        if account is None:
            raise RuntimeError("Нет данных счёта MT5 — проверьте подключение терминала")
        mode = int(getattr(account, "trade_mode", 2))
        real = int(getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2))
        if mode == real and not self.allow_live:
            raise RuntimeError(
                "Счёт реальный, а scalper.allow_live = false. "
                "Скальпер работает только на демо. Если это осознанное решение — "
                "поставьте allow_live: true в config.json."
            )
        if not bool(getattr(account, "trade_allowed", True)):
            raise RuntimeError("Торговля на счёте запрещена (trade_allowed = false)")
        if not bool(getattr(account, "trade_expert", True)):
            self.log("ВНИМАНИЕ: в терминале выключена алгоритмическая торговля")

    # --- состояние ---------------------------------------------------------
    def _load_state(self) -> dict:
        default = {
            "attempts": {"buy": 0, "sell": 0},
            "used": {"buy": [], "sell": []},
            "positions": {},
        }
        if not self.state_path.exists():
            return default
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default
        data.pop("cooldown_until", None)
        for key, value in default.items():
            data.setdefault(key, value)
        for side in ("buy", "sell"):
            data["attempts"].setdefault(side, 0)
            data["used"].setdefault(side, [])
        data.setdefault("positions", {})
        return data

    def _save_state(self) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.state_path)
        except OSError as exc:
            self.log(f"состояние не сохранено: {exc}")

    # --- брокер ------------------------------------------------------------
    def positions(self) -> dict[int, object]:
        result = {}
        for pos in mt5.positions_get(symbol=self.symbol) or ():
            if int(getattr(pos, "magic", 0)) == self.magic:
                result[int(pos.ticket)] = pos
        return result

    def pending_orders(self) -> dict[int, object]:
        result = {}
        for order in mt5.orders_get(symbol=self.symbol) or ():
            if int(getattr(order, "magic", 0)) == self.magic:
                result[int(order.ticket)] = order
        return result

    @staticmethod
    def _pos_side(pos) -> str:
        return "buy" if int(pos.type) == mt5.POSITION_TYPE_BUY else "sell"

    @staticmethod
    def _order_side(order) -> str:
        if int(order.type) in (mt5.ORDER_TYPE_BUY_LIMIT, mt5.ORDER_TYPE_BUY_STOP):
            return "buy"
        if int(order.type) in (mt5.ORDER_TYPE_SELL_LIMIT, mt5.ORDER_TYPE_SELL_STOP):
            return "sell"
        return ""

    def _filling_mode(self) -> int:
        mask = int(getattr(self.info, "filling_mode", 0))
        if mask & 2:
            return mt5.ORDER_FILLING_IOC
        if mask & 1:
            return mt5.ORDER_FILLING_FOK
        return mt5.ORDER_FILLING_RETURN

    def _round_lot(self, lot: float) -> float:
        step = float(self.info.volume_step or 0.01)
        min_lot = float(self.info.volume_min or step)
        max_lot = float(self.info.volume_max or lot)
        value = round(round(lot / step) * step, 8)
        return max(min_lot, min(value, max_lot))

    def _spread_points(self, tick) -> float:
        if tick is None or self.point <= 0:
            return 0.0
        return max(0.0, (float(tick.ask) - float(tick.bid)) / self.point)

    def _market_ok(self, tick) -> bool:
        if tick is None or tick.time <= 0 or tick.bid <= 0 or tick.ask <= 0:
            return False
        now_server = time.time() + self.server_offset * 3600
        if now_server - float(tick.time) > 300:
            return False
        moment = datetime.fromtimestamp(int(tick.time), tz=timezone.utc)
        if self.skip_weekend and moment.weekday() >= 5:
            return False
        if moment.hour in self.skip_hours:
            return False
        return True

    def _send(self, request: dict, pending: bool = False):
        """Отправляет заявку с повторами. Возвращает последний ответ MT5."""
        last = None
        for attempt in range(3):
            result = mt5.order_send(request)
            last = result
            if result is None:
                self.log(f"order_send вернул None: {mt5.last_error()}")
                time.sleep(0.3)
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                return result
            if pending and result.retcode == getattr(
                mt5, "TRADE_RETCODE_INVALID_EXPIRATION", 10022
            ):
                request = dict(request)
                request["type_time"] = mt5.ORDER_TIME_GTC
                request.pop("expiration", None)
                self.log("expiration не принята — ставлю GTC")
                continue
            if result.retcode == getattr(mt5, "TRADE_RETCODE_INVALID_FILL", 10030):
                request = dict(request)
                request["type_filling"] = mt5.ORDER_FILLING_RETURN
                self.log("filling не принят — пробую RETURN")
                continue
            if result.retcode in RETRY_CODES and attempt < 2:
                time.sleep(0.3)
                continue
            break
        return last

    # --- уровни ------------------------------------------------------------
    def _score_level(
        self,
        level: Level,
        price: float,
        bias: str,
        atr_value: float,
        spread_points: float,
        adx_value: float,
    ) -> float:
        side = "sell" if level.price > price else "buy"
        score = min(level.touches, 8) * 6.0
        score += STRENGTH_SCORE.get(level.strength, 0.0)
        if level.source == "both":
            score += 8.0
        elif level.live:
            score -= 4.0

        if self.bias_filter:
            if bias == "BUY":
                score += 10.0 if side == "buy" else -10.0
            elif bias == "SELL":
                score += 10.0 if side == "sell" else -10.0
            if (
                math.isfinite(adx_value)
                and adx_value >= 25
                and ((bias == "BUY" and side == "buy") or (bias == "SELL" and side == "sell"))
            ):
                score += 4.0

        distance = abs(level.price - price)
        if distance < max(self.level_min_distance, 0.05 * atr_value):
            score -= 15.0
        elif distance <= 2.0:
            score += 8.0
        elif distance <= 4.0:
            score += 4.0
        elif distance > 12.0:
            score -= min(20.0, (distance - 12.0) * 2.0)

        if atr_value > 0:
            ratio = distance / atr_value
            if 0.05 <= ratio <= 0.6:
                score += 6.0
            elif ratio > 2.0:
                score -= 6.0

        if spread_points > 25:
            score -= min(15.0, (spread_points - 25) * 0.5)
        return score

    def refresh_levels(self) -> None:
        try:
            live = get_rates(self.symbol, "M5", self.history_bars)
            closed = get_closed_rates(self.symbol, "M5", self.history_bars)
        except Exception as exc:  # noqa: BLE001 - сеть/терминал могут моргнуть
            self.log(f"уровни: нет данных MT5 ({exc})")
            return
        if live.empty or closed.empty or len(closed) < 50:
            self.log("уровни: мало данных MT5 — пропуск")
            return

        tick = get_tick(self.symbol)
        price = float(tick.bid if tick and tick.bid > 0 else live["close"].iloc[-1])
        try:
            atr_value = float(atr(closed, 14).iloc[-1])
        except Exception as exc:  # noqa: BLE001
            self.log(f"ATR: {exc}")
            atr_value = 0.0
        if not math.isfinite(atr_value) or atr_value < 0:
            atr_value = 0.0
        try:
            bias_info = market_bias(closed)
            bias = str(bias_info.get("bias", "FLAT"))
            adx_value = float(bias_info.get("adx_m15") or 0.0)
        except Exception as exc:  # noqa: BLE001
            self.log(f"bias: {exc}")
            bias = "FLAT"
            adx_value = 0.0
        if not math.isfinite(adx_value):
            adx_value = 0.0

        tol = self.touch_tol_points * self.point
        gap = self.touch_gap_points * self.point
        merge = self.merge_points * self.point
        levels: list[Level] = []

        for interval in (30, 60):
            stable_candles = candles_mod.aggregate(closed, interval).tail(self.point_candles)
            if not stable_candles.empty:
                rows = _rows_for_candles(closed, stable_candles, tol, gap)
                for item in candles_mod.levels(rows, price, cluster=merge):
                    if item["touches"] < self.min_touches:
                        continue
                    levels.append(
                        Level(
                            price=float(item["price"]),
                            side=str(item["side"]),
                            source="point",
                            touches=int(item["touches"]),
                            strength=_strength_from_touches(int(item["touches"])),
                            live=False,
                        )
                    )

            live_candles = _aggregate_live(live, interval).tail(1)
            if not live_candles.empty:
                rows = _rows_for_candles(live, live_candles, tol, gap)
                for item in candles_mod.levels(rows, price, cluster=merge):
                    if item["touches"] < self.live_min_touches:
                        continue
                    levels.append(
                        Level(
                            price=float(item["price"]),
                            side=str(item["side"]),
                            source="point",
                            touches=int(item["touches"]),
                            strength=_strength_from_touches(int(item["touches"])),
                            live=True,
                        )
                    )

        if atr_value > 0 and len(live) >= 30:
            window = live[
                live["time"] >= live["time"].iloc[-1] - pd.Timedelta(hours=self.zone_hours)
            ].reset_index(drop=True)
            try:
                zones = find_zones(
                    window,
                    atr_value,
                    price,
                    per_side=self.zone_per_side,
                    cluster_atr=self.zone_cluster_atr,
                    min_dist_atr=self.zone_min_dist_atr,
                )
            except Exception as exc:  # noqa: BLE001
                zones = []
                self.log(f"зоны: {exc}")
            min_rank = STRENGTH_RANK.get(self.zone_min_strength, 2)
            for zone in zones:
                if zone.touches < self.zone_min_touches:
                    continue
                if STRENGTH_RANK.get(zone.strength, 0) < min_rank:
                    continue
                levels.append(
                    Level(
                        price=float(zone.price),
                        side=str(zone.side),
                        source="zone",
                        touches=int(zone.touches),
                        strength=str(zone.strength),
                        live=False,
                    )
                )

        merged = _merge_levels(levels, merge, price)
        spread = self._spread_points(tick)
        for level in merged:
            level.score = self._score_level(
                level, price, bias, atr_value, spread, adx_value
            )

        self.levels = merged
        self.atr = atr_value
        self.price = price
        self.bias = bias
        self.adx = adx_value
        self.last_refresh = time.time()

        top = sorted(merged, key=lambda item: item.score, reverse=True)[:6]
        parts = [
            f"{item.side.upper()} {item.price:.2f} ({item.touches} кас., {item.source}, "
            f"{item.score:.0f})"
            for item in top
        ]
        self.log(
            f"уровни {price:.2f}: "
            + ("; ".join(parts) if parts else "нет")
            + f" | ATR {atr_value:.2f} | bias {bias} | spread {spread:.0f} п."
        )

    def _is_used(self, side: str, price: float) -> bool:
        for used in self.state["used"].get(side, []):
            if abs(float(used) - price) <= self.level_move_tolerance:
                return True
        return False

    # --- параметры ордера --------------------------------------------------
    def _target_tp(self, side: str, entry: float, sl: float, levels: list[Level]) -> float:
        risk = abs(entry - sl)
        min_stop = max(self.stops_level * self.point, self.point * 5)
        min_tp = max(self.tp_min_r * risk, min_stop)
        max_tp = max(min_tp, self.tp_max_r * risk)
        if side == "buy":
            candidates = [level.price for level in levels if level.price > entry + min_stop]
            target = min(candidates) if candidates else entry + self.tp_r * risk
            target = max(target, entry + min_tp)
            target = min(target, entry + max_tp)
        else:
            candidates = [level.price for level in levels if level.price < entry - min_stop]
            target = max(candidates) if candidates else entry - self.tp_r * risk
            target = min(target, entry - min_tp)
            target = max(target, entry - max_tp)
        return target

    def _order_params(
        self, side: str, level: Level, tick, atr_value: float
    ) -> dict | None:
        price = round(float(level.price), self.digits)
        min_gap = max(self.stops_level * self.point, self.level_min_distance)
        min_stop = max(self.stops_level * self.point, self.point * 5)
        buffer = self.sl_buffer + self.sl_atr_buffer * max(0.0, atr_value)

        if side == "buy":
            if price > float(tick.ask) - min_gap:
                return None
            sl = price - max(self.min_risk, buffer)
            if price - sl > self.max_risk:
                return None
            risk = price - sl
            tp = self._target_tp("buy", price, sl, self.levels)
            if price - sl < min_stop or tp - price < min_stop:
                return None
        else:
            if price < float(tick.bid) + min_gap:
                return None
            sl = price + max(self.min_risk, buffer)
            if sl - price > self.max_risk:
                return None
            risk = sl - price
            tp = self._target_tp("sell", price, sl, self.levels)
            if sl - price < min_stop or price - tp < min_stop:
                return None

        return {
            "price": round(price, self.digits),
            "sl": round(sl, self.digits),
            "tp": round(tp, self.digits),
            "risk": round(risk, 4),
            "lot": self._round_lot(self.lot),
        }

    def _margin_ok(self, side: str, price: float, lot: float) -> bool:
        order_type = mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL
        try:
            margin = mt5.order_calc_margin(order_type, self.symbol, lot, price)
        except Exception:  # noqa: BLE001
            margin = None
        account = get_account_info()
        if account is None:
            return True
        free = float(getattr(account, "margin_free", 0.0) or 0.0)
        if free <= 0:
            return False
        if margin is None:
            return True
        return float(margin) <= free * self.max_margin_use

    # --- лимитки -----------------------------------------------------------
    def _place_order(self, side: str, level: Level, params: dict, tick) -> None:
        order_type = (
            mt5.ORDER_TYPE_BUY_LIMIT if side == "buy" else mt5.ORDER_TYPE_SELL_LIMIT
        )
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": self.symbol,
            "volume": params["lot"],
            "type": order_type,
            "price": params["price"],
            "sl": params["sl"],
            "tp": params["tp"],
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"scalp {side} {level.source}"[:27],
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": int(tick.time + self.order_ttl * 60),
            "type_filling": self._filling_mode(),
        }

        if self.dry_run:
            check = mt5.order_check(request)
            ok = check is not None and check.retcode == 0
            comment = getattr(check, "comment", "?") if check is not None else "?"
            self.log(
                f"[DRY] {side.upper()} LIMIT {params['lot']} {self.symbol} @ "
                f"{params['price']:.2f} SL {params['sl']:.2f} TP {params['tp']:.2f} | "
                f"{level.source} {level.touches} кас. score {level.score:.0f} | "
                f"check={'OK' if ok else comment}"
            )
            self.journal(
                "dry_pending",
                side=side,
                source=level.source,
                level=level.price,
                price=params["price"],
                sl=params["sl"],
                tp=params["tp"],
                lot=params["lot"],
                score=round(level.score, 1),
                note="dry_run",
            )
            self._dry_orders.setdefault(side, []).append(
                (float(params["price"]), time.time())
            )
            self._last_place_log = time.time()
            return

        result = self._send(request, pending=True)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", "?")
            comment = getattr(result, "comment", "")
            self.log(f"{side.upper()} LIMIT не принят: {code} {comment}")
            self.journal("error", side=side, note=f"pending {code} {comment}")
            return

        ticket = int(result.order)
        self.log(
            f"ЛИМИТКА #{ticket}: {side.upper()} {params['lot']} @ {params['price']:.2f} "
            f"SL {params['sl']:.2f} TP {params['tp']:.2f} | {level.source} "
            f"{level.touches} кас. score {level.score:.0f}"
        )
        self.journal(
            "pending",
            side=side,
            source=level.source,
            level=level.price,
            price=params["price"],
            sl=params["sl"],
            tp=params["tp"],
            ticket=ticket,
            lot=params["lot"],
            score=round(level.score, 1),
        )
        self.notify(
            f"⏳ {side.upper()} LIMIT {params['lot']} {self.symbol} @ {params['price']:.2f}\n"
            f"SL {params['sl']:.2f} · TP {params['tp']:.2f}\n"
            f"{level.source} · {level.touches} кас. · score {level.score:.0f}"
        )

    def _modify_order(self, order, params: dict, tick) -> None:
        request = {
            "action": mt5.TRADE_ACTION_MODIFY,
            "order": int(order.ticket),
            "symbol": self.symbol,
            "price": params["price"],
            "sl": params["sl"],
            "tp": params["tp"],
            "type_time": mt5.ORDER_TIME_SPECIFIED,
            "expiration": int(tick.time + self.order_ttl * 60),
            "magic": self.magic,
        }
        if self.dry_run:
            self.log(
                f"[DRY] лимитка #{order.ticket}: {float(order.price_open):.2f} -> "
                f"{params['price']:.2f} SL {params['sl']:.2f} TP {params['tp']:.2f}"
            )
            return
        result = self._send(request, pending=True)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            self.log(
                f"лимитка #{order.ticket} переставлена: {float(order.price_open):.2f} -> "
                f"{params['price']:.2f} SL {params['sl']:.2f} TP {params['tp']:.2f}"
            )
            self.journal(
                "modify",
                side=self._order_side(order),
                price=params["price"],
                sl=params["sl"],
                tp=params["tp"],
                ticket=int(order.ticket),
                note="level_moved",
            )
            return
        code = getattr(result, "retcode", "?")
        comment = getattr(result, "comment", "")
        self.log(f"лимитка #{order.ticket} не изменена: {code} {comment}")
        if result is not None and result.retcode in (10014, 10015, 10016):
            self._remove_order(order, f"modify_fail_{result.retcode}")

    def _remove_order(self, order, reason: str) -> bool:
        if self.dry_run:
            self.log(f"[DRY] снять лимитку #{order.ticket} ({reason})")
            return True
        request = {"action": mt5.TRADE_ACTION_REMOVE, "order": int(order.ticket)}
        result = self._send(request)
        ok = result is not None and result.retcode == mt5.TRADE_RETCODE_DONE
        if not ok:
            try:
                ok = not mt5.orders_get(ticket=int(order.ticket))
            except Exception:  # noqa: BLE001
                ok = False
        if ok:
            self.log(f"лимитка #{order.ticket} снята ({reason})")
            self.journal("cancel", ticket=int(order.ticket), reason=reason)
        else:
            code = getattr(result, "retcode", "?")
            comment = getattr(result, "comment", "")
            self.log(f"лимитка #{order.ticket} не снята: {code} {comment}")
        return ok

    def _select_levels(
        self,
        side: str,
        price: float,
        limit: int,
        skips: list[str] | None = None,
    ) -> list[Level]:
        """Лучшие уровни на сторону для заготовок (без близких дублей)."""
        candidates: list[tuple[float, float, Level]] = []
        for level in self.levels:
            level_side = "sell" if level.price > price else "buy"
            if level_side != side:
                continue
            distance = abs(level.price - price)
            if distance < self.level_min_distance:
                if skips is not None:
                    skips.append(
                        f"{side} {level.price:.2f}: too_close {distance:.2f}$"
                    )
                continue
            if distance > self.level_max_distance:
                continue
            score = float(level.score)
            if score < self.min_score:
                continue
            if self._is_used(side, level.price):
                if skips is not None:
                    skips.append(f"{side} {level.price:.2f}: used")
                continue
            candidates.append((score, distance, level))

        candidates.sort(key=lambda item: (-item[0], item[1]))
        selected: list[Level] = []
        for score, _, level in candidates:
            if any(
                abs(level.price - other.price) < self.pending_min_gap
                for other in selected
            ):
                continue
            level.score = score
            selected.append(level)
            if len(selected) >= limit:
                break
        return selected

    def _log_skips(self, skips: list[str]) -> None:
        """Пишет причины пропусков, но не чаще раза в refresh_seconds."""
        if not skips:
            return
        if time.time() - self._last_skip_log < self.refresh_seconds:
            return
        self._last_skip_log = time.time()
        unique = list(dict.fromkeys(skips))
        self.log("пропуск: " + "; ".join(unique[:8]))

    def _dry_prices(self, side: str) -> list[float]:
        """Цены «виртуальных» заготовок в dry-run, чтобы не спамить лог."""
        if not self.dry_run:
            return []
        now = time.time()
        fresh = [
            (price, stamp)
            for price, stamp in self._dry_orders.get(side, [])
            if now - stamp < self.refresh_seconds
        ]
        self._dry_orders[side] = fresh
        return [price for price, _ in fresh]

    def _sync_grid(self, tick, positions: dict | None = None) -> None:
        """Держит набор заготовок: ставит недостающие, снимает лишние."""
        if tick is None or tick.time <= 0:
            return
        positions = positions if positions is not None else self.positions()
        orders = self.pending_orders()
        now = int(tick.time)
        mid = (float(tick.bid) + float(tick.ask)) / 2.0
        spread = self._spread_points(tick)
        min_gap = max(self.stops_level * self.point, self.level_min_distance)
        cancel_guard = max(
            self.pending_cancel_guard,
            self.stops_level * self.point,
            spread * self.point * 2,
        )
        skips: list[str] = []

        # Просроченные заготовки снимаем всегда, даже при закрытом рынке.
        for order in list(orders.values()):
            age = now - int(getattr(order, "time_setup", now))
            if age >= self.order_ttl * 60:
                self._remove_order(order, "expired")
                orders.pop(int(order.ticket), None)

        # Пока открыта позиция, новые заготовки не ставим: снимаем по политике.
        if positions:
            if self.cancel_pending_on_fill != "none":
                open_sides = {self._pos_side(pos) for pos in positions.values()}
                for order in list(orders.values()):
                    if (
                        self.cancel_pending_on_fill == "all"
                        or self._order_side(order) in open_sides
                    ):
                        self._remove_order(order, "position_open")
                        orders.pop(int(order.ticket), None)
            return

        # Рынок закрыт или спред широкий — новые заготовки не ставим.
        if not self._market_ok(tick):
            self._log_skips(["рынок закрыт / ночь / выходные"])
            return
        if spread > self.max_spread_points:
            self._log_skips(
                [f"спред {spread:.0f} п. > {self.max_spread_points:.0f}"]
            )
            return

        side_counts = {"buy": 0, "sell": 0}
        for order in orders.values():
            side = self._order_side(order)
            if side in side_counts:
                side_counts[side] += 1

        for side in ("buy", "sell"):
            attempts = int(self.state["attempts"].get(side, 0))
            blocked = attempts >= self.max_levels_per_side
            if blocked:
                skips.append(f"{side}: лимит входов {attempts}")
            desired = (
                []
                if blocked
                else self._select_levels(
                    side, mid, self.max_pending_per_side, skips
                )
            )

            # Сверяем уже стоящие заготовки с желаемыми уровнями.
            covered: list[float] = self._dry_prices(side)
            side_orders = [
                order for order in orders.values() if self._order_side(order) == side
            ]
            for order in list(side_orders):
                old_price = float(order.price_open)
                match = None
                for level in desired:
                    if abs(level.price - old_price) <= self.level_move_tolerance:
                        match = level
                        break
                if match is None:
                    about_to_fill = (
                        side == "buy"
                        and float(tick.bid) - old_price < cancel_guard
                    ) or (
                        side == "sell"
                        and old_price - float(tick.ask) < cancel_guard
                    )
                    if not about_to_fill:
                        self._remove_order(order, "level_gone")
                        orders.pop(int(order.ticket), None)
                    continue
                covered.append(match.price)
                params = self._order_params(side, match, tick, self.atr)
                if params is None:
                    continue
                if (
                    abs(params["price"] - old_price) > self.level_move_tolerance
                    or abs(params["sl"] - float(order.sl or 0))
                    > max(0.05, self.point * 2)
                    or abs(params["tp"] - float(order.tp or 0))
                    > max(0.05, self.point * 2)
                ):
                    self._modify_order(order, params, tick)

            side_counts[side] = len(covered)

            # Ставим недостающие заготовки.
            for level in desired:
                if any(
                    abs(level.price - price) <= self.level_move_tolerance
                    for price in covered
                ):
                    continue
                total = sum(side_counts.values())
                if total >= self.max_pending_total:
                    skips.append("лимит заготовок всего")
                    break
                if side_counts[side] >= self.max_pending_per_side:
                    skips.append(f"{side}: лимит заготовок на сторону")
                    break
                if side == "buy" and level.price > float(tick.ask) - min_gap:
                    skips.append(f"buy {level.price:.2f}: too_close")
                    continue
                if side == "sell" and level.price < float(tick.bid) + min_gap:
                    skips.append(f"sell {level.price:.2f}: too_close")
                    continue
                params = self._order_params(side, level, tick, self.atr)
                if params is None:
                    skips.append(f"{side} {level.price:.2f}: risk/tp")
                    continue
                if not self._margin_ok(side, params["price"], params["lot"]):
                    skips.append(f"{side} {level.price:.2f}: margin")
                    continue
                self._place_order(side, level, params, tick)
                covered.append(level.price)
                side_counts[side] += 1

        self._log_skips(skips)

        # Диагностика: если заготовок нет вовсе.
        if (
            not self.dry_run
            and not self.pending_orders()
            and time.time() - self._last_place_log >= self.refresh_seconds
        ):
            self._last_place_log = time.time()
            top = sorted(self.levels, key=lambda item: item.score, reverse=True)[:3]
            parts = [
                f"{item.side.upper()} {item.price:.2f} "
                f"(score {item.score:.0f}, {abs(item.price - mid):.2f} $)"
                for item in top
            ]
            self.log(
                "нет заготовок: "
                + ("; ".join(parts) if parts else "подходящих уровней нет")
            )

    # --- позиции -----------------------------------------------------------
    def _register_new_positions(self, positions: dict) -> None:
        changed = False
        for ticket, pos in positions.items():
            key = str(ticket)
            if key in self.state["positions"]:
                continue
            side = self._pos_side(pos)
            entry = float(pos.price_open)
            sl = float(pos.sl or 0.0)
            tp = float(pos.tp or 0.0)
            if sl <= 0:
                default_risk = max(
                    self.min_risk, self.sl_buffer + self.sl_atr_buffer * self.atr
                )
                sl = entry - default_risk if side == "buy" else entry + default_risk
            self.state["positions"][key] = {
                "side": side,
                "entry": entry,
                "initial_sl": sl,
                "tp": tp,
                "opened_at": int(getattr(pos, "time", 0) or time.time()),
                "level": entry,
                "source": "fill",
                "lot": float(getattr(pos, "volume", self.lot) or self.lot),
            }
            self.state["attempts"][side] = min(
                self.max_levels_per_side, int(self.state["attempts"].get(side, 0)) + 1
            )
            changed = True
            self.log(
                f"НОВАЯ ПОЗИЦИЯ #{ticket}: {side.upper()} {pos.volume} @ {entry:.2f} "
                f"SL {sl:.2f} TP {tp:.2f}"
            )
            self.journal(
                "open",
                side=side,
                price=entry,
                sl=sl,
                tp=tp,
                ticket=ticket,
                lot=pos.volume,
                note="position detected",
            )
            self.notify(
                f"🟢 SCALPER {side.upper()} {pos.volume} {self.symbol} @ {entry:.2f}\n"
                f"SL {sl:.2f} · TP {tp:.2f}"
            )
            if self.cancel_pending_on_fill != "none":
                for order in list(self.pending_orders().values()):
                    if (
                        self.cancel_pending_on_fill == "all"
                        or self._order_side(order) == side
                    ):
                        self._remove_order(order, "filled")
        if changed:
            self._save_state()

    def _better_sl(self, side: str, candidate: float, current: float, price: float) -> bool:
        min_stop = max(self.stops_level * self.point, self.point * 5)
        if side == "buy":
            candidate = min(candidate, price - min_stop)
            if current <= 0:
                return candidate > 0
            return candidate > current + self.point / 2
        candidate = max(candidate, price + min_stop)
        if current <= 0:
            return candidate > 0
        return candidate < current - self.point / 2

    def _better_tp(self, side: str, candidate: float, current: float) -> bool:
        if side == "buy":
            if current <= 0:
                return candidate > 0
            return candidate > current + self.point / 2
        if current <= 0:
            return candidate > 0
        return candidate < current - self.point / 2

    def _changed(self, new: float, old: float) -> bool:
        if new <= 0 and old <= 0:
            return False
        if (new <= 0) != (old <= 0):
            return True
        return abs(new - old) >= self.point / 2

    def _extension_tp(
        self,
        side: str,
        entry: float,
        initial_sl: float,
        current_tp: float,
        levels: list[Level],
    ) -> float | None:
        risk = abs(entry - initial_sl)
        if risk <= 0:
            return None
        max_tp = self.tp_max_r * risk
        if side == "buy":
            floor = max(current_tp, entry) + max(self.point * 5, 0.2)
            candidates = [level.price for level in levels if level.price > floor]
            if not candidates:
                return None
            target = min(candidates)
            target = min(target, entry + max_tp)
            if target <= current_tp + self.point / 2:
                return None
            return target
        ceil = min(current_tp, entry) - max(self.point * 5, 0.2)
        candidates = [level.price for level in levels if level.price < ceil]
        if not candidates:
            return None
        target = max(candidates)
        target = max(target, entry - max_tp)
        if target >= current_tp - self.point / 2:
            return None
        return target

    def _modify_position(self, pos, sl: float, tp: float, reason: str) -> None:
        if self.dry_run:
            self.log(
                f"[DRY] позиция #{pos.ticket}: SL {sl:.2f} TP {tp:.2f} ({reason})"
            )
            return
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": int(pos.ticket),
            "sl": round(float(sl), self.digits),
            "tp": round(float(tp), self.digits),
        }
        result = self._send(request)
        if result is not None and result.retcode in (
            mt5.TRADE_RETCODE_DONE,
            getattr(mt5, "TRADE_RETCODE_NO_CHANGES", 10025),
        ):
            self.log(
                f"позиция #{pos.ticket}: SL {sl:.2f} TP {tp:.2f} ({reason})"
            )
            self.journal(
                "modify",
                side=self._pos_side(pos),
                price=float(pos.price_open),
                sl=sl,
                tp=tp,
                ticket=int(pos.ticket),
                reason=reason,
            )
            return
        code = getattr(result, "retcode", "?")
        comment = getattr(result, "comment", "")
        self.log(f"позиция #{pos.ticket} не изменена: {code} {comment}")

    def _manage_positions(self, positions: dict, tick) -> None:
        if not positions:
            return
        for ticket, pos in list(positions.items()):
            state = self.state["positions"].get(str(ticket))
            if not state:
                continue
            side = str(state.get("side") or self._pos_side(pos))
            entry = float(state.get("entry") or pos.price_open)
            initial_sl = float(state.get("initial_sl") or 0.0)
            if initial_sl <= 0:
                initial_sl = float(pos.sl or 0.0)
            risk = abs(entry - initial_sl)
            if risk <= 0:
                continue

            opened_at = int(state.get("opened_at") or getattr(pos, "time", 0) or tick.time)
            age_min = (int(tick.time) - opened_at) / 60.0
            if age_min >= self.max_trade_minutes:
                if self._spread_points(tick) <= self.max_spread_points * 1.5:
                    self._close_position(pos, tick, "TIME")
                continue

            price = float(tick.bid) if side == "buy" else float(tick.ask)
            profit = (price - entry) if side == "buy" else (entry - price)
            current_sl = float(pos.sl or 0.0)
            current_tp = float(pos.tp or 0.0)
            new_sl = current_sl
            new_tp = current_tp
            reason = ""

            if current_sl <= 0:
                default_risk = max(
                    self.min_risk, self.sl_buffer + self.sl_atr_buffer * self.atr
                )
                candidate = (
                    entry - default_risk if side == "buy" else entry + default_risk
                )
                if self._better_sl(side, candidate, current_sl, price):
                    new_sl = candidate
                    reason = "ATTACH_SL"

            if profit >= self.be_r * risk:
                candidate = entry + self.be_buffer if side == "buy" else entry - self.be_buffer
                if self._better_sl(side, candidate, new_sl, price):
                    new_sl = candidate
                    reason = "BE"

            if profit >= self.trail_at_r * risk and self.atr > 0:
                distance = min(
                    self.trail_atr * self.atr, max(1.5 * risk, self.point * 5)
                )
                candidate = (
                    price - distance if side == "buy" else price + distance
                )
                if self._better_sl(side, candidate, new_sl, price):
                    new_sl = candidate
                    reason = "TRAIL"

            if current_tp <= 0:
                candidate = self._target_tp(side, entry, initial_sl, self.levels)
                if self._better_tp(side, candidate, current_tp):
                    new_tp = candidate
                    reason = (reason + "+TP" if reason else "ATTACH_TP")

            if profit >= self.tp_extend_at_r * risk:
                target = self._extension_tp(
                    side, entry, initial_sl, current_tp, self.levels
                )
                if target is not None and self._better_tp(side, target, new_tp):
                    new_tp = target
                    reason = reason + "+TP" if reason else "TP_EXTEND"

            sl_changed = self._changed(new_sl, current_sl)
            tp_changed = self._changed(new_tp, current_tp)
            if sl_changed or tp_changed:
                self._modify_position(
                    pos,
                    new_sl if new_sl > 0 else current_sl,
                    new_tp if new_tp > 0 else current_tp,
                    reason or "MANAGE",
                )

    def _close_position(self, pos, tick, reason: str) -> None:
        side = self._pos_side(pos)
        price = float(tick.bid) if side == "buy" else float(tick.ask)
        if self.dry_run:
            self.log(f"[DRY] закрыть #{pos.ticket} {side.upper()} по {reason} @ {price:.2f}")
            return
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": float(pos.volume),
            "type": mt5.ORDER_TYPE_SELL if side == "buy" else mt5.ORDER_TYPE_BUY,
            "position": int(pos.ticket),
            "price": price,
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"scalp {reason.lower()}"[:27],
            "type_filling": self._filling_mode(),
        }
        result = self._send(request)
        if result is not None and result.retcode == mt5.TRADE_RETCODE_DONE:
            state = self.state["positions"].get(str(pos.ticket))
            if state is not None:
                state["close_reason"] = reason
                self._save_state()
            self.log(f"закрываю #{pos.ticket} {side.upper()} по {reason} @ {price:.2f}")
            self.journal(
                "close_request",
                side=side,
                price=price,
                ticket=int(pos.ticket),
                reason=reason,
            )
        else:
            code = getattr(result, "retcode", "?")
            comment = getattr(result, "comment", "")
            self.log(f"#{pos.ticket} не закрыта: {code} {comment}")

    # --- закрытие ----------------------------------------------------------
    def _position_deals(self, ticket: int) -> list:
        for _ in range(5):
            try:
                deals = mt5.history_deals_get(position=int(ticket))
            except Exception:  # noqa: BLE001
                deals = None
            if deals:
                return list(deals)
            time.sleep(0.2)
        return []

    def _side_from_deals(self, deals: list) -> str:
        for deal in deals:
            if int(getattr(deal, "entry", -1)) == mt5.DEAL_ENTRY_IN:
                return (
                    "buy"
                    if int(getattr(deal, "type", -1)) == mt5.DEAL_TYPE_BUY
                    else "sell"
                )
        for deal in deals:
            if int(getattr(deal, "type", -1)) == mt5.DEAL_TYPE_SELL:
                return "buy"
            if int(getattr(deal, "type", -1)) == mt5.DEAL_TYPE_BUY:
                return "sell"
        return ""

    def _on_closed(self, ticket: int, now_server: int) -> None:
        state = self.state["positions"].pop(str(ticket), None)
        deals = self._position_deals(ticket)
        profit = sum(
            float(getattr(deal, "profit", 0.0))
            + float(getattr(deal, "swap", 0.0))
            + float(getattr(deal, "commission", 0.0))
            for deal in deals
        )
        reason = "OTHER"
        out_entries = {
            mt5.DEAL_ENTRY_OUT,
            getattr(mt5, "DEAL_ENTRY_INOUT", -1),
            getattr(mt5, "DEAL_ENTRY_OUT_BY", -1),
        }
        for deal in deals:
            if int(getattr(deal, "entry", -1)) not in out_entries:
                continue
            deal_reason = int(getattr(deal, "reason", -1))
            if deal_reason == getattr(mt5, "DEAL_REASON_SL", -100):
                reason = "SL"
            elif deal_reason == getattr(mt5, "DEAL_REASON_TP", -100):
                reason = "TP"
            elif deal_reason == getattr(mt5, "DEAL_REASON_SO", -100):
                reason = "SO"
        if state and state.get("close_reason") and reason == "OTHER":
            reason = str(state["close_reason"])

        side = str(state.get("side") or "") if state else ""
        if side not in ("buy", "sell"):
            side = self._side_from_deals(deals)
        entry = float(state.get("entry") or 0.0) if state else 0.0
        initial_sl = float(state.get("initial_sl") or 0.0) if state else 0.0
        level = float(state.get("level") or entry) if state else entry
        lot = float(state.get("lot") or 0.0) if state else 0.0
        risk = abs(entry - initial_sl)
        risk_money = risk * self.contract * lot
        result_r = profit / risk_money if risk_money > 0 else 0.0

        if side in ("buy", "sell"):
            if reason == "SL":
                used = self.state["used"].setdefault(side, [])
                used.append(level)
                self.state["used"][side] = [float(value) for value in used[-10:]]
                attempts = int(self.state["attempts"].get(side, 0))
                if attempts >= self.max_levels_per_side:
                    self.state["attempts"][side] = 0
            else:
                self.state["attempts"][side] = 0
                self.state["used"][side] = []
            self._save_state()

        sign = "+" if profit >= 0 else ""
        self.log(
            f"ЗАКРЫТО #{ticket}: {side.upper() or '?'} по {reason} "
            f"{sign}{profit:.2f} $ ({sign}{result_r:.2f}R)"
        )
        self.journal(
            "close",
            side=side,
            level=level,
            price=entry,
            ticket=ticket,
            lot=lot,
            reason=reason,
            note=f"profit={profit:.2f} r={result_r:.2f}",
        )
        self.notify(
            f"{'✅' if profit >= 0 else '❌'} SCALPER {side.upper() or '?'} закрыта по {reason}\n"
            f"{sign}{profit:.2f} $ ({sign}{result_r:.2f}R)"
        )

    def _sync_closed(self, positions: dict, now_server: int) -> None:
        current = set(positions)
        for ticket in sorted(self._known_positions - current):
            self._on_closed(ticket, now_server)
        self._known_positions = current

    def _reconcile_state(self, now_server: int) -> None:
        """После перезапуска закрывает в состоянии позиции, которых уже нет."""
        current = set(self.positions())
        for key in list(self.state["positions"]):
            try:
                ticket = int(key)
            except (TypeError, ValueError):
                self.state["positions"].pop(key, None)
                continue
            if ticket not in current:
                self.log(f"состояние: позиция #{ticket} уже закрыта — сверяю")
                self._on_closed(ticket, now_server)

    # --- запуск ------------------------------------------------------------
    def startup(self) -> None:
        account = self.account
        mode = "DRY-RUN (ордера не отправляются)" if self.dry_run else "БОЕВОЙ (демо)"
        self.log("=" * 62)
        self.log("  GOLDBOT SCALPER — лимитки на зонах и сильных точках")
        self.log("=" * 62)
        if account is not None:
            self.log(
                f"  счёт {account.login} ({account.server}), баланс "
                f"{account.balance:.2f} {account.currency}, плечо 1:{account.leverage}"
            )
        self.log(f"  режим: {mode}")
        self.log(
            f"  лот {self.lot} | magic {self.magic} | макс. позиций {self.max_positions}"
        )
        self.log(
            f"  уровни каждые {self.refresh_seconds} с, опрос {self.poll_seconds:g} с"
        )
        self.log(
            f"  заготовок на сторону {self.max_pending_per_side}, всего "
            f"{self.max_pending_total}, шаг {self.pending_min_gap:.2f} $ | "
            f"при заполнении снимать: {self.cancel_pending_on_fill}"
        )
        self.log(
            f"  лимитки живут {self.order_ttl} мин | без паузы после "
            f"{self.max_levels_per_side} SL"
        )
        self.log(f"  журнал: {self.journal_path}")
        self.log(f"  состояние: {self.state_path}")
        self.log("-" * 62)

        self._cleanup_pending()
        tick = get_tick(self.symbol)
        now_server = int(tick.time) if tick is not None and tick.time else int(time.time())
        self._reconcile_state(now_server)
        positions = self.positions()
        self._known_positions = set(positions)
        self._register_new_positions(positions)
        self.notify(
            "🤖 Скальпер запущен\n"
            f"счёт {getattr(account, 'login', '?')} · {'DRY-RUN' if self.dry_run else 'демо'}\n"
            f"лот {self.lot} · magic {self.magic}"
        )

    def _cleanup_pending(self) -> None:
        tick = get_tick(self.symbol)
        now = int(tick.time) if tick is not None and tick.time else int(time.time())
        for order in list(self.pending_orders().values()):
            age = now - int(getattr(order, "time_setup", now))
            if age >= self.order_ttl * 60:
                self._remove_order(order, "stale_on_start")

    def tick(self, force_refresh: bool = False) -> None:
        tick = get_tick(self.symbol)
        if tick is None or tick.time <= 0:
            self.log("нет тика MT5 — пропуск")
            return

        if (
            force_refresh
            or not self.levels
            or (time.time() - self.last_refresh) >= self.refresh_seconds
        ):
            self.refresh_levels()

        positions = self.positions()
        now_server = int(tick.time)
        self._sync_closed(positions, now_server)
        self._register_new_positions(positions)
        self._manage_positions(positions, tick)
        self._sync_grid(tick, positions)

    def run(self) -> None:
        self.startup()
        self.log("цикл запущен")
        try:
            while True:
                started = time.time()
                try:
                    self.tick()
                except Exception as exc:  # noqa: BLE001 - процесс не должен падать
                    self.log(f"ОШИБКА: {type(exc).__name__}: {exc}")
                elapsed = time.time() - started
                time.sleep(max(0.2, self.poll_seconds - elapsed))
        except KeyboardInterrupt:
            self.log("остановлен пользователем")
