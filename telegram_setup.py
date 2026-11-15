"""Привязка администратора Telegram-бота и проверка отправки.

chat_id в config.json — это ID администратора: он получает сигналы,
заявки на доступ и управляет белым списком командами /allow, /deny, /remove.

Примеры:
    python telegram_setup.py            # найти чат по последним сообщениям боту
    python telegram_setup.py --wait 120 # ждать сообщение боту до 2 минут
    python telegram_setup.py --test     # отправить тестовое сообщение

Запускайте при остановленном live-боте: иначе он тоже читает сообщения.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.config import DEFAULT_CONFIG_PATH, ROOT, load_config
from goldbot.subscribers import ACTIVE, SubscriberStore
from goldbot.telegram import TelegramBot, api_call

TEST_TEXT = (
    "✅ GoldBot подключён (администратор).\n"
    "Сюда будут приходить сигналы XAUUSD M5 и результаты отслеживаемых сделок.\n"
    "Доступ: /users, /allow <id|@username>, /deny, /remove."
)


def _chats_from_updates(updates: list[dict]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for update in updates:
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
        )
        if not message:
            continue
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            continue
        name = (
            chat.get("title")
            or chat.get("username")
            or chat.get("first_name")
            or str(chat_id)
        )
        found.append((str(chat_id), str(name), str(chat.get("type", ""))))
    return found


def _pick_chat(chats: list[tuple[str, str, str]]) -> tuple[str, str, str]:
    for chat in chats:
        if chat[2] == "private":
            return chat
    return chats[0]


def _save_chat_id(chat_id: str, config_path: str | None) -> Path:
    # Если проект настроен через .env — пишем туда, иначе в config.json.
    env_path = ROOT / ".env"
    if config_path is None and env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if line.strip().startswith("TELEGRAM_CHAT_ID="):
                lines[index] = f"TELEGRAM_CHAT_ID={chat_id}"
                break
        else:
            lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
        env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return env_path

    path = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    telegram = raw.setdefault("telegram", {})
    telegram["chat_id"] = chat_id
    telegram["enabled"] = True
    path.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def _send_test(token: str, chat_id: str, timeout: float) -> bool:
    bot = TelegramBot(token=token, admin_chat_id=chat_id, enabled=True, timeout=timeout)
    return bot.send(chat_id, TEST_TEXT)


def _seed_admin(cfg: dict, chat_id: str) -> None:
    """Админ всегда активен — иначе он не получит сигналы после рассылки."""
    telegram = cfg.get("telegram", {}) or {}
    path = telegram.get("db_path") or "data/goldbot.db"
    store = SubscriberStore(ROOT / path)
    user = store.get(chat_id)
    if user is None:
        store.upsert(chat_id, name="admin", status=ACTIVE, started=1)
    elif user.get("status") != ACTIVE:
        store.set_status(chat_id, ACTIVE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Настройка Telegram-уведомлений GoldBot")
    parser.add_argument(
        "--wait", type=int, default=0, help="сколько секунд ждать сообщение боту"
    )
    parser.add_argument(
        "--test", action="store_true", help="только отправить тестовое сообщение"
    )
    parser.add_argument("--config", default=None, help="путь к config.json")
    args = parser.parse_args()

    cfg = load_config(args.config)
    telegram = cfg.get("telegram", {}) or {}
    token = str(telegram.get("token", "")).strip()
    chat_id = str(telegram.get("chat_id", "")).strip()
    timeout = float(telegram.get("timeout_seconds", 10))

    if not token:
        print("В config.json не задан telegram.token.")
        return 1

    if args.test:
        if not chat_id:
            print("chat_id не задан — сначала запустите: python telegram_setup.py")
            return 1
        _seed_admin(cfg, chat_id)
        if _send_test(token, chat_id, timeout):
            print(f"Тестовое сообщение отправлено в чат {chat_id}.")
            return 0
        return 1

    me = api_call(token, "getMe", http_timeout=timeout)
    if not me.get("ok"):
        print(f"Не удалось проверить токен: {me.get('description')}")
        return 1
    username = me["result"].get("username", "")
    print(f"Бот: @{username}")

    if not chat_id:
        print(f"Откройте https://t.me/{username} и нажмите Start (или отправьте любое сообщение).")
        deadline = time.time() + max(0, args.wait)
        offset = None
        chats: list[tuple[str, str, str]] = []

        while True:
            params: dict = {"timeout": 25 if args.wait > 0 else 0}
            if offset is not None:
                params["offset"] = offset
            response = api_call(token, "getUpdates", http_timeout=40, **params)
            if not response.get("ok"):
                print(f"Не удалось получить сообщения: {response.get('description')}")
                return 1
            updates = response.get("result", [])
            if updates:
                offset = int(updates[-1]["update_id"]) + 1
            chats = _chats_from_updates(updates)
            if chats:
                break
            if time.time() >= deadline:
                print("Сообщение боту не получено.")
                print(f"Откройте https://t.me/{username}, нажмите Start и повторите запуск.")
                return 1

        chat_id, name, chat_type = _pick_chat(chats)
        print(f"Найден чат: {name} (id {chat_id}, тип {chat_type})")
        path = _save_chat_id(chat_id, args.config)
        print(f"chat_id администратора сохранён в {path}")

    _seed_admin(cfg, chat_id)

    if _send_test(token, chat_id, timeout):
        print(f"Готово: сигналы и заявки будут приходить админу в чат {chat_id}.")
        return 0
    print("chat_id сохранён, но тестовое сообщение не отправилось. Проверьте сеть/токен.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
