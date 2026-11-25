"""Отчёт по уровням: текст, картинка, отправка в Telegram."""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd

from . import i18n
from . import candles as candles_mod
from .config import ROOT
from .zones import build_report

CHART_KEEP_DAYS = 7


def build_text(
    report: dict, lang: str = i18n.DEFAULT_LANG, extra: list[str] | None = None
) -> str:
    """Собирает сообщение по данным отчёта. `extra` — блок сильных точек."""
    def t(key: str) -> str:
        return i18n.hourly_text(lang, key)

    stamp = report["time"]
    bias = report["bias"]
    bias_key = {"SELL": "bias_sell", "BUY": "bias_buy"}.get(bias, "bias_flat")
    plan_key = {"SELL": "plan_sell", "BUY": "plan_buy"}.get(bias, "plan_flat")

    lines = [
        t("title").format(hours=report["hours"]),
        t("stamp").format(
            day=stamp.strftime("%d.%m"),
            time=stamp.strftime("%H:%M"),
            price=f"{report['price']:.2f}",
        ),
        "",
        t(bias_key),
        t(plan_key),
        "",
        t("range").format(
            low=f"{report['low']:.2f}",
            high=f"{report['high']:.2f}",
            span=f"{report['high'] - report['low']:.2f}",
        ),
        t("change").format(
            change=f"{report['change']:+.2f}",
            pct=f"{report['change_pct']:+.2f}",
        ),
        "",
    ]

    sells = [z for z in report["zones"] if z.side == "sell"]
    buys = [z for z in report["zones"] if z.side == "buy"]
    nearest_sell = report["nearest_sell"]

    if sells:
        lines.append(t("zones_sell"))
        for zone in sorted(sells, key=lambda z: z.price, reverse=True):
            mark = t("decision") if nearest_sell is not None and zone is nearest_sell else ""
            lines.append(
                t("zone").format(
                    price=f"{zone.price:.2f}",
                    strength=t(zone.strength),
                    touches=zone.touches,
                )
                + mark
            )
        lines.append("")

    if buys:
        lines.append(t("zones_buy"))
        for zone in sorted(buys, key=lambda z: z.price):
            lines.append(
                t("zone").format(
                    price=f"{zone.price:.2f}",
                    strength=t(zone.strength),
                    touches=zone.touches,
                )
            )
        lines.append("")

    price = report["price"]
    if nearest_sell is not None:
        lines.append(
            t("nearest_up").format(
                price=f"{nearest_sell.price:.2f}",
                dist=f"{nearest_sell.price - price:+.2f}",
            )
        )
    nearest_buy = report["nearest_buy"]
    if nearest_buy is not None:
        lines.append(
            t("nearest_down").format(
                price=f"{nearest_buy.price:.2f}",
                dist=f"{nearest_buy.price - price:+.2f}",
            )
        )

    if extra:
        lines.append("")
        lines.extend(extra)

    trends = report["trends"]
    lines.append("")
    lines.append(
        t("trend").format(
            m5=t(trends.get("M5", "flat")),
            m15=t(trends.get("M15", "flat")),
            h1=t(trends.get("H1", "flat")),
        )
    )
    lines.append(
        t("footer").format(atr=f"{report['atr']:.2f}", adx=f"{report['adx_m15']:.1f}")
    )
    lines.append(t("note"))
    return "\n".join(lines)


def strong_points(
    df: pd.DataFrame,
    interval: int,
    tol: float,
    gap: float,
    *,
    merge: float = 1.0,
    min_touches: int = 3,
    limit: int | None = None,
) -> list[dict]:
    """Сильные точки по свечам ТФ отчёта: близкие уровни слиты, слабые отброшены."""
    all_rows = candles_mod.touch_rows(df, interval, tol, gap)
    count = candles_mod.display_count(interval)
    if count == 1:
        rows = all_rows[-1:]
    else:
        rows = candles_mod.active_rows(all_rows, min_touches=2, limit=count)
    if not rows:
        return []

    price = float(df["close"].iloc[-1])
    points: list[dict] = []
    for item in candles_mod.levels(rows, price, cluster=merge):
        if item["touches"] < min_touches:
            continue
        # две почти одинаковые точки не дублируем: оставляем сильнейшую
        if any(abs(item["price"] - kept["price"]) <= merge for kept in points):
            continue
        points.append(item)
        if limit and len(points) >= limit:
            break
    return points


def strong_block(
    df: pd.DataFrame,
    interval: int,
    lang: str,
    tol: float,
    gap: float,
    *,
    merge: float = 1.0,
    limit: int = 4,
) -> list[str]:
    """Блок отчёта: только сильные точки, без списка свечей."""
    points = strong_points(df, interval, tol, gap, merge=merge, limit=limit)
    if not points:
        return []

    def t(key: str) -> str:
        return i18n.hourly_text(lang, key)

    lines = [t("points_title").format(tf=candles_mod.tf_name(interval))]
    for item in points:
        lines.append(
            t("point_line").format(
                mark="🔴" if item["side"] == "sell" else "🟢",
                price=f"{item['price']:.2f}",
                touches=item["touches"],
                side="SELL" if item["side"] == "sell" else "BUY",
            )
        )
    return lines


def _cleanup_charts(out_dir: Path, keep_days: int = CHART_KEEP_DAYS) -> None:
    """Убирает старые картинки: при отправке каждые 30 минут их копится много."""
    deadline = time.time() - keep_days * 86400
    for pattern in ("hourly_*.png", "daily_*.png"):
        try:
            files = list(out_dir.glob(pattern))
        except OSError:
            continue
        for file in files:
            try:
                if file.stat().st_mtime < deadline:
                    file.unlink()
            except OSError:
                continue


def _chart(
    report: dict,
    lang: str,
    df: pd.DataFrame,
    chart_dir: str | Path | None,
    with_chart: bool,
    *,
    title: str = "GOLD M5",
    prefix: str = "hourly",
    interval: int | None = None,
    points: list[dict] | None = None,
    points_style: str = "A",
) -> Path | None:
    if not with_chart:
        return None
    try:
        from .chart import render_chart

        out_dir = Path(chart_dir) if chart_dir else ROOT / "data" / "charts"
        suffix = f"_{interval}" if interval is not None else ""
        name = f"{prefix}_{report['time']:%Y%m%d_%H%M}_{lang}{suffix}.png"
        image_path = render_chart(
            df,
            report,
            out_dir / name,
            lang=lang,
            title=title,
            points=points,
            points_style=points_style,
        )
        _cleanup_charts(out_dir)
        return image_path
    except Exception as exc:  # noqa: BLE001 - картинка не должна ломать отчёт
        print(f"[hourly] картинка не построена: {exc}")
        return None


def _prepare(
    report: dict,
    lang: str,
    df: pd.DataFrame,
    chart_dir: str | Path | None,
    with_chart: bool,
    *,
    title: str = "GOLD M5",
    prefix: str = "hourly",
    text: str | None = None,
) -> tuple[str, Path | None]:
    """Текст и картинка на одном языке."""
    body = text if text is not None else build_text(report, lang)
    image_path = _chart(
        report, lang, df, chart_dir, with_chart, title=title, prefix=prefix
    )
    return body, image_path


def _deliver(
    bot,
    recipients: list[tuple[str, str, int]],
    texts: dict[tuple[str, int], str],
    images: dict[tuple[str, int], Path | None],
    *,
    label: str | None = None,
) -> int:
    sent = 0
    for chat_id, user_lang, interval in recipients:
        text = texts[(user_lang, interval)]
        image_path = images.get((user_lang, interval))
        if image_path and image_path.exists():
            ok = bot.send_photo(chat_id, image_path, caption=text)
        else:
            ok = bot.send(chat_id, text)
        sent += 1 if ok else 0
        name = label or f"{interval}м"
        print(f"[hourly] {chat_id} ({user_lang}, {name}): {'ok' if ok else 'ошибка'}")
    print(f"[hourly] отправлено {sent} из {len(recipients)}")
    return sent


def send_report(
    cfg: dict,
    df: pd.DataFrame,
    lang: str = i18n.DEFAULT_LANG,
    hours: int = 24,
    telegram: bool = True,
    chart_dir: str | Path | None = None,
    with_chart: bool = True,
    to: str = "all",
    due: set[int] | None = None,
) -> dict:
    """Считает отчёт и отправляет.

    `to="all"` — каждому на его языке; `due` — какие интервалы отправляются
    сейчас (30/60), остальным получателям сообщение не идёт.
    """
    report = build_report(df, hours=hours)
    hourly_cfg = cfg.get("hourly", {}) or {}
    tol = float(hourly_cfg.get("touch_tol_points", 15)) * 0.01
    gap = float(hourly_cfg.get("touch_gap_points", 50)) * 0.01
    configured = hourly_cfg.get("intervals") or [60]
    default_interval = max(int(value) for value in (due or configured))
    merge = float(hourly_cfg.get("strong_merge_points", 100)) * 0.01
    points_style = str(hourly_cfg.get("points_style", "A"))
    chart_points = int(hourly_cfg.get("points_limit", 3))

    def text_for(user_lang: str, interval: int) -> str:
        extra = strong_block(df, interval, user_lang, tol, gap, merge=merge, limit=4)
        return build_text(report, user_lang, extra=extra)

    def chart_for(user_lang: str, interval: int) -> Path | None:
        points = strong_points(
            df, interval, tol, gap, merge=merge, limit=chart_points
        )
        return _chart(
            report,
            user_lang,
            df,
            chart_dir,
            with_chart,
            interval=interval,
            points=points,
            points_style=points_style,
        )

    if not telegram:
        print(text_for(lang, default_interval))
        image_path = chart_for(lang, default_interval)
        if image_path:
            print(f"\n[картинка] {image_path}")
        return report

    from .telegram import TelegramBot

    bot = TelegramBot.from_config(cfg)
    if not bot.enabled:
        print("[hourly] Telegram выключен")
        print(text_for(lang, default_interval))
        return report

    if to == "admin":
        recipients = (
            [(bot.admin_chat_id, lang, default_interval)] if bot.admin_chat_id else []
        )
    else:
        recipients = bot.report_recipients(due)
        if not recipients and bot.admin_chat_id and due is None:
            recipients = [(bot.admin_chat_id, lang, default_interval)]

    if not recipients:
        print("[hourly] нет получателей")
        return report

    keys = {(user_lang, interval) for _, user_lang, interval in recipients}
    texts = {key: text_for(*key) for key in keys}
    images = {key: chart_for(*key) for key in keys}
    _deliver(bot, recipients, texts, images)
    return report


def _zone_result(window: pd.DataFrame, zone, atr_value: float) -> str:
    """Как отработала зона после последнего касания: held / broken / пусто."""
    if atr_value <= 0 or window.empty:
        return ""
    tol = 0.2 * atr_value
    after = window[window["time"] >= zone.last_touch]
    if after.empty:
        return ""
    if zone.side == "sell":
        if bool((after["close"] > zone.price + tol).any()):
            return "broken"
        if float(after["close"].min()) <= zone.price - 0.4 * atr_value:
            return "held"
    else:
        if bool((after["close"] < zone.price - tol).any()):
            return "broken"
        if float(after["close"].max()) >= zone.price + 0.4 * atr_value:
            return "held"
    return ""


def daily_picks(report: dict, df: pd.DataFrame) -> list[dict]:
    """Сильные и сильнейшие зоны дня с результатом отработки."""
    window = df[df["time"] >= report["time"] - pd.Timedelta(hours=report["hours"])]
    zones = [z for z in report["zones"] if z.strength in ("strong", "strongest")]
    zones.sort(key=lambda z: (z.strength == "strongest", z.touches), reverse=True)
    return [{"zone": z, "result": _zone_result(window, z, report["atr"])} for z in zones]


def build_daily_text(report: dict, picks: list[dict], lang: str = i18n.DEFAULT_LANG) -> str:
    """Итоги дня: только сильные зоны, что с ними стало."""
    def t(key: str) -> str:
        return i18n.hourly_text(lang, key)

    stamp = report["time"]
    lines = [
        t("daily_title"),
        t("stamp").format(
            day=stamp.strftime("%d.%m"),
            time=stamp.strftime("%H:%M"),
            price=f"{report['price']:.2f}",
        ),
        t("range").format(
            low=f"{report['low']:.2f}",
            high=f"{report['high']:.2f}",
            span=f"{report['high'] - report['low']:.2f}",
        ),
        t("change").format(
            change=f"{report['change']:+.2f}",
            pct=f"{report['change_pct']:+.2f}",
        ),
        "",
    ]

    if picks:
        lines.append(t("daily_zones"))
        held = broken = 0
        for item in picks:
            zone = item["zone"]
            result = item["result"]
            held += 1 if result == "held" else 0
            broken += 1 if result == "broken" else 0
            result_key = {"held": "daily_held", "broken": "daily_broken"}.get(
                result, "daily_unknown"
            )
            lines.append(
                t("daily_zone").format(
                    price=f"{zone.price:.2f}",
                    strength=t(zone.strength),
                    touches=zone.touches,
                    result=t(result_key),
                )
            )
        lines.append("")
        lines.append(t("daily_summary").format(held=held, broken=broken))
    else:
        lines.append(t("daily_none"))

    bias = report["bias"]
    bias_key = {"SELL": "bias_sell", "BUY": "bias_buy"}.get(bias, "bias_flat")
    lines.append("")
    lines.append(t(bias_key))
    lines.append(t("note"))
    return "\n".join(lines)


def send_daily(
    cfg: dict,
    df: pd.DataFrame,
    lang: str = i18n.DEFAULT_LANG,
    hours: int = 24,
    telegram: bool = True,
    chart_dir: str | Path | None = None,
    with_chart: bool = True,
    to: str = "all",
    per_side: int = 3,
) -> dict:
    """Итоги дня по сильным зонам. По умолчанию всем активным получателям."""
    report = build_report(df, hours=hours, per_side=per_side)
    picks = daily_picks(report, df)
    chart_report = dict(report)
    chart_report["zones"] = [item["zone"] for item in picks] or report["zones"]

    def prepare(user_lang: str) -> tuple[str, Path | None]:
        return _prepare(
            chart_report,
            user_lang,
            df,
            chart_dir,
            with_chart,
            title=i18n.hourly_text(user_lang, "daily_chart_title"),
            prefix="daily",
            text=build_daily_text(chart_report, picks, user_lang),
        )

    if not telegram:
        text, image_path = prepare(lang)
        print(text)
        if image_path:
            print(f"\n[картинка] {image_path}")
        return report

    from .telegram import TelegramBot

    bot = TelegramBot.from_config(cfg)
    if not bot.enabled:
        print("[hourly] Telegram выключен")
        print(build_daily_text(report, picks, lang))
        return report

    if to == "admin" and bot.admin_chat_id:
        recipients = [(bot.admin_chat_id, lang, 0)]
    else:
        recipients = [
            (chat_id, user_lang, 0) for chat_id, user_lang, _ in bot.report_recipients()
        ]
    if not recipients:
        print("[hourly] нет получателей")
        return report

    langs = {user_lang for _, user_lang, _ in recipients}
    prepared = {user_lang: prepare(user_lang) for user_lang in langs}
    texts = {(user_lang, 0): value[0] for user_lang, value in prepared.items()}
    images = {(user_lang, 0): value[1] for user_lang, value in prepared.items()}
    _deliver(bot, recipients, texts, images, label="day")
    return report
