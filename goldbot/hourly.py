"""Часовой отчёт: текст, картинка, отправка в Telegram."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import i18n
from .config import ROOT
from .zones import build_report


def build_text(report: dict, lang: str = i18n.DEFAULT_LANG) -> str:
    """Собирает сообщение по данным отчёта."""
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


def send_report(
    cfg: dict,
    df: pd.DataFrame,
    lang: str = i18n.DEFAULT_LANG,
    hours: int = 24,
    telegram: bool = True,
    chart_dir: str | Path | None = None,
) -> dict:
    """Считает отчёт, рисует картинку и отправляет. Возвращает данные отчёта."""
    report = build_report(df, hours=hours)
    text = build_text(report, lang)

    image_path = None
    try:
        from .chart import render_chart

        out_dir = Path(chart_dir) if chart_dir else ROOT / "data" / "charts"
        name = f"hourly_{report['time']:%Y%m%d_%H%M}.png"
        image_path = render_chart(df, report, out_dir / name, lang=lang)
    except Exception as exc:  # noqa: BLE001 - картинка не должна ломать отчёт
        print(f"[hourly] картинка не построена: {exc}")

    if not telegram:
        print(text)
        if image_path:
            print(f"\n[картинка] {image_path}")
        return report

    from .telegram import TelegramBot

    bot = TelegramBot.from_config(cfg)
    if not bot.enabled:
        print("[hourly] Telegram выключен")
        print(text)
        return report

    chat_id = bot.admin_chat_id
    if not chat_id:
        print("[hourly] не задан chat_id")
        print(text)
        return report

    if image_path and image_path.exists():
        ok = bot.send_photo(chat_id, image_path, caption=text)
    else:
        ok = bot.send(chat_id, text)
    print(f"[hourly] отправлено: {ok}")
    return report
