"""Live-режим: ожидание закрытия баров M5, вывод сигналов и журнал результатов."""
from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from . import i18n
from .config import ROOT, load_config
from .data import (
    MT5Error,
    connect,
    ensure_symbol,
    fmt_utc,
    get_account_info,
    get_closed_rates,
    get_symbol_info,
    get_tick,
    server_utc_offset_hours,
    shutdown,
)
from .strategy import Signal, prepare, signal_at
from .telegram import TelegramBot, format_close_message, format_signal_message

JOURNAL_COLUMNS = [
    "id", "created_utc", "bar_time_server", "bar_time_utc", "checked_until", "setup",
    "setup_label", "direction", "entry", "sl", "tp", "sl_current", "risk_price",
    "rr", "spread_points", "time_exit_bars", "be_at_r", "be_hit", "confidence",
    "reasons", "status", "exit_time_utc", "exit_price", "exit_reason", "result_r",
    "result_points", "bars_held", "mfe", "gap_to_tp", "exit_bar_peak",
]


def _as_float(value, default: float = 0.0) -> float:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def tp_note(gap: float, mfe: float, exit_bar_peak: float | None) -> str:
    """Пояснение: насколько цена не дошла до TP (или насколько его пробила)."""
    if gap > 0:
        return f"макс. цена {mfe:.2f}, до TP не хватило {gap:.2f} $"
    peak = f", пик в баре тейка {exit_bar_peak:.2f}" if exit_bar_peak is not None else ""
    return f"TP с проколом на {-gap:.2f} ${peak}"


def _as_int(value, default: int = 0) -> int:
    try:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


class SignalJournal:
    """CSV-журнал сигналов с автоматическим отслеживанием TP/SL/времени."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> pd.DataFrame:
        if not self.path.exists():
            return pd.DataFrame({col: pd.Series(dtype=object) for col in JOURNAL_COLUMNS})
        df = pd.read_csv(self.path, encoding="utf-8-sig")
        for col in JOURNAL_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[JOURNAL_COLUMNS].astype(object)

    def save(self, df: pd.DataFrame) -> None:
        # Пишем через временный файл: исполнитель в другом процессе читает этот
        # журнал и не должен увидеть недописанный CSV.
        tmp = self.path.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        tmp.replace(self.path)

    def has_signal(self, signal_id: str) -> bool:
        df = self.load()
        return bool((df["id"] == signal_id).any()) if not df.empty else False

    def add(self, sig: Signal, utc_offset_hours: int) -> str:
        signal_id = f"{sig.time:%Y%m%d_%H%M}_{sig.setup}"
        if self.has_signal(signal_id):
            return signal_id
        df = self.load()
        row = {
            "id": signal_id,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "bar_time_server": sig.time.strftime("%Y-%m-%d %H:%M:%S"),
            "bar_time_utc": (sig.time - pd.Timedelta(hours=utc_offset_hours)).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
            "checked_until": sig.time.strftime("%Y-%m-%d %H:%M:%S"),
            "setup": sig.setup,
            "setup_label": sig.setup_label,
            "direction": sig.direction,
            "entry": round(sig.entry, 2),
            "sl": round(sig.sl, 2),
            "tp": round(sig.tp, 2),
            "sl_current": round(sig.sl, 2),
            "risk_price": round(sig.risk_price, 4),
            "rr": sig.rr,
            "spread_points": sig.spread_points,
            "time_exit_bars": sig.time_exit_bars,
            "be_at_r": sig.be_at_r,
            "be_hit": 0,
            "confidence": sig.confidence,
            "reasons": "; ".join(sig.reasons),
            "status": "OPEN",
            "exit_time_utc": "",
            "exit_price": "",
            "exit_reason": "",
            "result_r": "",
            "result_points": "",
            "bars_held": 0,
            "mfe": round(sig.entry, 2),
            "gap_to_tp": "",
            "exit_bar_peak": "",
        }
        row_df = pd.DataFrame([row], columns=JOURNAL_COLUMNS)
        df = row_df if df.empty else pd.concat([df, row_df], ignore_index=True)
        self.save(df)
        return signal_id

    def update(self, df_bars: pd.DataFrame, utc_offset_hours: int, point: float = 0.01) -> list[dict]:
        """Обновляет открытые сигналы по закрытым барам. Возвращает закрытые сделки."""
        df = self.load()
        if df.empty:
            return []

        closed: list[dict] = []
        changed = False

        for idx, row in df.iterrows():
            if row["status"] != "OPEN":
                continue
            bar_time = pd.Timestamp(row["bar_time_server"])
            try:
                checked = pd.Timestamp(row["checked_until"])
            except (TypeError, ValueError):
                checked = bar_time
            if pd.isna(checked):
                checked = bar_time
            bars = df_bars[df_bars["time"] > checked]
            if bars.empty:
                continue

            direction = str(row["direction"])
            entry = float(row["entry"])
            sl = float(row["sl_current"])
            tp = float(row["tp"])
            risk = float(row["risk_price"])
            be_r = float(row["be_at_r"])
            be_hit = bool(_as_int(row["be_hit"]))
            time_exit = _as_int(row["time_exit_bars"], 24)
            mfe = _as_float(row.get("mfe"), entry)
            if mfe <= 0:
                mfe = entry

            exit_price = None
            reason = ""
            exit_time = None
            bars_held = _as_int(row["bars_held"], 0)

            for _, bar in bars.iterrows():
                bars_held += 1
                if direction == "BUY":
                    mfe = max(mfe, float(bar["high"]))
                else:
                    mfe = min(mfe, float(bar["low"]))
                if direction == "BUY":
                    if bar["open"] <= sl:
                        exit_price, reason, exit_time = float(bar["open"]), "SL", bar["time"]
                        break
                    if bar["low"] <= sl:
                        exit_price, reason, exit_time = sl, "SL", bar["time"]
                        break
                    if bar["open"] >= tp:
                        exit_price, reason, exit_time = float(bar["open"]), "TP", bar["time"]
                        break
                    if bar["high"] >= tp:
                        exit_price, reason, exit_time = tp, "TP", bar["time"]
                        break
                    if bars_held >= time_exit:
                        exit_price, reason, exit_time = float(bar["close"]), "TIME", bar["time"]
                        break
                    if be_r > 0 and not be_hit and bar["high"] >= entry + be_r * risk:
                        sl = entry
                        be_hit = True
                        changed = True
                else:
                    if bar["open"] >= sl:
                        exit_price, reason, exit_time = float(bar["open"]), "SL", bar["time"]
                        break
                    if bar["high"] >= sl:
                        exit_price, reason, exit_time = sl, "SL", bar["time"]
                        break
                    if bar["open"] <= tp:
                        exit_price, reason, exit_time = float(bar["open"]), "TP", bar["time"]
                        break
                    if bar["low"] <= tp:
                        exit_price, reason, exit_time = tp, "TP", bar["time"]
                        break
                    if bars_held >= time_exit:
                        exit_price, reason, exit_time = float(bar["close"]), "TIME", bar["time"]
                        break
                    if be_r > 0 and not be_hit and bar["low"] <= entry - be_r * risk:
                        sl = entry
                        be_hit = True
                        changed = True

            if exit_price is None:
                df.at[idx, "checked_until"] = pd.Timestamp(
                    bars["time"].iloc[-1]
                ).strftime("%Y-%m-%d %H:%M:%S")
                df.at[idx, "sl_current"] = round(sl, 2)
                df.at[idx, "be_hit"] = int(be_hit)
                df.at[idx, "bars_held"] = bars_held
                df.at[idx, "mfe"] = round(mfe, 2)
                changed = True
                continue

            # entry — реальная цена входа (ask для BUY, bid для SELL),
            # поэтому спред уже учтён в разнице цен и отдельно не вычитается.
            gross = (exit_price - entry) * (1 if direction == "BUY" else -1)
            net = gross
            result_r = net / risk if risk else 0.0
            result_points = net
            if reason == "SL" and be_hit and abs(exit_price - entry) < 1e-9:
                reason = "BE"

            # Максимум цены за сделку и недолёт до тейка (плюс = не дошло).
            gap_to_tp = (tp - mfe) if direction == "BUY" else (mfe - tp)
            exit_bar_peak = None
            exit_bars = bars[bars["time"] == exit_time]
            if not exit_bars.empty:
                exit_bar = exit_bars.iloc[0]
                exit_bar_peak = float(
                    exit_bar["high"] if direction == "BUY" else exit_bar["low"]
                )

            df.at[idx, "sl_current"] = round(sl, 2)
            df.at[idx, "be_hit"] = int(be_hit)
            df.at[idx, "mfe"] = round(mfe, 2)
            df.at[idx, "gap_to_tp"] = round(gap_to_tp, 2)
            df.at[idx, "exit_bar_peak"] = (
                round(exit_bar_peak, 2) if exit_bar_peak is not None else ""
            )
            df.at[idx, "checked_until"] = pd.Timestamp(exit_time).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            df.at[idx, "status"] = "CLOSED"
            df.at[idx, "exit_time_utc"] = (
                pd.Timestamp(exit_time) - pd.Timedelta(hours=utc_offset_hours)
            ).strftime("%Y-%m-%d %H:%M:%S")
            df.at[idx, "exit_price"] = round(exit_price, 2)
            df.at[idx, "exit_reason"] = reason
            df.at[idx, "result_r"] = round(result_r, 3)
            df.at[idx, "result_points"] = round(result_points, 2)
            df.at[idx, "bars_held"] = bars_held
            changed = True
            closed.append(
                {
                    "id": row["id"],
                    "direction": direction,
                    "setup_label": row["setup_label"],
                    "reason": reason,
                    "result_r": result_r,
                    "result_points": result_points,
                    "tp": tp,
                    "mfe": mfe,
                    "gap_to_tp": gap_to_tp,
                    "exit_bar_peak": exit_bar_peak,
                    "tp_note": tp_note(gap_to_tp, mfe, exit_bar_peak),
                }
            )

        if changed:
            self.save(df)
        return closed


def format_signal(sig: Signal, utc_offset: int) -> str:
    line = "=" * 66
    arrow = "BUY " if sig.direction == "BUY" else "SELL"
    risk_dist = sig.entry - sig.sl if sig.direction == "BUY" else sig.sl - sig.entry
    tp_dist = sig.tp - sig.entry if sig.direction == "BUY" else sig.entry - sig.tp
    hours = sig.time_exit_bars * 5 / 60

    lines = [
        "",
        line,
        f"  {arrow}  XAUUSD (M5)   |   {sig.setup_label}",
        f"  {fmt_utc(sig.time, utc_offset)}   (сервер: {sig.time:%d.%m.%Y %H:%M})",
        line,
        f"  Вход по рынку:     {sig.entry:.2f}",
        f"  Стоп-лосс:         {sig.sl:.2f}   ({risk_dist:.2f} $ = {sig.risk_price / sig.atr:.1f} ATR)",
        f"  Тейк-профит:       {sig.tp:.2f}   ({tp_dist:.2f} $ = {sig.rr:.1f}R)",
        f"  Риск по цене:      {risk_dist:.2f} $ ({risk_dist / 0.01:.0f} пунктов)",
        f"  R:R:               1 : {sig.rr:.1f}",
        f"  Спред:             {sig.spread_points:.0f} пунктов",
        f"  Уверенность:       {sig.confidence}/100",
        f"  Макс. удержание:   {sig.time_exit_bars} баров (~{hours:.1f} ч), затем выход по рынку",
        "-" * 66,
        "  Причины:",
    ]
    lines += [f"   - {reason}" for reason in sig.reasons]
    lines += [line]
    return "\n".join(lines)


def _today_server_signals(journal: SignalJournal, now_server: datetime) -> int:
    df = journal.load()
    if df.empty:
        return 0
    dates = pd.to_datetime(df["bar_time_server"], errors="coerce").dt.date
    return int((dates == now_server.date()).sum())


def _log_error(exc: BaseException, context: str = "") -> Path:
    """Пишет трейсбек в data/live_error.log, чтобы падения не терялись."""
    path = ROOT / "data" / "live_error.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(f"\n=== {datetime.now():%Y-%m-%d %H:%M:%S} [{context}] ===\n")
            traceback.print_exception(type(exc), exc, exc.__traceback__, file=fh)
    except Exception:  # noqa: BLE001 - логирование не должно ронять цикл
        pass
    return path


def load_xmarket(cfg: dict) -> dict[str, pd.DataFrame]:
    """Бары кросс-активов для фильтров (XAGUSD и т.п.).

    Пустой dict — фильтр выключен или не нужен. Ошибка загрузки не должна
    ронять цикл: без данных фильтр просто пропускается.
    """
    xm_cfg = cfg.get("cross_market") or {}
    if not xm_cfg.get("enabled"):
        return {}
    needed = any(
        setup.get("enabled", True) and setup.get("xm")
        for setup in cfg["strategy"].get("setups", {}).values()
    )
    if not needed:
        return {}
    bars = int(xm_cfg.get("bars", 600))
    frames: dict[str, pd.DataFrame] = {}
    for key, spec in (xm_cfg.get("series") or {}).items():
        symbol = spec.get("symbol")
        if not symbol:
            continue
        try:
            ensure_symbol(symbol)
            frames[key] = get_closed_rates(symbol, spec.get("timeframe", "M15"), bars)
        except Exception as exc:  # noqa: BLE001 - фильтр не должен ронять цикл
            print(f"[кросс-рынок] {symbol}: {exc} — фильтр пропущен")
    return frames


def run_live(cfg: dict | None = None, once: bool = False, terminal_path: str | None = None) -> None:
    cfg = cfg or load_config()
    symbol = cfg["symbol"]
    tf = cfg["timeframe"]
    poll = int(cfg["live"].get("poll_seconds", 5))
    history_bars = int(cfg["live"].get("history_bars", 600))
    journal_path = ROOT / cfg["live"].get("journal_path", "data/signals.csv")
    journal = SignalJournal(journal_path)
    bot = TelegramBot.from_config(cfg)

    connect(path=terminal_path)
    try:
        info = ensure_symbol(symbol)
        account = get_account_info()
        offset = server_utc_offset_hours(symbol)

        print("=" * 66)
        print("  GOLDBOT — сигналы XAUUSD M5")
        print("=" * 66)
        if account:
            print(f"  Счёт: {account.login} ({account.server})  баланс: {account.balance:.2f} {account.currency}")
        print(f"  Символ: {symbol}   ТФ: {tf}")
        print(f"  Спред сейчас: {info.spread} пунктов (макс. {cfg['max_spread_points']})")
        print("  Лот: не фиксирован — размер позиции выбираете сами")
        print(f"  Смещение времени сервера: UTC+{offset}")
        setups = [n for n, s in cfg["strategy"]["setups"].items() if s.get("enabled", True)]
        print(f"  Активные сетапы: {', '.join(setups)}")
        xm_cfg = cfg.get("cross_market") or {}
        if xm_cfg.get("enabled"):
            xm_list = ", ".join(
                f"{key.upper()} {spec.get('symbol', '?')} {spec.get('timeframe', 'M15')}"
                for key, spec in (xm_cfg.get("series") or {}).items()
            )
            print(f"  Кросс-рынок: включён ({xm_list})")
        else:
            print("  Кросс-рынок: выключен")
        print(f"  Журнал: {journal_path}")
        telegram_cfg = cfg.get("telegram", {}) or {}
        if bot.enabled:
            username = bot.prepare()
            handle = f" @{username}" if username else ""
            print(f"  Telegram: включён{handle}, активных пользователей: {len(bot.store.active())}")
            if bot.admin_chat_id:
                print(f"  Telegram: админ {bot.admin_chat_id}")
        elif telegram_cfg.get("enabled") and telegram_cfg.get("token"):
            print("  Telegram: включён, но не настроен — запустите python telegram_setup.py")
        else:
            print("  Telegram: выключен")
        print("=" * 66)
        print("  Ожидание закрытия баров M5... Ctrl+C для выхода.\n")

        if not once and bot.enabled and telegram_cfg.get("notify_startup", True):
            lang = bot.admin_lang()
            startup = [
                i18n.t(lang, "startup_title"),
                i18n.t(lang, "startup_setups", setups=", ".join(setups)),
                i18n.t(lang, "startup_wait"),
            ]
            bot.notify_admin("\n".join(startup))

        last_bar_time = None
        while True:
            if not once:
                try:
                    bot.poll()
                except Exception as exc:  # noqa: BLE001 - команды не должны ронять цикл
                    print(f"[telegram] ошибка опроса: {exc}")
            try:
                df = get_closed_rates(symbol, tf, history_bars)
                if df.empty:
                    time.sleep(poll)
                    continue
                offset = server_utc_offset_hours(symbol)

                # Обновляем ранее выданные сигналы по новым барам.
                for closed in journal.update(df, offset, point=float(info.point)):
                    sign = "+" if closed["result_r"] >= 0 else ""
                    print(
                        f"[журнал] {closed['direction']} {closed['setup_label']} закрыт "
                        f"по {closed['reason']}: {sign}{closed['result_r']:.2f}R "
                        f"({sign}{closed['result_points']:.2f} $ по цене) | "
                        f"{closed['tp_note']}"
                    )
                    bot.broadcast(lambda lang: format_close_message(closed, lang))

                bar_time = pd.Timestamp(df["time"].iloc[-1])
                if last_bar_time is None or bar_time != last_bar_time:
                    is_first = last_bar_time is None
                    last_bar_time = bar_time
                    if is_first and not once:
                        print(f"[{bar_time:%d.%m %H:%M}] старт: ждём следующий закрытый бар M5")
                        time.sleep(poll)
                        continue
                    xmarket = load_xmarket(cfg)
                    prepared = prepare(df, cfg, xmarket=xmarket)
                    tick = get_tick(symbol)
                    if tick is None or tick.time <= 0:
                        print(f"[{bar_time:%d.%m %H:%M}] рынок закрыт")
                    else:
                        spread = (tick.ask - tick.bid) / float(info.point)
                        utc_hm = (bar_time - pd.Timedelta(hours=offset)).strftime("%H:%M")
                        print(
                            f"[{bar_time:%d.%m %H:%M} сервер / {utc_hm} UTC] "
                            f"цена {tick.bid:.2f}/{tick.ask:.2f}  спред {spread:.0f}"
                        )

                        setup_name = str(prepared["setup"].iloc[-1])
                        if setup_name:
                            direction = cfg["strategy"]["setups"][setup_name].get(
                                "direction", "BUY"
                            )
                            entry = tick.ask if direction == "BUY" else tick.bid
                            sig = signal_at(
                                prepared, len(prepared) - 1, cfg, entry_price=entry
                            )
                        else:
                            sig = None

                        if sig is not None:
                            sig.spread_points = spread
                            skip_reason = None
                            max_risk_price = float(cfg.get("max_risk_price", 0.0) or 0.0)
                            if spread > float(cfg["max_spread_points"]):
                                skip_reason = f"спред {spread:.0f} > {cfg['max_spread_points']}"
                            elif max_risk_price > 0 and sig.risk_price > max_risk_price:
                                skip_reason = (
                                    f"SL {sig.risk_price:.1f}$ по цене "
                                    f"> {max_risk_price:.1f}$"
                                )
                            else:
                                signal_id = f"{sig.time:%Y%m%d_%H%M}_{sig.setup}"
                                if journal.has_signal(signal_id):
                                    skip_reason = "сигнал уже был выдан"

                            if skip_reason is None:
                                now_server = datetime.now(timezone.utc) + pd.Timedelta(hours=offset)
                                today_count = _today_server_signals(journal, now_server)
                                last = journal.load()
                                open_count = (
                                    int((last["status"] == "OPEN").sum())
                                    if not last.empty
                                    else 0
                                )
                                max_per_day = int(cfg["strategy"]["max_signals_per_day"])
                                cooldown_min = int(cfg["strategy"]["cooldown_bars"]) * 5

                                if today_count >= max_per_day:
                                    skip_reason = "достигнут лимит сигналов на день"
                                elif open_count > 0:
                                    skip_reason = "предыдущий сигнал ещё активен"
                                else:
                                    ref_time = None
                                    if not last.empty:
                                        times = pd.to_datetime(
                                            last["bar_time_server"], errors="coerce"
                                        ).dropna()
                                        if len(times):
                                            ref_time = times.max()
                                        closed = last[last["status"] == "CLOSED"]
                                        if not closed.empty:
                                            exit_utc = pd.to_datetime(
                                                closed["exit_time_utc"], errors="coerce"
                                            ).dropna()
                                            if len(exit_utc):
                                                exit_server = exit_utc.max() + pd.Timedelta(
                                                    hours=offset
                                                )
                                                ref_time = (
                                                    exit_server
                                                    if ref_time is None
                                                    else max(ref_time, exit_server)
                                                )
                                    if ref_time is not None:
                                        delta_min = (
                                            bar_time - ref_time
                                        ).total_seconds() / 60
                                        if delta_min < cooldown_min:
                                            skip_reason = (
                                                f"пауза после сделки "
                                                f"({delta_min:.0f} < {cooldown_min} мин)"
                                            )

                            if skip_reason:
                                print(f"  [пропуск] {sig.setup_label}: {skip_reason}")
                            else:
                                journal.add(sig, offset)
                                print(format_signal(sig, offset))
                                bot.broadcast(
                                    lambda lang: format_signal_message(sig, lang)
                                )

                if once:
                    break
                time.sleep(poll)

            except MT5Error as exc:
                print(f"\n[!] Ошибка MT5: {exc}. Переподключение через 10 секунд...")
                try:
                    shutdown()
                except Exception:  # noqa: BLE001
                    pass
                time.sleep(10)
                try:
                    connect()
                    ensure_symbol(symbol)
                    offset = server_utc_offset_hours(symbol)
                except MT5Error as exc2:
                    print(f"[!] Переподключиться не удалось: {exc2}")
                if once:
                    break
            except KeyboardInterrupt:
                print("\nОстановлено пользователем.")
                break
            except Exception as exc:  # noqa: BLE001 - цикл не должен падать молча
                _log_error(exc, "цикл")
                print(
                    f"[!] Неожиданная ошибка: {exc} — продолжаю через 5 с "
                    "(подробности в data/live_error.log)"
                )
                if once:
                    break
                time.sleep(5)
    finally:
        try:
            shutdown()
        except Exception:  # noqa: BLE001
            pass
