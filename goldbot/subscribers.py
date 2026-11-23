"""Хранилище пользователей бота (белый список) в SQLite.

База — обычный файл (по умолчанию data/goldbot.db), без сервера и пароля.
Статусы:
    active  — получает сигналы;
    paused  — сам отключил сигналы командой /stop;
    blocked — знает бота, но доступа нет (по умолчанию для всех новых).

Админ добавляет доступ командой /allow <id|@username>. Пока пользователь
не добавлен, бот не отвечает ему и не присылает сигналы.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ACTIVE = "active"
PAUSED = "paused"
BLOCKED = "blocked"

STATUSES = (ACTIVE, PAUSED, BLOCKED)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id      TEXT PRIMARY KEY,
    username     TEXT NOT NULL DEFAULT '',
    name         TEXT NOT NULL DEFAULT '',
    lang         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'blocked',
    started      INTEGER NOT NULL DEFAULT 0,
    created_utc  TEXT NOT NULL,
    last_seen_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_username ON users (username COLLATE NOCASE);
CREATE TABLE IF NOT EXISTS bot_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class SubscriberStore:
    """SQLite-хранилище пользователей. Ошибки БД не должны ронять бота."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._ensure_columns()
        self._migrate_json()

    def _ensure_columns(self) -> None:
        """Добавляет started в старую базу и считает старых пользователей живыми."""
        cols = {row["name"] for row in self._conn.execute("PRAGMA table_info(users)")}
        if "started" not in cols:
            self._conn.execute(
                "ALTER TABLE users ADD COLUMN started INTEGER NOT NULL DEFAULT 0"
            )
            self._conn.execute("UPDATE users SET started = 1")
            self._conn.commit()

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # ------------------------------------------------------------- состояние
    def _get_state(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM bot_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else default

    def _set_state(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)",
            (key, str(value)),
        )
        self._conn.commit()

    # ------------------------------------------------------------- миграция
    def _migrate_json(self) -> None:
        """Один раз переносит старый data/subscribers.json в SQLite."""
        if self._get_state("json_migrated"):
            return
        legacy = self.path.parent / "subscribers.json"
        if legacy.exists():
            try:
                raw = json.loads(legacy.read_text(encoding="utf-8-sig"))
                for key, user in (raw.get("users") or {}).items():
                    if not isinstance(user, dict):
                        continue
                    status = str(user.get("status") or BLOCKED)
                    if status not in STATUSES:
                        status = BLOCKED
                    self.upsert(
                        key,
                        username=user.get("username", ""),
                        name=user.get("name", ""),
                        lang=user.get("lang", ""),
                        status=status,
                        started=1,
                    )
                last = int(raw.get("last_update_id", 0) or 0)
                if last:
                    self.set_last_update_id(last)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        self._set_state("json_migrated", "1")

    # ------------------------------------------------------------- пользователи
    def users(self) -> dict[str, dict]:
        rows = self._conn.execute("SELECT * FROM users").fetchall()
        return {str(row["chat_id"]): dict(row) for row in rows}

    def get(self, chat_id: str | int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM users WHERE chat_id = ?", (str(chat_id),)
        ).fetchone()
        return dict(row) if row else None

    def find(self, ref: str) -> dict | None:
        """Находит пользователя по числовому ID или @username."""
        value = str(ref or "").strip()
        if not value:
            return None
        if value.startswith("@"):
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
                (value[1:],),
            ).fetchone()
        elif value.isdigit():
            row = self._conn.execute(
                "SELECT * FROM users WHERE chat_id = ?", (value,)
            ).fetchone()
        else:
            row = self._conn.execute(
                "SELECT * FROM users WHERE username = ? COLLATE NOCASE", (value,)
            ).fetchone()
        return dict(row) if row else None

    def upsert(self, chat_id: str | int, **fields) -> dict:
        key = str(chat_id)
        now = _now()
        if self.get(key) is None:
            status = str(fields.get("status") or BLOCKED)
            if status not in STATUSES:
                status = BLOCKED
            self._conn.execute(
                "INSERT INTO users "
                "(chat_id, username, name, lang, status, started, created_utc, last_seen_utc) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    str(fields.get("username") or ""),
                    str(fields.get("name") or ""),
                    str(fields.get("lang") or ""),
                    status,
                    1 if fields.get("started") else 0,
                    now,
                    now,
                ),
            )
        else:
            updates = {
                k: v
                for k, v in fields.items()
                if v is not None and k in ("username", "name", "lang", "status", "started")
            }
            updates["last_seen_utc"] = now
            sets = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [key]
            self._conn.execute(f"UPDATE users SET {sets} WHERE chat_id = ?", values)
        self._conn.commit()
        return self.get(key) or {}

    def set_status(self, chat_id: str | int, status: str) -> dict | None:
        if status not in STATUSES:
            raise ValueError(f"Неизвестный статус: {status}")
        if self.get(chat_id) is None:
            return None
        return self.upsert(chat_id, status=status)

    def set_lang(self, chat_id: str | int, lang: str) -> dict | None:
        if self.get(chat_id) is None:
            return None
        return self.upsert(chat_id, lang=lang)

    def remove(self, chat_id: str | int) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM users WHERE chat_id = ?", (str(chat_id),)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    def active(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM users WHERE status = ?", (ACTIVE,)
        ).fetchall()
        return [dict(row) for row in rows]

    def blocked(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM users WHERE status = ?", (BLOCKED,)
        ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------- Telegram
    @property
    def last_update_id(self) -> int:
        try:
            return int(self._get_state("last_update_id", "0") or 0)
        except ValueError:
            return 0

    def set_last_update_id(self, update_id: int) -> None:
        self._set_state("last_update_id", str(int(update_id)))
