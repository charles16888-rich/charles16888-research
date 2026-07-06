"""
build_futures_chart.py  (v2 — 12-year history + range buttons)
==============================================================
Build the futures basis chart from:
    1. E:\\Lynus\\assets\\_raw\\tx_daily_yuanta.csv   (Yuanta TXF1 daily, big5)
    2. yfinance ^TWII                                  (TAIEX cash close)

Outputs:
    - E:\\Lynus\\assets\\futures_basis.json   ~3000-row daily series
    - E:\\Lynus\\reports\\futures-basis.html  ECharts page with 1M/6M/1Y/2Y/3Y/5Y/ALL buttons
    - manifest.json entry refreshed
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import yfinance as yf
except ImportError:
    print("[ERR] yfinance not installed. pip install yfinance", file=sys.stderr)
    sys.exit(2)

LYNUS_ROOT = Path(__file__).resolve().parent.parent
YF_CACHE_DIR = Path(tempfile.gettempdir()) / "charles1688_yfinance_cache"
YF_CACHE_DIR.mkdir(parents=True, exist_ok=True)
yf.set_tz_cache_location(str(YF_CACHE_DIR))

SOURCE_CSV = LYNUS_ROOT / "assets" / "_raw" / "tx_daily_yuanta.csv"
DATA_OUT = LYNUS_ROOT / "assets" / "futures_basis.json"
PAGE_OUT = LYNUS_ROOT / "reports" / "futures-basis.html"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"

# Daily increment beyond CSV end date — sourced from industry_map's
# market_pulse_daily table (same data line Telegram pulse uses).
INDUSTRY_DB = Path(r"E:\industry_map\sector_daily.db")

TPE = ZoneInfo("Asia/Taipei")


def read_yuanta_csv(path: Path) -> dict[str, float]:
    """Yuanta TXF1 daily CSV (big5). Return {date_str: close}."""
    out: dict[str, float] = {}
    with path.open("r", encoding="big5", errors="ignore") as f:
        reader = csv.reader(f)
        next(reader, None)
        for row in reader:
            if not row or not row[0].strip():
                continue
            try:
                d = datetime.strptime(row[0].strip(), "%Y/%m/%d").strftime("%Y-%m-%d")
                close = float(row[4])
                if close > 0:
                    out[d] = close
            except (ValueError, IndexError):
                continue
    return out


def read_market_pulse_db(after_date: str) -> list[dict]:
    """Daily increment from industry_map's market_pulse_daily — same source as
    the Telegram pulse. Returns rows strictly AFTER `after_date` (so CSV-era
    history stays authoritative; only the new tail comes from the DB)."""
    if not INDUSTRY_DB.exists():
        print(f"[WARN] {INDUSTRY_DB} not found — skipping daily increment")
        return []
    try:
        conn = sqlite3.connect(f"file:{INDUSTRY_DB}?mode=ro", uri=True, timeout=5)
        rows = conn.execute(
            """
            SELECT date, twse_close, fut_close, basis
            FROM market_pulse_daily
            WHERE date > ?
              AND fut_close IS NOT NULL
            ORDER BY date ASC
            """,
            (after_date,),
        ).fetchall()
        conn.close()
    except sqlite3.Error as e:
        print(f"[WARN] market_pulse_daily query failed: {e}")
        return []

    twii_fallback: dict[str, float] = {}
    missing_cash_dates = [r[0] for r in rows if r[1] is None and r[2] is not None]
    if missing_cash_dates:
        from datetime import timedelta
        start = min(missing_cash_dates)
        end_dt = datetime.strptime(max(missing_cash_dates), "%Y-%m-%d") + timedelta(days=1)
        twii_fallback = fetch_twii(start, end_dt.strftime("%Y-%m-%d"))
        print(f"[INFO] ^TWII fallback for DB increment: {len(twii_fallback)} rows")

    out = []
    for r in rows:
        twse_close = r[1] if r[1] is not None else twii_fallback.get(r[0])
        if twse_close is None:
            print(f"[WARN] skip {r[0]}: missing TWSE close for basis")
            continue
        fut_close = float(r[2])
        basis = float(r[3]) if r[3] is not None else fut_close - float(twse_close)
        out.append({
            "date":        r[0],
            "twse_close":  round(float(twse_close), 2),
            "fut_close":   round(fut_close, 2),
            "basis":       round(basis, 2),
        })
    return out


def fetch_twii(start_date: str, end_date: str) -> dict[str, float]:
    """yfinance ^TWII close. {date_str: close}."""
    print(f"[INFO] Fetching ^TWII {start_date} -> {end_date} ...")
    df = yf.Ticker("^TWII").history(start=start_date, end=end_date)
    out: dict[str, float] = {}
    for idx, row in df.iterrows():
        d = idx.strftime("%Y-%m-%d")
        try:
            out[d] = float(row["Close"])
        except (ValueError, TypeError):
            continue
    print(f"[INFO] yfinance returned {len(out)} rows")
    return out


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>期貨正逆價差 12 年 — charles16888</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
  <style>
    .range-tabs {
      display: flex; gap: 6px; margin: 18px 0 10px; flex-wrap: wrap;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px; letter-spacing: .15em;
    }
    .range-tab {
      padding: 7px 16px; border: 1px solid var(--rule-strong);
      background: var(--panel); color: var(--ink-dim);
      cursor: pointer; transition: all .15s; font-family: inherit;
      font-size: inherit; letter-spacing: inherit; font-weight: 700;
    }
    .range-tab:hover { color: var(--ink); border-color: var(--rule-gold-strong); background: var(--panel-strong); }
    .range-tab.is-active {
      background: var(--gold); color: var(--bg);
      border-color: var(--gold); font-weight: 800;
    }
    .market-chart { min-height: 520px; }
    .market-chart-title { margin-top: 28px; }
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
          <a class="nav__link is-active" href="../category.html?cat=taiex">大盤</a>
          <a class="nav__link" href="../category.html?cat=calendar">行事曆</a>
          <a class="nav__link" href="../category.html?cat=txo">選擇權</a>
          <a class="nav__link" href="../category.html?cat=chips">籌碼</a>
          <a class="nav__link" href="../category.html?cat=stocks">個股</a>
          <a class="nav__link" href="../category.html?cat=research">研報統計</a>
        </nav>
      </div>
    </div>
  </header>

  <main class="container">

    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="../index.html">charles16888</a>
      <span class="breadcrumb__sep">/</span>
      <a href="../category.html?cat=taiex">大盤</a>
      <span class="breadcrumb__sep">/</span>
      <span>期貨正逆價差 · 12 年</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>大盤</strong> · 期現對照</span>
        <span>__DATE_RANGE__</span>
        <span>__DAYS__ 個交易日</span>
      </div>
      <h1 class="report-title">期貨正逆<em>價差</em> · 12 年</h1>
      <p class="report-lead">台指期 TXF1 vs 加權現貨。正值 = 期貨看多現貨；負值 = 期貨看空現貨。資料源：元大期貨（TXF1 日線）+ Yahoo Finance（^TWII）。</p>
      <div class="stat-row stat-row--3">
        <div class="stat">
          <div class="stat__label">最新基差</div>
          <div class="stat__value stat__value--sm __LATEST_COLOR__">__LATEST_SIGN____LATEST_BASIS__ 點</div>
        </div>
        <div class="stat">
          <div class="stat__label">最新加權</div>
          <div class="stat__value stat__value--sm num-neutral">__LATEST_TWSE__</div>
        </div>
        <div class="stat">
          <div class="stat__label">12 年區間</div>
          <div class="stat__value stat__value--sm num-neutral">__DATE_RANGE__</div>
        </div>
      </div>
    </section>

    <section class="market-charts">
      <h2 class="market-chart-title">期貨 - 現貨 價差 × 加權指數</h2>
      <div class="range-tabs" id="range-tabs">
        <button class="range-tab" data-range="1M" type="button">1M</button>
        <button class="range-tab" data-range="6M" type="button">6M</button>
        <button class="range-tab is-active" data-range="1Y" type="button">1Y</button>
        <button class="range-tab" data-range="2Y" type="button">2Y</button>
        <button class="range-tab" data-range="3Y" type="button">3Y</button>
        <button class="range-tab" data-range="5Y" type="button">5Y</button>
        <button class="range-tab" data-range="ALL" type="button">ALL · 12Y</button>
      </div>
      <div id="chart-basis" class="market-chart"></div>
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

  const rootStyle = getComputedStyle(document.documentElement);
  const bodyStyle = getComputedStyle(document.body);
  const cssColor = (name, fallback) => rootStyle.getPropertyValue(name).trim() || fallback;
  function luminance(color) {
    const nums = (color || '').match(/[0-9.]+/g);
    if (!nums || nums.length < 3) return 1;
    const [r, g, b] = nums.slice(0, 3).map(Number).map(v => v / 255);
    const lin = c => c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
  }
  const pageIsDark = luminance(bodyStyle.backgroundColor) < 0.45;
  const ink     = cssColor('--ink', pageIsDark ? '#fff6e8' : '#15110d');
  const inkDim  = cssColor('--ink-dim', pageIsDark ? '#d1bea0' : '#4b4032');
  const gold    = cssColor('--gold', pageIsDark ? '#ffd15f' : '#735000');
  const goldSft = cssColor('--gold-soft', pageIsDark ? '#f0c273' : '#865f08');
  const up      = cssColor('--up', pageIsDark ? '#ff7a80' : '#d03845');
  const down    = cssColor('--down', pageIsDark ? '#77e39b' : '#0f7a43');
  const rule    = cssColor('--rule', pageIsDark ? 'rgba(255,246,232,0.28)' : 'rgba(21,17,13,0.28)');
  const ruleGold= cssColor('--rule-gold-strong', pageIsDark ? 'rgba(240,200,90,0.62)' : 'rgba(115,80,0,0.72)');
  const bg      = cssColor('--label-bg', pageIsDark ? 'rgba(33,28,23,0.96)' : 'rgba(251,247,239,0.96)');
  const redAreaTop = pageIsDark ? 'rgba(255,122,128,0.30)' : 'rgba(208,56,69,0.20)';
  const redAreaBottom = pageIsDark ? 'rgba(255,122,128,0.02)' : 'rgba(208,56,69,0.00)';
  const zoomFill = pageIsDark ? 'rgba(255,209,95,0.18)' : 'rgba(115,80,0,0.18)';
  const zoomBg = pageIsDark ? 'rgba(255,209,95,0.10)' : 'rgba(115,80,0,0.10)';

  const chart = echarts.init(document.getElementById('chart-basis'), null, { renderer: 'svg' });

  const dates = DATA.map(d => d.date);
  const baseOption = {
    backgroundColor: 'transparent',
    grid: { left: 70, right: 70, top: 50, bottom: 70 },
    tooltip: {
      trigger: 'axis',
      backgroundColor: bg,
      borderColor: ruleGold,
      borderWidth: 1,
      textStyle: { color: ink, fontFamily: 'Inter', fontSize: 13, fontWeight: 600 },
      axisPointer: { type: 'cross', lineStyle: { color: goldSft, width: 1.4 } },
      formatter: function(params) {
        if (!params || !params.length) return '';
        const d = params[0].axisValue;
        let html = '<div style="font-family:JetBrains Mono;font-size:11px;color:' + inkDim + '">' + d + '</div>';
        for (const p of params) {
          const v = p.value;
          let label = p.seriesName;
          let val = (typeof v === 'number') ? v.toFixed(2) : v;
          if (label === '基差' && typeof v === 'number') {
            val = (v >= 0 ? '+' : '') + v.toFixed(1) + ' 點';
          } else if (label === '加權指數' && typeof v === 'number') {
            val = v.toLocaleString('en-US', { maximumFractionDigits: 0 });
          }
          html += '<div style="margin-top:4px;color:' + ink + '"><span style="color:' + p.color + ';font-weight:900">●</span> '
               + label + ': <b>' + val + '</b></div>';
        }
        return html;
      },
    },
    legend: {
      data: ['基差', '加權指數'],
      textStyle: { color: ink, fontSize: 13, fontWeight: 700 },
      top: 8,
      itemGap: 28,
    },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: inkDim, fontSize: 11, fontWeight: 700, fontFamily: 'JetBrains Mono' },
      axisTick: { show: false },
    },
    yAxis: [
      {
        type: 'value', name: '基差 (點)',
        nameTextStyle: { color: inkDim, fontSize: 11, fontWeight: 700 },
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: inkDim, fontSize: 11, fontWeight: 700 },
        splitLine: { lineStyle: { color: rule, type: 'dashed', width: 1.1 } },
        markLine: { silent: true, lineStyle: { color: ruleGold, width: 1.6 }, data: [{ yAxis: 0 }] },
      },
      {
        type: 'value', name: '加權',
        nameTextStyle: { color: inkDim, fontSize: 11, fontWeight: 700 },
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: inkDim, fontSize: 11, fontWeight: 700, formatter: v => (v/1000).toFixed(0) + 'k' },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: 'inside', startValue: 0, endValue: dates.length - 1 },
      {
        type: 'slider', height: 22, bottom: 12,
        borderColor: 'transparent', backgroundColor: 'transparent',
        fillerColor: zoomFill,
        handleStyle: { color: gold }, moveHandleStyle: { color: gold },
        textStyle: { color: inkDim, fontFamily: 'JetBrains Mono', fontSize: 10, fontWeight: 700 },
        dataBackground: {
          lineStyle: { color: goldSft },
          areaStyle: { color: zoomBg },
        },
      },
    ],
    series: [
      {
        name: '基差',
        type: 'line',
        showSymbol: false,
        sampling: 'lttb',
        lineStyle: { width: 2.1, color: up },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: redAreaTop },
              { offset: 1, color: redAreaBottom },
            ],
          },
        },
        data: DATA.map(d => d.basis),
      },
      {
        name: '加權指數',
        type: 'line',
        yAxisIndex: 1,
        showSymbol: false,
        sampling: 'lttb',
        lineStyle: { color: gold, width: 2.1 },
        data: DATA.map(d => d.twse_close),
      },
    ],
  };
  chart.setOption(baseOption);
  window.addEventListener('resize', () => chart.resize());

  // ---- Range buttons ----
  const RANGE_DAYS = {
    '1M': 21, '6M': 126, '1Y': 252,
    '2Y': 504, '3Y': 756, '5Y': 1260,
    'ALL': dates.length,
  };
  function applyRange(range) {
    const days = Math.min(RANGE_DAYS[range] || dates.length, dates.length);
    const endIdx = dates.length - 1;
    const startIdx = Math.max(0, endIdx - days + 1);
    chart.dispatchAction({
      type: 'dataZoom',
      dataZoomIndex: [0, 1],
      startValue: dates[startIdx],
      endValue: dates[endIdx],
    });
  }
  document.querySelectorAll('.range-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.range-tab').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      applyRange(btn.dataset.range);
    });
  });
  // Default: 1Y
  applyRange('1Y');
  </script>
</body>
</html>
"""


def render_template(tpl: str, ctx: dict) -> str:
    out = tpl
    for k, v in ctx.items():
        out = out.replace(f"__{k}__", str(v))
    return out


def main() -> int:
    if not SOURCE_CSV.exists():
        print(f"[ERR] {SOURCE_CSV} not found")
        return 1

    print("[INFO] Reading Yuanta TXF1 CSV ...")
    tx = read_yuanta_csv(SOURCE_CSV)
    print(f"[INFO] TXF1: {len(tx)} rows, {min(tx)} -> {max(tx)}")

    start = min(tx)
    end_dt = datetime.strptime(max(tx), "%Y-%m-%d")
    # yfinance end is exclusive — bump 1 day
    end = (end_dt.replace(day=end_dt.day) ).strftime("%Y-%m-%d")
    # Actually yfinance end is exclusive — pass end +1 to include the last day
    from datetime import timedelta
    end_plus = (end_dt + timedelta(days=1)).strftime("%Y-%m-%d")
    twii = fetch_twii(start, end_plus)
    print(f"[INFO] ^TWII: {len(twii)} rows")

    # Align by date — only keep dates where both have data
    common = sorted(set(tx) & set(twii))
    print(f"[INFO] Common trading days: {len(common)}")

    series = []
    for d in common:
        t = twii[d]
        f = tx[d]
        series.append({
            "date": d,
            "twse_close": round(t, 2),
            "fut_close": round(f, 2),
            "basis": round(f - t, 2),
        })

    # Daily increment: append rows strictly after the CSV's last date.
    csv_end = series[-1]["date"] if series else "0000-00-00"
    increment = read_market_pulse_db(csv_end)
    if increment:
        print(f"[INFO] DB increment: +{len(increment)} rows after {csv_end} "
              f"({increment[0]['date']} -> {increment[-1]['date']})")
        series.extend(increment)
    else:
        print(f"[INFO] DB increment: 0 rows (CSV already current to {csv_end})")

    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)} — {len(series)} rows")

    # Render page
    latest = series[-1]
    latest_basis = latest["basis"]
    ctx = {
        "DATA_JSON":     json.dumps(series, ensure_ascii=False),
        "DATE_RANGE":    f"{series[0]['date']} → {series[-1]['date']}",
        "DAYS":          len(series),
        "LATEST_BASIS":  f"{latest_basis:.1f}",
        "LATEST_SIGN":   "+" if latest_basis >= 0 else "",
        "LATEST_COLOR":  "num-up" if latest_basis >= 0 else "num-down",
        "LATEST_TWSE":   f"{latest['twse_close']:,.0f}",
    }
    rendered = render_template(PAGE_TEMPLATE, ctx)
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(rendered, encoding="utf-8")
    print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)} — chart page written")

    # Manifest
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = {
        "id":              "futures-basis",
        "category":        "taiex",
        "type":            "pulse",
        "date":            latest["date"],
        "time":            "20:00",
        "title":           "期貨正逆價差 · 12 年期現對照",
        "title_em":        "12 年",
        "summary":         (
            f"近 {len(series)} 個交易日（{series[0]['date']} → {series[-1]['date']}）"
            f"台指期 TXF1 vs 加權指數價差。最新 ({latest['date']})："
            f"基差 {'+' if latest_basis >= 0 else ''}{latest_basis:.1f} 點，"
            f"加權收 {latest['twse_close']:,.0f}。"
        ),
        "tags":            ["期貨", "基差", "正逆價差", "TX", "12 年"],
        "source_pipeline": "yuanta + yfinance",
        "url":             "reports/futures-basis.html",
        "stats": [
            {"label": "最新基差", "value": f"{'+' if latest_basis >= 0 else ''}{latest_basis:.1f}",
             "color": "up" if latest_basis >= 0 else "down"},
            {"label": "加權", "value": f"{latest['twse_close']:,.0f}", "color": "neutral"},
            {"label": "區間", "value": f"{len(series)} 日", "color": "neutral"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != "futures-basis"]
    manifest["entries"].append(entry)
    manifest["entries"].sort(key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")), reverse=True)
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[OK] manifest.json — futures-basis entry refreshed")

    return 0


if __name__ == "__main__":
    sys.exit(main())
