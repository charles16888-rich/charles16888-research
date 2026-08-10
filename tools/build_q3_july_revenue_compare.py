"""
Build July-2026 MOPS vs Q3 forecast monthly tracking (local preview only).

Rule: compare the announced July monthly revenue against (Q3 forecast revenue / 3).
Status uses the same ±3% band as the Q2 full-quarter tracker:
  - above  : july >= target * 1.03
  - below  : july <= target * 0.97
  - inline : within ±3%
  - incomplete : July MOPS missing
  - no_forecast : no Q3 revenue forecast
  - suspect : OCR/unit-scale quarantine (forecast implausibly small vs July)

Does not write production site folders or deploy anything.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup

TPE = ZoneInfo("Asia/Taipei")

ROOT = Path(__file__).resolve().parent.parent  # charles1688-cloudflare-sync
FORECAST_REPORT = ROOT / "reports" / "q3-forecast-2026q3.html"
REVENUE_DB = Path(r"E:\stock_data\mops_index.db")
DATA_OUT = ROOT / "assets" / "q3_july_revenue_compare.json"
PAGE_OUT = ROOT / "reports" / "q3-july-mops-compare.html"

QUARTER = "2026Q3"
TRACK_MONTH = (115, 7)  # July 2026
TRACK_PERIOD = "2026-07"
SURPRISE_THRESHOLD_PCT = 3.0
MONTHLY_FRACTION = 1.0 / 3.0
MOPS_REVENUE_URL = "https://mops.twse.com.tw/mops/web/t05st10_ifrs"

SUSPECT_MIN_MONTH_RATIO = 0.5
SUSPECT_MAX_UNDERSTATE_RATIO = 20.0


def _period_label(roc_year: int, month: int) -> str:
    return f"{roc_year + 1911}-{month:02d}"


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "--", "—", "nan", "None"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_int(value) -> int | None:
    number = _to_float(value)
    return None if number is None else int(number)


def _code(value) -> str:
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        return text.zfill(4)
    return text


def _read_html_tables(path: Path) -> list[pd.DataFrame]:
    try:
        return pd.read_html(path)
    except Exception:
        pass
    soup = BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    tables: list[pd.DataFrame] = []
    for table in soup.find_all("table"):
        rows: list[list[str]] = []
        for tr in table.find_all("tr"):
            cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        header = rows[0]
        body = rows[1:]
        normalized = []
        for row in body:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[: len(header)]
            normalized.append(row)
        tables.append(pd.DataFrame(normalized, columns=header))
    return tables


def load_forecast_table(path: Path = FORECAST_REPORT) -> pd.DataFrame:
    tables = _read_html_tables(path)
    if not tables:
        raise RuntimeError(f"no tables found in {path}")
    required = {"代號", "公司", "樣本", "營收 NT$百萬", "信心"}
    df = None
    for table in tables:
        if required.issubset(set(table.columns)):
            df = table.copy()
            break
    if df is None:
        seen = [list(table.columns) for table in tables]
        raise RuntimeError(f"forecast table missing columns: {sorted(required)}; seen={seen}")
    return pd.DataFrame(
        {
            "code": df["代號"].map(_code),
            "name": df["公司"].astype(str).str.strip(),
            "sample_count": df["樣本"].map(_to_int),
            "forecast_revenue_m": df["營收 NT$百萬"].map(_to_float),
            "confidence": df["信心"].astype(str).str.strip(),
        }
    )


def load_july_revenue(db_path: Path = REVENUE_DB) -> tuple[pd.DataFrame, str | None]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    roc_year, month = TRACK_MONTH
    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=120) as conn:
        latest = conn.execute(
            "SELECT roc_year, month FROM mops_revenue ORDER BY roc_year DESC, month DESC LIMIT 1"
        ).fetchone()
        df = pd.read_sql_query(
            """
            SELECT code, name, roc_year, month, revenue_k, mom, yoy, ytd_yoy, publish_time
            FROM mops_revenue
            WHERE roc_year = ? AND month = ?
            """,
            conn,
            params=[roc_year, month],
        )
    latest_period = _period_label(int(latest[0]), int(latest[1])) if latest else None
    if df.empty:
        return df, latest_period
    df["code"] = df["code"].astype(str).map(_code)
    df["period"] = TRACK_PERIOD
    df["revenue_m"] = pd.to_numeric(df["revenue_k"], errors="coerce") / 1000.0
    return df, latest_period


def _is_suspect_forecast(forecast_revenue_m: float, july_revenue_m: float) -> bool:
    if forecast_revenue_m <= 0 or july_revenue_m is None:
        return False
    monthly_target = forecast_revenue_m * MONTHLY_FRACTION
    if monthly_target <= 0:
        return False
    if july_revenue_m / monthly_target >= SUSPECT_MAX_UNDERSTATE_RATIO:
        return True
    if july_revenue_m > 0 and monthly_target < july_revenue_m * SUSPECT_MIN_MONTH_RATIO:
        return True
    return False


def _status_for(
    forecast_revenue_m: float | None,
    july_revenue_m: float | None,
) -> tuple[str, float | None, float | None, float | None]:
    """Return status, monthly_target, surprise_m, surprise_pct."""
    if forecast_revenue_m is None:
        return "no_forecast", None, None, None
    monthly_target = forecast_revenue_m * MONTHLY_FRACTION
    if july_revenue_m is None:
        return "incomplete", monthly_target, None, None
    if _is_suspect_forecast(forecast_revenue_m, july_revenue_m):
        diff = july_revenue_m - monthly_target
        pct = diff / monthly_target * 100 if monthly_target else None
        return "suspect", monthly_target, diff, pct
    diff = july_revenue_m - monthly_target
    pct = diff / monthly_target * 100 if monthly_target else None
    if pct is not None and pct >= SURPRISE_THRESHOLD_PCT:
        return "above", monthly_target, diff, pct
    if pct is not None and pct <= -SURPRISE_THRESHOLD_PCT:
        return "below", monthly_target, diff, pct
    return "inline", monthly_target, diff, pct


def _status_label(status: str, *, has_july: bool) -> str:
    labels = {
        "above": "高於預期",
        "below": "低於預期",
        "inline": "符合預期",
        "incomplete": "尚未公告7月",
        "suspect": "財測異常",
        "no_forecast": "無營收財測" if not has_july else "無研報營收預估（7月已公告）",
    }
    return labels.get(status, status)


def build_comparison(
    forecast: pd.DataFrame,
    revenue: pd.DataFrame,
    *,
    latest_mops_period: str | None,
) -> dict:
    revenue_by_code = {}
    if not revenue.empty:
        for code, rows in revenue.groupby("code"):
            revenue_by_code[str(code)] = rows.iloc[0].to_dict()

    rows_out: list[dict] = []
    for _, f in forecast.iterrows():
        code = str(f["code"])
        month_row = revenue_by_code.get(code)
        july_m = _to_float((month_row or {}).get("revenue_m"))
        forecast_m = _to_float(f.get("forecast_revenue_m"))
        status, monthly_target, surprise_m, surprise_pct = _status_for(forecast_m, july_m)
        rows_out.append(
            {
                "code": code,
                "name": str(f["name"]),
                "sample_count": _to_int(f.get("sample_count")),
                "confidence": str(f.get("confidence") or ""),
                "forecast_revenue_m": forecast_m,
                "monthly_target_m": monthly_target,
                "july_revenue_m": july_m,
                "announced_revenue_m": july_m,
                "actual_revenue_m": july_m if status in ("above", "below", "inline", "suspect") else None,
                "surprise_m": surprise_m,
                "surprise_pct": surprise_pct,
                "status": status,
                "status_label": _status_label(status, has_july=july_m is not None),
                "months": {
                    TRACK_PERIOD: {
                        "revenue_m": july_m,
                        "mom": _to_float((month_row or {}).get("mom")),
                        "yoy": _to_float((month_row or {}).get("yoy")),
                        "publish_time": (month_row or {}).get("publish_time"),
                    }
                }
                if july_m is not None or month_row
                else {},
                "missing_months": [] if july_m is not None else [TRACK_PERIOD],
                "latest_publish_time": (month_row or {}).get("publish_time"),
                "mops_url": MOPS_REVENUE_URL,
            }
        )

    status_order = {
        "below": 0,
        "above": 1,
        "inline": 2,
        "suspect": 3,
        "incomplete": 4,
        "no_forecast": 5,
    }
    rows_out.sort(
        key=lambda r: (
            status_order.get(r["status"], 9),
            -(abs(r["surprise_pct"]) if r["surprise_pct"] is not None else -1),
            -(r["forecast_revenue_m"] or 0),
            r["code"],
        )
    )
    stats = {
        "total_companies": len(rows_out),
        "revenue_forecast_count": sum(1 for r in rows_out if r["forecast_revenue_m"] is not None),
        "complete_count": sum(1 for r in rows_out if r["status"] in ("above", "below", "inline")),
        "above_count": sum(1 for r in rows_out if r["status"] == "above"),
        "below_count": sum(1 for r in rows_out if r["status"] == "below"),
        "inline_count": sum(1 for r in rows_out if r["status"] == "inline"),
        "suspect_count": sum(1 for r in rows_out if r["status"] == "suspect"),
        "incomplete_count": sum(1 for r in rows_out if r["status"] == "incomplete"),
        "no_forecast_count": sum(1 for r in rows_out if r["status"] == "no_forecast"),
        "july_announced_count": sum(1 for r in rows_out if r["july_revenue_m"] is not None),
    }
    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "quarter": QUARTER,
        "track_period": TRACK_PERIOD,
        "quarter_months": [TRACK_PERIOD],
        "latest_mops_period": latest_mops_period,
        "surprise_threshold_pct": SURPRISE_THRESHOLD_PCT,
        "monthly_fraction": MONTHLY_FRACTION,
        "compare_rule": "july_revenue vs (q3_forecast_revenue / 3)",
        "source": {
            "forecast": "reports/q3-forecast-2026q3.html anonymized consensus table",
            "mops": "E:\\stock_data\\mops_index.db mops_revenue",
            "mops_url": MOPS_REVENUE_URL,
        },
        "note": "以 7 月 MOPS 月營收對照 Q3 財測營收的 1/3。僅追蹤統計，不構成投資建議。",
        "stats": stats,
        "rows": rows_out,
    }


def render_local_page(payload: dict) -> str:
    """Standalone local page; light paper headers (not dark)."""
    s = payload.get("stats") or {}
    return f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>2026-07 MOPS vs Q3 財測 1/3 — charles16888</title>
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    body {{ background: #f7f1e6; color: #15110d; }}
    .wrap {{ max-width: 1180px; margin: 0 auto; padding: 28px 18px 60px; }}
    .badge-local {{ display:inline-block; padding:4px 10px; border-radius:999px; background:#8a650b; color:#fffdf8; font-size:12px; font-weight:800; }}
    h1 {{ font-size: clamp(28px, 4vw, 42px); font-weight: 500; margin: 12px 0 8px; }}
    .lead {{ color: #5b5144; line-height: 1.7; max-width: 820px; }}
    .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(126px,1fr)); gap:10px; margin:18px 0; }}
    .card {{ border-top:2px solid rgba(138,101,11,.55); padding-top:8px; }}
    .card span, .card small {{ display:block; color:#5b5144; font-size:12px; font-weight:700; }}
    .card strong {{ display:block; font-size:clamp(22px,2.3vw,30px); margin:2px 0; font-variant-numeric: tabular-nums; }}
    .tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 12px; }}
    .tab {{ border:1.5px solid rgba(138,101,11,.4); background:#fffdf8; color:#15110d; min-height:36px; padding:7px 12px; border-radius:6px; font:inherit; font-size:13px; font-weight:800; cursor:pointer; }}
    .tab[aria-pressed="true"] {{ background:#8a650b; border-color:#8a650b; color:#fffdf8; }}
    .tab[data-status="above"][aria-pressed="true"] {{ background:#0f7a43; border-color:#0f7a43; }}
    .tab[data-status="below"][aria-pressed="true"] {{ background:#b62634; border-color:#b62634; }}
    .table-wrap {{ overflow-x:auto; border:1px solid rgba(115,80,0,.22); border-radius:4px; background:#fffdf8; }}
    table {{ width:100%; border-collapse:collapse; font-size:13px; color:#1a1612; }}
    th, td {{ padding:11px 10px; border-bottom:1px solid rgba(21,17,13,.1); white-space:nowrap; vertical-align:middle; }}
    th {{ position:sticky; top:0; background:#efe4cf; z-index:2; text-align:left; color:#3f2b00 !important; font-size:12px; font-weight:800; letter-spacing:.02em; border-bottom:1.5px solid rgba(115,80,0,.35); }}
    th small {{ display:block; margin-top:2px; font-size:10px; font-weight:700; color:#735000; }}
    td.num, th.num {{ text-align:right; font-variant-numeric: tabular-nums; }}
    tbody tr:nth-child(even) {{ background: rgba(242,234,219,.55); }}
    tbody tr:hover {{ background: rgba(184,138,23,.12); }}
    tbody td {{ font-weight:600; }}
    .muted {{ color:#8a7d6a; }}
    .num-pos {{ color:#0a5c32 !important; font-weight:800; }}
    .num-neg {{ color:#8f1c28 !important; font-weight:800; }}
    .status {{ display:inline-flex; align-items:center; justify-content:center; min-width:5.2em; padding:4px 10px; border-radius:999px; font-size:12px; font-weight:800; border:1px solid transparent; }}
    .status--above {{ background:rgba(15,122,67,.16); color:#0a5c32; border-color:rgba(15,122,67,.35); }}
    .status--below {{ background:rgba(182,38,52,.14); color:#8f1c28; border-color:rgba(182,38,52,.32); }}
    .status--inline {{ background:rgba(138,101,11,.16); color:#6b4f08; border-color:rgba(138,101,11,.38); }}
    .status--incomplete, .status--no_forecast {{ background:rgba(75,64,50,.12); color:#3d3428; border-color:rgba(75,64,50,.28); }}
    .status--suspect {{ background:rgba(180,90,20,.14); color:#8a3d00; border-color:rgba(180,90,20,.4); }}
    .mono {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; font-weight:800; }}
    .search {{ min-width:min(420px,100%); border:1px solid rgba(138,101,11,.42); background:rgba(255,255,255,.7); padding:10px 12px; border-radius:6px; font:inherit; margin: 8px 0 14px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <span class="badge-local">公開追蹤 · 非投資建議</span>
    <h1>2026-07 <em>MOPS</em> vs Q3 財測 1/3</h1>
    <p class="lead" id="note">讀取中...</p>
    <div class="grid" id="stats"></div>
    <div class="tabs" id="tabs" aria-label="狀態篩選">
      <button class="tab" type="button" data-status="all" aria-pressed="true">全部</button>
      <button class="tab" type="button" data-status="below" aria-pressed="false">低於預期</button>
      <button class="tab" type="button" data-status="above" aria-pressed="false">高於預期</button>
      <button class="tab" type="button" data-status="inline" aria-pressed="false">符合預期</button>
      <button class="tab" type="button" data-status="suspect" aria-pressed="false">財測異常</button>
      <button class="tab" type="button" data-status="incomplete" aria-pressed="false">尚未公告7月</button>
      <button class="tab" type="button" data-status="no_forecast" aria-pressed="false">無營收財測</button>
    </div>
    <input class="search" id="search" type="search" placeholder="輸入代號或公司，多筆用空白分隔" />
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>狀態</th>
            <th>代號</th>
            <th>公司</th>
            <th class="num">Q3財測<br><small>億元</small></th>
            <th class="num">1/3門檻<br><small>億元</small></th>
            <th class="num">7月實際<br><small>億元</small></th>
            <th class="num">差異<br><small>億元</small></th>
            <th class="num">差異%</th>
            <th class="num">樣本</th>
            <th>信心</th>
          </tr>
        </thead>
        <tbody id="body"><tr><td colspan="10" class="muted">讀取中...</td></tr></tbody>
      </table>
    </div>
  </div>
  <script>
    const DATA_URL = '../assets/q3_july_revenue_compare.json';
    const state = {{ payload: null, status: 'all', q: '' }};
    const labels = {{
      all: '全部', above: '高於預期', below: '低於預期', inline: '符合預期',
      suspect: '財測異常', incomplete: '尚未公告7月', no_forecast: '無營收財測',
    }};
    const numberFmt = new Intl.NumberFormat('zh-TW', {{ maximumFractionDigits: 1 }});
    const intFmt = new Intl.NumberFormat('zh-TW', {{ maximumFractionDigits: 0 }});
    const dash = '<span class="muted">—</span>';
    const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (ch) => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[ch]));
    const isNum = (v) => v !== null && v !== undefined && v !== '' && Number.isFinite(Number(v));
    const tone = (v) => (!isNum(v) || Number(v) === 0) ? '' : (Number(v) > 0 ? 'num-pos' : 'num-neg');
    const fmtYi = (v) => isNum(v) ? numberFmt.format(Number(v) / 100) : dash;
    const fmtSignedYi = (v) => {{
      if (!isNum(v)) return dash;
      const n = Number(v) / 100;
      return `<span class="${{tone(v)}}">${{n > 0 ? '+' : ''}}${{numberFmt.format(n)}}</span>`;
    }};
    const fmtPct = (v) => {{
      if (!isNum(v)) return dash;
      const n = Number(v);
      return `<span class="${{tone(v)}}">${{n > 0 ? '+' : ''}}${{numberFmt.format(n)}}%</span>`;
    }};

    function renderStats(p) {{
      const s = p.stats || {{}};
      const cards = [
        ['低於預期', s.below_count, '7月 < Q3財測/3'],
        ['高於預期', s.above_count, '7月 > Q3財測/3'],
        ['符合預期', s.inline_count, `門檻 ±${{p.surprise_threshold_pct ?? 3}}%`],
        ['財測異常', s.suspect_count, '單位/OCR 可疑'],
        ['尚未公告7月', s.incomplete_count, '有財測、缺7月'],
        ['7月已公告', s.july_announced_count, p.latest_mops_period || '—'],
      ];
      document.getElementById('stats').innerHTML = cards.map(([a,b,c]) => `
        <div class="card"><span>${{esc(a)}}</span><strong>${{esc(b ?? 0)}}</strong><small>${{esc(c || '')}}</small></div>
      `).join('');
    }}

    function filteredRows() {{
      const rows = state.payload.rows || [];
      const tokens = state.q.trim().toLowerCase().split(/[\\s,，、]+/).filter(Boolean);
      return rows.filter((r) => {{
        if (state.status !== 'all' && r.status !== state.status) return false;
        if (!tokens.length) return true;
        const hay = `${{r.code}} ${{r.name}}`.toLowerCase();
        return tokens.some((t) => hay.includes(t));
      }});
    }}

    function updateTabs() {{
      const rows = state.payload.rows || [];
      const counts = {{ all: rows.length }};
      for (const k of Object.keys(labels)) {{
        if (k === 'all') continue;
        counts[k] = rows.filter((r) => r.status === k).length;
      }}
      for (const btn of document.querySelectorAll('#tabs [data-status]')) {{
        const st = btn.dataset.status;
        btn.textContent = `${{labels[st] || st}} ${{counts[st] ?? 0}}`;
        btn.setAttribute('aria-pressed', st === state.status ? 'true' : 'false');
      }}
    }}

    function renderRows() {{
      const body = document.getElementById('body');
      const rows = filteredRows();
      updateTabs();
      if (!rows.length) {{
        body.innerHTML = '<tr><td colspan="10" class="muted">沒有符合條件的公司。</td></tr>';
        return;
      }}
      body.innerHTML = rows.map((r) => `
        <tr>
          <td><span class="status status--${{esc(r.status)}}">${{esc(r.status_label || labels[r.status] || r.status)}}</span></td>
          <td class="mono">${{esc(r.code)}}</td>
          <td>${{esc(r.name)}}</td>
          <td class="num">${{fmtYi(r.forecast_revenue_m)}}</td>
          <td class="num">${{fmtYi(r.monthly_target_m)}}</td>
          <td class="num">${{fmtYi(r.july_revenue_m)}}</td>
          <td class="num">${{fmtSignedYi(r.surprise_m)}}</td>
          <td class="num">${{fmtPct(r.surprise_pct)}}</td>
          <td class="num">${{isNum(r.sample_count) ? intFmt.format(r.sample_count) : dash}}</td>
          <td>${{esc(r.confidence || '—')}}</td>
        </tr>
      `).join('');
    }}

    function render(p) {{
      state.payload = p;
      const s = p.stats || {{}};
      document.getElementById('note').textContent =
        `${{p.quarter || '2026Q3'}} · 追蹤月 ${{p.track_period}} · 標準：7月營收 vs Q3財測/3 · 門檻 ±${{p.surprise_threshold_pct ?? 3}}% · `
        + `可比對 ${{intFmt.format(s.complete_count || 0)}} 檔 · 7月已公告 ${{intFmt.format(s.july_announced_count || 0)}} 檔。僅做追蹤/統計，不做投資建議。`;
      renderStats(p);
      renderRows();
    }}

    document.getElementById('tabs').addEventListener('click', (e) => {{
      const btn = e.target.closest('[data-status]');
      if (!btn) return;
      state.status = btn.dataset.status || 'all';
      renderRows();
    }});
    document.getElementById('search').addEventListener('input', (e) => {{
      state.q = e.target.value || '';
      renderRows();
    }});

    // Prefer embedded payload if present; else fetch JSON.
    if (window.__Q3_JULY_PAYLOAD__) {{
      render(window.__Q3_JULY_PAYLOAD__);
    }} else {{
      fetch(`${{DATA_URL}}?v=${{Date.now()}}`, {{ cache: 'no-store' }})
        .then((r) => {{ if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }})
        .then(render)
        .catch((err) => {{
          document.getElementById('note').textContent = '資料讀取失敗：' + err.message;
          document.getElementById('body').innerHTML = '<tr><td colspan="10" class="muted">請先執行 build_q3_july_revenue_compare.py</td></tr>';
        }});
    }}
  </script>
</body>
</html>
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Build July MOPS vs Q3/3 compare (local only).")
    parser.add_argument("--forecast-report", default=str(FORECAST_REPORT))
    parser.add_argument("--revenue-db", default=str(REVENUE_DB))
    parser.add_argument("--out", default=str(DATA_OUT))
    parser.add_argument("--page-out", default=str(PAGE_OUT))
    args = parser.parse_args()

    report_path = Path(args.forecast_report)
    if not report_path.exists():
        print(f"[ERR] forecast report missing: {report_path}", file=sys.stderr)
        print("[HINT] first run build_q3_forecast_public_pages.py --write-charles", file=sys.stderr)
        return 1

    forecast = load_forecast_table(report_path)
    revenue, latest = load_july_revenue(Path(args.revenue_db))
    payload = build_comparison(forecast, revenue, latest_mops_period=latest)

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    page_path = Path(args.page_out)
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_html = render_local_page(payload)
    # Embed payload BEFORE the render script so file:// open works without fetch.
    embed = (
        "<script>window.__Q3_JULY_PAYLOAD__ = "
        + json.dumps(payload, ensure_ascii=False)
        + ";</script>\n"
    )
    render_marker = "  <script>\n    const DATA_URL = '../assets/q3_july_revenue_compare.json';"
    if render_marker in page_html:
        page_html = page_html.replace(render_marker, embed + render_marker, 1)
    else:
        page_html = page_html.replace("</body>", embed + "</body>", 1)
    page_path.write_text(page_html, encoding="utf-8")

    print(f"[OK] {out_path}")
    print(f"[OK] {page_path}")
    print(
        "[INFO] "
        f"forecast={payload['stats']['revenue_forecast_count']} "
        f"july={payload['stats']['july_announced_count']} "
        f"above={payload['stats']['above_count']} "
        f"below={payload['stats']['below_count']} "
        f"inline={payload['stats']['inline_count']} "
        f"incomplete={payload['stats']['incomplete_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
