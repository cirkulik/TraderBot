"""Картинка часового отчёта: свечи M5 за окно + горизонтальные зоны (Pillow).

Pillow — единственная зависимость; если его нет, отчёт уйдёт только текстом.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from . import i18n

W, H = 1240, 680
LEFT, RIGHT, TOP, BOTTOM = 18, 185, 84, 46

BG = (13, 20, 31)
GRID = (28, 40, 56)
UP = (38, 166, 154)
DOWN = (239, 83, 80)
TEXT = (206, 216, 226)
MUTED = (120, 136, 154)
SELL = (235, 87, 87)
BUY = (38, 166, 154)
LAST = (240, 200, 80)

FONT_CANDIDATES = (
    ("C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/segoeui.ttf"),
    ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf"),
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ),
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for bold_path, regular_path in FONT_CANDIDATES:
        path = bold_path if bold else regular_path
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _dashed(
    draw: ImageDraw.ImageDraw,
    x0: int,
    x1: int,
    y: int,
    color,
    width: int = 1,
    dash: int = 7,
    gap: int = 5,
) -> None:
    x = x0
    while x < x1:
        draw.line([(x, y), (min(x + dash, x1), y)], fill=color, width=width)
        x += dash + gap


def render_chart(
    df: pd.DataFrame,
    report: dict,
    path: str | Path,
    lang: str = i18n.DEFAULT_LANG,
    title: str = "GOLD M5",
) -> Path:
    """Рисует свечи окна, зоны и последнюю цену. Возвращает путь к PNG."""
    window = df[df["time"] >= report["time"] - pd.Timedelta(hours=report["hours"])]
    window = window.reset_index(drop=True)
    if window.empty:
        window = df.tail(2).reset_index(drop=True)

    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)

    plot_w = W - LEFT - RIGHT
    plot_h = H - TOP - BOTTOM

    high = float(window["high"].max())
    low = float(window["low"].min())
    pad = (high - low) * 0.06 or 1.0
    top_price, bottom_price = high + pad, low - pad

    def y_of(price: float) -> float:
        return TOP + (top_price - price) / (top_price - bottom_price) * plot_h

    # Сетка и шкала цены
    f_small = _font(15)
    for i in range(6):
        price = bottom_price + (top_price - bottom_price) * i / 5
        y = y_of(price)
        draw.line([(LEFT, y), (LEFT + plot_w, y)], fill=GRID, width=1)
        draw.text((LEFT + plot_w + 8, y), f"{price:.1f}", fill=MUTED, font=f_small, anchor="lm")

    # Свечи
    n = len(window)
    step = max(1.0, plot_w / n)
    body_w = max(1, int(step * 0.7))
    for i, row in window.iterrows():
        x = LEFT + step * (i + 0.5)
        color = UP if row["close"] >= row["open"] else DOWN
        draw.line([(x, y_of(row["high"])), (x, y_of(row["low"]))], fill=color, width=1)
        y1, y2 = y_of(row["open"]), y_of(row["close"])
        if abs(y2 - y1) < 1:
            y2 = y1 + 1
        draw.rectangle(
            [x - body_w / 2, min(y1, y2), x + body_w / 2, max(y1, y2)],
            fill=color,
        )

    # Зоны: подписи раскладываем так, чтобы не наезжали друг на друга
    y_last = y_of(report["price"])
    f_zone = _font(14, bold=True)
    items = []
    for zone in report["zones"]:
        y = y_of(zone.price)
        if TOP <= y <= TOP + plot_h:
            items.append({"y": y, "zone": zone})
    items.append({"y": y_last, "zone": None})
    items.sort(key=lambda item: item["y"])

    gap = 22
    previous = None
    for item in items:
        label_y = item["y"]
        if previous is not None and label_y - previous < gap:
            label_y = previous + gap
        item["label_y"] = min(label_y, TOP + plot_h)
        previous = item["label_y"]

    for item in items:
        zone = item["zone"]
        if zone is None:
            continue
        color = SELL if zone.side == "sell" else BUY
        width = 2 if zone.strength in ("strong", "strongest") else 1
        _dashed(draw, LEFT, LEFT + plot_w, int(item["y"]), color, width=width)
        text = f"{i18n.hourly_text(lang, zone.strength)} {zone.price:.2f}"
        bbox = draw.textbbox((0, 0), text, font=f_zone)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        box_x = LEFT + plot_w + 4
        draw.rectangle(
            [box_x, item["label_y"] - th / 2 - 3, box_x + tw + 10, item["label_y"] + th / 2 + 3],
            fill=color,
        )
        draw.text(
            (box_x + 5, item["label_y"]),
            text,
            fill=(10, 15, 22),
            font=f_zone,
            anchor="lm",
        )

    # Последняя цена (поверх зон, чтобы не сливалась с подписью)
    _dashed(draw, LEFT, LEFT + plot_w, int(y_last), LAST, width=1, dash=3, gap=3)
    f_last = _font(15, bold=True)
    last_text = f"{report['price']:.2f}"
    last_bbox = draw.textbbox((0, 0), last_text, font=f_last)
    last_w = last_bbox[2] - last_bbox[0]
    last_h = last_bbox[3] - last_bbox[1]
    draw.rectangle(
        [LEFT + plot_w + 4, y_last - last_h / 2 - 3, LEFT + plot_w + last_w + 14, y_last + last_h / 2 + 3],
        fill=LAST,
    )
    draw.text(
        (LEFT + plot_w + 9, y_last),
        last_text,
        fill=(10, 15, 22),
        font=f_last,
        anchor="lm",
    )

    # Заголовок
    bias = report["bias"]
    bias_color = SELL if bias == "SELL" else BUY if bias == "BUY" else MUTED
    draw.text((LEFT, 26), title, fill=TEXT, font=_font(24, bold=True), anchor="lm")
    draw.text(
        (LEFT + 150, 26),
        i18n.hourly_text(lang, "chart_hours").format(hours=report["hours"]),
        fill=MUTED,
        font=_font(18),
        anchor="lm",
    )
    draw.text((LEFT + 250, 26), "Bias:", fill=MUTED, font=_font(18), anchor="lm")
    draw.text((LEFT + 300, 26), bias, fill=bias_color, font=_font(22, bold=True), anchor="lm")

    change = report["change"]
    arrow = "▲" if change >= 0 else "▼"
    draw.text(
        (W - RIGHT, 26),
        f"{arrow} {change:+.2f} ({report['change_pct']:+.2f}%)",
        fill=UP if change >= 0 else DOWN,
        font=_font(18, bold=True),
        anchor="rm",
    )

    # Время по низу
    f_time = _font(14)
    ticks = 6
    for i in range(ticks):
        idx = int((n - 1) * i / (ticks - 1))
        x = LEFT + step * (idx + 0.5)
        x = max(LEFT + 40, min(LEFT + plot_w - 40, x))
        stamp = window["time"].iloc[idx]
        draw.text((x, H - 18), stamp.strftime("%d.%m %H:%M"), fill=MUTED, font=f_time, anchor="mm")

    draw.line([(LEFT, TOP), (LEFT, TOP + plot_h)], fill=GRID, width=1)
    draw.line([(LEFT, TOP + plot_h), (LEFT + plot_w, TOP + plot_h)], fill=GRID, width=1)

    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    image.save(out)
    return out
