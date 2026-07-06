from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
HISTORY = ASSETS / "chip_history"
DATA_OUT = ASSETS / "high_price_watch.json"
REPORT_OUT = ROOT / "reports" / "high-price-watch.html"
MANIFEST_PATH = ROOT / "manifest.json"
CATEGORIES_PATH = ROOT / "categories.json"
TPE = ZoneInfo("Asia/Taipei")

MIN_PRICE = 100.0
DAILY_TOP_N = 40
MATRIX_DAYS = 30
MATRIX_ROWS = 30

SOURCE_LABELS = {
    "chip_concentration": "分點集中度",
    "three_factor_ranking": "三因子排行",
    "tri_source_lamp": "三源燈號",
    "shareholder_divergence": "大股東背離",
}


def read_json(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def to_float(value) -> float | None:
    if value in (None, "", "—"):
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


RET_KEYS = ("ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_30d", "ret_60d")


def is_stock_code(code: str) -> bool:
    code = str(code or "").strip()
    if not code.isdigit() or len(code) != 4:
        return False
    # Exclude Taiwan ETF / ETN families for this 個股 view.
    return not code.startswith("00")


def add_observation(observations: dict, date: str, row: dict, source: str) -> None:
    code = str(row.get("code") or "").strip()
    if not is_stock_code(code):
        return
    close = to_float(row.get("close"))
    if close is None:
        close = to_float(row.get("close_latest"))
    if close is None or close <= 0:
        return

    chg_pct = to_float(row.get("chg_pct"))
    if chg_pct is None:
        chg_pct = to_float(row.get("close_chg_pct"))
    if chg_pct is None:
        chg_pct = to_float(row.get("ret_1d"))
    future_returns = {
        key.replace("ret_", "").replace("d", ""): to_float(row.get(key))
        for key in RET_KEYS
        if row.get(key) is not None
    }

    by_code = observations.setdefault(date, {})
    current = by_code.get(code)
    if current is None:
        by_code[code] = {
            "code": code,
            "name": str(row.get("name") or "").strip(),
            "close": close,
            "chg_pct": chg_pct,
            "future_returns": future_returns,
            "sources": {source},
            "source_dates": {source: date},
        }
        return

    # Same stock can arrive from several views. Keep the most informative row,
    # but preserve every source for traceability.
    current["sources"].add(source)
    current.setdefault("source_dates", {})[source] = date
    current.setdefault("future_returns", {}).update(future_returns)
    if close >= current["close"]:
        current["close"] = close
        if row.get("name"):
            current["name"] = str(row["name"]).strip()
        if chg_pct is not None:
            current["chg_pct"] = chg_pct
        if future_returns:
            current["future_returns"] = future_returns


def collect_observations() -> dict:
    observations: dict[str, dict[str, dict]] = {}

    for path in sorted((HISTORY / "chip_concentration").glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = read_json(path)
        for window in data.get("windows", []):
            if window.get("key") != "1d":
                continue
            date = window.get("end_date") or path.stem
            for side in ("buy_top", "sell_top"):
                for row in window.get(side, []):
                    add_observation(observations, date, row, "chip_concentration")

    for path in sorted((HISTORY / "three_factor_ranking").glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = read_json(path)
        date = data.get("signal_date") or data.get("price_date") or path.stem
        for row in data.get("rankings", []):
            add_observation(observations, date, row, "three_factor_ranking")

    for path in sorted((HISTORY / "tri_source_lamp").glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = read_json(path)
        date = data.get("broker_end") or path.stem
        for group in ("triple_buy", "triple_sell", "partial_buy", "partial_sell"):
            for row in data.get(group, []):
                add_observation(observations, date, row, "tri_source_lamp")

    for path in sorted((HISTORY / "shareholder_divergence").glob("*.json")):
        if path.name.startswith("_"):
            continue
        data = read_json(path)
        date = path.stem
        for rows in data.get("quadrant_rankings", {}).values():
            for row in rows:
                add_observation(observations, date, row, "shareholder_divergence")

    return observations


def merge_latest_source_snapshots(observations: dict) -> None:
    """Build the latest day from each source's freshest available snapshot."""
    if not observations:
        return
    latest_date = max(observations)
    latest_by_source: dict[str, str] = {}
    for date, rows in observations.items():
        for item in rows.values():
            for source in item.get("sources", set()):
                if date > latest_by_source.get(source, ""):
                    latest_by_source[source] = date

    target = observations.setdefault(latest_date, {})
    for source, source_date in latest_by_source.items():
        for item in observations.get(source_date, {}).values():
            if source not in item.get("sources", set()):
                continue
            code = item["code"]
            current = target.get(code)
            if current is None:
                target[code] = {
                    "code": code,
                    "name": item.get("name") or "",
                    "close": item.get("close"),
                    "chg_pct": item.get("chg_pct"),
                    "future_returns": dict(item.get("future_returns", {})),
                    "sources": {source},
                    "source_dates": {source: source_date},
                }
                continue
            current.setdefault("sources", set()).add(source)
            current.setdefault("source_dates", {})[source] = source_date
            if source_date == latest_date:
                current["close"] = item.get("close")
                current["chg_pct"] = item.get("chg_pct")
                current["future_returns"] = dict(item.get("future_returns", {}))
                if item.get("name"):
                    current["name"] = item["name"]


def build_daily_rankings(
    observations: dict,
    min_price: float = MIN_PRICE,
    top_n: int = DAILY_TOP_N,
) -> list[dict]:
    prev_rank: dict[str, int] = {}
    prev_streak: dict[str, int] = {}
    daily: list[dict] = []

    for date in sorted(observations):
        candidates = [
            item for item in observations[date].values()
            if item.get("close") is not None and item["close"] >= min_price
        ]
        candidates = sorted(
            candidates,
            key=lambda r: (-float(r["close"]), str(r["code"])),
        )[:top_n]

        rows: list[dict] = []
        today_rank: dict[str, int] = {}
        today_streak: dict[str, int] = {}
        for idx, item in enumerate(candidates, start=1):
            code = item["code"]
            old_rank = prev_rank.get(code)
            streak = prev_streak.get(code, 0) + 1 if old_rank else 1
            today_rank[code] = idx
            today_streak[code] = streak
            rows.append({
                "rank": idx,
                "code": code,
                "name": item.get("name") or "",
                "close": round(float(item["close"]), 2),
                "chg_pct": round(float(item["chg_pct"]), 2) if item.get("chg_pct") is not None else None,
                "previous_rank": old_rank,
                "rank_change": (old_rank - idx) if old_rank else None,
                "is_new": old_rank is None,
                "streak": streak,
                "future_returns": item.get("future_returns", {}),
                "sources": sorted(item.get("sources", [])),
                "source_dates": {
                    source: item.get("source_dates", {}).get(source, date)
                    for source in sorted(item.get("sources", []))
                },
            })

        daily.append({
            "date": date,
            "count": len(rows),
            "rows": rows,
        })
        prev_rank = today_rank
        prev_streak = today_streak

    return daily


def build_matrix(daily: list[dict]) -> list[dict]:
    recent = daily[-MATRIX_DAYS:]
    latest_rows = recent[-1]["rows"] if recent else []
    codes = [r["code"] for r in latest_rows[:MATRIX_ROWS]]
    by_date_code = {
        item["date"]: {r["code"]: r for r in item["rows"]}
        for item in recent
    }
    names = {
        r["code"]: r.get("name", "")
        for item in recent
        for r in item["rows"]
    }

    matrix = []
    for code in codes:
        cells = []
        appearances = 0
        for item in recent:
            row = by_date_code[item["date"]].get(code)
            if row:
                appearances += 1
                cells.append({
                    "date": item["date"],
                    "rank": row["rank"],
                    "close": row["close"],
                    "chg_pct": row["chg_pct"],
                })
            else:
                cells.append({"date": item["date"], "rank": None, "close": None, "chg_pct": None})
        matrix.append({
            "code": code,
            "name": names.get(code, ""),
            "appearances": appearances,
            "cells": cells,
        })
    return matrix


def build_daily_change(latest: dict, previous: dict) -> dict:
    previous_by_code = {row["code"]: row for row in previous.get("rows", [])}
    latest_by_code = {row["code"]: row for row in latest.get("rows", [])}
    new_rows = [row for row in latest.get("rows", []) if row["code"] not in previous_by_code]
    exit_rows = [row for row in previous.get("rows", []) if row["code"] not in latest_by_code]
    stayed_rows = [row for row in latest.get("rows", []) if row["code"] in previous_by_code]
    rank_up = [row for row in stayed_rows if (row.get("rank_change") or 0) > 0]
    rank_down = [row for row in stayed_rows if (row.get("rank_change") or 0) < 0]
    stayed_rows.sort(key=lambda row: (-row.get("streak", 0), row["rank"]))
    rank_up.sort(key=lambda row: (-(row.get("rank_change") or 0), row["rank"]))
    rank_down.sort(key=lambda row: ((row.get("rank_change") or 0), row["rank"]))
    return {
        "new": new_rows,
        "exit": exit_rows,
        "stayed": stayed_rows,
        "rank_up": rank_up,
        "rank_down": rank_down,
        "summary": {
            "new_count": len(new_rows),
            "exit_count": len(exit_rows),
            "stayed_count": len(stayed_rows),
            "rank_up_count": len(rank_up),
            "rank_down_count": len(rank_down),
        },
    }


def build_weekly_summary(daily: list[dict]) -> dict:
    week = daily[-5:]
    prev_week = daily[-10:-5]
    week_codes = {
        row["code"]
        for item in week
        for row in item.get("rows", [])
    }
    prev_week_codes = {
        row["code"]
        for item in prev_week
        for row in item.get("rows", [])
    }
    appearances: dict[str, dict] = {}
    for item in week:
        for row in item.get("rows", []):
            bucket = appearances.setdefault(row["code"], {
                "code": row["code"],
                "name": row.get("name") or "",
                "count": 0,
                "best_rank": row["rank"],
                "latest_rank": row["rank"],
                "latest_close": row.get("close"),
                "streak": row.get("streak", 0),
            })
            bucket["count"] += 1
            bucket["best_rank"] = min(bucket["best_rank"], row["rank"])
            bucket["latest_rank"] = row["rank"]
            bucket["latest_close"] = row.get("close")
            bucket["streak"] = row.get("streak", 0)
    top_appearances = sorted(
        appearances.values(),
        key=lambda row: (-row["count"], row["best_rank"], row["code"]),
    )[:20]
    weekly_new = [
        appearances[code] for code in sorted(week_codes - prev_week_codes)
        if code in appearances
    ]
    weekly_exit = []
    for code in sorted(prev_week_codes - week_codes):
        last_row = next(
            (row for item in reversed(prev_week) for row in item.get("rows", []) if row["code"] == code),
            None,
        )
        if last_row:
            weekly_exit.append(last_row)
    champions = [row for row in top_appearances if row["count"] == len(week)]
    return {
        "start": week[0]["date"] if week else None,
        "end": week[-1]["date"] if week else None,
        "days": len(week),
        "top_appearances": top_appearances,
        "new": weekly_new[:12],
        "exit": weekly_exit[:12],
        "champions": champions[:12],
    }


def build_tracks(daily: list[dict]) -> list[dict]:
    latest_rows = daily[-1].get("rows", []) if daily else []
    targets = [row["code"] for row in latest_rows[:12]]
    by_code: dict[str, list[dict]] = {code: [] for code in targets}
    for item in daily:
        for row in item.get("rows", []):
            if row["code"] in by_code:
                by_code[row["code"]].append({
                    "date": item["date"],
                    "rank": row["rank"],
                    "close": row.get("close"),
                    "chg_pct": row.get("chg_pct"),
                    "future_returns": row.get("future_returns", {}),
                })
    tracks = []
    latest_by_code = {row["code"]: row for row in latest_rows}
    for code in targets:
        history = by_code.get(code, [])
        if not history:
            continue
        latest = latest_by_code[code]
        tracks.append({
            "code": code,
            "name": latest.get("name") or "",
            "latest_rank": latest["rank"],
            "latest_close": latest.get("close"),
            "latest_chg_pct": latest.get("chg_pct"),
            "streak": latest.get("streak", 0),
            "appearances": len(history),
            "first_date": history[0]["date"],
            "last_date": history[-1]["date"],
            "best_rank": min(row["rank"] for row in history),
            "history": history[-12:],
            "future_returns": latest.get("future_returns", {}),
        })
    return tracks


def build_payload() -> dict:
    observations = collect_observations()
    merge_latest_source_snapshots(observations)
    daily = build_daily_rankings(observations)
    if not daily:
        raise RuntimeError("no high price stock observations found")

    latest = daily[-1]
    previous = daily[-2] if len(daily) > 1 else {"rows": []}
    latest_codes = {r["code"] for r in latest["rows"]}
    previous_codes = {r["code"] for r in previous["rows"]}
    new_rows = [r for r in latest["rows"] if r["code"] not in previous_codes]
    exit_rows = [r for r in previous["rows"] if r["code"] not in latest_codes]
    daily_change = build_daily_change(latest, previous)
    top = latest["rows"][0] if latest["rows"] else None

    source_counts = defaultdict(int)
    for item in daily:
        for row in item["rows"]:
            for source in row["sources"]:
                source_counts[source] += 1

    closes = [r["close"] for r in latest["rows"] if r.get("close") is not None]
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        "source_pipeline": "lynus_chip_history_high_price_watch",
        "source_note": (
            "由 Lynus 既有籌碼歷史檔彙整，包含分點集中度、三因子排行、三源燈號、"
            "大股東背離；最新清單採各來源最近可用日期合併，來源欄保留日期。"
            "此頁只做追蹤與統計，不做投資建議判斷。"
        ),
        "thresholds": {
            "min_price": MIN_PRICE,
            "daily_top_n": DAILY_TOP_N,
            "matrix_days": MATRIX_DAYS,
            "matrix_rows": MATRIX_ROWS,
        },
        "date_range": {
            "start": daily[0]["date"],
            "end": latest["date"],
            "count": len(daily),
        },
        "stats": {
            "latest_count": latest["count"],
            "new_count": len(new_rows),
            "exit_count": len(exit_rows),
            "avg_close": round(sum(closes) / len(closes), 2) if closes else None,
            "max_close": max(closes) if closes else None,
            "top_code": top["code"] if top else None,
            "top_name": top["name"] if top else None,
            "top_close": top["close"] if top else None,
        },
        "source_counts": dict(sorted(source_counts.items())),
        "latest": latest,
        "previous": previous,
        "daily_change": daily_change,
        "weekly_summary": build_weekly_summary(daily),
        "tracks": build_tracks(daily),
        "new_rows": new_rows[:12],
        "exit_rows": exit_rows[:12],
        "matrix_dates": [d["date"] for d in daily[-MATRIX_DAYS:]],
        "matrix": build_matrix(daily),
        "daily": daily,
    }
    return payload


def fmt_price(value) -> str:
    if value is None:
        return "—"
    value = float(value)
    return f"{value:,.0f}" if value >= 1000 else f"{value:,.1f}".rstrip("0").rstrip(".")


def fmt_pct(value) -> str:
    if value is None:
        return "—"
    value = float(value)
    return f"{value:+.2f}%"


def rank_change_text(value) -> str:
    if value is None:
        return "NEW"
    if value > 0:
        return f"+{value}"
    if value < 0:
        return str(value)
    return "0"


def source_text(sources: list[str], source_dates: dict | None = None) -> str:
    source_dates = source_dates or {}
    labels = []
    for source in sources:
        label = SOURCE_LABELS.get(source, source)
        date = source_dates.get(source)
        if date:
            label = f"{label}({date[5:]})"
        labels.append(label)
    return " / ".join(labels)


def cell_tier(rank: int | None) -> str:
    if rank is None:
        return "is-empty"
    if rank <= 5:
        return "tier-1"
    if rank <= 10:
        return "tier-2"
    if rank <= 20:
        return "tier-3"
    return "tier-4"


def render_latest_table(rows: list[dict]) -> str:
    return "\n".join(
        f"""
        <tr>
          <td class="num">#{row['rank']}</td>
          <td><strong>{escape(row['code'])}</strong> {escape(row.get('name') or '')}</td>
          <td class="num">{fmt_price(row.get('close'))}</td>
          <td class="num {'num-up' if (row.get('chg_pct') or 0) > 0 else 'num-down' if (row.get('chg_pct') or 0) < 0 else 'num-neutral'}">{fmt_pct(row.get('chg_pct'))}</td>
          <td class="num">{escape(rank_change_text(row.get('rank_change')))}</td>
          <td class="num">{row.get('streak', 0)}d</td>
          <td>{escape(source_text(row.get('sources', []), row.get('source_dates', {})))}</td>
        </tr>
        """
        for row in rows
    )


def render_chip_list(rows: list[dict], empty_text: str) -> str:
    if not rows:
        return f'<span class="hpw-chip hpw-chip--quiet">{escape(empty_text)}</span>'
    return "\n".join(
        f'<span class="hpw-chip"><strong>{escape(r["code"])}</strong> {escape(r.get("name") or "")} <em>{fmt_price(r.get("close"))}</em></span>'
        for r in rows
    )


def pct_class(value) -> str:
    if value is None:
        return "num-neutral"
    return "num-up" if float(value) > 0 else "num-down" if float(value) < 0 else "num-neutral"


def render_change_list(rows: list[dict], empty_text: str, mode: str = "rank") -> str:
    if not rows:
        return f'<div class="hpw-empty">{escape(empty_text)}</div>'
    items = []
    for row in rows[:10]:
        if mode == "exit":
            meta = f'前次 #{row.get("rank")} / {fmt_price(row.get("close"))}'
        elif row.get("rank_change") is None:
            meta = f'NEW / #{row.get("rank")} / {fmt_price(row.get("close"))}'
        else:
            meta = f'{rank_change_text(row.get("rank_change"))} / #{row.get("rank")} / {fmt_price(row.get("close"))}'
        items.append(
            f'<li><strong>{escape(row["code"])}</strong><span>{escape(row.get("name") or "")}</span><em>{escape(meta)}</em></li>'
        )
    return f'<ul class="hpw-change-list">{"".join(items)}</ul>'


def render_daily_change(payload: dict) -> str:
    change = payload["daily_change"]
    s = change["summary"]
    return f"""
      <section class="hpw-panel hpw-panel--summary">
        <h2>今天 vs 前一交易日<em>每日變動</em></h2>
        <div class="hpw-kpi-grid">
          <div><strong>{s["new_count"]}</strong><span>新進榜</span></div>
          <div><strong>{s["exit_count"]}</strong><span>掉出榜</span></div>
          <div><strong>{s["stayed_count"]}</strong><span>連續留榜</span></div>
          <div><strong>{s["rank_up_count"]}/{s["rank_down_count"]}</strong><span>排名升 / 降</span></div>
        </div>
        <div class="hpw-change-grid">
          <div><h3>新進榜</h3>{render_change_list(change["new"], "沒有新進", "new")}</div>
          <div><h3>掉出榜</h3>{render_change_list(change["exit"], "沒有掉出", "exit")}</div>
          <div><h3>排名上升</h3>{render_change_list(change["rank_up"], "沒有明顯上升")}</div>
          <div><h3>連續留榜</h3>{render_change_list(change["stayed"], "沒有連續留榜")}</div>
        </div>
      </section>
    """


def render_track_cards(payload: dict) -> str:
    cards = []
    for track in payload.get("tracks", [])[:8]:
        history = track.get("history", [])
        bars = []
        for point in history:
            height = max(18, 78 - min(point["rank"], DAILY_TOP_N) * 1.3)
            bars.append(
                f'<span style="height:{height:.0f}%" title="{escape(point["date"])} #{point["rank"]} {fmt_price(point.get("close"))}"></span>'
            )
        returns = []
        for key in ("1", "3", "5", "10"):
            value = track.get("future_returns", {}).get(key)
            returns.append(f'<span class="{pct_class(value)}">{key}d {fmt_pct(value)}</span>')
        cards.append(f"""
          <article class="hpw-track-card">
            <div class="hpw-track-head">
              <strong>{escape(track["code"])}</strong>
              <span>{escape(track.get("name") or "")}</span>
              <em>#{track["latest_rank"]}</em>
            </div>
            <div class="hpw-track-meta">
              <span>收盤 {fmt_price(track.get("latest_close"))}</span>
              <span class="{pct_class(track.get("latest_chg_pct"))}">{fmt_pct(track.get("latest_chg_pct"))}</span>
              <span>連續 {track.get("streak", 0)}d</span>
              <span>出現 {track.get("appearances", 0)} 次</span>
            </div>
            <div class="hpw-bars">{"".join(bars)}</div>
            <div class="hpw-return-row">{"".join(returns)}</div>
          </article>
        """)
    return f"""
      <section class="hpw-panel">
        <h2>個股<em>軌跡卡</em></h2>
        <p class="hpw-note">先列最新高價股前 8 檔；柱狀高度代表排名強弱，越高表示排名越前。後續表現若來源尚未回填會顯示空值。</p>
        <div class="hpw-track-grid">{"".join(cards)}</div>
      </section>
    """


def render_weekly_summary(payload: dict) -> str:
    weekly = payload.get("weekly_summary", {})
    top = weekly.get("top_appearances", [])[:20]
    rows = "".join(
        f'<tr><td class="num">{idx}</td><td><strong>{escape(row["code"])}</strong> {escape(row.get("name") or "")}</td>'
        f'<td class="num">{row["count"]}/{weekly.get("days", 0)}</td><td class="num">#{row["best_rank"]}</td>'
        f'<td class="num">{fmt_price(row.get("latest_close"))}</td></tr>'
        for idx, row in enumerate(top, start=1)
    )
    return f"""
      <section class="hpw-panel">
        <h2>近 5 筆<em>每週總結</em></h2>
        <p class="hpw-note">區間 {escape(str(weekly.get("start") or ""))} -> {escape(str(weekly.get("end") or ""))}，用來看本週最常出現、新進、掉出的高價股。</p>
        <div class="hpw-week-grid">
          <div>
            <h3>本週最常出現 Top 20</h3>
            <div class="hpw-scroll"><table class="hpw-table hpw-table--compact">
              <thead><tr><th class="num">#</th><th>股票</th><th class="num">出現</th><th class="num">最佳</th><th class="num">最新收盤</th></tr></thead>
              <tbody>{rows}</tbody>
            </table></div>
          </div>
          <div>
            <h3>新進高價股</h3>
            <div class="hpw-chip-row">{render_chip_list(weekly.get("new", []), "本週無新進")}</div>
            <h3>連續霸榜股</h3>
            <div class="hpw-chip-row">{render_chip_list(weekly.get("champions", []), "本週無 5/5 連續出現")}</div>
            <h3>掉出高價股</h3>
            <div class="hpw-chip-row">{render_chip_list(weekly.get("exit", []), "本週無掉出")}</div>
          </div>
        </div>
      </section>
    """


def render_heatmap(payload: dict) -> str:
    dates = payload["matrix_dates"]
    cols = len(dates)
    header = '<div class="hpw-sticky hpw-grid-head">股票</div>' + "".join(
        f'<div class="hpw-grid-date">{escape(d[5:].replace("-", "/"))}</div>'
        for d in dates
    )
    body = []
    for row in payload["matrix"]:
        body.append(
            f'<div class="hpw-sticky hpw-stock"><strong>{escape(row["code"])}</strong>'
            f'<span>{escape(row.get("name") or "")}</span><em>{row["appearances"]}/{cols}</em></div>'
        )
        for cell in row["cells"]:
            rank = cell.get("rank")
            tier = cell_tier(rank)
            chg = cell.get("chg_pct")
            direction = "is-up" if (chg or 0) > 0 else "is-down" if (chg or 0) < 0 else "is-flat"
            title = (
                f'{row["code"]} {row.get("name") or ""} | {cell["date"]} | '
                f'rank {rank or "-"} | close {fmt_price(cell.get("close"))} | {fmt_pct(chg)}'
            )
            body.append(
                f'<div class="hpw-cell {tier} {direction}" title="{escape(title)}">'
                f'{"#" + str(rank) if rank else ""}</div>'
            )
    return (
        f'<div class="hpw-heatmap" style="--hpw-cols:{cols}">'
        f'{header}{"".join(body)}</div>'
    )


PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>高價股觀測 | charles16888</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    .hpw-wrap { margin: 44px 0 90px; }
    .hpw-panel { border-top: 1px solid rgba(255, 209, 95, .42); padding-top: 26px; margin-top: 34px; }
    .hpw-panel h2 { margin: 0 0 18px; font-family: var(--font-serif); font-size: clamp(26px, 4vw, 48px); font-weight: 500; color: var(--ink); }
    .hpw-panel h2 em { color: var(--gold-soft); font-style: italic; }
    .hpw-note { color: var(--muted); line-height: 1.8; max-width: 980px; }
    .hpw-scroll { overflow-x: auto; padding-bottom: 8px; }
    .hpw-heatmap { display: grid; grid-template-columns: minmax(160px, 220px) repeat(var(--hpw-cols), 48px); gap: 5px; min-width: calc(180px + var(--hpw-cols) * 53px); align-items: stretch; }
    .hpw-sticky { position: sticky; left: 0; z-index: 2; background: var(--bg); }
    .hpw-grid-head, .hpw-grid-date { font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; color: var(--muted); text-transform: uppercase; padding: 7px 4px; border-bottom: 1px solid rgba(255, 246, 232, .14); }
    .hpw-grid-date { text-align: center; }
    .hpw-stock { min-height: 42px; border: 1px solid rgba(255, 246, 232, .14); padding: 8px 10px; display: grid; grid-template-columns: auto 1fr auto; gap: 7px; align-items: center; }
    .hpw-stock strong { color: var(--gold); font-family: var(--font-mono); }
    .hpw-stock span { color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .hpw-stock em { color: var(--muted); font-style: normal; font-family: var(--font-mono); font-size: 11px; }
    .hpw-cell { min-height: 42px; border: 1px solid rgba(255, 246, 232, .12); display: flex; flex-direction: column; align-items: center; justify-content: center; font-family: var(--font-mono); font-size: 11px; color: rgba(255, 246, 232, .86); }
    .hpw-cell span { font-size: 10px; color: rgba(255, 246, 232, .68); margin-top: 2px; }
    .hpw-cell.is-empty { background: rgba(255, 246, 232, .03); color: transparent; }
    .hpw-cell.tier-1 { background: rgba(255, 209, 95, .42); border-color: rgba(255, 209, 95, .72); color: #fff7df; }
    .hpw-cell.tier-2 { background: rgba(232, 90, 90, .34); border-color: rgba(232, 90, 90, .58); }
    .hpw-cell.tier-3 { background: rgba(90, 154, 232, .26); border-color: rgba(90, 154, 232, .44); }
    .hpw-cell.tier-4 { background: rgba(255, 246, 232, .11); }
    .hpw-cell.is-down { box-shadow: inset 0 -3px 0 rgba(113, 225, 151, .65); }
    .hpw-cell.is-up { box-shadow: inset 0 3px 0 rgba(255, 119, 126, .65); }
    .hpw-table { width: 100%; border-collapse: collapse; font-size: 14px; }
    .hpw-table th { text-align: left; color: var(--muted); font-family: var(--font-mono); font-size: 11px; letter-spacing: .08em; border-bottom: 1px solid rgba(255,246,232,.2); padding: 10px 8px; }
    .hpw-table td { border-bottom: 1px solid rgba(255,246,232,.09); padding: 12px 8px; color: var(--ink); }
    .hpw-table .num { text-align: right; font-family: var(--font-mono); white-space: nowrap; }
    .hpw-chip-row { display: flex; flex-wrap: wrap; gap: 8px; }
    .hpw-chip { border: 1px solid rgba(255,209,95,.28); color: var(--ink); padding: 8px 10px; font-size: 13px; background: rgba(255,246,232,.04); }
    .hpw-chip strong { color: var(--gold); font-family: var(--font-mono); }
    .hpw-chip em { color: var(--muted); font-style: normal; font-family: var(--font-mono); margin-left: 5px; }
    .hpw-chip--quiet { color: var(--muted); border-color: rgba(255,246,232,.14); }
    .hpw-panel {
      background: #fff8ea;
      border: 1px solid #d8bd7a;
      color: #1c1711;
      padding: 26px;
      margin-top: 28px;
    }
    .hpw-panel h2 { color: #1c1711; font-size: clamp(24px, 3.4vw, 42px); }
    .hpw-panel h2 em { color: #9a6b00; }
    .hpw-panel h3 { color: #362615; margin: 0 0 10px; font-size: 15px; }
    .hpw-note { color: #5b4a35; font-size: 15px; line-height: 1.75; }
    .hpw-kpi-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin: 18px 0 22px; }
    .hpw-kpi-grid div { border: 1px solid #d8bd7a; background: #fffdf7; padding: 14px; }
    .hpw-kpi-grid strong { display: block; color: #8d1f2d; font-family: var(--font-mono); font-size: 30px; line-height: 1; }
    .hpw-kpi-grid span { display: block; margin-top: 6px; color: #5b4a35; font-size: 13px; }
    .hpw-change-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
    .hpw-change-list { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
    .hpw-change-list li { display: grid; grid-template-columns: 52px 1fr; gap: 4px 8px; align-items: baseline; border-bottom: 1px solid #eadbb8; padding: 7px 0; }
    .hpw-change-list strong { color: #9a6b00; font-family: var(--font-mono); font-size: 15px; }
    .hpw-change-list span { color: #1c1711; font-weight: 700; }
    .hpw-change-list em { grid-column: 2; color: #6d5a40; font-family: var(--font-mono); font-size: 12px; font-style: normal; }
    .hpw-empty { color: #7e705d; font-style: italic; }
    .hpw-heatmap { grid-template-columns: minmax(190px, 240px) repeat(var(--hpw-cols), 56px); gap: 6px; min-width: calc(220px + var(--hpw-cols) * 62px); }
    .hpw-sticky { background: #fff8ea; }
    .hpw-grid-head, .hpw-grid-date { color: #6d5a40; border-bottom-color: #d8bd7a; font-size: 12px; }
    .hpw-stock { min-height: 48px; border-color: #d8bd7a; background: #fffdf7; }
    .hpw-stock strong { color: #9a6b00; font-size: 15px; }
    .hpw-stock span { color: #1c1711; font-weight: 700; }
    .hpw-stock em { color: #6d5a40; font-size: 12px; }
    .hpw-cell { min-height: 48px; border-color: rgba(40, 30, 18, .18); color: #1c1711; font-size: 13px; font-weight: 800; }
    .hpw-cell.is-empty { background: #f1eadc; color: transparent; }
    .hpw-cell.tier-1 { background: #9f2f3f; border-color: #7f2030; color: #fff8ea; }
    .hpw-cell.tier-2 { background: #d66a4a; border-color: #b24f35; color: #fff8ea; }
    .hpw-cell.tier-3 { background: #e9bd55; border-color: #c99c2f; color: #1c1711; }
    .hpw-cell.tier-4 { background: #f6e1a9; border-color: #d8bd7a; color: #1c1711; }
    .hpw-cell.is-up { box-shadow: inset 0 4px 0 #b81f35; }
    .hpw-cell.is-down { box-shadow: inset 0 -4px 0 #16814f; }
    .hpw-table { font-size: 15px; background: #fffdf7; }
    .hpw-table th { color: #6d5a40; border-bottom-color: #d8bd7a; font-size: 12px; }
    .hpw-table td { color: #1c1711; border-bottom-color: #eadbb8; }
    .hpw-table--compact { font-size: 13px; }
    .hpw-chip { border-color: #d8bd7a; color: #1c1711; background: #fffdf7; font-size: 14px; }
    .hpw-chip strong { color: #9a6b00; }
    .hpw-chip em { color: #6d5a40; }
    .hpw-track-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px; }
    .hpw-track-card { border: 1px solid #d8bd7a; background: #fffdf7; padding: 14px; }
    .hpw-track-head { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; align-items: baseline; }
    .hpw-track-head strong { color: #9a6b00; font-family: var(--font-mono); font-size: 16px; }
    .hpw-track-head span { color: #1c1711; font-weight: 800; }
    .hpw-track-head em { color: #8d1f2d; font-family: var(--font-mono); font-style: normal; }
    .hpw-track-meta, .hpw-return-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; font-family: var(--font-mono); font-size: 12px; color: #5b4a35; }
    .hpw-bars { height: 74px; display: flex; align-items: flex-end; gap: 4px; margin-top: 14px; padding: 8px 0 0; border-top: 1px dashed #d8bd7a; }
    .hpw-bars span { flex: 1; min-width: 6px; background: #9f2f3f; opacity: .9; }
    .hpw-return-row span { border: 1px solid #eadbb8; padding: 3px 5px; background: #fff8ea; }
    .hpw-week-grid { display: grid; grid-template-columns: 1.35fr .85fr; gap: 20px; align-items: start; }
    @media (max-width: 720px) {
      .hpw-panel { padding: 18px; }
      .hpw-kpi-grid, .hpw-change-grid, .hpw-track-grid, .hpw-week-grid { grid-template-columns: 1fr; }
      .hpw-table { font-size: 13px; }
      .hpw-table th:nth-child(7), .hpw-table td:nth-child(7) { display: none; }
      .hpw-heatmap { grid-template-columns: minmax(150px, 190px) repeat(var(--hpw-cols), 46px); min-width: calc(160px + var(--hpw-cols) * 52px); }
    }
  </style>
</head>
<body data-page="report">
  <header class="masthead">
    <div class="container">
      <div class="masthead__row">
        <a class="brand" href="../index.html">
          <span class="brand__mark">charles<em>16888</em></span>
          <span class="brand__plate">Market Edition · MMXXVI</span>
        </a>
        <nav class="nav" aria-label="Primary">
          <a class="nav__link" href="../category.html?cat=sectors">族群</a>
          <a class="nav__link" href="../category.html?cat=taiex">大盤</a>
          <a class="nav__link" href="financial-calendar.html">行事曆</a>
          <a class="nav__link" href="../category.html?cat=txo">選擇權</a>
          <a class="nav__link" href="../category.html?cat=chips">籌碼</a>
          <a class="nav__link is-active" href="../category.html?cat=stocks">個股</a>
          <a class="nav__link" href="../category.html?cat=research">研報統計</a>
        </nav>
      </div>
    </div>
  </header>

  <main class="container">
    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="../index.html">charles16888</a>
      <span class="breadcrumb__sep">/</span>
      <a href="../category.html?cat=stocks">個股</a>
      <span class="breadcrumb__sep">/</span>
      <span>高價股觀測</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>個股</strong> · 高價股觀測</span>
        <span>__DATE_RANGE__</span>
        <span>__GENERATED_AT__</span>
      </div>
      <h1 class="report-title">高價股觀測 · <em>排名熱圖</em></h1>
      <p class="report-lead">把既有籌碼與股價追蹤檔整理成高價股清單、排名變化與近 30 筆出現熱度，先補上 Telegram 第四頁那塊的網站版本。</p>
      <div class="stat-row stat-row--3">
        <div class="stat">
          <div class="stat__label">最新樣本</div>
          <div class="stat__value stat__value--sm num-neutral">__LATEST_COUNT__ 檔</div>
        </div>
        <div class="stat">
          <div class="stat__label">最高收盤</div>
          <div class="stat__value stat__value--sm num-neutral">__MAX_CLOSE__</div>
        </div>
        <div class="stat">
          <div class="stat__label">新進名單</div>
          <div class="stat__value stat__value--sm num-neutral">__NEW_COUNT__ 檔</div>
        </div>
      </div>
    </section>

    <div class="hpw-wrap">
      __DAILY_CHANGE__

      <section class="hpw-panel">
        <h2>近 30 筆<em>排名熱圖</em></h2>
        <p class="hpw-note">顏色越亮代表排名越前；上緣紅線代表該筆漲幅為正，下緣綠線代表該筆跌幅為負。左側分數為近 30 筆出現次數。</p>
        <div class="hpw-scroll">__HEATMAP__</div>
      </section>

      __TRACK_CARDS__

      <section class="hpw-panel">
        <h2>最新<em>高價股清單</em></h2>
        <div class="hpw-scroll">
          <table class="hpw-table">
            <thead>
              <tr>
                <th class="num">Rank</th>
                <th>股票</th>
                <th class="num">收盤</th>
                <th class="num">漲跌幅</th>
                <th class="num">排名變化</th>
                <th class="num">連續</th>
                <th>來源</th>
              </tr>
            </thead>
            <tbody>__LATEST_ROWS__</tbody>
          </table>
        </div>
      </section>

      <section class="hpw-panel">
        <h2>新進與<em>淡出</em></h2>
        <p class="hpw-note">與前一筆高價股名單比較，只呈現追蹤名單的進出，不代表買賣訊號。</p>
        <h3 class="section__meta" style="display:block;margin:16px 0 10px">NEW IN</h3>
        <div class="hpw-chip-row">__NEW_ROWS__</div>
        <h3 class="section__meta" style="display:block;margin:22px 0 10px">EXITED</h3>
        <div class="hpw-chip-row">__EXIT_ROWS__</div>
      </section>

      __WEEKLY_SUMMARY__

      <section class="hpw-panel">
        <h2>資料<em>說明</em></h2>
        <p class="hpw-note">__SOURCE_NOTE__</p>
      </section>
    </div>
  </main>

  <footer class="footer">
    <div class="container">
      <div id="footer-line" class="footer__row"></div>
    </div>
  </footer>
  <script src="../assets/main.js" defer></script>
</body>
</html>
"""


def render_html(payload: dict) -> None:
    stats = payload["stats"]
    date_range = payload["date_range"]
    latest_rows = render_latest_table(payload["latest"]["rows"])
    html = PAGE_TEMPLATE
    html = html.replace("__DATE_RANGE__", f'{date_range["start"]} -> {date_range["end"]}')
    html = html.replace("__GENERATED_AT__", payload["generated_at"].replace("T", " ")[:16])
    html = html.replace("__LATEST_COUNT__", str(stats["latest_count"]))
    html = html.replace("__MAX_CLOSE__", fmt_price(stats["max_close"]))
    html = html.replace("__NEW_COUNT__", str(stats["new_count"]))
    html = html.replace("__DAILY_CHANGE__", render_daily_change(payload))
    html = html.replace("__HEATMAP__", render_heatmap(payload))
    html = html.replace("__TRACK_CARDS__", render_track_cards(payload))
    html = html.replace("__LATEST_ROWS__", latest_rows)
    html = html.replace("__NEW_ROWS__", render_chip_list(payload["new_rows"], "沒有新進名單"))
    html = html.replace("__EXIT_ROWS__", render_chip_list(payload["exit_rows"], "沒有淡出名單"))
    html = html.replace("__WEEKLY_SUMMARY__", render_weekly_summary(payload))
    html = html.replace("__SOURCE_NOTE__", escape(payload["source_note"]))
    html = "\n".join(line.rstrip() for line in html.splitlines()) + "\n"
    REPORT_OUT.write_text(html, encoding="utf-8")


def update_manifest(payload: dict) -> None:
    manifest = read_json(MANIFEST_PATH)
    stats = payload["stats"]
    latest_date = payload["latest"]["date"]
    top = payload["latest"]["rows"][0] if payload["latest"]["rows"] else None
    entry = {
        "id": "high-price-watch",
        "category": "stocks",
        "type": "high_price_watch",
        "date": latest_date,
        "time": "21:40",
        "title": "高價股觀測 · 排名熱圖",
        "title_em": "排名熱圖",
        "summary": (
            f"高價股追蹤 {stats['latest_count']} 檔，"
            f"新進 {stats['new_count']} 檔、淡出 {stats['exit_count']} 檔；"
            f"榜首 {top['code']} {top['name']} 收盤 {fmt_price(top['close'])}。"
            if top else "高價股追蹤統計。"
        ),
        "tags": ["個股", "高價股", "排名熱圖", "Telegram第四頁"],
        "source_pipeline": payload["source_pipeline"],
        "url": "reports/high-price-watch.html",
        "stats": [
            {"label": "最新樣本", "value": f"{stats['latest_count']} 檔", "color": "neutral"},
            {"label": "新進", "value": f"{stats['new_count']} 檔", "color": "up"},
            {"label": "最高收盤", "value": fmt_price(stats["max_close"]), "color": "neutral"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != entry["id"]]
    manifest["entries"].append(entry)
    manifest["entries"].sort(key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")), reverse=True)
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def update_categories() -> None:
    categories = read_json(CATEGORIES_PATH)
    stocks = next((c for c in categories.get("categories", []) if c.get("id") == "stocks"), None)
    if not stocks:
        raise RuntimeError("stocks category not found")

    pipelines = stocks.setdefault("source_pipelines", [])
    if "high_price_watch" not in pipelines:
        pipelines.append("high_price_watch")

    subcategories = stocks.setdefault("subcategories", [])
    if not any(s.get("id") == "high-price" for s in subcategories):
        subcategories.append({
            "id": "high-price",
            "name_zh": "高價股",
            "name_en": "High Price",
            "match_id_prefix": ["high-price-watch"],
        })

    report_types = stocks.setdefault("report_types", [])
    if not any(t.get("id") == "high_price_watch" for t in report_types):
        report_types.append({
            "id": "high_price_watch",
            "name_zh": "高價股觀測",
            "name_en": "High Price Watch",
        })

    CATEGORIES_PATH.write_text(json.dumps(categories, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    payload = build_payload()
    DATA_OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    render_html(payload)
    update_manifest(payload)
    update_categories()
    stats = payload["stats"]
    print(
        f"[OK] high_price_watch {payload['latest']['date']} "
        f"rows={stats['latest_count']} new={stats['new_count']} exit={stats['exit_count']}"
    )
    print(f"[OK] {DATA_OUT.relative_to(ROOT)}")
    print(f"[OK] {REPORT_OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
