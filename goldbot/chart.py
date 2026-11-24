"""Картинка часового отчёта: свечи M5 за окно + горизонтальные зоны (Pillow).

Pillow — единственная зависимость; если его нет, отчёт уйдёт только текстом.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from . import i18n

W, H = 1240, 680
LEFT, RIGHT, TOP, BOTTOM = 18, 226, 84, 46

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

    # Зоны и текущая цена: подписи в правой колонке.
    y_last = y_of(report["price"])
    f_zone = _font(14, bold=True)
    f_last = _font(13, bold=True)

    def _text_size(text: str, font) -> tuple[float, float]:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]

    items: list[dict] = []
    for zone in report["zones"]:
        y = y_of(zone.price)
        if TOP <= y <= TOP + plot_h:
            text = f"{i18n.hourly_text(lang, zone.strength)} {zone.price:.2f}"
            tw, th = _text_size(text, f_zone)
            items.append(
                {
                    "y": y,
                    "zone": zone,
                    "text": text,
                    "font": f_zone,
                    "w": tw + 10,
                    "h": th + 6,
                    "kind": "zone",
                }
            )
    last_text = f"{report['price']:.2f}"
    tw, th = _text_size(last_text, f_last)
    items.append(
        {
            "y": y_last,
            "zone": None,
            "text": last_text,
            "font": f_last,
            "w": tw + 8,
            "h": th + 4,
            "kind": "last",
        }
    )
    items.sort(key=lambda item: item["y"])

    # Плашки зон одной высоты — так столбик выглядит ровно
    zone_h = max((it["h"] for it in items if it["kind"] == "zone"), default=0)
    for item in items:
        if item["kind"] == "zone":
            item["h"] = zone_h

    # Подписи, чьи плашки наезжают друг на друга по вертикали, ставим в одну
    # строку: | сильная 3647.66 | 3643.08 |
    ROW_STEP = 6
    STACK_GAP = 4
    avail_w = W - (LEFT + plot_w + 4) - 4
    rows: list[dict] = []
    for item in items:
        if rows:
            row = rows[-1]
            gap = item["y"] - row["items"][-1]["y"]
            overlap = gap < (row["h"] + item["h"]) / 2 + 1
            width = sum(it["w"] for it in row["items"]) + ROW_STEP * len(row["items"]) + item["w"]
            if overlap and width <= avail_w:
                row["items"].append(item)
                row["h"] = max(row["h"], item["h"])
                continue
        rows.append({"items": [item], "h": item["h"]})

    # Строку с текущей ценой держим на её уровне, остальные — по средней цене
    limit_top = TOP + 6
    limit_bottom = TOP + plot_h - 6
    for row in rows:
        prices = [it["y"] for it in row["items"]]
        last_y = [it["y"] for it in row["items"] if it["kind"] == "last"]
        row["y"] = last_y[0] if last_y else sum(prices) / len(prices)
        row["y"] = min(max(row["y"], limit_top + row["h"] / 2), limit_bottom - row["h"] / 2)

    previous_bottom = None
    for row in rows:
        if previous_bottom is not None:
            row["y"] = max(row["y"], previous_bottom + STACK_GAP + row["h"] / 2)
        previous_bottom = row["y"] + row["h"] / 2

    overflow = rows[-1]["y"] + rows[-1]["h"] / 2 - limit_bottom
    if overflow > 0:
        shift = min(overflow, rows[0]["y"] - rows[0]["h"] / 2 - limit_top)
        if shift > 0:
            for row in rows:
                row["y"] -= shift

    x0 = LEFT + plot_w + 4
    for row in rows:
        x = x0
        for item in row["items"]:
            item["label_y"] = row["y"]
            item["box_x"] = x
            x += item["w"] + ROW_STEP

    for item in items:
        zone = item["zone"]
        if zone is None:
            continue
        color = SELL if zone.side == "sell" else BUY
        width = 2 if zone.strength in ("strong", "strongest") else 1
        _dashed(draw, LEFT, LEFT + plot_w, int(item["y"]), color, width=width)
        item["color"] = color

    for item in items:
        zone = item["zone"]
        if zone is None:
            continue
        top = item["label_y"] - item["h"] / 2
        draw.rectangle(
            [item["box_x"], top, item["box_x"] + item["w"], top + item["h"]],
            fill=item["color"],
        )
        draw.text(
            (item["box_x"] + 5, item["label_y"]),
            item["text"],
            fill=(10, 15, 22),
            font=item["font"],
            anchor="lm",
        )

    # Последняя цена
    _dashed(draw, LEFT, LEFT + plot_w, int(y_last), LAST, width=1, dash=3, gap=3)
    last_item = next(it for it in items if it["kind"] == "last")
    top = last_item["label_y"] - last_item["h"] / 2
    draw.rectangle(
        [last_item["box_x"], top, last_item["box_x"] + last_item["w"], top + last_item["h"]],
        fill=LAST,
    )
    draw.text(
        (last_item["box_x"] + 4, last_item["label_y"]),
        last_item["text"],
        fill=(10, 15, 22),
        font=last_item["font"],
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
