"""Конфигурация бота: значения по умолчанию + config.json."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"

DEFAULTS: dict[str, Any] = {
    "symbol": "XAUUSD",
    "timeframe": "M5",
    "max_spread_points": 30,
    "max_risk_price": 0.0,
    "strategy": {
        "atr_period": 14,
        "rsi_period": 14,
        "adx_period": 14,
        "cooldown_bars": 6,
        "max_signals_per_day": 8,
        "trail_atr": 0.0,
        "trail_start_r": 1.0,
        "setups": {
            "dip_london": {
                "enabled": True,
                "direction": "BUY",
                "session_start_hour": 9,
                "session_end_hour": 15,
                "session_label": "Лондонская сессия",
                "trigger": "drop",
                "drop_bars": 6,
                "drop_atr": 3.0,
                "sl_atr": 1.2,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 1.7,
                "rr": 1.0,
                "time_exit_bars": 24,
                "breakeven_at_r": 0.0,
            },
            "dip_late": {
                "enabled": True,
                "direction": "BUY",
                "session_start_hour": 11,
                "session_end_hour": 15,
                "session_label": "День Лондона",
                "trigger": "drop",
                "drop_bars": 6,
                "drop_atr": 2.75,
                "sl_atr": 1.2,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 1.7,
                "rr": 1.0,
                "time_exit_bars": 24,
                "breakeven_at_r": 0.0,
            },
            "sell_noon": {
                "enabled": True,
                "direction": "SELL",
                "session_start_hour": 12,
                "session_end_hour": 13,
                "session_label": "Дневной откат",
                "trigger": "clock",
                "enter_hour": 12,
                "enter_minute": 0,
                "sl_atr": 1.5,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 2.0,
                "rr": 1.0,
                "time_exit_bars": 6,
                "breakeven_at_r": 0.0,
            },
            "rsi_ny": {
                "enabled": True,
                "direction": "BUY",
                "session_start_hour": 15,
                "session_end_hour": 18,
                "session_label": "Нью-Йоркская сессия",
                "trigger": "rsi",
                "rsi_level": 30,
                "sl_atr": 1.0,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 1.5,
                "rr": 1.5,
                "time_exit_bars": 24,
                "breakeven_at_r": 1.0,
            },
            "stretch_ny": {
                "enabled": True,
                "direction": "BUY",
                "session_start_hour": 15,
                "session_end_hour": 18,
                "session_label": "Нью-Йоркская сессия",
                "trigger": "stretch",
                "ema_period": 50,
                "stretch_atr": 2.0,
                "sl_atr": 1.2,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 1.7,
                "rr": 1.0,
                "time_exit_bars": 24,
                "breakeven_at_r": 0.0,
            },
            "sell_break": {
                "enabled": True,
                "direction": "SELL",
                "session_start_hour": 12,
                "session_end_hour": 18,
                "session_label": "Вторая половина дня",
                "trigger": "breakout",
                "breakout_bars": 7,
                "breakout_buffer_atr": 0.0,
                "trend_filter": "ema",
                "sl_atr": 1.2,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 1.7,
                "rr": 1.0,
                "time_exit_bars": 24,
                "breakeven_at_r": 0.0,
            },
            "short_rally_london": {
                "enabled": False,
                "direction": "SELL",
                "session_start_hour": 10,
                "session_end_hour": 14,
                "session_label": "Лондонская сессия",
                "trigger": "drop",
                "drop_bars": 6,
                "drop_atr": 3.0,
                "sl_atr": 1.2,
                "sl_lookback": 3,
                "sl_buffer_atr": 0.1,
                "max_sl_atr": 1.7,
                "rr": 1.0,
                "time_exit_bars": 24,
                "breakeven_at_r": 0.0,
            },
        },
    },
    "backtest": {
        "spread_points": 13,
        "initial_balance": 1000.0,
        "commission_per_lot": 0.0,
        "reference_lot": 0.01,
    },
    "live": {
        "poll_seconds": 5,
        "history_bars": 600,
        "journal_path": "data/signals.csv",
    },
    "cross_market": {
        "enabled": False,
        "minutes": 15,
        "bars": 600,
        "series": {},
    },
    "execution": {
        "enabled": False,
        "symbol": "",
        "lot": 0.01,
        "magic": 770001,
        "server": "",
        "terminal_path": "",
        "max_positions": 1,
        "max_signal_age_minutes": 3,
        "max_entry_slip_points": 40,
        "deviation_points": 20,
        "poll_seconds": 2,
        "state_path": "data/executor.csv",
        "log_path": "data/executor.log",
    },
    "telegram": {
        "enabled": False,
        "token": "",
        "chat_id": "",
        "notify_startup": True,
        "timeout_seconds": 10,
        "db_path": "data/goldbot.db",
        "default_lang": "ru",
        "poll_commands": True,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: str | Path | None = None) -> dict:
    """Читает config.json и дополняет его значениями по умолчанию."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    user_cfg: dict = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            user_cfg = json.load(fh)
    return _deep_merge(DEFAULTS, user_cfg)


def save_config(cfg: dict, path: str | Path | None = None) -> Path:
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    with cfg_path.open("w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    return cfg_path
