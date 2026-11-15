"""Исполнитель: читает журнал сигналов и ставит сделки на счёте MT5.

Отдельный процесс. Анализатор (`run_live.py`) пишет сигналы в
`data/signals.csv`, исполнитель (`run_executor.py`) их читает и открывает
рыночные ордера с фиксированным лотом из `config.json → execution.lot`.

Что делает:
  * берёт только новые сигналы (по `created_utc`, не старше N минут);
  * открывает не больше `max_positions` позиций одновременно;
  * ставит SL и TP сразу в заявке, дальше позицию не трогает;
  * пишет свои сделки в `data/executor.csv` и строки в `data/executor.log`;
  * после перезапуска подхватывает уже открытые позиции по `magic`.

Чего не делает: не считает лот от риска, не двигает стоп, не закрывает
позиции вручную — всем этим управляет брокер по SL/TP.
"""
from __future__ import annotations

import getpass
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from .config import DEFAULT_CONFIG_PATH, ROOT, load_config
from .data import (
    connect,
    ensure_symbol,
    forget_account,
    get_account_info,
    load_account,
    save_account,
    shutdown,
)

STATE_COLUMNS = [
    "signal_id",
    "ticket",
    "opened_utc",
    "setup",
    "direction",
    "lot",
    "signal_entry",
    "open_price",
    "sl",
    "tp",
    "risk_price",
    "status",
    "close_utc",
    "close_price",
    "profit",
    "close_reason",
    "result_r",
]

# Коды, при которых имеет смысл повторить заявку (цена успела измениться).
RETRY_CODES = {10004, 10006, 10008, 10021, 10030}
MARKET_CLOSED_CODES = {10018, 10017, 10019}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class Executor:
    def __init__(self, cfg: dict, dry_run: bool = False) -> None:
        self.cfg = cfg
        ex = cfg.get("execution") or {}
        self.dry_run = dry_run
        self.symbol = str(ex.get("symbol") or cfg["symbol"])
        self.lot = float(ex.get("lot", 0.01))
        self.magic = int(ex.get("magic", 770001))
        self.max_positions = max(1, int(ex.get("max_positions", 1)))
        self.max_age = float(ex.get("max_signal_age_minutes", 3))
        self.max_slip = float(ex.get("max_entry_slip_points", 40))
        self.deviation = int(ex.get("deviation_points", 20))
        self.poll = float(ex.get("poll_seconds", 2))
        self.state_path = ROOT / str(ex.get("state_path", "data/executor.csv"))
        self.log_path = ROOT / str(ex.get("log_path", "data/executor.log"))
        self.journal_path = ROOT / str(cfg["live"].get("journal_path", "data/signals.csv"))
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.cfg_path = DEFAULT_CONFIG_PATH
        try:
            self._cfg_mtime = self.cfg_path.stat().st_mtime
        except OSError:
            self._cfg_mtime = 0.0
        self.info = ensure_symbol(self.symbol)
        self.point = float(self.info.point)

    # --- журнал и состояние ------------------------------------------------
    def log(self, message: str) -> None:
        line = f"[{_fmt(_now_utc())}] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def load_state(self) -> pd.DataFrame:
        if not self.state_path.exists():
            return pd.DataFrame({col: pd.Series(dtype=object) for col in STATE_COLUMNS})
        try:
            df = pd.read_csv(self.state_path, encoding="utf-8-sig")
        except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
            return pd.DataFrame({col: pd.Series(dtype=object) for col in STATE_COLUMNS})
        for col in STATE_COLUMNS:
            if col not in df.columns:
                df[col] = ""
        return df[STATE_COLUMNS].astype(object)

    def save_state(self, df: pd.DataFrame) -> None:
        tmp = self.state_path.with_suffix(".tmp")
        df.to_csv(tmp, index=False, encoding="utf-8-sig")
        tmp.replace(self.state_path)

    def journal(self) -> pd.DataFrame:
        """Чтение журнала сигналов с повторами: анализатор может писать в него."""
        for _ in range(5):
            try:
                df = pd.read_csv(self.journal_path, encoding="utf-8-sig")
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError):
                time.sleep(0.2)
                continue
            if {"id", "direction", "entry", "sl", "tp"}.issubset(df.columns):
                return df
            time.sleep(0.2)
        return pd.DataFrame()

    # --- позиции брокера ---------------------------------------------------
    def positions(self) -> dict[int, object]:
        result = {}
        for pos in mt5.positions_get(symbol=self.symbol) or ():
            if int(pos.magic) == self.magic:
                result[int(pos.ticket)] = pos
        return result

    def account(self):
        return get_account_info()

    # --- открытие ----------------------------------------------------------
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
        steps = round(lot / step)
        value = round(steps * step, 8)
        return max(min_lot, min(value, max_lot))

    def open_signal(self, row: pd.Series) -> bool:
        direction = str(row["direction"]).upper()
        is_buy = direction == "BUY"
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None or (tick.ask <= 0 and tick.bid <= 0):
            self.log(f"{row['id']}: нет тика — пропуск")
            return False

        price = float(tick.ask if is_buy else tick.bid)
        entry = float(row["entry"])
        # Сравниваем движение цены по bid с обеих сторон: так спред не мешает
        # отличить «цена ушла» от «просто широкий спред».
        ref = float(tick.bid)
        slip_points = (ref - entry) / self.point if is_buy else (entry - ref) / self.point
        if slip_points > self.max_slip:
            self.log(
                f"{row['id']}: цена ушла на {slip_points:.0f} пт от сигнала "
                f"({entry:.2f} -> {ref:.2f}), больше лимита {self.max_slip:.0f} — пропуск"
            )
            return False

        sl = float(row["sl"])
        tp = float(row["tp"])
        raw_risk = row.get("risk_price")
        risk_price = (
            float(raw_risk)
            if pd.notna(raw_risk) and float(raw_risk) > 0
            else abs(entry - sl)
        )
        if risk_price <= 0 or abs(entry - sl) <= 0:
            self.log(f"{row['id']}: некорректный SL — пропуск")
            return False

        lot = self._round_lot(self.lot)
        margin = mt5.order_calc_margin(
            mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            self.symbol,
            lot,
            price,
        )
        account = self.account()
        free = float(getattr(account, "margin_free", 0.0) or 0.0) if account else 0.0
        if margin is not None and free and margin > free * 0.9:
            self.log(
                f"{row['id']}: не хватает маржи — нужно {margin:.2f}, свободно {free:.2f} — пропуск"
            )
            return False

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "deviation": self.deviation,
            "magic": self.magic,
            "comment": f"goldbot {row['id']}"[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._filling_mode(),
        }

        if self.dry_run:
            check = mt5.order_check(request)
            ok = check is not None and check.retcode == 0
            self.log(
                f"[DRY] {row['id']}: {direction} {lot} {self.symbol} по {price:.2f} "
                f"SL {sl:.2f} TP {tp:.2f} | order_check={'OK' if ok else getattr(check, 'comment', '?')}"
            )
            self._append_state(
                {
                    "signal_id": str(row["id"]),
                    "ticket": 0,
                    "opened_utc": _fmt(_now_utc()),
                    "setup": str(row.get("setup", "")),
                    "direction": direction,
                    "lot": lot,
                    "signal_entry": round(entry, 2),
                    "open_price": round(price, 2),
                    "sl": round(sl, 2),
                    "tp": round(tp, 2),
                    "risk_price": round(risk_price, 4),
                    "status": "DRYRUN",
                    "close_utc": "",
                    "close_price": "",
                    "profit": "",
                    "close_reason": "",
                    "result_r": "",
                }
            )
            return ok

        result = None
        for attempt in range(3):
            result = mt5.order_send(request)
            if result is None:
                self.log(f"{row['id']}: order_send вернул None ({mt5.last_error()})")
                time.sleep(0.5)
                continue
            if result.retcode == mt5.TRADE_RETCODE_DONE:
                break
            if result.retcode in MARKET_CLOSED_CODES:
                self.log(f"{row['id']}: рынок закрыт или торговля запрещена ({result.retcode})")
                return False
            if result.retcode in RETRY_CODES and attempt < 2:
                tick = mt5.symbol_info_tick(self.symbol)
                if tick is not None:
                    request["price"] = float(tick.ask if is_buy else tick.bid)
                time.sleep(0.4)
                continue
            break

        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            code = getattr(result, "retcode", "?")
            comment = getattr(result, "comment", "")
            self.log(f"{row['id']}: ордер не принят — {code} {comment}")
            return False

        fill = float(result.price or price)
        self.log(
            f"ОТКРЫТО #{result.order} ticket {result.order}: {direction} {lot} {self.symbol} "
            f"по {fill:.2f} (сигнал {entry:.2f}) SL {sl:.2f} TP {tp:.2f} "
            f"| {row.get('setup', '')}"
        )
        self._append_state(
            {
                "signal_id": str(row["id"]),
                "ticket": int(result.order),
                "opened_utc": _fmt(_now_utc()),
                "setup": str(row.get("setup", "")),
                "direction": direction,
                "lot": lot,
                "signal_entry": round(entry, 2),
                "open_price": round(fill, 2),
                "sl": round(sl, 2),
                "tp": round(tp, 2),
                "risk_price": round(risk_price, 4),
                "status": "OPEN",
                "close_utc": "",
                "close_price": "",
                "profit": "",
                "close_reason": "",
                "result_r": "",
            }
        )
        return True

    def _append_state(self, row: dict) -> None:
        df = self.load_state()
        row_df = pd.DataFrame([row], columns=STATE_COLUMNS)
        df = row_df if df.empty else pd.concat([df, row_df], ignore_index=True)
        self.save_state(df)

    # --- сверка закрытых ---------------------------------------------------
    def sync(self) -> None:
        df = self.load_state()
        if df.empty:
            return
        open_now = self.positions()
        changed = False
        for idx, row in df.iterrows():
            if str(row["status"]) != "OPEN":
                continue
            ticket = int(float(row["ticket"] or 0))
            if not ticket:
                continue
            if ticket in open_now:
                continue

            deals = mt5.history_deals_get(position=ticket) or ()
            profit = sum(float(d.profit) + float(d.swap) + float(d.commission) for d in deals)
            close_price = ""
            reason = "OTHER"
            for d in deals:
                if int(getattr(d, "entry", 0)) == mt5.DEAL_ENTRY_OUT:
                    close_price = f"{float(d.price):.2f}"
                    deal_reason = int(getattr(d, "reason", -1))
                    if deal_reason == getattr(mt5, "DEAL_REASON_SL", -100):
                        reason = "SL"
                    elif deal_reason == getattr(mt5, "DEAL_REASON_TP", -100):
                        reason = "TP"
                    elif deal_reason == getattr(mt5, "DEAL_REASON_SO", -100):
                        reason = "SO"
            raw_risk = row["risk_price"]
            raw_lot = row["lot"]
            risk_price = float(raw_risk) if pd.notna(raw_risk) else 0.0
            lot = float(raw_lot) if pd.notna(raw_lot) else 0.0
            contract = float(self.info.trade_contract_size or 100.0)
            risk_money = risk_price * contract * lot
            result_r = profit / risk_money if risk_money > 0 else 0.0

            df.at[idx, "status"] = "CLOSED"
            df.at[idx, "close_utc"] = _fmt(_now_utc())
            df.at[idx, "close_price"] = close_price
            df.at[idx, "profit"] = round(profit, 2)
            df.at[idx, "close_reason"] = reason
            df.at[idx, "result_r"] = round(result_r, 2)
            changed = True
            sign = "+" if profit >= 0 else ""
            self.log(
                f"ЗАКРЫТО ticket {ticket}: {row['direction']} {row['setup']} по {reason} "
                f"{sign}{profit:.2f} $ ({sign}{result_r:.2f}R)"
            )
        if changed:
            self.save_state(df)

    # --- главный цикл ------------------------------------------------------
    def reload_settings(self) -> None:
        """Подхватывает изменения лота и лимитов без перезапуска.

        Лот можно поменять из Telegram командой /lot, вписать в config.json
        руками или через set_lot.py — исполнитель увидит новое значение в
        течение пары секунд.
        """
        try:
            mtime = self.cfg_path.stat().st_mtime
        except OSError:
            return
        if mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        try:
            cfg = load_config(self.cfg_path)
        except Exception as exc:  # noqa: BLE001 - битый файл не должен ронять цикл
            self.log(f"config.json не прочитан ({exc}) — работаю на старых настройках")
            return

        ex = cfg.get("execution") or {}
        try:
            new_lot = float(ex.get("lot", self.lot))
            new_max = max(1, int(ex.get("max_positions", self.max_positions)))
            new_age = float(ex.get("max_signal_age_minutes", self.max_age))
        except (TypeError, ValueError):
            self.log("в config.json некорректные execution.* — оставляю как было")
            return

        if new_lot != self.lot:
            self.log(f"лот изменён: {self.lot:g} -> {new_lot:g}")
            self.lot = new_lot
        if new_max != self.max_positions:
            self.log(f"макс. позиций: {self.max_positions} -> {new_max}")
            self.max_positions = new_max
        if new_age != self.max_age:
            self.log(f"возраст сигнала: {self.max_age:g} -> {new_age:g} мин")
            self.max_age = new_age

    def pending(self) -> list[pd.Series]:
        journal = self.journal()
        if journal.empty:
            return []
        state = self.load_state()
        seen = set(state["signal_id"].astype(str)) if not state.empty else set()
        now = _now_utc()
        result = []
        for _, row in journal.iterrows():
            signal_id = str(row.get("id", ""))
            if not signal_id or signal_id in seen:
                continue
            created = pd.to_datetime(row.get("created_utc"), utc=True, errors="coerce")
            if pd.isna(created):
                continue
            age_min = (now - created.to_pydatetime()).total_seconds() / 60
            if age_min > self.max_age:
                continue
            result.append(row)
        return result

    def tick_once(self) -> None:
        self.sync()
        if len(self.positions()) >= self.max_positions:
            return
        for row in self.pending():
            if len(self.positions()) >= self.max_positions:
                break
            self.open_signal(row)

    def run(self) -> None:
        account = self.account()
        mode = "DRY-RUN (ордера не отправляются)" if self.dry_run else "БОЕВОЙ (демо/реальный счёт)"
        self.log("=" * 60)
        self.log("  GOLDBOT EXECUTOR — исполнение сигналов")
        self.log("=" * 60)
        if account:
            self.log(
                f"  счёт {account.login} ({account.server}), баланс {account.balance:.2f} "
                f"{account.currency}, плечо 1:{account.leverage}"
            )
        self.log(f"  режим: {mode}")
        self.log(
            f"  лот: {self.lot} (фиксированный) | макс. позиций: {self.max_positions} | "
            f"magic: {self.magic}"
        )
        self.log(f"  журнал сигналов: {self.journal_path}")
        self.log(f"  сигналы старше {self.max_age:g} мин игнорируются")
        self.log("-" * 60)
        while True:
            try:
                self.tick_once()
            except Exception as exc:  # noqa: BLE001 - процесс не должен падать
                self.log(f"ОШИБКА: {type(exc).__name__}: {exc}")
            time.sleep(self.poll)


def ask_account(
    cfg: dict,
    login: str | int | None = None,
    password: str | None = None,
    server: str | None = None,
    terminal_path: str | None = None,
    ask_remember: bool = True,
) -> dict:
    """Выясняет, под каким счётом работать.

    Порядок: аргументы командной строки → data/account.json → вопрос в консоли.
    Пустой логин = использовать счёт, в который уже залогинен терминал.
    """
    ex = cfg.get("execution") or {}
    default_server = str(ex.get("server") or "")
    path = terminal_path or str(ex.get("terminal_path") or "") or None

    saved = load_account()
    if not login and saved:
        print(
            f"Счёт из data/account.json: {saved['login']} ({saved['server']}). "
            "Чтобы сменить — удалите файл или запустите с --forget."
        )
        return {**saved, "path": path}

    if login:
        server = server or default_server
        if not server:
            try:
                server = input("Сервер (например MetaQuotes-Demo): ").strip()
            except EOFError:
                server = ""
        if not password:
            try:
                password = getpass.getpass("Пароль MT5 (ввод не отображается): ")
            except EOFError:
                password = ""
        if not (password and server):
            raise SystemExit("Не хватает пароля или сервера для входа в счёт.")
        return {"login": int(login), "password": password, "server": server, "path": path}

    try:
        print()
        print("Вход в торговый счёт MT5")
        print("  Enter без логина — использовать счёт, открытый в терминале.")
        raw_login = input("Логин MT5: ").strip()
    except EOFError:
        raw_login = ""

    if not raw_login:
        return {"path": path}

    try:
        password = getpass.getpass("Пароль MT5 (ввод не отображается): ")
        prompt_server = f"Сервер [{default_server}]: " if default_server else "Сервер (например MetaQuotes-Demo): "
        server = input(prompt_server).strip() or default_server
    except EOFError:
        return {"path": path}

    if not (password and server):
        print("Пароль или сервер пустые — использую счёт терминала.")
        return {"path": path}

    if ask_remember:
        try:
            answer = input("Запомнить в data/account.json? (y/n): ").strip().lower()
        except EOFError:
            answer = "n"
        if answer in ("y", "yes", "д", "да"):
            file = save_account(raw_login, password, server)
            print(f"Сохранено: {file} (пароль хранится в открытом виде)")

    return {"login": int(raw_login), "password": password, "server": server, "path": path}


def ask_lot(default: float) -> float:
    """Спрашивает лот в консоли. Enter — значение из config.json."""
    print()
    print("Настройки исполнения")
    try:
        raw = input(f"  Лот (Enter — оставить {default:g}): ").strip().replace(",", ".")
    except EOFError:
        return default
    if not raw:
        return default
    try:
        lot = float(raw)
    except ValueError:
        print(f"  Не понял «{raw}» — беру {default:g}")
        return default
    if not (0.01 <= lot <= 100.0):
        print(f"  Лот {lot:g} вне диапазона 0.01–100 — беру {default:g}")
        return default
    return round(round(lot / 0.01) * 0.01, 2)


def remember_lot(lot: float) -> None:
    """Сохраняет введённый лот в config.json, чтобы он стал значением по умолчанию."""
    try:
        raw = json.loads(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    node = raw.get("execution")
    if not isinstance(node, dict):
        node = {}
        raw["execution"] = node
    node["lot"] = lot
    try:
        DEFAULT_CONFIG_PATH.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"  Не удалось сохранить лот в config.json: {exc}")


def main(
    dry_run: bool = False,
    once: bool = False,
    lot: float | None = None,
    login: str | int | None = None,
    password: str | None = None,
    server: str | None = None,
    terminal_path: str | None = None,
    forget: bool = False,
) -> int:
    cfg = load_config()
    ex = cfg.get("execution") or {}
    if not ex.get("enabled"):
        print("Исполнение выключено: config.json → execution.enabled = false")
        return 1
    if lot is not None:
        cfg["execution"]["lot"] = lot

    if forget:
        removed = forget_account()
        print("Данные счёта удалены." if removed else "Файла data/account.json нет.")

    creds = ask_account(
        cfg,
        login=login,
        password=password,
        server=server,
        terminal_path=terminal_path,
    )

    if lot is None:
        default_lot = float(cfg["execution"].get("lot", 0.01))
        chosen = ask_lot(default_lot)
        cfg["execution"]["lot"] = chosen
        if abs(chosen - default_lot) > 1e-9:
            remember_lot(chosen)
            print(f"  Лот {chosen:g} сохранён в config.json как новый по умолчанию.")
    try:
        connect(
            path=creds.get("path"),
            login=creds.get("login"),
            password=creds.get("password"),
            server=creds.get("server"),
            use_saved=False,
        )
    except Exception as exc:  # noqa: BLE001 - показываем причину и выходим
        print(f"Ошибка подключения: {exc}")
        return 1

    try:
        executor = Executor(cfg, dry_run=dry_run)
        if once:
            executor.sync()
            pending = executor.pending()
            if not pending:
                print("Новых сигналов нет.")
            for row in pending:
                executor.open_signal(row)
            return 0
        executor.run()
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
    finally:
        shutdown()
    return 0
