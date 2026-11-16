"""Работа с MetaTrader 5: подключение, загрузка котировок, кеш истории."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

import MetaTrader5 as mt5
import pandas as pd

from .config import DATA_DIR

TF_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}

TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400}


class MT5Error(RuntimeError):
    pass


ACCOUNT_FILE = DATA_DIR / "account.json"


def load_account(path: str | Path | None = None) -> dict:
    """Читает сохранённые логин/пароль/сервер из data/account.json."""
    import json

    file = Path(path) if path else ACCOUNT_FILE
    if not file.exists():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not data.get("login") or not data.get("password") or not data.get("server"):
        return {}
    return data


def save_account(login: int | str, password: str, server: str, path: str | Path | None = None) -> Path:
    """Сохраняет данные счёта в data/account.json (пароль в открытом виде)."""
    import json

    file = Path(path) if path else ACCOUNT_FILE
    file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "login": int(login),
        "password": password,
        "server": server,
    }
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return file


def forget_account(path: str | Path | None = None) -> bool:
    file = Path(path) if path else ACCOUNT_FILE
    if file.exists():
        file.unlink()
        return True
    return False


def known_servers() -> set[str]:
    """Имена серверов, которые уже есть в терминале.

    MT5 хранит их папками в `bases/` и списком в `config/servers.dat`.
    Нужно, чтобы отсеять опечатку в имени сервера до попытки входа: неверное
    имя вешает `mt5.login()` на минуты и может отключить терминал от счёта.
    """
    info = mt5.terminal_info()
    if info is None:
        return set()
    data_path = Path(info.data_path)
    names: set[str] = set()
    bases = data_path / "bases"
    if bases.is_dir():
        for child in bases.iterdir():
            if child.is_dir() and child.name not in {"Chats", "Custom", "Default", "signals"}:
                names.add(child.name)
    servers_dat = data_path / "config" / "servers.dat"
    if servers_dat.exists():
        try:
            raw = servers_dat.read_bytes()
        except OSError:
            raw = b""
        names.update(_servers_from_dat(raw))
    return names


def _servers_from_dat(raw: bytes) -> set[str]:
    """Достаёт имена серверов из бинарного servers.dat (utf-16le и ascii)."""
    found: set[str] = set()
    try:
        text = raw.decode("utf-16-le", errors="ignore")
    except Exception:  # noqa: BLE001
        text = ""
    for chunk in text.replace("\x00", "\n").split("\n"):
        chunk = chunk.strip()
        if 3 <= len(chunk) <= 40 and all(c.isprintable() for c in chunk):
            if any(sep in chunk for sep in ("-", ".", "_")) and " " not in chunk:
                found.add(chunk)
    return found


def _login_with_timeout(
    login: int | str, password: str, server: str, timeout: float = 30.0
) -> None:
    """Вход в счёт с жёстким ограничением по времени.

    `mt5.login()` — блокирующий вызов C-библиотеки, который не отпускает GIL:
    при неверном имени сервера он вешает весь процесс на минуты, и никакой
    поток-сторож не сработает. Поэтому вход выполняется в отдельном процессе,
    который можно убить по таймауту. Пароль передаётся через stdin, чтобы не
    светиться в списке процессов.
    """
    import subprocess
    import sys

    helper = (
        "import sys, MetaTrader5 as mt5\n"
        "password = sys.stdin.readline().rstrip('\\n')\n"
        "if not mt5.initialize():\n"
        "    print('FAIL')\n"
        "    print(mt5.last_error())\n"
        "    raise SystemExit(0)\n"
        "ok = mt5.login(int(sys.argv[1]), password=password, server=sys.argv[2])\n"
        "print('OK' if ok else 'FAIL')\n"
        "print(mt5.last_error())\n"
    )

    try:
        proc = subprocess.run(
            [sys.executable, "-c", helper, str(login), str(server)],
            input=str(password) + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        raise MT5Error(
            f"Вход в счёт {login} ({server}) не завершился за {timeout:.0f} с. "
            "Скорее всего неверно имя сервера — оно должно точно совпадать с "
            "терминалом (например MetaQuotes-Demo)."
        ) from None

    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if lines and lines[0] == "OK":
        return
    detail = lines[1] if len(lines) > 1 else (proc.stderr or "").strip()
    raise MT5Error(
        f"Не удалось войти в счёт {login} ({server}): {detail}. "
        "Проверьте логин, пароль и имя сервера."
    )


def connect(
    path: str | None = None,
    login: int | str | None = None,
    password: str | None = None,
    server: str | None = None,
    use_saved: bool = True,
) -> None:
    """Подключается к терминалу MT5 и при необходимости входит в счёт.

    Без логина/пароля используется счёт, в который уже залогинен терминал.
    Если `use_saved` и есть `data/account.json` — вход выполняется по нему.
    """
    if login is None and use_saved:
        saved = load_account()
        if saved:
            login = saved["login"]
            password = saved["password"]
            server = saved["server"]

    kwargs = {"path": path} if path else {}
    if not mt5.initialize(**kwargs):
        error = mt5.last_error()
        hint = (
            "Проверьте: 1) терминал MetaTrader 5 установлен и это 64-битная версия; "
            "2) терминал запущен и в нём включена алгоритмическая торговля; "
            "3) если терминал стоит в нестандартной папке — укажите путь: "
            'python run_live.py --terminal "C:\\путь\\terminal64.exe" '
            '(для исполнителя: run_executor.py --terminal ...).'
        )
        raise MT5Error(f"Не удалось подключиться к MetaTrader 5: {error}. {hint}")

    if login and password and server:
        known = known_servers()
        if known and str(server) not in known:
            sample = ", ".join(sorted(known)[:10])
            raise MT5Error(
                f"Сервер «{server}» не найден в терминале (есть: {sample}). "
                "Добавьте счёт в MT5 через File → Login to Trade Account и повторите, "
                "либо проверьте написание сервера."
            )
        _login_with_timeout(login, password, server)
        # Вход выполнялся в отдельном процессе — переподключаемся, чтобы
        # основной процесс увидел новый счёт.
        mt5.shutdown()
        if not mt5.initialize(**kwargs):
            raise MT5Error(
                f"Не удалось переподключиться после входа: {mt5.last_error()}"
            )
        info = mt5.account_info()
        if info is not None and int(info.login) != int(login):
            raise MT5Error(
                f"Терминал остался на счёте {info.login}, а нужен {login}. "
                "Войдите в счёт вручную в терминале MT5 и запустите снова."
            )


def shutdown() -> None:
    mt5.shutdown()


def ensure_symbol(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        raise MT5Error(
            f"Символ {symbol} не найден в Market Watch. "
            "Добавьте его вручную в терминале."
        )
    if not info.visible:
        mt5.symbol_select(symbol, True)
        info = mt5.symbol_info(symbol)
    return info


def get_rates(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    """Возвращает последние `count` баров (включая текущий формирующийся)."""
    if timeframe not in TF_MAP:
        raise MT5Error(f"Неизвестный таймфрейм: {timeframe}")

    last_error = None
    for _ in range(3):
        rates = mt5.copy_rates_from_pos(symbol, TF_MAP[timeframe], 0, count)
        if rates is not None and len(rates) > 0:
            df = pd.DataFrame(rates)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df = df[["time", "open", "high", "low", "close", "tick_volume", "spread"]]
            return df
        last_error = mt5.last_error()
        time.sleep(0.5)

    raise MT5Error(f"Не удалось получить котировки {symbol} {timeframe}: {last_error}")


def get_closed_rates(symbol: str, timeframe: str, count: int) -> pd.DataFrame:
    """Возвращает бары без текущего незакрытого бара."""
    df = get_rates(symbol, timeframe, min(count + 1, 99999))
    tick = mt5.symbol_info_tick(symbol)
    tf_sec = TF_SECONDS[timeframe]

    if tick is not None and tick.time > 0 and len(df) > 1:
        last_open = df["time"].iloc[-1]
        last_open_epoch = int(last_open.timestamp())
        if tick.time < last_open_epoch + tf_sec:
            df = df.iloc[:-1]
    elif len(df) > 1:
        # Нет тика (рынок закрыт) — на всякий случай убираем последний бар.
        df = df.iloc[:-1]

    return df.reset_index(drop=True)


def get_tick(symbol: str):
    return mt5.symbol_info_tick(symbol)


def get_symbol_info(symbol: str):
    return mt5.symbol_info(symbol)


def get_account_info():
    return mt5.account_info()


def server_utc_offset_hours(symbol: str) -> int:
    """Смещение времени сервера MT5 относительно UTC в часах."""
    tick = mt5.symbol_info_tick(symbol)
    if tick is None or tick.time <= 0:
        return 2
    return int(round((tick.time - time.time()) / 3600.0))


def to_utc(ts: pd.Timestamp | datetime, offset_hours: int) -> pd.Timestamp:
    """Переводит время сервера (naive) в UTC (naive)."""
    return pd.Timestamp(ts) - pd.Timedelta(hours=offset_hours)


def fmt_utc(ts: pd.Timestamp | datetime, offset_hours: int) -> str:
    return to_utc(ts, offset_hours).strftime("%d.%m.%Y %H:%M UTC")


def history_cache_path(symbol: str, timeframe: str) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return DATA_DIR / f"{symbol}_{timeframe}.csv"


def load_history(
    symbol: str,
    timeframe: str,
    count: int = 99999,
    refresh: bool = False,
) -> pd.DataFrame:
    """Загружает историю из MT5 и кеширует в CSV (чтобы бэктест был быстрым)."""
    cache = history_cache_path(symbol, timeframe)
    if cache.exists() and not refresh:
        df = pd.read_csv(cache, parse_dates=["time"])
        return df

    ensure_symbol(symbol)
    df = get_closed_rates(symbol, timeframe, count)
    cache.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache, index=False)
    return df
