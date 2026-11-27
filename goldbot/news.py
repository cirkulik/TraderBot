"""Ежедневный дайджест новостей ForexFactory для каналов Telegram.

Источник — JSON-календарь ForexFactory (`ff_calendar_thisweek.json` и
`ff_calendar_nextweek.json`). Скрипт выбирает новости за сегодня по времени
МСК, оставляет только сильные и средние, собирает текст и отправляет его в
каналы, подключённые к боту. Предыдущее закреплённое сообщение снимается,
новое закрепляется.

Торговли нет: только чтение календаря и отправка сообщений.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from pathlib import Path

from .config import ROOT
from .subscribers import SubscriberStore
from .telegram import api_call

FEED_THIS_WEEK = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED_NEXT_WEEK = "https://nfs.faireconomy.media/ff_calendar_nextweek.json"

IMPACT_ICON = {"High": "🔴", "Medium": "🟠", "Low": "🟡", "Holiday": "🏖"}
IMPACT_LABEL = {
    "High": "сильная новость",
    "Medium": "средняя",
    "Low": "слабая",
    "Holiday": "выходной",
}
IMPACT_RANK = {"High": 0, "Medium": 1, "Low": 2, "Holiday": 3}

TITLES = {
    "Core PCE Price Index m/m": "Базовый PCE м/м",
    "PCE Price Index m/m": "PCE м/м",
    "Core PCE Price Index y/y": "Базовый PCE г/г",
    "PCE Price Index y/y": "PCE г/г",
    "Prelim GDP q/q": "Предварительный ВВП к/к",
    "Final GDP q/q": "Итоговый ВВП к/к",
    "GDP q/q": "ВВП к/к",
    "Prelim GDP Price Index q/q": "Индекс цен ВВП к/к",
    "GDP Price Index q/q": "Индекс цен ВВП к/к",
    "CPI m/m": "CPI м/м",
    "CPI y/y": "CPI г/г",
    "Core CPI m/m": "Базовый CPI м/м",
    "Core CPI y/y": "Базовый CPI г/г",
    "Trimmed Mean CPI m/m": "Усечённое среднее CPI м/м",
    "PPI m/m": "PPI м/м",
    "Core PPI m/m": "Базовый PPI м/м",
    "Retail Sales m/m": "Розничные продажи м/м",
    "Core Retail Sales m/m": "Базовые розничные продажи м/м",
    "Non-Farm Employment Change": "Изменение числа занятых в несельхоз. секторе",
    "Unemployment Rate": "Уровень безработицы",
    "Average Hourly Earnings m/m": "Средняя почасовая оплата м/м",
    "ADP Employment Change": "Изменение занятости ADP",
    "ADP Weekly Employment Change": "Недельное изменение занятости ADP",
    "Unemployment Claims": "Заявки на пособие по безработице",
    "FOMC Statement": "Заявление FOMC",
    "Federal Funds Rate": "Ставка ФРС",
    "FOMC Economic Projections": "Экономические прогнозы FOMC",
    "FOMC Press Conference": "Пресс-конференция FOMC",
    "ISM Manufacturing PMI": "PMI производственного сектора ISM",
    "ISM Services PMI": "PMI сферы услуг ISM",
    "Flash Manufacturing PMI": "Предварительный PMI производственного сектора",
    "Flash Services PMI": "Предварительный PMI сферы услуг",
    "Manufacturing PMI": "PMI производственного сектора",
    "Services PMI": "PMI сферы услуг",
    "Crude Oil Inventories": "Запасы сырой нефти",
    "Natural Gas Storage": "Запасы природного газа",
    "New Home Sales": "Продажи новых домов",
    "Existing Home Sales": "Продажи существующих домов",
    "Pending Home Sales m/m": "Незавершённые продажи домов м/м",
    "CB Consumer Confidence": "Потребительское доверие CB",
    "Beige Book": "Бежевая книга",
    "Monetary Policy Report Hearings": "Слушания по денежно-кредитной политике",
    "Monetary Policy Statement": "Заявление по денежно-кредитной политике",
    "Official Bank Rate": "Официальная ставка",
    "German Final GDP q/q": "Итоговый ВВП Германии к/к",
    "German Unemployment Change": "Изменение безработицы Германии",
    "German ifo Business Climate": "Деловой климат Германии (ifo)",
    "German GfK Consumer Climate": "Потребительский климат Германии (GfK)",
    "French Prelim CPI m/m": "Предварительный CPI Франции м/м",
    "French Final CPI m/m": "Итоговый CPI Франции м/м",
    "Spanish Flash CPI y/y": "Предварительный CPI Испании г/г",
    "Current Account": "Текущий счёт",
    "Goods Trade Balance": "Торговый баланс товаров",
    "Trade Balance": "Торговый баланс",
    "Prelim Wholesale Inventories m/m": "Предварительные оптовые запасы м/м",
    "Wholesale Inventories m/m": "Оптовые запасы м/м",
    "Industrial Production m/m": "Промышленное производство м/м",
    "Manufacturing Production m/m": "Промышленное производство м/м",
    "Construction PMI": "PMI строительства",
    "Building Permits": "Разрешения на строительство",
    "Housing Starts": "Начало строительства домов",
    "CPI Flash Estimate y/y": "Предварительный CPI г/г",
    "Core CPI Flash Estimate y/y": "Базовый предварительный CPI г/г",
    "ECB Rate Decision": "Решение ЕЦБ по ставке",
    "ECB Press Conference": "Пресс-конференция ЕЦБ",
    "Main Refinancing Rate": "Основная ставка рефинансирования",
    "RBA Rate Decision": "Решение РБА по ставке",
    "RBA Statement": "Заявление РБА",
    "BOJ Policy Rate": "Ставка BOJ",
    "BOJ Statement": "Заявление BOJ",
    "Tokyo Core CPI y/y": "Базовый CPI Токио г/г",
    "Bank Holiday": "Банковский выходной",
}


def _tz(name: str):
    """Часовой пояс: zoneinfo, а на Windows без tzdata — pytz."""
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - pytz есть вместе с pandas
        import pytz

        return pytz.timezone(name)


def _localize(naive: datetime, tz) -> datetime:
    if hasattr(tz, "localize"):
        return tz.localize(naive)
    return naive.replace(tzinfo=tz)


def _parse_hhmm(value: str, default: str = "11:00") -> dtime:
    text = str(value or default).strip()
    try:
        hour, minute = text.split(":", 1)
        return dtime(int(hour), int(minute))
    except (TypeError, ValueError):
        hour, minute = default.split(":", 1)
        return dtime(int(hour), int(minute))


def translate_title(title: str) -> str:
    """Русское название для популярных событий, иначе — оригинал."""
    title = str(title or "").strip()
    if not title:
        return ""
    if title in TITLES:
        return TITLES[title]

    match = re.match(r"^FOMC Member (.+) Speaks$", title)
    if match:
        return f"Выступление члена FOMC {match.group(1)}"
    match = re.match(r"^(.+) Speaks$", title)
    if match:
        return f"Выступление: {match.group(1)}"

    return (
        title.replace(" m/m", " м/м")
        .replace(" q/q", " к/к")
        .replace(" y/y", " г/г")
    )


def _clean_value(value) -> str:
    text = str(value or "").strip()
    return "" if text in ("", "-", "—") else text


@dataclass
class Event:
    title: str
    country: str
    when: datetime
    impact: str
    forecast: str
    previous: str

    @property
    def icon(self) -> str:
        return IMPACT_ICON.get(self.impact, "•")

    @property
    def ru_title(self) -> str:
        return translate_title(self.title)


class NewsDigest:
    def __init__(self, cfg: dict, cfg_path: str | Path | None = None) -> None:
        self.cfg = cfg
        n = cfg.get("news") or {}

        self.enabled = bool(n.get("enabled", True))
        self.send_time = _parse_hhmm(n.get("time", "11:00"))
        self.tz_name = str(n.get("tz") or "Europe/Moscow")
        self.tz = _tz(self.tz_name)
        self.tz_label = "МСК" if self.tz_name == "Europe/Moscow" else self.tz_name
        self.impacts = tuple(
            str(value).strip().title()
            for value in (n.get("impacts") or ["High", "Medium"])
        )
        self.currencies = tuple(
            str(value).strip().upper() for value in (n.get("currencies") or [])
        )
        self.max_events = max(1, int(n.get("max_events", 15)))
        self.skip_weekend = bool(n.get("skip_weekend", True))
        self.pin = bool(n.get("pin", True))
        self.channels_cfg = [str(value) for value in (n.get("channels") or [])]
        self.send_admin = bool(n.get("send_admin", False))
        self.exchange_block = bool(n.get("exchange_block", True))

        self.log_path = ROOT / str(n.get("log_path", "data/news.log"))
        self.state_path = ROOT / str(n.get("state_path", "data/news_state.json"))
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)

        tg = cfg.get("telegram") or {}
        self.token = str(tg.get("token") or "").strip()
        self.admin_chat = str(tg.get("chat_id") or "").strip()
        self.db_path = ROOT / str(tg.get("db_path") or "data/goldbot.db")

        self.state = self._load_state()
        self._channel_cache: tuple[float, list[str]] = (0.0, [])

    # --- служебное ---------------------------------------------------------
    def log(self, message: str) -> None:
        stamp = datetime.now(self.tz).strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{stamp}] {message}"
        print(line, flush=True)
        try:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    def _load_state(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("last_date", "")
        pins = data.get("pins")
        if not isinstance(pins, dict):
            data["pins"] = {}
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

    # --- календарь ---------------------------------------------------------
    def _fetch(self, url: str) -> list[dict]:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GoldBot/1.0",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        if not isinstance(payload, list):
            raise ValueError("календарь вернул не список")
        return payload

    def fetch_events(self, day: date) -> list[Event]:
        raw: list[dict] = []
        errors: list[str] = []
        for url in (FEED_THIS_WEEK, FEED_NEXT_WEEK):
            try:
                raw.extend(self._fetch(url))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                errors.append(f"{url}: {exc}")

        if not raw and errors:
            raise RuntimeError("; ".join(errors))

        events: dict[tuple, Event] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            impact = str(item.get("impact") or "").strip().title()
            if impact not in self.impacts:
                continue
            country = str(item.get("country") or "").strip().upper()
            if self.currencies and country not in self.currencies:
                continue
            try:
                when = datetime.fromisoformat(str(item.get("date") or ""))
            except (TypeError, ValueError):
                continue
            if when.tzinfo is None:
                when = when.replace(tzinfo=self.tz)
            when = when.astimezone(self.tz)
            if when.date() != day:
                continue

            key = (str(item.get("title") or ""), country, when.isoformat())
            events[key] = Event(
                title=str(item.get("title") or ""),
                country=country,
                when=when,
                impact=impact,
                forecast=_clean_value(item.get("forecast")),
                previous=_clean_value(item.get("previous")),
            )

        return sorted(
            events.values(),
            key=lambda event: (
                event.when,
                IMPACT_RANK.get(event.impact, 9),
                event.country,
            ),
        )

    # --- текст -------------------------------------------------------------
    def _session(
        self,
        day: date,
        tz_name: str,
        start: tuple[int, int],
        end: tuple[int, int],
    ) -> tuple[str, str]:
        tz = _tz(tz_name)
        left = _localize(datetime.combine(day, dtime(*start)), tz).astimezone(self.tz)
        right = _localize(datetime.combine(day, dtime(*end)), tz).astimezone(self.tz)
        return left.strftime("%H:%M"), right.strftime("%H:%M")

    def exchange_lines(self, day: date) -> list[str]:
        title = f"🕒 Биржи ({self.tz_label}):"
        if day.weekday() >= 5:
            return [f"🕒 Биржи: выходной (суббота/воскресенье)"]

        lse = self._session(day, "Europe/London", (8, 0), (16, 30))
        nyse = self._session(day, "America/New_York", (9, 30), (16, 0))
        tse_open = self._session(day, "Asia/Tokyo", (9, 0), (11, 30))
        tse_close = self._session(day, "Asia/Tokyo", (12, 30), (15, 0))
        asx = self._session(day, "Australia/Sydney", (10, 0), (16, 0))

        return [
            title,
            f"🇬🇧 LSE — {lse[0]}–{lse[1]}",
            f"🇺🇸 NYSE — {nyse[0]}–{nyse[1]}",
            f"🇯🇵 TSE — {tse_open[0]}–{tse_close[1]} "
            f"(перерыв {tse_open[1]}–{tse_close[0]})",
            f"🇦🇺 ASX (Сидней) — {asx[0]}–{asx[1]}",
        ]

    def build_text(self, events: list[Event], day: date) -> str:
        lines = [
            f"GOLD M5 — {day.strftime('%d.%m.%Y')}",
            "Новости на сегодня",
            f"Публикация: {self.send_time.strftime('%H:%M')} ({self.tz_label})",
            "",
        ]

        if not events:
            lines.append("🔴/🟠 новостей на сегодня нет.")
            lines.append("")
        else:
            shown = events[: self.max_events]
            for event in shown:
                lines.append(
                    f"{event.icon} {event.when.strftime('%H:%M')} "
                    f"{event.country} — {event.ru_title}"
                )
                details = []
                if event.forecast:
                    details.append(f"прогноз {event.forecast}")
                if event.previous:
                    details.append(f"прошлое {event.previous}")
                if details:
                    lines.append(" · ".join(details))
                lines.append("")

            left = len(events) - len(shown)
            if left > 0:
                lines.append(f"… и ещё {left} новостей")
                lines.append("")

        if self.exchange_block:
            lines.append("━" * 14)
            lines.extend(self.exchange_lines(day))
            lines.append("")

        legend = " · ".join(
            f"{IMPACT_ICON[impact]} — {IMPACT_LABEL[impact]}"
            for impact in self.impacts
            if impact in IMPACT_ICON
        )
        if legend:
            lines.append(legend)

        text = "\n".join(lines).strip()
        if len(text) > 4000:
            text = text[:3950].rstrip() + "\n…"
        return text

    # --- каналы и отправка -------------------------------------------------
    def channels(self) -> list[str]:
        if self.channels_cfg:
            return list(dict.fromkeys(self.channels_cfg))

        now = time.time()
        stamp, cached = self._channel_cache
        if cached and now - stamp < 600:
            return list(cached)

        out: list[str] = []
        try:
            store = SubscriberStore(self.db_path)
        except Exception as exc:  # noqa: BLE001 - база может быть занята
            self.log(f"каналы: база недоступна: {exc}")
            return []
        try:
            for user in store.active():
                chat_id = str(user.get("chat_id") or "")
                if not chat_id.startswith("-"):
                    continue
                info = api_call(self.token, "getChat", chat_id=chat_id)
                if not info.get("ok"):
                    self.log(
                        f"каналы: {chat_id}: {info.get('description', 'getChat')}"
                    )
                    continue
                result = info.get("result") or {}
                if str(result.get("type") or "") == "channel":
                    out.append(chat_id)
        finally:
            store.close()

        if self.send_admin and self.admin_chat:
            out.append(self.admin_chat)

        out = list(dict.fromkeys(out))
        self._channel_cache = (now, out)
        return out

    def _send(self, chat_id: str, text: str) -> int:
        payload = api_call(
            self.token,
            "sendMessage",
            chat_id=chat_id,
            text=text,
            disable_web_page_preview="true",
        )
        if not payload.get("ok"):
            self.log(f"{chat_id}: не отправлено: {payload.get('description')}")
            return 0
        message_id = int((payload.get("result") or {}).get("message_id") or 0)
        self.log(f"{chat_id}: отправлено (message_id {message_id})")
        return message_id

    def _old_pin(self, chat_id: str) -> int:
        saved = int(self.state.get("pins", {}).get(chat_id) or 0)
        if saved:
            return saved

        info = api_call(self.token, "getChat", chat_id=chat_id)
        if not info.get("ok"):
            return 0
        pinned = (info.get("result") or {}).get("pinned_message") or {}
        text = str(pinned.get("text") or "")
        if text.startswith("GOLD M5"):
            return int(pinned.get("message_id") or 0)
        return 0

    def _pin(self, chat_id: str, message_id: int) -> None:
        old = self._old_pin(chat_id)
        if old:
            result = api_call(
                self.token, "unpinChatMessage", chat_id=chat_id, message_id=old
            )
            if not result.get("ok"):
                self.log(
                    f"{chat_id}: старый пин {old} не снят: "
                    f"{result.get('description')}"
                )

        result = api_call(
            self.token,
            "pinChatMessage",
            chat_id=chat_id,
            message_id=message_id,
            disable_notification="true",
        )
        if result.get("ok"):
            self.state.setdefault("pins", {})[chat_id] = message_id
            self._save_state()
            self.log(f"{chat_id}: закреплено (message_id {message_id})")
        else:
            self.log(
                f"{chat_id}: не закреплено: {result.get('description')}"
            )

    def send_daily(
        self,
        day: date | None = None,
        pin: bool | None = None,
        print_only: bool = False,
    ) -> tuple[int, int]:
        day = day or datetime.now(self.tz).date()
        events = self.fetch_events(day)
        text = self.build_text(events, day)

        if print_only:
            print(text)
            return 0, 0

        if not self.token:
            self.log("нет TELEGRAM_TOKEN — отправка невозможна")
            return 0, 0

        targets = self.channels()
        if not targets:
            self.log("нет подключённых каналов")
            return 0, 0

        pin = self.pin if pin is None else pin
        sent = 0
        for chat_id in targets:
            message_id = self._send(chat_id, text)
            if not message_id:
                continue
            sent += 1
            if pin:
                self._pin(chat_id, message_id)

        if sent:
            self.state["last_date"] = day.isoformat()
            self._save_state()
        return sent, len(targets)

    # --- запуск ------------------------------------------------------------
    def startup(self) -> None:
        self.log("=" * 62)
        self.log("  GOLDBOT NEWS — новости ForexFactory в каналы")
        self.log("=" * 62)
        self.log(
            f"  отправка: {self.send_time.strftime('%H:%M')} ({self.tz_label}), "
            f"будни: {'да' if self.skip_weekend else 'нет'}"
        )
        self.log(f"  новости: {', '.join(self.impacts)}")
        self.log(f"  закрепление: {'да' if self.pin else 'нет'}")
        self.log(f"  каналы: {'из конфига' if self.channels_cfg else 'подключённые к боту'}")
        self.log(f"  лог: {self.log_path}")
        self.log("-" * 62)

    def run(self) -> None:
        self.startup()
        self.log("цикл запущен")
        last_warn = 0.0
        try:
            while True:
                now = datetime.now(self.tz)
                today = now.date()

                if self.skip_weekend and today.weekday() >= 5:
                    time.sleep(60)
                    continue

                if (
                    now.time() < self.send_time
                    or self.state.get("last_date") == today.isoformat()
                ):
                    time.sleep(20)
                    continue

                try:
                    sent, total = self.send_daily(today)
                except Exception as exc:  # noqa: BLE001 - процесс не должен падать
                    self.log(f"ОШИБКА: {type(exc).__name__}: {exc}")
                    time.sleep(60)
                    continue

                if sent:
                    self.log(f"дайджест отправлен: {sent} из {total}")
                    time.sleep(60)
                else:
                    if time.time() - last_warn > 300:
                        last_warn = time.time()
                        self.log("дайджест не отправлен — повтор через 5 минут")
                    time.sleep(300)
        except KeyboardInterrupt:
            self.log("остановлен пользователем")
