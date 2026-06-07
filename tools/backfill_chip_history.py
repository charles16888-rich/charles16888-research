"""
backfill_chip_history.py
=========================
Compute and publish per-day chip concentration ranking (1d, Top 50 each side)
for the last N trading days. Writes one HTML file per date for Lynus AND
pushes one card per date to charles1688.

Two outputs per date:
    1. Lynus :  E:\\Lynus\\reports\\<date>\\chip-concentration.html
                + manifest.json entry id "<date>-chip-concentration"
    2. charles1688: one POST with pipeline="籌碼", ts=<date>

Run:
    python tools/backfill_chip_history.py                 # last 30 trading days
    python tools/backfill_chip_history.py --days 60       # last 60 days
    python tools/backfill_chip_history.py --start 2026-05-01  # from a date
    python tools/backfill_chip_history.py --no-charles    # Lynus only
    python tools/backfill_chip_history.py --no-lynus      # charles1688 only
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, r"C:\Users\birdk\watchdog")
from web_push import post as web_post, clear_all  # noqa: E402

LYNUS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LYNUS_ROOT / "tools"))
from build_chip_concentration import (  # noqa: E402
    CHIP_DB, BROKER_DB, get_price_info, get_stock_names,
    compute_ranking_for_window, split_rankings,
)
from push_to_charles1688 import build_chip_html  # noqa: E402

REPORTS_DIR = LYNUS_ROOT / "reports"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"
TPE = ZoneInfo("Asia/Taipei")

TOP_N_PER_SIDE = 50  # Lynus 顯示量
TOP_N_CHARLES = 10   # charles1688 顯示量


# ─── Lynus per-day HTML template (simplified 1d only) ─────────────────────

PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>__DATE__ 籌碼集中度 — Lynus' Research</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../../assets/fonts.css" />
  <link rel="stylesheet" href="../../assets/style.css" />
  <style>
    .chip-grid { display:grid; grid-template-columns:1fr 1fr; gap:24px; margin:14px 0 28px; }
    .chip-col {
      border:1px solid rgba(232,223,211,0.10);
      padding:18px 20px 8px; background:rgba(232,223,211,0.02);
    }
    .chip-col__head {
      display:flex; align-items:baseline; justify-content:space-between;
      margin-bottom:14px; padding-bottom:10px;
      border-bottom:1px solid rgba(212,175,55,0.30);
    }
    .chip-col__title {
      font-family:'Noto Serif TC',serif; font-size:16px; font-weight:700;
      color:#e8dfd3; margin:0;
    }
    .chip-col__title em { font-style:normal; color:#d4af37; }
    .chip-col__meta {
      font-family:'JetBrains Mono',monospace;
      font-size:10px; color:#6e6350; letter-spacing:.12em;
    }
    .chip-row {
      display:grid; grid-template-columns:28px 1fr auto; gap:10px; align-items:center;
      padding:10px 0; border-bottom:1px dashed rgba(232,223,211,0.08);
    }
    .chip-row__rank {
      font-family:'JetBrains Mono',monospace; font-size:10px;
      color:#6e6350; text-align:right;
    }
    .chip-row__main { display:flex; flex-direction:column; gap:2px; min-width:0; }
    .chip-row__title {
      font-family:'Noto Serif TC',serif; font-size:14px; font-weight:600; color:#e8dfd3;
      white-space:nowrap; overflow:hidden; text-overflow:ellipsis;
    }
    .chip-row__title .chip-row__code {
      font-family:'JetBrains Mono',monospace; font-size:11px;
      color:#b8985c; margin-right:6px;
    }
    .chip-row__sub {
      font-family:'JetBrains Mono',monospace; font-size:10px;
      color:#6a6557; letter-spacing:.05em;
    }
    .chip-row__bar {
      position:relative; width:100%; height:4px;
      background:rgba(232,223,211,0.04); border-radius:1px; margin-top:4px; overflow:hidden;
    }
    .chip-row__bar::after {
      content:""; position:absolute; top:0; left:0; height:100%;
      width:var(--w,0%); background:var(--c,#d4af37);
    }
    .chip-row__pct {
      font-family:'JetBrains Mono',monospace;
      font-size:14px; font-weight:700; text-align:right; min-width:70px;
    }
    .chip-row__pct--up   { color:#e85a5a; }
    .chip-row__pct--down { color:#5fb87a; }
    .chip-row__price {
      font-family:'JetBrains Mono',monospace; font-size:10px;
      color:#6e6350; text-align:right; margin-top:2px;
    }
    .chip-row__price--up   { color:#e85a5a; }
    .chip-row__price--down { color:#5fb87a; }
    @media (max-width:760px) { .chip-grid { grid-template-columns:1fr; } }
  </style>
</head>
<body data-page="report">

  <header class="masthead">
    <div class="container">
      <div class="masthead__row">
        <a class="brand" href="../../index.html">
          <span class="brand__mark">Lynus' <em>Research</em></span>
          <span class="brand__plate">Private Edition · MMXXVI</span>
        </a>
        <nav class="nav" aria-label="Primary">
          <a class="nav__link" href="../../category.html?cat=sectors">族群</a>
          <a class="nav__link" href="../../category.html?cat=taiex">大盤</a>
          <a class="nav__link nav__link--disabled" href="#" aria-disabled="true">選擇權</a>
          <a class="nav__link is-active" href="../../category.html?cat=chips">籌碼</a>
          <a class="nav__link" href="../../category.html?cat=stocks">個股</a>
          <a class="nav__link" href="../../category.html?cat=news">新聞</a>
        </nav>
      </div>
    </div>
  </header>

  <main class="container">

    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="../../index.html">Lynus' Research</a>
      <span class="breadcrumb__sep">/</span>
      <a href="../../category.html?cat=chips">籌碼</a>
      <span class="breadcrumb__sep">/</span>
      <span>__DATE__ 集中度</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 分點集中度</span>
        <span>__DATE__</span>
        <span>當日 1d Top __TOP_N__ 排行</span>
      </div>
      <h1 class="report-title">__DATE__ 籌碼<em>集中度</em></h1>
      <p class="report-lead">前 15 大買超 / 賣超分點淨買賣量 ÷ 該股總成交量。+ = 主力大買 / − = 主力大賣。過濾成交量 &lt; 1,000 張的雜訊股。</p>
    </section>

    <div class="chip-grid">__GRID__</div>

  </main>

  <footer class="footer">
    <div class="container">
      <div id="footer-line" class="footer__row"></div>
    </div>
  </footer>

  <script src="../../assets/main.js" defer></script>
</body>
</html>
"""


def _row_html(r: dict, side: str, idx: int) -> str:
    pct = r.get("concentration_pct", 0) or 0
    pct_class = "chip-row__pct--up" if pct >= 0 else "chip-row__pct--down"
    sign = "+" if pct >= 0 else ""
    bar_w = min(100, abs(pct) * 2)
    bar_c = "rgba(232,90,90,0.55)" if side == "buy" else "rgba(95,184,122,0.55)"
    main_val = r.get("main_buy") if side == "buy" else r.get("main_sell")
    close = r.get("close")
    chg_pct = r.get("chg_pct") or 0
    price_class = "chip-row__price--up" if chg_pct >= 0 else "chip-row__price--down"
    price_html = ""
    if close is not None:
        price_html = (
            f'<div class="chip-row__price {price_class}">'
            f'{close:,.2f}　{"+" if chg_pct >= 0 else ""}{chg_pct:.2f}%</div>'
        )
    return f'''
      <div class="chip-row">
        <div class="chip-row__rank">{idx + 1}.</div>
        <div class="chip-row__main">
          <div class="chip-row__title">
            <span class="chip-row__code">{r['code']}</span>{r.get('name', r['code'])}
          </div>
          <div class="chip-row__sub">主力{('買' if side == 'buy' else '賣')} {main_val:,} 張 · 成交 {r['vol']:,} 張</div>
          <div class="chip-row__bar" style="--w:{bar_w}%;--c:{bar_c}"></div>
        </div>
        <div>
          <div class="chip-row__pct {pct_class}">{sign}{pct:.1f}%</div>
          {price_html}
        </div>
      </div>
    '''


def render_lynus_page(date_str: str, buy_top: list, sell_top: list) -> str:
    """Build the standalone per-day HTML for Lynus."""
    buy_html = "".join(_row_html(r, "buy", i) for i, r in enumerate(buy_top))
    sell_html = "".join(_row_html(r, "sell", i) for i, r in enumerate(sell_top))
    grid_html = f'''
      <div class="chip-col">
        <div class="chip-col__head">
          <h2 class="chip-col__title">買超集中 <em>Top {len(buy_top)}</em></h2>
          <span class="chip-col__meta">{date_str}</span>
        </div>
        {buy_html}
      </div>
      <div class="chip-col">
        <div class="chip-col__head">
          <h2 class="chip-col__title">賣超集中 <em>Top {len(sell_top)}</em></h2>
          <span class="chip-col__meta">{date_str}</span>
        </div>
        {sell_html}
      </div>
    '''
    return (PAGE_TEMPLATE
        .replace("__DATE__", date_str)
        .replace("__TOP_N__", str(TOP_N_PER_SIDE))
        .replace("__GRID__", grid_html)
    )


def compute_and_enrich(date_str: str) -> tuple[list[dict], list[dict]] | None:
    """Return (buy_top, sell_top) enriched with name + price for one date."""
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=10)
    conn.execute(f"ATTACH DATABASE 'file:{BROKER_DB.as_posix()}?mode=ro' AS broker")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS broker_trading AS SELECT * FROM broker.broker_trading")
    try:
        records = compute_ranking_for_window(conn, date_str, date_str, "1d")
        name_map = get_stock_names(conn)
    finally:
        conn.close()
    if not records:
        return None
    buy_top, sell_top = split_rankings(records)
    # Truncate to TOP_N_PER_SIDE for Lynus
    buy_top = buy_top[:TOP_N_PER_SIDE]
    sell_top = sell_top[:TOP_N_PER_SIDE]
    # Enrich
    codes = list({r["code"] for r in buy_top + sell_top})
    price_map = get_price_info(codes, date_str)
    for r in buy_top + sell_top:
        p = price_map.get(r["code"], {})
        r["name"]    = p.get("name") or name_map.get(r["code"], r["code"])
        r["close"]   = p.get("close")
        r["chg"]     = p.get("chg")
        r["chg_pct"] = p.get("chg_pct")
    return buy_top, sell_top


def write_lynus_files(date_str: str, buy_top: list, sell_top: list) -> str:
    """Write the Lynus per-day HTML and return the relative manifest URL."""
    date_dir = REPORTS_DIR / date_str
    date_dir.mkdir(parents=True, exist_ok=True)
    out_path = date_dir / "chip-concentration.html"
    html = render_lynus_page(date_str, buy_top, sell_top)
    out_path.write_text(html, encoding="utf-8")
    return f"reports/{date_str}/chip-concentration.html"


def upsert_manifest_entries(date_entries: list[dict]) -> None:
    """Batch upsert all per-day chip-concentration entries."""
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    new_ids = {e["id"] for e in date_entries}
    kept = [e for e in manifest.get("entries", []) if e.get("id") not in new_ids]
    manifest["entries"] = kept + date_entries
    manifest["entries"].sort(
        key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")),
        reverse=True,
    )
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def push_charles1688(date_str: str, buy_top: list, sell_top: list) -> bool:
    """Push the day's Top 10 to charles1688. Returns True on success."""
    # Build a tiny "data" structure matching what build_chip_html expects
    fake = {"windows": [{
        "key": "1d",
        "label": f"日 · {date_str}",
        "start_date": date_str,
        "end_date": date_str,
        "n_days": 1,
        "total_stocks_after_filter": len(buy_top) + len(sell_top),
        "buy_top": [dict(r) for r in buy_top],   # shallow copies
        "sell_top": [dict(r) for r in sell_top],
    }]}
    html = build_chip_html(fake, window_key="1d", top_n=TOP_N_CHARLES)
    if not html:
        return False
    top_buy = buy_top[0] if buy_top else None
    top_sell = sell_top[0] if sell_top else None
    stats = []
    if top_buy:
        stats.append({"label": "Top 買",
                      "value": f"{top_buy['code']} +{top_buy['concentration_pct']:.0f}%",
                      "color": "up"})
    if top_sell:
        stats.append({"label": "Top 賣",
                      "value": f"{top_sell['code']} {top_sell['concentration_pct']:.0f}%",
                      "color": "down"})
    return bool(web_post(
        pipeline="籌碼",
        title=f"{date_str} 籌碼集中度 · 分點 Top {TOP_N_CHARLES} 排行",
        content=html,
        content_type="html",
        ts=date_str,
        stats=stats,
        tags=["籌碼", "分點", "集中度"],
    ))


def get_trading_dates(n_back: int | None, start: str | None) -> list[str]:
    """Return list of trading days to backfill, sorted DESC."""
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=5)
    conn.execute(f"ATTACH DATABASE 'file:{BROKER_DB.as_posix()}?mode=ro' AS broker")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS broker_trading AS SELECT * FROM broker.broker_trading")
    try:
        rows = conn.execute(
            "SELECT DISTINCT date FROM broker_trading ORDER BY date DESC"
        ).fetchall()
    finally:
        conn.close()
    dates = [r[0] for r in rows]
    if start:
        dates = [d for d in dates if d >= start]
    elif n_back:
        dates = dates[:n_back]
    return dates


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days",  type=int, default=30, help="Last N trading days to backfill.")
    p.add_argument("--start", type=str, default=None, help="YYYY-MM-DD; overrides --days.")
    p.add_argument("--no-lynus",   action="store_true", help="Skip Lynus output.")
    p.add_argument("--no-charles", action="store_true", help="Skip charles1688 push.")
    p.add_argument("--no-clear",   action="store_true", help="Skip clearing 籌碼 on charles1688 before push.")
    args = p.parse_args()

    dates = get_trading_dates(args.days, args.start)
    if not dates:
        print("[ERR] no trading dates found")
        return 1
    dates.sort()  # ASC so the latest gets stored last (highest post_date on WP)
    print(f"[INFO] backfilling {len(dates)} dates: {dates[0]} → {dates[-1]}")

    # Optionally clear charles1688's 籌碼 pipeline first so we don't double-stack.
    if not args.no_charles and not args.no_clear:
        n = clear_all(pipeline="籌碼")
        print(f"[INFO] charles1688 cleared {n} existing 籌碼 posts")

    manifest_entries: list[dict] = []
    lynus_done = pushed = skipped = 0
    t0 = time.time()

    for date_str in dates:
        result = compute_and_enrich(date_str)
        if result is None:
            print(f"  [SKIP] {date_str}: no records")
            skipped += 1
            continue
        buy_top, sell_top = result
        if not buy_top and not sell_top:
            print(f"  [SKIP] {date_str}: empty rankings")
            skipped += 1
            continue

        # Lynus per-day HTML
        if not args.no_lynus:
            url = write_lynus_files(date_str, buy_top, sell_top)
            tb = buy_top[0]
            ts = sell_top[0] if sell_top else None
            summary = (f"當日 Top {len(buy_top)} 買超 / Top {len(sell_top)} 賣超分點集中度。"
                       f"買超 #1 {tb['code']} {tb['name']} +{tb['concentration_pct']:.1f}%。"
                       + (f"賣超 #1 {ts['code']} {ts['name']} {ts['concentration_pct']:.1f}%。" if ts else ""))
            stats_entry = [
                {"label": "Top 買",
                 "value": f"{tb['code']} +{tb['concentration_pct']:.0f}%",
                 "color": "up"},
            ]
            if ts:
                stats_entry.append({"label": "Top 賣",
                                    "value": f"{ts['code']} {ts['concentration_pct']:.0f}%",
                                    "color": "down"})
            stats_entry.append({"label": "排行檔數",
                                "value": f"{len(buy_top) + len(sell_top)}",
                                "color": "neutral"})
            manifest_entries.append({
                "id":              f"{date_str}-chip-concentration",
                "category":        "chips",
                "type":            "ranking",
                "date":            date_str,
                "time":            "21:00",
                "title":           f"{date_str} 籌碼集中度 · 分點 Top {len(buy_top)} 排行",
                "title_em":        "集中度",
                "summary":         summary,
                "tags":            ["籌碼", "分點", "集中度", date_str[:7]],
                "source_pipeline": "stock_chip_crawler",
                "url":             url,
                "stats":           stats_entry,
            })
            lynus_done += 1

        # charles1688
        if not args.no_charles:
            ok = push_charles1688(date_str, buy_top, sell_top)
            if ok:
                pushed += 1
            time.sleep(0.4)  # be polite to WordPress

        if (lynus_done + pushed) % 5 == 0:
            print(f"  ... {date_str}: lynus={lynus_done} push={pushed} skip={skipped}")

    # Batch update manifest once at the end
    if manifest_entries:
        upsert_manifest_entries(manifest_entries)

    print(f"[DONE] lynus={lynus_done} push={pushed} skip={skipped} "
          f"({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
