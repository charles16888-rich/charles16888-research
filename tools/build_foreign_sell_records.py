"""
build_foreign_sell_records.py
=============================
Build the all-time foreign-investor single-day sell records table.

This is a tracking/statistics view only. It ranks historical
institutional.foreign_net sell events and records the stock's later
1/3/5/10/20/30/60-trading-day path from the event close.

Outputs:
    assets/foreign_sell_records.json
    reports/foreign-sell-records.html
    assets/chip_history/foreign_sell_records/<as_of_date>.json
    manifest.json entry under category "chips"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chip_analysis.data_access import CHIP_DB  # noqa: E402


LYNUS_ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = LYNUS_ROOT / "assets" / "foreign_sell_records.json"
PAGE_OUT = LYNUS_ROOT / "reports" / "foreign-sell-records.html"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"
HISTORY_DIR_NAME = "foreign_sell_records"
TPE = ZoneInfo("Asia/Taipei")

DEFAULT_HORIZONS = [1, 3, 5, 10, 20, 30, 60]
DEFAULT_TOP_N = 20
SHARES_PER_LOT = 1000
EXCLUDE_PREFIX = ("00",)


def build_records_from_frames(
    institutional: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    top_n: int = DEFAULT_TOP_N,
    horizons: list[int] | None = None,
    include_etf: bool = False,
) -> list[dict]:
    """Return ranked historical foreign sell records from in-memory frames."""

    horizons = horizons or DEFAULT_HORIZONS
    inst = _normalize_institutional(institutional)
    px = _normalize_prices(prices)

    inst = inst[inst["foreign_net_buy_shares"] < 0].copy()
    if not include_etf:
        inst = inst[~inst["stock_id"].astype(str).str.startswith(EXCLUDE_PREFIX)]
    inst = inst.sort_values(
        ["foreign_net_buy_shares", "trade_date", "stock_id"],
        ascending=[True, True, True],
    ).head(top_n)

    if inst.empty:
        return []

    prices_by_code = {code: sub.sort_values("trade_date").reset_index(drop=True) for code, sub in px.groupby("stock_id")}
    records: list[dict] = []
    for rank, (_, event) in enumerate(inst.iterrows(), start=1):
        code = str(event["stock_id"])
        event_date = pd.Timestamp(event["trade_date"]).normalize()
        sub = prices_by_code.get(code, pd.DataFrame())
        price_row = _event_price_row(sub, event_date)
        close = _to_float(price_row.get("close") if price_row is not None else None)
        name = _first_text(
            price_row.get("name") if price_row is not None else None,
            event.get("name"),
            code,
        )

        row = {
            "rank": rank,
            "trade_date": event_date.date().isoformat(),
            "code": code,
            "name": name,
            "foreign_net_shares": _to_int(event.get("foreign_net_buy_shares")),
            "foreign_net_lots": _to_lots(event.get("foreign_net_buy_shares")),
            "foreign_buy_lots": _to_lots(event.get("foreign_buy")),
            "foreign_sell_lots": _to_lots(event.get("foreign_sell")),
            "close": close,
            "price_date_actual": price_row.get("trade_date").date().isoformat() if price_row is not None else None,
        }
        row.update(_future_returns_from_prices(sub, event_date, close, horizons))
        records.append(row)
    return records


def _normalize_institutional(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "date": "trade_date",
        "code": "stock_id",
        "foreign_net": "foreign_net_buy_shares",
    }
    out = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns}).copy()
    required = {"trade_date", "stock_id", "foreign_net_buy_shares"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"institutional is missing required columns: {sorted(missing)}")
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    for col in ("foreign_net_buy_shares", "foreign_buy", "foreign_sell"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["trade_date", "stock_id", "foreign_net_buy_shares"])


def _normalize_prices(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {"date": "trade_date", "code": "stock_id"}
    out = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns}).copy()
    required = {"trade_date", "stock_id", "close"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"prices is missing required columns: {sorted(missing)}")
    if "name" not in out.columns:
        out["name"] = pd.NA
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.drop_duplicates(["stock_id", "trade_date"], keep="last")
    return out.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)


def _event_price_row(prices: pd.DataFrame, event_date: pd.Timestamp) -> pd.Series | None:
    if prices.empty:
        return None
    match = prices[prices["trade_date"] == event_date]
    if match.empty:
        return None
    return match.iloc[0]


def _future_returns_from_prices(
    prices: pd.DataFrame,
    event_date: pd.Timestamp,
    close: float | None,
    horizons: list[int],
) -> dict[str, float | None]:
    out = {f"ret_{h}d": None for h in horizons}
    if prices.empty or close is None or close == 0:
        return out
    matches = prices.index[prices["trade_date"] == event_date].tolist()
    if not matches:
        return out
    pos = matches[0]
    for horizon in horizons:
        target_pos = pos + horizon
        if target_pos >= len(prices):
            continue
        target_close = _to_float(prices.iloc[target_pos].get("close"))
        if target_close is None:
            continue
        out[f"ret_{horizon}d"] = round((target_close - close) / close * 100, 2)
    return out


def _to_lots(value: object) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return round(number / SHARES_PER_LOT, 2)


def _to_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _to_int(value: object) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def _first_text(*values: object) -> str:
    for value in values:
        if value is not None and not pd.isna(value):
            text = str(value).strip()
            if text:
                return text
    return ""


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=300)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _date_bounds(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    row = conn.execute("SELECT MIN(date), MAX(date) FROM institutional").fetchone()
    return (row[0], row[1]) if row else (None, None)


def _load_top_events(
    conn: sqlite3.Connection,
    *,
    top_n: int,
    start_date: str | None,
    end_date: str | None,
    include_etf: bool,
) -> pd.DataFrame:
    candidate_limit = max(top_n * 50, 200)
    max_candidate_limit = 1_000_000
    while True:
        raw = pd.read_sql_query(
            """
            SELECT date AS trade_date,
                   code AS stock_id,
                   name,
                   foreign_buy,
                   foreign_sell,
                   foreign_net AS foreign_net_buy_shares
            FROM institutional INDEXED BY idx_inst_fnet
            WHERE foreign_net < 0
            ORDER BY foreign_net ASC
            LIMIT ?
            """,
            conn,
            params=[candidate_limit],
            parse_dates=["trade_date"],
        )
        filtered = raw
        if start_date:
            filtered = filtered[filtered["trade_date"] >= pd.Timestamp(start_date)]
        if end_date:
            filtered = filtered[filtered["trade_date"] <= pd.Timestamp(end_date)]
        if not include_etf:
            filtered = filtered[~filtered["stock_id"].astype(str).str.startswith(EXCLUDE_PREFIX)]
        filtered = filtered.sort_values(
            ["foreign_net_buy_shares", "trade_date", "stock_id"],
            ascending=[True, True, True],
        ).head(top_n)
        if len(filtered) >= top_n or len(raw) < candidate_limit or candidate_limit >= max_candidate_limit:
            return filtered.reset_index(drop=True)
        candidate_limit = min(candidate_limit * 2, max_candidate_limit)


def _load_prices_for_events(conn: sqlite3.Connection, events: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=["trade_date", "stock_id", "name", "close"])
    codes = sorted(events["stock_id"].astype(str).unique())
    placeholders = ",".join("?" for _ in codes)
    min_event = pd.to_datetime(events["trade_date"]).min().date().isoformat()
    max_event = pd.to_datetime(events["trade_date"]).max().date().isoformat()
    max_calendar_days = max(horizons) * 2
    params: list[object] = [*codes, min_event, max_event, f"+{max_calendar_days} days"]
    return pd.read_sql_query(
        f"""
        SELECT date AS trade_date,
               code AS stock_id,
               name,
               close
        FROM daily_price
        WHERE code IN ({placeholders})
          AND date >= ?
          AND date <= date(?, ?)
          AND close IS NOT NULL
        ORDER BY code, date
        """,
        conn,
        params=params,
        parse_dates=["trade_date"],
    )


def build_payload(
    records: list[dict],
    *,
    as_of_date: str | None,
    source_start_date: str | None,
    source_end_date: str | None,
    top_n: int,
    horizons: list[int],
    include_etf: bool,
) -> dict:
    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "as_of_date": as_of_date,
        "source_start_date": source_start_date,
        "source_end_date": source_end_date,
        "top_n": top_n,
        "horizons": horizons,
        "include_etf": include_etf,
        "source": "stock_chip.db institutional.foreign_net; later returns from daily_price close",
        "note": "僅追蹤外資單日賣超事件後走勢，不構成投資建議。",
        "rankings": {
            "top10": records[:10],
            "top20": records[:20],
        },
        "records": records,
    }


def _update_history_index(as_of_date: str) -> None:
    idx_path = LYNUS_ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / "_index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if idx_path.exists():
        try:
            existing = json.loads(idx_path.read_text(encoding="utf-8")).get("dates", [])
        except Exception:
            pass
    if as_of_date not in existing:
        existing.append(as_of_date)
    existing.sort(reverse=True)
    idx_path.write_text(
        json.dumps({"latest": existing[0] if existing else None, "dates": existing}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build all-time foreign sell records.")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Number of records to keep. Default: 20.")
    parser.add_argument("--start-date", default=None, help="Optional institutional date lower bound.")
    parser.add_argument("--end-date", default=None, help="Optional institutional date upper bound.")
    parser.add_argument("--include-etf", action="store_true", help="Include 00xx ETF/product codes.")
    parser.add_argument("--history-only", action="store_true", help="Only write dated JSON; do not touch main file/manifest.")
    args = parser.parse_args()

    if not CHIP_DB.exists():
        print(f"[ERR] {CHIP_DB} not found")
        return 1
    if args.top_n <= 0:
        print("[ERR] --top-n must be positive")
        return 2

    horizons = DEFAULT_HORIZONS
    with _connect_ro() as conn:
        source_start, source_end = _date_bounds(conn)
        events = _load_top_events(
            conn,
            top_n=args.top_n,
            start_date=args.start_date,
            end_date=args.end_date,
            include_etf=args.include_etf,
        )
        prices = _load_prices_for_events(conn, events, horizons)

    records = build_records_from_frames(
        events,
        prices,
        top_n=args.top_n,
        horizons=horizons,
        include_etf=args.include_etf,
    )
    as_of_date = args.end_date or source_end
    payload = build_payload(
        records,
        as_of_date=as_of_date,
        source_start_date=args.start_date or source_start,
        source_end_date=args.end_date or source_end,
        top_n=args.top_n,
        horizons=horizons,
        include_etf=args.include_etf,
    )

    if as_of_date is None:
        print("[ERR] institutional has no date range")
        return 3

    dated_path = LYNUS_ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / f"{as_of_date}.json"
    dated_path.parent.mkdir(parents=True, exist_ok=True)
    dated_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {dated_path.relative_to(LYNUS_ROOT)} (dated)")
    _update_history_index(as_of_date)

    is_latest_run = as_of_date == source_end
    if is_latest_run and not args.history_only:
        DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
        DATA_OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)}")
        render_html(payload)
        print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)}")
        update_manifest(payload)
        print("[OK] manifest.json — foreign-sell-records entry refreshed")
    else:
        print("[SKIP main] backfill mode — main file/manifest untouched")
    return 0


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>外資史上賣超紀錄 — Lynus' Research</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    .fsr-tabs {
      display: flex; gap: 6px; margin: 18px 0 18px; flex-wrap: wrap;
      font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .15em;
    }
    .fsr-tab {
      padding: 8px 18px; border: 1px solid rgba(232,223,211,0.18);
      background: transparent; color: #9a9486; cursor: pointer; transition: all .15s; font: inherit;
    }
    .fsr-tab:hover { color: #e8e4d8; border-color: #b8985c; }
    .fsr-tab.is-active { background: #d4af37; color: #1a1612; border-color: #d4af37; font-weight: 600; }
    .fsr-table-wrap {
      overflow-x: auto; border: 1px solid rgba(232,223,211,0.10);
      background: rgba(232,223,211,0.02); margin: 0 0 28px;
    }
    .fsr-table {
      width: 100%; min-width: 980px; border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
    }
    .fsr-table th {
      text-align: left; padding: 10px 12px; color: #9a9486; font-weight: 500;
      letter-spacing: .1em; border-bottom: 1px solid rgba(212,175,55,0.30);
      white-space: nowrap;
    }
    .fsr-table td {
      padding: 10px 12px; color: #c9c0b3; border-bottom: 1px dashed rgba(232,223,211,0.08);
      white-space: nowrap;
    }
    .fsr-rank { color: #6e6350; text-align: right; }
    .fsr-code { color: #b8985c; }
    .fsr-name { font-family: 'Noto Serif TC', serif; color: #e8dfd3; font-size: 13px; }
    .fsr-right { text-align: right; }
    .fsr-neg { color: #5fb87a; font-weight: 700; }
    .fsr-pos { color: #e85a5a; font-weight: 700; }
    .fsr-muted { color: #6e6350; }
    .fsr-stat-row {
      display: flex; gap: 24px; flex-wrap: wrap; margin: 14px 0 20px;
      font-family: 'JetBrains Mono', monospace; font-size: 11px; letter-spacing: .12em; color: #9a9486;
    }
    .fsr-stat-row strong { color: #e8dfd3; font-weight: 600; }
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
      <span>外資賣超紀錄</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 外資史上賣超</span>
        <span>__SOURCE_RANGE__</span>
        <span>更新至 __AS_OF_DATE__</span>
      </div>
      <h1 class="report-title">外資史上<em>賣超</em>紀錄</h1>
      <p class="report-lead">
        以 institutional.foreign_net 單日淨買賣超排序，記錄史上外資賣超最大的個股事件，
        並追蹤事件收盤後 1/3/5/10/20/30/60 個交易日漲跌幅。本表只做追蹤與統計，不做投資建議。
      </p>
      <div class="fsr-stat-row" id="fsr-stats"></div>
    </section>

    <section>
      <div class="fsr-tabs">
        <button class="fsr-tab is-active" type="button" data-limit="10">TOP 10</button>
        <button class="fsr-tab" type="button" data-limit="20">TOP 20</button>
      </div>
      <div class="fsr-table-wrap" id="fsr-table"></div>
    </section>
  </main>

  <footer class="footer">
    <div class="container">
      <div id="footer-line" class="footer__row"></div>
    </div>
  </footer>

  <script src="../assets/main.js" defer></script>
  <script>
  const DATA = __DATA_JSON__;
  const horizons = DATA.horizons || [1,3,5,10,20,30,60];

  function cls(v) {
    if (v == null) return 'fsr-muted';
    return v >= 0 ? 'fsr-pos' : 'fsr-neg';
  }
  function fmt(v, digits = 1, suffix = '') {
    if (v == null) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}${Number(v).toFixed(digits)}${suffix}`;
  }
  function fmtLots(v) {
    if (v == null) return '—';
    return `${Math.round(v).toLocaleString()} 張`;
  }
  function renderStats() {
    const r = DATA.records || [];
    const top = r[0] || {};
    document.getElementById('fsr-stats').innerHTML = [
      `<span>紀錄筆數：<strong>${r.length}</strong></span>`,
      `<span>最大賣超：<strong>${top.code || '—'} ${top.foreign_net_lots != null ? fmtLots(top.foreign_net_lots) : ''}</strong></span>`,
      `<span>走勢欄位：<strong>${horizons.join('/')} 日</strong></span>`
    ].join('');
  }
  function render(limit) {
    const rows = (DATA.records || []).slice(0, limit);
    const futureHeads = horizons.map(h => `<th class="fsr-right">${h}日後</th>`).join('');
    const body = rows.map(r => {
      const futureCells = horizons.map(h => {
        const v = r[`ret_${h}d`];
        return `<td class="fsr-right ${cls(v)}">${fmt(v, 1, '%')}</td>`;
      }).join('');
      return `<tr>
        <td class="fsr-rank">${r.rank}</td>
        <td>${r.trade_date}</td>
        <td><span class="fsr-code">${r.code}</span></td>
        <td><span class="fsr-name">${r.name}</span></td>
        <td class="fsr-right fsr-neg">${fmtLots(r.foreign_net_lots)}</td>
        <td class="fsr-right">${r.close != null ? Number(r.close).toFixed(2) : '—'}</td>
        ${futureCells}
      </tr>`;
    }).join('');
    document.getElementById('fsr-table').innerHTML = `<table class="fsr-table">
      <thead><tr>
        <th class="fsr-right">#</th><th>日期</th><th>代號</th><th>名稱</th>
        <th class="fsr-right">外資淨賣超</th><th class="fsr-right">事件收盤</th>${futureHeads}
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }
  document.querySelectorAll('.fsr-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.fsr-tab').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      render(Number(btn.dataset.limit));
    });
  });
  renderStats();
  render(10);
  </script>
</body>
</html>
"""


def render_html(out: dict) -> None:
    source_range = f"{out.get('source_start_date') or '-'} -> {out.get('source_end_date') or '-'}"
    rendered = (
        PAGE_TEMPLATE
        .replace("__DATA_JSON__", json.dumps(out, ensure_ascii=False))
        .replace("__SOURCE_RANGE__", source_range)
        .replace("__AS_OF_DATE__", out.get("as_of_date") or "-")
    )
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(rendered, encoding="utf-8")


def update_manifest(out: dict) -> None:
    if not MANIFEST_PATH.exists():
        return
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    records = out.get("records", [])
    top = records[0] if records else None
    summary = (
        f"外資單日淨賣超史上 Top {min(20, len(records))}，"
        f"追蹤事件後 {'/'.join(str(x) for x in out.get('horizons', []))} 日走勢。"
    )
    if top:
        summary += f" 最大紀錄：{top['trade_date']} {top['code']} {top['name']} {top['foreign_net_lots']:,.0f} 張。"

    entry = {
        "id": "foreign-sell-records",
        "category": "chips",
        "type": "ranking",
        "date": out.get("as_of_date"),
        "time": "21:05",
        "title": "外資史上賣超紀錄 · Top 20",
        "title_em": "賣超",
        "summary": summary,
        "tags": ["籌碼", "外資", "賣超", "事件追蹤"],
        "source_pipeline": "stock_chip_crawler",
        "url": "reports/foreign-sell-records.html",
        "stats": [
            {
                "label": "最大賣超",
                "value": f"{top['code']} {top['foreign_net_lots']:,.0f}張" if top else "-",
                "color": "down",
            },
            {"label": "紀錄筆數", "value": str(len(records)), "color": "neutral"},
            {"label": "走勢", "value": "1-60日", "color": "neutral"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != entry["id"]]
    manifest["entries"].append(entry)
    manifest["entries"].sort(key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")), reverse=True)
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
