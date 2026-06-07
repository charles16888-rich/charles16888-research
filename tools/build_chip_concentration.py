"""
build_chip_concentration.py
============================
Build the「籌碼集中度排行榜」(broker chip concentration ranking) for Lynus.

Reads E:\stock_chip_crawler\stock_chip.db `broker_trading` table and ranks
every stock by main-force concentration across five time windows.

Concentration formula (industry standard):
    For each (stock, window):
        rank brokers by cumulative net_vol over the window
        main_buy  = sum of TOP 15 brokers with net_vol > 0
        main_sell = sum of |TOP 15 brokers with net_vol < 0|
        concentration% = (main_buy − main_sell) / total_volume * 100

Stocks with cumulative volume < 1,000 are dropped (low-liquidity noise).

Outputs:
    assets/chip_concentration.json   — all windows, full ranking lists
    reports/chip-concentration.html  — interactive page with 5 window tabs
    manifest.json entry refreshed under category "chips"
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import pandas as pd
except ImportError:
    print("[ERR] pandas required: pip install pandas", file=sys.stderr)
    sys.exit(2)

# Default to live crawler DB; override with CHIP_DB_PATH env var when the
# crawler holds a write lock (use the snapshot in E:\Lynus\_cache\).
CHIP_DB = Path(os.environ.get("CHIP_DB_PATH", r"E:\stock_chip_crawler\stock_chip.db"))
# 分點 broker_trading 已拆到獨立的 broker_chip.db
BROKER_DB = Path(os.environ.get("BROKER_DB_PATH", r"E:\stock_chip_crawler\broker_chip.db"))

HISTORY_DIR_NAME = "chip_concentration"   # for assets/chip_history/<this>/<date>.json
KLINE_DB = Path(r"E:\stock_chip_crawler\kline.db")
LYNUS_ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = LYNUS_ROOT / "assets" / "chip_concentration.json"
PAGE_OUT = LYNUS_ROOT / "reports" / "chip-concentration.html"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"
TPE = ZoneInfo("Asia/Taipei")

# ── Parameters ────────────────────────────────────────────────────────────
TOP_BROKERS_PER_SIDE = 15      # 前 15 大買超 + 前 15 大賣超
TOP_STOCKS_PER_SIDE  = 50      # 排行榜每邊 50 檔
# DB 內 buy_vol / sell_vol 是「股」單位（1 張 = 1000 股）。
# 過濾門檻：累計成交量 < 1,000 張 = 1,000,000 股 不算。
MIN_VOLUME_SHARES = 1_000_000  # 1000 張的股數
SHARES_PER_LOT    = 1000

WINDOWS = [
    ("1d",  1,  "日 · 最近 1 個交易日"),
    ("5d",  5,  "週 · 最近 5 個交易日"),
    ("10d", 10, "雙週 · 最近 10 個交易日"),
    ("20d", 20, "月 · 最近 20 個交易日"),
    ("all", None, "ALL · 全部資料"),
]


def get_trading_dates(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT date FROM broker_trading ORDER BY date DESC"
    ).fetchall()
    return [r[0] for r in rows]


def get_stock_names(conn: sqlite3.Connection) -> dict[str, str]:
    """Return {code: name} from meta_stock if available."""
    try:
        rows = conn.execute("SELECT code, name FROM meta_stock").fetchall()
        return {r[0]: r[1] for r in rows}
    except sqlite3.OperationalError:
        return {}


def load_broker_name_override() -> dict[str, str]:
    """Load the broker_id → preferred Chinese name override map."""
    path = LYNUS_ROOT / "assets" / "_raw" / "broker_name_override.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("overrides", {})
    except Exception as e:
        print(f"[WARN] could not load broker override: {e}")
        return {}


def get_price_info(codes: list[str], latest_date: str) -> dict[str, dict]:
    """Pull name, close, chg, chg_pct from stock_chip.db's daily_price table.

    Returns {code: {name, close, chg, chg_pct}}.
    Computes change by comparing close vs the most recent prior trading day.
    """
    if not codes:
        return {}
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=5)
    placeholders = ",".join("?" * len(codes))
    rows = conn.execute(
        f"""
        SELECT code, date, name, close
        FROM daily_price
        WHERE code IN ({placeholders})
          AND date >= date(?, '-15 days')
          AND date <= ?
        ORDER BY code, date DESC
        """,
        (*codes, latest_date, latest_date),
    ).fetchall()
    conn.close()

    by_code: dict[str, list] = {}
    for r in rows:
        by_code.setdefault(r[0], []).append(r)

    out = {}
    for code, entries in by_code.items():
        if not entries:
            continue
        latest = entries[0]   # ORDER BY date DESC
        if latest[3] is None:   # 停牌/未交易日 close NULL
            continue
        info = {"name": latest[2], "close": float(latest[3])}
        if len(entries) >= 2 and entries[1][3]:
            prev_close = float(entries[1][3])
            chg = info["close"] - prev_close
            info["chg"] = round(chg, 2)
            info["chg_pct"] = round(chg / prev_close * 100, 2)
        else:
            info["chg"] = 0.0
            info["chg_pct"] = 0.0
        out[code] = info
    return out


def compute_ranking_for_window(
    conn: sqlite3.Connection,
    start_date: str,
    end_date: str,
    label: str,
) -> list[dict]:
    """Compute concentration for every stock over [start_date, end_date].

    Returns sorted list of dicts with: code, name, vol, main_buy, main_sell,
    net_concentration_pct (and side-specific).
    """
    print(f"  [{label}] {start_date} → {end_date}")

    # Pull every (code, broker, sum_net_vol) over the window.
    df = pd.read_sql(
        """
        SELECT code, broker_id, broker_name,
               SUM(net_vol) AS net_vol,
               SUM(buy_vol) AS buy_vol,
               SUM(sell_vol) AS sell_vol
        FROM broker_trading
        WHERE date BETWEEN ? AND ?
        GROUP BY code, broker_id
        """,
        conn,
        params=[start_date, end_date],
    )

    if df.empty:
        return []

    # Per-stock totals — use max(buy, sell) as the volume denominator. Some
    # niche stocks have asymmetric reporting (e.g. 6843 — single broker on
    # one side reported, the matching trade missing), which would otherwise
    # blow up the concentration ratio when dividing by the smaller side.
    vol_buy  = df.groupby("code")["buy_vol"].sum()
    vol_sell = df.groupby("code")["sell_vol"].sum()

    # Per-stock main_buy / main_sell.
    records = []
    for code, group in df.groupby("code"):
        sb, ss = int(vol_buy.loc[code]), int(vol_sell.loc[code])
        total_vol_shares = max(sb, ss)
        if total_vol_shares < MIN_VOLUME_SHARES:
            continue
        # Reject stocks with severely imbalanced reporting (one side < 50% of
        # the other) — almost always a data-integrity edge case for illiquid
        # tickers, not a genuine signal.
        if min(sb, ss) < 0.5 * max(sb, ss):
            continue

        sorted_net = group["net_vol"].sort_values(ascending=False)
        top_buy_brokers  = sorted_net.head(TOP_BROKERS_PER_SIDE)
        top_sell_brokers = sorted_net.tail(TOP_BROKERS_PER_SIDE)
        main_buy_shares  = int(top_buy_brokers[top_buy_brokers > 0].sum())
        main_sell_shares = int(-top_sell_brokers[top_sell_brokers < 0].sum())
        net_shares = main_buy_shares - main_sell_shares
        concentration_pct = net_shares / total_vol_shares * 100 if total_vol_shares else 0

        # Clamp to ±150% — anything outside that is residual numerical noise.
        if abs(concentration_pct) > 150:
            continue

        # Store values in "張" (lots) — divide by 1000 since DB stores raw shares.
        records.append({
            "code": code,
            "vol":      total_vol_shares // SHARES_PER_LOT,
            "main_buy":  main_buy_shares // SHARES_PER_LOT,
            "main_sell": main_sell_shares // SHARES_PER_LOT,
            "net":       net_shares       // SHARES_PER_LOT,
            "concentration_pct": round(concentration_pct, 2),
        })
    return records


def split_rankings(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split into Top N buy-concentration and Top N sell-concentration."""
    records_sorted = sorted(records, key=lambda r: r["concentration_pct"], reverse=True)
    buy_top = records_sorted[:TOP_STOCKS_PER_SIDE]
    sell_top = list(reversed(records_sorted[-TOP_STOCKS_PER_SIDE:]))
    return buy_top, sell_top


def _update_history_index(latest_date: str) -> None:
    """Maintain assets/chip_history/<HISTORY_DIR_NAME>/_index.json with date list."""
    idx_path = LYNUS_ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / "_index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if idx_path.exists():
        try:
            existing = json.loads(idx_path.read_text(encoding="utf-8")).get("dates", [])
        except Exception:
            pass
    if latest_date not in existing:
        existing.append(latest_date)
    existing.sort(reverse=True)
    idx_path.write_text(
        json.dumps({"latest": existing[0] if existing else None, "dates": existing}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--end-date", type=str, default=None,
                   help="使用此日為窗口終點（預設用 broker_trading 最新日期）")
    p.add_argument("--history-only", action="store_true",
                   help="只寫歷史 dated JSON、不動主檔/manifest（給 backfill 用）")
    args = p.parse_args()

    if not CHIP_DB.exists():
        print(f"[ERR] {CHIP_DB} not found")
        return 1

    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=60)
    # 分點拆庫：ATTACH broker_chip.db(ro) + 同名 temp view，broker_trading 查詢無痛指向新 DB
    conn.execute(f"ATTACH DATABASE 'file:{BROKER_DB.as_posix()}?mode=ro' AS broker")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS broker_trading AS SELECT * FROM broker.broker_trading")
    dates = get_trading_dates(conn)
    if not dates:
        print("[ERR] broker_trading is empty")
        return 2
    print(f"[INFO] trading days available: {len(dates)} "
          f"({dates[-1]} → {dates[0]})")

    name_map = get_stock_names(conn)
    print(f"[INFO] stock name map: {len(name_map)} codes")

    # 決定 latest_date — 預設用 dates[0]，或者用 --end-date
    if args.end_date:
        if args.end_date not in dates:
            print(f"[ERR] end-date {args.end_date} not in trading days")
            return 3
        idx = dates.index(args.end_date)
        sliced_dates = dates[idx:]   # 從該日往前推
        latest_date = args.end_date
    else:
        sliced_dates = dates
        latest_date = dates[0]

    is_latest_run = (latest_date == dates[0])

    # Build every window.
    out = {"windows": [], "generated_at": datetime.now(TPE).isoformat()}

    for key, n_days, label in WINDOWS:
        if n_days is None:
            window_dates = sliced_dates
        else:
            window_dates = sliced_dates[:n_days]
        if not window_dates:
            continue
        end_d = window_dates[0]
        start_d = window_dates[-1]
        records = compute_ranking_for_window(conn, start_d, end_d, label)
        buy_top, sell_top = split_rankings(records)
        out["windows"].append({
            "key": key,
            "label": label,
            "start_date": start_d,
            "end_date": end_d,
            "n_days": len(window_dates),
            "total_stocks_after_filter": len(records),
            "buy_top": buy_top,
            "sell_top": sell_top,
        })

    conn.close()

    # Pull name + close + change for every stock that appears in any window.
    shown_codes = set()
    for w in out["windows"]:
        for r in w["buy_top"] + w["sell_top"]:
            shown_codes.add(r["code"])
    price_map = get_price_info(list(shown_codes), latest_date)
    print(f"[INFO] price map: {len(price_map)} codes")

    # 未來走勢 (1d/3d/5d/10d/20d) — 以 latest_date 為起點往後算
    from chip_analysis.data_access import get_future_returns
    future_returns = get_future_returns(list(shown_codes), latest_date)
    print(f"[INFO] future returns: {len(future_returns)} codes")

    for w in out["windows"]:
        for r in w["buy_top"] + w["sell_top"]:
            p = price_map.get(r["code"], {})
            # Prefer the name on the latest daily_price row (matches the
            # market display); fall back to meta_stock then raw code.
            r["name"]    = p.get("name") or name_map.get(r["code"], r["code"])
            r["close"]   = p.get("close")
            r["chg"]     = p.get("chg")
            r["chg_pct"] = p.get("chg_pct")
            fr = future_returns.get(r["code"], {})
            r["ret_1d"]  = fr.get("ret_1d")
            r["ret_3d"]  = fr.get("ret_3d")
            r["ret_5d"]  = fr.get("ret_5d")
            r["ret_10d"] = fr.get("ret_10d")
            r["ret_20d"] = fr.get("ret_20d")

    # Write dated history JSON (always)
    dated_path = LYNUS_ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / f"{latest_date}.json"
    dated_path.parent.mkdir(parents=True, exist_ok=True)
    dated_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {dated_path.relative_to(LYNUS_ROOT)} (dated)")
    _update_history_index(latest_date)

    # Write main JSON + HTML only if running for latest date AND not in history-only mode
    if is_latest_run and not args.history_only:
        DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
        DATA_OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)} — {sum(len(w['buy_top']) + len(w['sell_top']) for w in out['windows'])} entries")

        # Write HTML page
        render_html(out, latest_date)
        print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)} — chart page written")
    else:
        print(f"[SKIP main] backfill mode (end-date={latest_date} ≠ latest {dates[0]}) — main file untouched")

    # Refresh manifest entry only on latest non-backfill runs
    if is_latest_run and not args.history_only:
        update_manifest(out, latest_date)
        print(f"[OK] manifest.json — chip-concentration entry refreshed")

    return 0


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>籌碼集中度排行 — Lynus' Research</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    .chip-window-tabs {
      display: flex; gap: 6px; margin: 18px 0 18px; flex-wrap: wrap;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px; letter-spacing: .15em;
    }
    .chip-window-tab {
      padding: 8px 18px; border: 1px solid rgba(232,223,211,0.18);
      background: transparent; color: #9a9486; cursor: pointer;
      transition: all .15s; font: inherit;
    }
    .chip-window-tab:hover { color: #e8e4d8; border-color: #b8985c; }
    .chip-window-tab.is-active {
      background: #d4af37; color: #1a1612;
      border-color: #d4af37; font-weight: 600;
    }
    .chip-grid {
      display: grid; grid-template-columns: 1fr 1fr;
      gap: 24px; margin: 0 0 32px;
    }
    .chip-col {
      border: 1px solid rgba(232,223,211,0.10);
      padding: 18px 20px 8px;
      background: rgba(232,223,211,0.02);
    }
    .chip-col__head {
      display: flex; align-items: baseline; justify-content: space-between;
      margin-bottom: 14px; padding-bottom: 10px;
      border-bottom: 1px solid rgba(212,175,55,0.30);
    }
    .chip-col__title {
      font-family: 'Noto Serif TC', serif; font-size: 16px; font-weight: 700;
      color: #e8dfd3; margin: 0;
    }
    .chip-col__title em { font-style: normal; color: #d4af37; }
    .chip-col__meta {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px; color: #6e6350; letter-spacing: .12em;
    }
    .chip-row {
      display: grid; grid-template-columns: 28px 1fr auto;
      gap: 10px; align-items: center;
      padding: 10px 0; border-bottom: 1px dashed rgba(232,223,211,0.08);
    }
    .chip-row__rank {
      font-family: 'JetBrains Mono', monospace; font-size: 10px;
      color: #6e6350; text-align: right;
    }
    .chip-row__main {
      display: flex; flex-direction: column; gap: 2px; min-width: 0;
    }
    .chip-row__title {
      font-family: 'Noto Serif TC', serif; font-size: 14px; font-weight: 600;
      color: #e8dfd3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .chip-row__title .chip-row__code {
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
      color: #b8985c; margin-right: 6px;
    }
    .chip-row__sub {
      font-family: 'JetBrains Mono', monospace; font-size: 10px;
      color: #6a6557; letter-spacing: .05em;
    }
    .chip-row__bar {
      position: relative; width: 100%; height: 4px;
      background: rgba(232,223,211,0.04); border-radius: 1px; margin-top: 4px;
      overflow: hidden;
    }
    .chip-row__bar::after {
      content: ""; position: absolute; top: 0; left: 0; height: 100%;
      width: var(--w, 0%);
      background: var(--c, #d4af37);
    }
    .chip-row__pct {
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px; font-weight: 700; text-align: right;
      min-width: 70px;
    }
    .chip-row__pct--up   { color: #e85a5a; }
    .chip-row__pct--down { color: #5fb87a; }
    .chip-row__price {
      font-family: 'JetBrains Mono', monospace;
      font-size: 10px; color: #6e6350; text-align: right;
      margin-top: 2px;
    }
    .chip-row__price--up   { color: #e85a5a; }
    .chip-row__price--down { color: #5fb87a; }

    .chip-row__future {
      display: flex; gap: 8px; flex-wrap: wrap;
      margin-top: 4px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 9px; letter-spacing: .05em;
    }
    .chip-row__future span {
      color: #6e6350;
    }
    .chip-row__future span strong {
      font-weight: 600;
    }
    .chip-row__future .up { color: #e85a5a; }
    .chip-row__future .dn { color: #5fb87a; }

    @media (max-width: 760px) {
      .chip-grid { grid-template-columns: 1fr; }
    }

    .chip-date-picker {
      background: transparent;
      border: 1px solid rgba(212,175,55,0.40);
      color: #d4af37;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 4px 10px;
      cursor: pointer;
      letter-spacing: .1em;
    }
    .chip-date-picker option { background: #1a1612; color: #e8dfd3; }
  </style>
</head>
<body data-page="report">

  <header class="masthead">
    <div class="container">
      <div class="masthead__row">
        <a class="brand" href="../index.html">
          <span class="brand__mark">Lynus' <em>Research</em></span>
          <span class="brand__plate">Private Edition · MMXXVI</span>
        </a>
        <nav class="nav" aria-label="Primary">
          <a class="nav__link" href="../category.html?cat=sectors">族群</a>
          <a class="nav__link" href="../category.html?cat=taiex">大盤</a>
          <a class="nav__link nav__link--disabled" href="#" aria-disabled="true">選擇權</a>
          <a class="nav__link is-active" href="../category.html?cat=chips">籌碼</a>
          <a class="nav__link" href="../category.html?cat=stocks">個股</a>
          <a class="nav__link" href="../category.html?cat=news">新聞</a>
        </nav>
      </div>
    </div>
  </header>

  <main class="container">

    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="../index.html">Lynus' Research</a>
      <span class="breadcrumb__sep">/</span>
      <a href="../category.html?cat=chips">籌碼</a>
      <span class="breadcrumb__sep">/</span>
      <span>集中度排行</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 分點集中度</span>
        <span>__DATE_RANGE__</span>
        <span>切換日期：<select id="date-picker" class="chip-date-picker" aria-label="切換歷史日期"></select></span>
      </div>
      <h1 class="report-title">籌碼集中度<em>排行</em></h1>
      <p class="report-lead">前 15 大買超 / 賣超分點淨買賣量 ÷ 該股總成交量。+ = 主力大買集中 / − = 主力大賣集中。過濾成交量 &lt; 1,000 張的雜訊股。</p>
    </section>

    <section>
      <div class="chip-window-tabs" id="window-tabs">
        __TAB_BUTTONS__
      </div>
      <div id="chip-content">
        <!-- rendered by JS -->
      </div>
    </section>

  </main>

  <footer class="footer">
    <div class="container">
      <div id="footer-line" class="footer__row"></div>
    </div>
  </footer>

  <script src="../assets/main.js" defer></script>
  <script>
  const INITIAL_DATA = __DATA_JSON__;

  function render(windowKey) {
    const w = DATA.windows.find(x => x.key === windowKey);
    if (!w) return;
    const host = document.getElementById('chip-content');

    function row(r, idx, side) {
      const pct = r.concentration_pct || 0;
      const pctClass = pct >= 0 ? 'chip-row__pct--up' : 'chip-row__pct--down';
      const priceClass = (r.chg_pct || 0) >= 0 ? 'chip-row__price--up' : 'chip-row__price--down';
      const sign = pct >= 0 ? '+' : '';
      const barWidth = Math.min(100, Math.abs(pct) * 2);
      const barColor = side === 'buy' ? 'rgba(232,90,90,0.55)' : 'rgba(95,184,122,0.55)';
      const mainVal = side === 'buy' ? r.main_buy : r.main_sell;
      const priceLine = r.close
        ? `<div class="chip-row__price ${priceClass}">收 ${r.close.toLocaleString('en-US',{maximumFractionDigits:2})} ${r.chg >= 0 ? '+' : ''}${(r.chg ?? 0).toFixed(2)} (${r.chg_pct >= 0 ? '+' : ''}${(r.chg_pct ?? 0).toFixed(2)}%)</div>`
        : '';
      const futCell = (label, v) => {
        if (v == null) return `<span>${label} —</span>`;
        const cls = v >= 0 ? 'up' : 'dn';
        return `<span>${label} <strong class="${cls}">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</strong></span>`;
      };
      const futureLine = (r.ret_1d != null || r.ret_5d != null || r.ret_20d != null)
        ? `<div class="chip-row__future">
             ${futCell('1d', r.ret_1d)}
             ${futCell('3d', r.ret_3d)}
             ${futCell('5d', r.ret_5d)}
             ${futCell('10d', r.ret_10d)}
             ${futCell('20d', r.ret_20d)}
           </div>` : '';
      return `
        <div class="chip-row">
          <div class="chip-row__rank">${idx + 1}.</div>
          <div class="chip-row__main">
            <div class="chip-row__title">
              <span class="chip-row__code">${r.code}</span>${r.name}
            </div>
            <div class="chip-row__sub">主力${side === 'buy' ? '買' : '賣'} ${mainVal.toLocaleString()} 張 · 成交 ${r.vol.toLocaleString()} 張</div>
            <div class="chip-row__bar" style="--w:${barWidth}%;--c:${barColor}"></div>
          </div>
          <div>
            <div class="chip-row__pct ${pctClass}">${sign}${pct.toFixed(1)}%</div>
            ${priceLine}
            ${futureLine}
          </div>
        </div>
      `;
    }

    host.innerHTML = `
      <div class="chip-grid">
        <div class="chip-col">
          <div class="chip-col__head">
            <h2 class="chip-col__title">買超集中 <em>Top ${w.buy_top.length}</em></h2>
            <span class="chip-col__meta">${w.label}</span>
          </div>
          ${w.buy_top.map((r, i) => row(r, i, 'buy')).join('')}
        </div>
        <div class="chip-col">
          <div class="chip-col__head">
            <h2 class="chip-col__title">賣超集中 <em>Top ${w.sell_top.length}</em></h2>
            <span class="chip-col__meta">${w.label}</span>
          </div>
          ${w.sell_top.map((r, i) => row(r, i, 'sell')).join('')}
        </div>
      </div>
    `;
  }

  document.querySelectorAll('.chip-window-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.chip-window-tab').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      render(btn.dataset.window);
    });
  });

  let DATA = INITIAL_DATA;
  const HISTORY_DIR = '../assets/chip_history/chip_concentration';
  const urlDate = new URLSearchParams(window.location.search).get('date');
  async function loadIndex() {
    try {
      const idx = await fetch(`${HISTORY_DIR}/_index.json?t=${Date.now()}`).then(r => r.json());
      const sel = document.getElementById('date-picker');
      sel.innerHTML = (idx.dates || []).map(d => `<option value="${d}">${d}</option>`).join('');
      // URL 帶 ?date=X 優先；否則用 INITIAL_DATA 內的最新日期
      const wantDate = urlDate && (idx.dates || []).includes(urlDate) ? urlDate : (DATA.windows && DATA.windows[0] ? DATA.windows[0].end_date : null);
      if (wantDate) sel.value = wantDate;
      sel.addEventListener('change', () => loadDate(sel.value));
      // 若 URL 指定不同日期，立即載入該日 JSON
      if (urlDate && urlDate !== (DATA.windows && DATA.windows[0]?.end_date)) {
        await loadDate(urlDate);
      }
    } catch (e) { console.warn('[date-picker]', e); }
  }
  async function loadDate(date) {
    try {
      const data = await fetch(`${HISTORY_DIR}/${date}.json?t=${Date.now()}`).then(r => r.json());
      DATA = data;
      const activeKey = document.querySelector('.chip-window-tab.is-active')?.dataset.window || '1d';
      render(activeKey);
    } catch (e) { console.warn('[loadDate]', date, e); }
  }
  loadIndex();
  render('1d');
  </script>
</body>
</html>
"""


def render_html(out: dict, latest_date: str) -> None:
    tabs = []
    for w in out["windows"]:
        active = " is-active" if w["key"] == "1d" else ""
        # Strip the leading "key · " bit for the button label
        label = w["label"].split("·")[0].strip()
        tabs.append(f'<button class="chip-window-tab{active}" data-window="{w["key"]}" type="button">{label}</button>')
    tab_html = "\n        ".join(tabs)

    first = out["windows"][0] if out["windows"] else {}
    date_range = f"{first.get('start_date', '—')} → {first.get('end_date', '—')}"

    rendered = (PAGE_TEMPLATE
        .replace("__DATA_JSON__",   json.dumps(out, ensure_ascii=False))
        .replace("__TAB_BUTTONS__", tab_html)
        .replace("__DATE_RANGE__",  date_range)
        .replace("__LATEST_DATE__", latest_date)
    )
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(rendered, encoding="utf-8")


def update_manifest(out: dict, latest_date: str) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    today_w = next((w for w in out["windows"] if w["key"] == "1d"), None)
    top_buy = today_w["buy_top"][0] if today_w and today_w["buy_top"] else None
    top_sell = today_w["sell_top"][0] if today_w and today_w["sell_top"] else None

    summary_bits = [f"5 個時段 · 每邊 Top 50 · 過濾成交量 < 1,000 張。"]
    if top_buy:
        summary_bits.append(f"今日買超集中 #1：{top_buy['code']} {top_buy['name']} +{top_buy['concentration_pct']:.1f}%。")
    if top_sell:
        summary_bits.append(f"賣超集中 #1：{top_sell['code']} {top_sell['name']} {top_sell['concentration_pct']:.1f}%。")

    entry = {
        "id":              "chip-concentration",
        "category":        "chips",
        "type":            "ranking",
        "date":            latest_date,
        "time":            "21:00",
        "title":           "籌碼集中度 · 分點 Top 50 排行",
        "title_em":        "集中度",
        "summary":         " ".join(summary_bits),
        "tags":            ["籌碼", "分點", "集中度", "排行榜"],
        "source_pipeline": "stock_chip_crawler",
        "url":             "reports/chip-concentration.html",
        "stats":           [
            {"label": "時段數", "value": str(len(out["windows"])), "color": "neutral"},
            {"label": "Top 買超", "value": (f"{top_buy['code']} +{top_buy['concentration_pct']:.0f}%" if top_buy else "—"), "color": "up"},
            {"label": "Top 賣超", "value": (f"{top_sell['code']} {top_sell['concentration_pct']:.0f}%" if top_sell else "—"), "color": "down"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != "chip-concentration"]
    manifest["entries"].append(entry)
    manifest["entries"].sort(key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")), reverse=True)
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
