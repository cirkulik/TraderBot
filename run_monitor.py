"""Проверка сетапов на текущем окне и сравнение с прошлой проверкой.

Смысл: работаем по текущему режиму, а не по всей истории. Сетап, который
сломался сейчас, выключаем; сетап, который в минусе только на коротком окне,
оставляем.

Запуск:
    python run_monitor.py                # последние 30 дней
    python run_monitor.py --days 15      # последние 15 дней (раннее предупреждение)
    python run_monitor.py --days 60      # последние 60 дней
    python run_monitor.py --since        # с сохранённой даты внедрения сетапа
    python run_monitor.py --since today  # с сегодняшнего дня
    python run_monitor.py --since 2026-11-17
    python run_monitor.py --refresh      # докачать историю из MT5
    python run_monitor.py --stats        # статистика 7/14 дней для карточек в TG

Окно «с даты» удобно после смены набора сетапов: считается только то, что
бот сделал после внедрения. У каждого окна свой снимок в
data/monitor_last.json, поэтому проверки за 30 дней, 60 дней и с даты
не мешают друг другу. Дата внедрения запоминается в data/monitor_anchor.json
(дальше достаточно `--since` без аргумента).

Что показывает:
  * по каждому включённому сетапу — сделки, winrate, profit factor, R, просадку;
  * соло-цифры (сетап сам по себе) и фактические в портфеле: в портфеле
    открыта одна позиция за раз, поэтому часть сигналов вытесняется;
  * сколько сигналов реально ушло в журнал за окно;
  * что изменилось с прошлой проверки (новые сделки и добавленный R);
  * какие сетапы ушли в минус и сколько сделок для этого нужно.

Правило: сетап выключаем, если он в минусе и за 30, и за 60 дней.

История проверок хранится в data/monitor_last.json (перезаписывается
каждым запуском).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from goldbot.backtest import run_backtest, trades_to_frame
from goldbot.config import DATA_DIR, ROOT, load_config
from goldbot.data import (
    connect,
    ensure_symbol,
    get_closed_rates,
    history_cache_path,
    load_history,
    shutdown,
)
from goldbot.stats import setup_stats

STATE_PATH = DATA_DIR / "monitor_last.json"
STATS_PATH = DATA_DIR / "setup_stats.json"
ANCHOR_PATH = DATA_DIR / "monitor_anchor.json"
MIN_TRADES = 10          # меньше — статистики нет
WARN_TRADES = 5          # для сравнения с прошлой проверкой
SHORT_WINDOW = 20        # окно короче — только раннее предупреждение
WARMUP_DAYS = 10         # история до окна, чтобы индикаторы успели сойтись


def refresh_history(cfg: dict) -> pd.DataFrame:
    """Докачивает свежие бары к кешу, не теряя старую историю."""
    symbol, tf = cfg["symbol"], cfg["timeframe"]
    cache = history_cache_path(symbol, tf)
    old = load_history(symbol, tf) if cache.exists() else pd.DataFrame()
    connect()
    try:
        ensure_symbol(symbol)
        fresh = get_closed_rates(symbol, tf, 3000)
    finally:
        shutdown()
    if old.empty:
        fresh.to_csv(cache, index=False)
        return fresh
    new = fresh[fresh["time"] > old["time"].max()]
    if new.empty:
        return old
    merged = (
        pd.concat([old, new], ignore_index=True)
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )
    merged.to_csv(cache, index=False)
    print(f"История обновлена: +{len(new)} баров, последний {merged['time'].iloc[-1]}")
    return merged


def dd_r(trades: pd.DataFrame) -> float:
    if trades.empty:
        return 0.0
    eq = trades["r_multiple"].cumsum()
    return float((eq.cummax() - eq).max())


def one(
    df: pd.DataFrame, cfg: dict, name: str, setup: dict, since: pd.Timestamp
) -> dict:
    cfg_try = {**cfg, "strategy": {**cfg["strategy"], "setups": {name: setup}}}
    t = trades_to_frame(run_backtest(df, cfg_try).trades)
    if not t.empty and "r_multiple" in t.columns:
        t = t[t["signal_time"] >= since]
    else:
        t = pd.DataFrame(columns=["r_multiple"])
    wins = int((t["r_multiple"] > 0).sum()) if len(t) else 0
    gw = float(t.loc[t["r_multiple"] > 0, "r_multiple"].sum()) if len(t) else 0.0
    gl = float(-t.loc[t["r_multiple"] < 0, "r_multiple"].sum()) if len(t) else 0.0
    pf = round(gw / gl, 2) if gl else None
    return {
        "n": len(t),
        "wins": wins,
        "wr": round(100 * wins / len(t), 1) if len(t) else 0.0,
        "pf": pf,
        "r": round(float(t["r_multiple"].sum()), 1) if len(t) else 0.0,
        "dd": round(dd_r(t), 1),
    }


def journal_counts(cfg: dict, start_utc: pd.Timestamp) -> dict[str, int]:
    path = ROOT / cfg["live"].get("journal_path", "data/signals.csv")
    if not path.exists():
        return {}
    try:
        j = pd.read_csv(path)
    except Exception:  # noqa: BLE001 - битый журнал не должен ломать проверку
        return {}
    if "created_utc" not in j.columns or j.empty:
        return {}
    j["created_utc"] = pd.to_datetime(j["created_utc"], errors="coerce", utc=True)
    j = j.dropna(subset=["created_utc"])
    sub = j[j["created_utc"].dt.tz_localize(None) >= start_utc]
    return sub["setup"].value_counts().to_dict()


def executor_results(cfg: dict) -> dict[str, dict]:
    path = ROOT / cfg["execution"].get("state_path", "data/executor.csv")
    if not path.exists():
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:  # noqa: BLE001
        return {}
    if "status" not in df.columns:
        return {}
    closed = df[df["status"] == "CLOSED"].copy()
    if closed.empty:
        return {}
    closed["result_r"] = pd.to_numeric(closed["result_r"], errors="coerce")
    out: dict[str, dict] = {}
    for setup, group in closed.groupby("setup"):
        out[str(setup)] = {
            "n": int(len(group)),
            "r": round(float(group["result_r"].sum()), 1),
        }
    return out


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def load_anchor() -> str | None:
    """Дата внедрения текущего набора сетапов (для окна «с даты»)."""
    if not ANCHOR_PATH.exists():
        return None
    try:
        data = json.loads(ANCHOR_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    value = data.get("since")
    return str(value) if value else None


def save_anchor(value: str) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ANCHOR_PATH.write_text(
        json.dumps({"since": value}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def window_id(window_key: dict) -> str:
    """Ключ снимка: у каждого окна своя история проверок."""
    if window_key["type"] == "since":
        return f"since:{window_key['since']}"
    return f"days:{window_key['days']}"


def status(row: dict, delta_r: float | None, new_trades: int | None) -> str:
    """Статус по фактическому вкладу в портфель, а не по соло-цифрам."""
    if row["n_p"] == 0:
        return "нет сделок"
    if row["r_p"] < 0:
        if row["n_p"] < MIN_TRADES:
            return "В МИНУСЕ (мало сд.)"
        return "В МИНУСЕ"
    if delta_r is not None and new_trades and new_trades >= WARN_TRADES and delta_r < 0:
        return "ухудшился"
    if row["n_p"] < MIN_TRADES:
        return "мало сделок"
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверка сетапов GoldBot")
    parser.add_argument("--days", type=int, default=30, help="окно в днях (по умолчанию 30)")
    parser.add_argument(
        "--since",
        nargs="?",
        const="saved",
        default=None,
        help="считать с даты (ГГГГ-ММ-ДД), 'today' или без значения — сохранённая дата",
    )
    parser.add_argument("--refresh", action="store_true", help="докачать историю из MT5")
    parser.add_argument("--no-save", action="store_true", help="не сохранять снимок")
    parser.add_argument(
        "--stats",
        action="store_true",
        help="только статистика 7/14 дней для карточек сигналов (пишет data/setup_stats.json)",
    )
    args = parser.parse_args()

    cfg = load_config()
    df = refresh_history(cfg) if args.refresh else load_history(
        cfg["symbol"], cfg["timeframe"]
    )
    if df.empty:
        print("Нет истории: запустите с --refresh")
        return 1

    last_bar = df["time"].iloc[-1]

    if args.stats:
        stats = setup_stats(df, cfg, last_bar=last_bar)
        payload = {
            "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "last_bar": str(last_bar),
            "windows": stats,
        }
        STATS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Статистика для карточек, бар {last_bar:%d.%m %H:%M}:")
        for days, rows in stats.items():
            print(f"  --- {days} дней: сделок / винрейт / вытеснено ---")
            for name, v in rows.items():
                print(
                    f"    {name:<16} {v['n']:>2} / {v['wr']:>5.1f}% / {v['displaced']}"
                )
        print(f"\nСохранено: {STATS_PATH}")
        return 0

    now_utc = pd.Timestamp(datetime.now(timezone.utc).replace(tzinfo=None))
    offset_h = int(round((last_bar - now_utc).total_seconds() / 3600))

    since_value: str | None = None
    if args.since:
        raw = args.since
        if raw == "saved":
            since_value = load_anchor() or str(last_bar.date())
        elif raw.lower() in ("today", "сегодня"):
            since_value = str(last_bar.date())
        else:
            try:
                since_value = str(pd.Timestamp(raw).date())
            except Exception:  # noqa: BLE001
                print(f"Не понял дату: {raw}. Пример: --since 2026-11-17")
                return 1
        start_server = pd.Timestamp(since_value)
        window_key = {"type": "since", "since": since_value}
    else:
        start_server = last_bar - pd.Timedelta(days=args.days)
        window_key = {"type": "days", "days": args.days}

    start_utc = start_server - pd.Timedelta(hours=offset_h)
    window = df[df["time"] >= start_server].reset_index(drop=True)
    if window.empty:
        print(f"В окне нет баров (с {start_server:%d.%m.%Y}). "
              "Проверьте дату или запустите с --refresh")
        return 1
    span_days = int((last_bar - start_server).total_seconds() // 86400) + 1

    # Индикаторы считаются с запасом истории до окна: иначе EMA200 на M15
    # не успевает сойтись и фильтры старшего ТФ врут на первых днях окна.
    warm = df[df["time"] >= start_server - pd.Timedelta(days=WARMUP_DAYS)].reset_index(drop=True)

    setups = {
        k: v for k, v in cfg["strategy"]["setups"].items() if v.get("enabled", True)
    }
    base_cfg = {**cfg, "strategy": {**cfg["strategy"], "setups": setups}}
    bt = trades_to_frame(run_backtest(warm, base_cfg).trades)
    if bt.empty or "r_multiple" not in bt.columns:
        bt = pd.DataFrame(columns=["setup", "r_multiple", "exit_time", "signal_time"])
    else:
        bt = bt[bt["signal_time"] >= start_server]
    port_wins = int((bt["r_multiple"] > 0).sum()) if len(bt) else 0
    port_wr = round(100 * port_wins / len(bt), 1) if len(bt) else 0.0
    port_gw = float(bt.loc[bt["r_multiple"] > 0, "r_multiple"].sum()) if len(bt) else 0.0
    port_gl = float(-bt.loc[bt["r_multiple"] < 0, "r_multiple"].sum()) if len(bt) else 0.0
    port_pf = port_gw / port_gl if port_gl else float("inf")

    journal = journal_counts(cfg, start_utc)
    live = executor_results(cfg)
    state = load_state()
    wid = window_id(window_key)
    snapshots = state.get("windows", {})
    prev_block = snapshots.get(wid)
    if prev_block is None and window_key["type"] == "days" and state.get("days") == window_key["days"]:
        prev_block = state  # старый формат снимка (один на файл)
    prev = (prev_block or {}).get("setups", {})

    print()
    print("=" * 96)
    if window_key["type"] == "since":
        print(f"  GOLDBOT — проверка сетапов с {start_server:%d.%m.%Y}")
    else:
        print(f"  GOLDBOT — проверка сетапов за {args.days} дней")
    print(f"  окно: {start_server:%d.%m %H:%M} .. {last_bar:%d.%m %H:%M} "
          f"(сервер), {span_days} дн., {len(window)} баров")
    if prev_block is None:
        print("  первый замер по этому окну — сравнение появится со следующего запуска")
    print("=" * 96)
    pf_text = "—" if port_pf == float("inf") else f"{port_pf:.2f}"
    print(f"  портфель: {len(bt)} сделок, WR {port_wr:.1f}%, "
          f"PF {pf_text}, "
          f"{bt['r_multiple'].sum():+.1f} R, просадка {dd_r(bt):.1f} R")
    if len(bt) < 30:
        print("  выборка маленькая — это точка отсчёта, выводы делать рано")
    if window_key["type"] == "days" and args.days < SHORT_WINDOW:
        print(f"  короткое окно ({args.days} дн.) — раннее предупреждение, не приговор")
    if journal:
        total = sum(journal.values())
        print(f"  сигналов в журнале за окно: {total}")
    print("  соло — сетап сам по себе; «в портфеле» — фактические сделки "
          "(одна позиция за раз)")
    print()

    header = (
        f"  {'сетап':<20}{'сделок':>7}{'WR':>7}{'PF':>7}{'R':>8}{'DD':>7}"
        f"{'сигн.':>7}   {'с прошлой проверки':<26}{'статус'}"
    )
    print(header)
    print("  " + "-" * 92)

    rows: dict[str, dict] = {}
    problems: list[str] = []
    for name, setup in setups.items():
        row = one(warm, cfg, name, setup, start_server)
        pt = bt[bt["setup"] == name]
        row["n_p"] = int(len(pt))
        row["r_p"] = round(float(pt["r_multiple"].sum()), 1) if len(pt) else 0.0
        rows[name] = row
        old = prev.get(name)
        delta_text = "—"
        delta_r = None
        new_trades = None
        if old and "n_p" in old:
            new_trades = row["n_p"] - int(old.get("n_p", 0))
            delta_r = round(row["r_p"] - float(old.get("r_p", 0.0)), 1)
            delta_text = f"{new_trades:+d} сд., {delta_r:+.1f} R"
        state_text = status(row, delta_r, new_trades)
        if state_text.startswith("В МИНУСЕ") or state_text == "ухудшился":
            problems.append(
                f"{name}: {state_text.lower()} ({row['r_p']:+.1f} R в портфеле, "
                f"{row['n_p']} сделок)"
            )
        live_part = live.get(name)
        live_text = f"{live_part['n']} / {live_part['r']:+.1f}R" if live_part else "—"
        pf_text = "—" if row["pf"] is None else f"{row['pf']:.2f}"
        print(
            f"  {name:<20}{row['n']:>7}{row['wr']:>6.1f}%{pf_text:>7}"
            f"{row['r']:>+8.1f}{row['dd']:>7.1f}{journal.get(name, 0):>7}"
            f"   {delta_text:<26}{state_text}"
        )
        displaced = row["n"] - row["n_p"]
        extra = f", вытеснено {displaced}" if displaced > 0 else ""
        print(
            f"  {'':<20}└ в портфеле: {row['n_p']} сд., {row['r_p']:+.1f} R{extra}"
        )
        if live_part:
            print(f"  {'':<20}  факт. сделки исполнителя: {live_text}")

    print("  " + "-" * 92)
    if problems:
        print("\n  Требуют решения:")
        for line in problems:
            print(f"    - {line}")
        if window_key["type"] == "days" and args.days < SHORT_WINDOW:
            print("  Короткое окно: это раннее предупреждение. Решение — по 30 и 60 дням.")
        else:
            print("  Отключать сетап стоит, если он в минусе и за 30, и за 60 дней "
                  "(проверьте --days 60).")
    else:
        if all(r["n_p"] == 0 for r in rows.values()):
            print("\n  Сделок в этом окне пока нет — это точка отсчёта.")
        else:
            print("\n  Всё в плюсе: сетапы можно не трогать.")

    if not args.no_save:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        snapshots[wid] = {
            "checked_at": now_utc.isoformat(timespec="seconds"),
            "window": window_key,
            "last_bar": str(last_bar),
            "portfolio": {
                "n": len(bt),
                "wr": port_wr,
                "pf": None if port_pf == float("inf") else round(port_pf, 2),
                "r": round(float(bt["r_multiple"].sum()), 1),
            },
            "setups": rows,
        }
        STATE_PATH.write_text(
            json.dumps(
                {
                    "checked_at": now_utc.isoformat(timespec="seconds"),
                    "windows": snapshots,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        if since_value:
            save_anchor(since_value)
            print(f"  Дата отсчёта: {since_value} (запомнена в {ANCHOR_PATH.name})")
        print(f"\n  Снимок сохранён: {STATE_PATH}  [{wid}]")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
