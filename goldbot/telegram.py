"""Telegram: рассылка сигналов подписчикам, команды и выбор языка (только stdlib)."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from . import i18n
from .config import ROOT
from .subscribers import ACTIVE, BLOCKED, PAUSED, SubscriberStore

API_URL = "https://api.telegram.org/bot{token}/{method}"
MAX_TEXT = 4000

STATUS_ICONS = {ACTIVE: "✅", PAUSED: "⏸", BLOCKED: "🚫"}


def api_call(token: str, method: str, http_timeout: float = 10.0, **params) -> dict:
    """Вызывает метод Bot API. Возвращает dict с полем ok, без исключений."""
    clean = {k: v for k, v in params.items() if v is not None}
    data = urllib.parse.urlencode(clean).encode("utf-8")
    request = urllib.request.Request(API_URL.format(token=token, method=method), data=data)
    try:
        with urllib.request.urlopen(request, timeout=http_timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {"ok": False, "description": body or str(exc)}
        payload.setdefault("ok", False)
        return payload
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "description": f"сеть: {exc}"}


class TelegramNotifier:
    """Отправка в один чат. Оставлено для telegram_setup.py."""

    def __init__(
        self,
        token: str = "",
        chat_id: str = "",
        enabled: bool = True,
        timeout: float = 10.0,
    ):
        self.token = str(token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        self.timeout = float(timeout)
        self.enabled = bool(enabled) and bool(self.token) and bool(self.chat_id)

    @classmethod
    def from_config(cls, cfg: dict) -> "TelegramNotifier":
        params = cfg.get("telegram", {}) or {}
        return cls(
            token=params.get("token", ""),
            chat_id=params.get("chat_id", ""),
            enabled=bool(params.get("enabled", False)),
            timeout=float(params.get("timeout_seconds", 10)),
        )

    def send(self, text: str) -> bool:
        if not self.enabled:
            return False
        payload = api_call(
            self.token,
            "sendMessage",
            http_timeout=self.timeout,
            chat_id=self.chat_id,
            text=text,
            disable_web_page_preview="true",
        )
        if not payload.get("ok"):
            print(f"[telegram] сообщение не отправлено: {payload.get('description')}")
            return False
        return True


class TelegramBot:
    """Рассылка подписчикам + обработка команд /start, /stop, /lang и админских."""

    def __init__(
        self,
        token: str = "",
        admin_chat_id: str = "",
        store: SubscriberStore | None = None,
        enabled: bool = True,
        timeout: float = 10.0,
        default_lang: str = i18n.DEFAULT_LANG,
        poll_commands: bool = True,
    ):
        self.token = str(token or "").strip()
        self.admin_chat_id = str(admin_chat_id or "").strip()
        self.store = store or SubscriberStore(ROOT / "data" / "goldbot.db")
        self.timeout = float(timeout)
        self.default_lang = i18n.normalize_lang(default_lang)
        self.poll_commands = bool(poll_commands)
        self.enabled = bool(enabled) and bool(self.token)
        self.username = ""

    @classmethod
    def from_config(cls, cfg: dict, store: SubscriberStore | None = None) -> "TelegramBot":
        params = cfg.get("telegram", {}) or {}
        path = params.get("db_path") or "data/goldbot.db"
        if store is None:
            store = SubscriberStore(ROOT / path)
        return cls(
            token=params.get("token", ""),
            admin_chat_id=params.get("chat_id", ""),
            store=store,
            enabled=bool(params.get("enabled", False)),
            timeout=float(params.get("timeout_seconds", 10)),
            default_lang=params.get("default_lang", i18n.DEFAULT_LANG),
            poll_commands=bool(params.get("poll_commands", True)),
        )

    # ------------------------------------------------------------- отправка
    def send(self, chat_id: str | int, text: str, reply_markup: str | None = None) -> bool:
        if not self.enabled:
            return False
        payload = api_call(
            self.token,
            "sendMessage",
            http_timeout=self.timeout,
            chat_id=str(chat_id),
            text=text[:MAX_TEXT],
            disable_web_page_preview="true",
            reply_markup=reply_markup,
        )
        if not payload.get("ok"):
            print(f"[telegram] {chat_id}: {payload.get('description')}")
            return False
        return True

    def broadcast(self, render) -> int:
        """Отправляет каждому активному подписчику текст на его языке."""
        if not self.enabled:
            return 0
        sent = 0
        for user in self.store.active():
            if not user.get("started"):
                # Telegram не даёт писать тому, кто ни разу не запускал бота.
                continue
            lang = self._user_lang(user)
            if self.send(user["chat_id"], render(lang)):
                sent += 1
        return sent

    def notify_admin(self, text: str, reply_markup: str | None = None) -> bool:
        if not self.admin_chat_id:
            return False
        return self.send(self.admin_chat_id, text, reply_markup=reply_markup)

    def admin_lang(self) -> str:
        """Язык администратора (для стартового сообщения и ответов)."""
        return self._admin_lang()

    # ------------------------------------------------------------- настройка
    def prepare(self) -> str:
        """Снимает вебхук (иначе getUpdates не работает) и узнаёт username бота."""
        if not self.enabled:
            return ""
        api_call(self.token, "deleteWebhook", http_timeout=self.timeout)
        me = api_call(self.token, "getMe", http_timeout=self.timeout)
        if me.get("ok"):
            self.username = str((me.get("result") or {}).get("username", ""))
        self._seed_admin()
        return self.username

    def _seed_admin(self) -> None:
        """Админ из config.json всегда в списке — иначе он не получит сигналы."""
        if not self.admin_chat_id:
            return
        user = self.store.get(self.admin_chat_id)
        if user is None:
            self.store.upsert(
                self.admin_chat_id,
                name="admin",
                status=ACTIVE,
                lang=self.default_lang,
                started=1,
            )
        elif user.get("status") != ACTIVE or not user.get("started"):
            self.store.upsert(self.admin_chat_id, status=ACTIVE, started=1)

    # --------------------------------------------------------------- команды
    def poll(self) -> int:
        """Забирает новые сообщения и команды. Возвращает число обработанных."""
        if not self.enabled or not self.poll_commands:
            return 0
        offset = self.store.last_update_id
        payload = api_call(
            self.token,
            "getUpdates",
            http_timeout=self.timeout,
            timeout=0,
            offset=offset or None,
            allowed_updates=json.dumps(["message", "callback_query"]),
        )
        if not payload.get("ok"):
            print(f"[telegram] getUpdates: {payload.get('description')}")
            return 0
        handled = 0
        for update in payload.get("result", []):
            try:
                self._handle_update(update)
                if "update_id" in update:
                    self.store.set_last_update_id(int(update["update_id"]) + 1)
                handled += 1
            except Exception as exc:  # noqa: BLE001 - команда не должна ронять бота
                print(f"[telegram] ошибка обработки команды: {exc}")
        return handled

    def _handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if message:
            self._handle_message(message)

    def _handle_message(self, message: dict) -> None:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id") or "")
        if not chat_id or chat_id == "None":
            return
        sender = message.get("from") or {}
        name = (
            chat.get("first_name")
            or sender.get("first_name")
            or chat.get("username")
            or chat_id
        )
        username = str(sender.get("username") or "")
        contact = self._contact_fields(name, username, chat_id)

        user = self.store.get(chat_id)
        if user is None:
            is_admin = self._is_admin(chat_id)
            self.store.upsert(
                chat_id,
                **contact,
                lang="",
                status=ACTIVE if is_admin else BLOCKED,
                started=1,
            )
            if not is_admin:
                # Незнакомого запоминаем, но не отвечаем и доступа не даём.
                return
            user = self.store.get(chat_id)
        elif self._is_admin(chat_id):
            user = self.store.upsert(chat_id, **contact, status=ACTIVE, started=1)
        elif user.get("status") == BLOCKED:
            # Контакт обновляем (чтобы админ мог добавить по @username), но молчим.
            self.store.upsert(chat_id, started=1, **contact)
            return
        else:
            user = self.store.upsert(chat_id, started=1, **contact)

        lang = self._user_lang(user or {})

        text = (message.get("text") or "").strip()
        button = text.lower()
        if button == "start":
            command, arg = "/start", ""
        elif button == "stop":
            command, arg = "/stop", ""
        elif button == "lang":
            command, arg = "/lang", ""
        elif text.startswith("/"):
            command, _, arg = text.partition(" ")
            command = command.split("@")[0].lower()
            arg = arg.strip()
        else:
            self.send(chat_id, i18n.t(lang, "help"), self._main_keyboard())
            return

        if command == "/start":
            self._cmd_start(chat_id, user or {}, lang)
        elif command == "/stop":
            self._cmd_stop(chat_id, user or {}, lang)
        elif command == "/lang":
            self.send(chat_id, i18n.t(lang, "choose_lang"), self._lang_keyboard())
        elif command in ("/users", "/allow", "/deny", "/remove"):
            self._cmd_admin(command, arg, chat_id, lang)
        else:
            self.send(chat_id, i18n.t(lang, "help"))

    def _cmd_start(self, chat_id: str, user: dict, lang: str) -> None:
        # Язык спрашиваем только пока он не выбран.
        if not user.get("lang"):
            self.send(chat_id, i18n.t(lang, "choose_lang"), self._lang_keyboard())
            return

        if self._is_admin(chat_id):
            self.send(chat_id, i18n.t(lang, "welcome_admin"), self._main_keyboard())
            return

        if user.get("status") == PAUSED:
            self.store.set_status(chat_id, ACTIVE)
        self.send(chat_id, i18n.t(lang, "welcome"), self._main_keyboard())

    def _cmd_stop(self, chat_id: str, user: dict, lang: str) -> None:
        if self._is_admin(chat_id):
            self.send(chat_id, i18n.t(lang, "admin_no_stop"), self._main_keyboard())
            return
        self.store.set_status(chat_id, PAUSED)
        self.send(chat_id, i18n.t(lang, "paused"), self._main_keyboard())

    def _cmd_admin(self, command: str, arg: str, chat_id: str, lang: str) -> None:
        if not self._is_admin(chat_id):
            self.send(chat_id, i18n.t(lang, "not_admin"))
            return
        if command == "/users":
            self._send_users(chat_id, lang)
            return
        if not arg:
            self.send(chat_id, i18n.t(lang, "admin_need_ref", command=command))
            return

        ref = arg.split()[0]
        target = self.store.find(ref)
        if target and str(target["chat_id"]) == self.admin_chat_id:
            self.send(chat_id, i18n.t(lang, "admin_self"))
            return

        if command == "/allow":
            self._allow(ref, target, chat_id, lang)
        elif command == "/remove":
            self._remove(ref, target, chat_id, lang)
        else:  # /deny
            self._revoke(ref, target, chat_id, lang)

    def _allow(self, ref: str, target: dict | None, by_chat_id: str, by_lang: str) -> None:
        known = target is not None
        if target is None:
            if not ref.isdigit():
                self.send(by_chat_id, i18n.t(by_lang, "admin_not_found_username"))
                return
            # Добавление по ID работает, даже если человек ещё не писал боту.
            target = self.store.upsert(ref, status=ACTIVE)
        else:
            target = self.store.set_status(target["chat_id"], ACTIVE) or target
        name = target.get("name") or target["chat_id"]
        key = "admin_added" if known else "admin_added_not_started"
        self.send(
            by_chat_id,
            i18n.t(by_lang, key, name=name, chat_id=target["chat_id"]),
        )
        if known:
            user_lang = self._user_lang(target)
            self.send(
                target["chat_id"],
                i18n.t(user_lang, "user_allowed_you"),
                self._main_keyboard(),
            )

    def _revoke(self, ref: str, target: dict | None, by_chat_id: str, by_lang: str) -> None:
        if target is None:
            self.send(by_chat_id, i18n.t(by_lang, "admin_not_found", ref=ref))
            return
        target = self.store.set_status(target["chat_id"], BLOCKED) or target
        name = target.get("name") or target["chat_id"]
        self.send(
            by_chat_id,
            i18n.t(by_lang, "admin_revoked", name=name, chat_id=target["chat_id"]),
        )
        self.send(
            target["chat_id"],
            i18n.t(self._user_lang(target), "user_revoked_you"),
        )

    def _remove(self, ref: str, target: dict | None, by_chat_id: str, by_lang: str) -> None:
        if target is None:
            self.send(by_chat_id, i18n.t(by_lang, "admin_not_found", ref=ref))
            return
        name = target.get("name") or target["chat_id"]
        self.store.remove(target["chat_id"])
        self.send(
            by_chat_id,
            i18n.t(by_lang, "admin_removed", name=name, chat_id=target["chat_id"]),
        )

    def _send_users(self, chat_id: str, lang: str) -> None:
        users = self.store.users()
        if not users:
            self.send(chat_id, i18n.t(lang, "admin_users_empty"))
            return
        order = {ACTIVE: 0, PAUSED: 1, BLOCKED: 2}
        rows = sorted(
            users.values(),
            key=lambda u: (order.get(u.get("status"), 9), str(u.get("name", ""))),
        )
        lines = [i18n.t(lang, "admin_users_header", count=len(rows))]
        for user in rows:
            icon = STATUS_ICONS.get(user.get("status"), "•")
            handle = f" @{user['username']}" if user.get("username") else ""
            suffix = "" if user.get("started") else f" · {i18n.t(lang, 'users_not_started')}"
            lines.append(
                f"{icon} {user.get('name') or user['chat_id']}{handle} — "
                f"{user['chat_id']}, {user.get('lang') or self.default_lang}{suffix}"
            )
        self.send(chat_id, "\n".join(lines))

    # ---------------------------------------------------------------- кнопки
    def _handle_callback(self, callback: dict) -> None:
        data = str(callback.get("data") or "")
        message = callback.get("message") or {}
        chat_id = str((message.get("chat") or {}).get("id") or "")
        api_call(
            self.token,
            "answerCallbackQuery",
            http_timeout=self.timeout,
            callback_query_id=callback.get("id"),
        )
        if not chat_id:
            return

        user = self.store.get(chat_id)
        if user is None or (
            user.get("status") == BLOCKED and not self._is_admin(chat_id)
        ):
            return

        if data.startswith("lang:"):
            old = self.store.get(chat_id) or {}
            had_lang = bool(old.get("lang"))
            lang = i18n.normalize_lang(data.split(":", 1)[1])
            self.store.set_lang(chat_id, lang)
            self.send(
                chat_id,
                i18n.t(lang, "lang_set", name=i18n.LANGUAGE_NAMES[lang]),
                self._main_keyboard(),
            )
            if not had_lang:
                if self._is_admin(chat_id):
                    self.send(chat_id, i18n.t(lang, "welcome_admin"), self._main_keyboard())
                else:
                    self.send(chat_id, i18n.t(lang, "welcome"), self._main_keyboard())

    # ---------------------------------------------------------------- утилиты
    def _main_keyboard(self) -> str:
        """Постоянная клавиатура с кнопками Start / Stop / Lang."""
        return json.dumps(
            {
                "keyboard": [[{"text": "Start"}, {"text": "Stop"}, {"text": "Lang"}]],
                "resize_keyboard": True,
                "is_persistent": True,
            }
        )

    def _contact_fields(self, name: str, username: str, chat_id: str) -> dict:
        """Не перезаписываем сохранённые имя/username пустыми значениями."""
        fields: dict = {}
        if username:
            fields["username"] = username
        if name and name != chat_id:
            fields["name"] = name
        return fields

    def _lang_keyboard(self) -> str:
        buttons = [
            {"text": label, "callback_data": f"lang:{code}"}
            for code, label in i18n.LANGUAGES.items()
        ]
        return json.dumps({"inline_keyboard": [buttons]})

    def _is_admin(self, chat_id: str | int) -> bool:
        return bool(self.admin_chat_id) and str(chat_id) == self.admin_chat_id

    def _admin_lang(self) -> str:
        user = self.store.get(self.admin_chat_id) if self.admin_chat_id else None
        return self._user_lang(user or {})

    def _user_lang(self, user: dict) -> str:
        return i18n.normalize_lang(user.get("lang") or self.default_lang)


# ------------------------------------------------------------------ сообщения
def format_signal_message(
    sig, lang: str = i18n.DEFAULT_LANG, stats: dict | None = None
) -> str:
    """Сигнал для Telegram на нужном языке."""
    code = i18n.normalize_lang(lang)
    arrow = "🟢 BUY" if sig.direction == "BUY" else "🔴 SELL"
    risk_dist = sig.entry - sig.sl if sig.direction == "BUY" else sig.sl - sig.entry
    items = getattr(sig, "reason_items", None) or []
    reasons = (
        [i18n.render_reason(item, code) for item in items]
        if items
        else list(getattr(sig, "reasons", []))
    )
    head = i18n.SIGNAL_TEMPLATE[code].format(
        arrow=arrow,
        label=i18n.setup_label(sig.setup, code),
        bar=f"{sig.time:%d.%m.%Y %H:%M}",
        entry=sig.entry,
        sl=sig.sl,
        tp=sig.tp,
        risk=risk_dist,
        rr=sig.rr,
        points=risk_dist / 0.01,
        spread=sig.spread_points,
        confidence=sig.confidence,
        bars=sig.time_exit_bars,
        hours=sig.time_exit_bars * 5 / 60,
    )
    lines = [head] + [f"• {reason}" for reason in reasons]

    if stats:
        block = []
        for days in ("7", "14"):
            item = (stats.get(days) or {}).get(sig.setup)
            if item is None:
                continue
            if item.get("n"):
                block.append(
                    i18n.STATS_LINE[code].format(
                        days=days,
                        n=item["n"],
                        wr=item.get("wr", 0.0),
                        displaced=item.get("displaced", 0),
                    )
                )
            else:
                block.append(i18n.STATS_EMPTY[code].format(days=days))
        if block:
            lines.append("")
            lines.extend(block)

    return "\n".join(lines)


def format_close_message(trade: dict, lang: str = i18n.DEFAULT_LANG) -> str:
    """Сообщение о закрытии отслеживаемой сделки на нужном языке."""
    code = i18n.normalize_lang(lang)
    result_r = float(trade["result_r"])
    result_points = float(trade["result_points"])
    if result_r > 0:
        icon = "✅"
    elif result_r < 0:
        icon = "❌"
    else:
        icon = "➖"
    sign = "+" if result_r >= 0 else ""
    label = i18n.setup_label(str(trade.get("setup", "")), code) or trade.get(
        "setup_label", ""
    )
    text = i18n.CLOSE_TEMPLATE[code].format(
        icon=icon,
        direction=trade.get("direction", ""),
        label=label,
        reason=trade.get("reason", ""),
        sign=sign,
        r=result_r,
        points=result_points,
    )
    gap = trade.get("gap_to_tp")
    if gap is not None and gap != "":
        note = i18n.tp_note(
            code, gap, trade.get("mfe", 0.0), trade.get("exit_bar_peak")
        )
        if note:
            text += f"\n{note}"
    elif trade.get("tp_note"):
        text += f"\n{trade['tp_note']}"
    return text
