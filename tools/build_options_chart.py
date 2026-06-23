"""
build_options_chart.py  (v2 — full history + range buttons)
============================================================
Reads industry_map's market_pulse_daily (now backfilled by
tools/backfill_options_history.py) and plots TXO ATM Call+Put 價平和
for the two nearest expiries against the TAIEX cash index.

Outputs:
    assets/options_atm.json   — full daily series
    reports/options-atm.html  — ECharts page with 1M/6M/1Y/2Y/3Y/5Y/ALL buttons
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB = Path(r"E:\industry_map\sector_daily.db")
LYNUS_ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = LYNUS_ROOT / "assets" / "options_atm.json"
PAGE_OUT = LYNUS_ROOT / "reports" / "options-atm.html"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"
TPE = ZoneInfo("Asia/Taipei")


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>選擇權價平和 — charles16888</title>
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
          <a class="nav__link" href="../category.html?cat=taiex">大盤</a>
          <a class="nav__link is-active" href="../category.html?cat=txo">選擇權</a>
          <a class="nav__link nav__link--disabled" href="#" aria-disabled="true">籌碼</a>
          <a class="nav__link" href="../category.html?cat=stocks">個股</a>
          <a class="nav__link" href="../category.html?cat=news">新聞</a>
        </nav>
      </div>
    </div>
  </header>

  <main class="container">

    <nav class="breadcrumb" aria-label="Breadcrumb">
      <a href="../index.html">charles16888</a>
      <span class="breadcrumb__sep">/</span>
      <a href="../category.html?cat=txo">選擇權</a>
      <span class="breadcrumb__sep">/</span>
      <span>選擇權價平和</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>選擇權</strong> · 價平波動代理</span>
        <span>__DATE_RANGE__</span>
        <span>__DAYS__ 個交易日</span>
      </div>
      <h1 class="report-title">選擇權<em>價平和</em></h1>
      <p class="report-lead">TXO 近月 + 次遠月 ATM Call + Put 加總(隱含波動代理)。資料源:期交所選擇權每日簡表 + TWSE 加權指數。</p>

      <div class="stat-row stat-row--3">
        <div class="stat">
          <div class="stat__label">最新近期 C+P</div>
          <div class="stat__value stat__value--sm num-neutral">__LATEST_NEAR__</div>
        </div>
        <div class="stat">
          <div class="stat__label">最新次遠期</div>
          <div class="stat__value stat__value--sm num-neutral">__LATEST_FAR__</div>
        </div>
        <div class="stat">
          <div class="stat__label">近期到期</div>
          <div class="stat__value stat__value--sm num-neutral">__LATEST_EXPIRY__</div>
        </div>
      </div>
    </section>

    <section class="market-charts">
      <h2 class="market-chart-title">ATM 價平和 × 加權指數</h2>
      <div class="range-tabs" id="range-tabs">
        <button class="range-tab" data-range="1M" type="button">1M</button>
        <button class="range-tab" data-range="6M" type="button">6M</button>
        <button class="range-tab is-active" data-range="1Y" type="button">1Y</button>
        <button class="range-tab" data-range="2Y" type="button">2Y</button>
        <button class="range-tab" data-range="3Y" type="button">3Y</button>
        <button class="range-tab" data-range="ALL" type="button">ALL</button>
      </div>
      <div id="chart-opt" class="market-chart"></div>
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

  const chart = echarts.init(document.getElementById('chart-opt'), null, { renderer: 'svg' });
  const dates = DATA.map(d => d.date);

  chart.setOption({
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
          let val = (typeof v === 'number') ? v.toFixed(0) : v;
          if (p.seriesName === '加權指數' && typeof v === 'number') {
            val = v.toLocaleString('en-US', { maximumFractionDigits: 0 });
          } else if (typeof v === 'number') {
            val = v.toFixed(0) + ' 點';
          }
          html += '<div style="margin-top:4px;color:' + ink + '"><span style="color:' + p.color + ';font-weight:900">●</span> '
               + p.seriesName + ': <b>' + val + '</b></div>';
        }
        return html;
      },
    },
    legend: {
      data: ['近期 C+P', '次遠期 C+P', '加權指數'],
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
        type: 'value', name: '價平和 (點)',
        nameTextStyle: { color: inkDim, fontSize: 11, fontWeight: 700 },
        axisLine: { lineStyle: { color: rule } },
        axisLabel: { color: inkDim, fontSize: 11, fontWeight: 700 },
        splitLine: { lineStyle: { color: rule, type: 'dashed', width: 1.1 } },
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
        name: '近期 C+P',
        type: 'line',
        showSymbol: false,
        sampling: 'lttb',
        lineStyle: { width: 2.2, color: up },
        areaStyle: {
          color: {
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: redAreaTop },
              { offset: 1, color: redAreaBottom },
            ],
          },
        },
        data: DATA.map(d => d.opt_near_cp_sum),
      },
      {
        name: '次遠期 C+P',
        type: 'line',
        showSymbol: false,
        sampling: 'lttb',
        lineStyle: { width: 2.0, color: goldSft, type: 'dashed' },
        data: DATA.map(d => d.opt_far_cp_sum),
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
  });

  window.addEventListener('resize', () => chart.resize());

  // ---- Range buttons ----
  const RANGE_DAYS = {
    '1M': 21, '6M': 126, '1Y': 252,
    '2Y': 504, '3Y': 756,
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
    if not DB.exists():
        print(f"[ERR] {DB} not found")
        return 1

    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5)
    rows = conn.execute("""
        SELECT date, twse_close,
               opt_near_expiry, opt_near_strike, opt_near_cp_sum,
               opt_far_expiry,  opt_far_strike,  opt_far_cp_sum
        FROM market_pulse_daily
        WHERE opt_near_cp_sum IS NOT NULL
        ORDER BY date ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("[ERR] no rows with opt_near_cp_sum")
        return 2

    series = []
    for r in rows:
        series.append({
            "date":            r[0],
            "twse_close":      r[1],
            "opt_near_expiry": r[2],
            "opt_near_strike": r[3],
            "opt_near_cp_sum": r[4],
            "opt_far_expiry":  r[5],
            "opt_far_strike":  r[6],
            "opt_far_cp_sum":  r[7],
        })

    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)} — {len(series)} rows")

    latest = series[-1]
    ctx = {
        "DATA_JSON":     json.dumps(series, ensure_ascii=False),
        "DATE_RANGE":    f"{series[0]['date']} → {series[-1]['date']}",
        "DAYS":          len(series),
        "LATEST_NEAR":   f"{latest['opt_near_cp_sum']:.0f} 點" if latest['opt_near_cp_sum'] else "—",
        "LATEST_FAR":    f"{latest['opt_far_cp_sum']:.0f} 點" if latest['opt_far_cp_sum'] else "—",
        "LATEST_EXPIRY": latest['opt_near_expiry'] or "—",
    }
    rendered = render_template(PAGE_TEMPLATE, ctx)
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(rendered, encoding="utf-8")
    print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)} — chart page written")

    # Manifest
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entry = {
        "id":              "options-atm",
        "category":        "txo",
        "type":            "pulse",
        "date":            latest["date"],
        "time":            "20:00",
        "title":           f"選擇權價平和 · 隱含波動代理",
        "title_em":        "價平和",
        "summary":         (
            f"近 {len(series)} 個交易日（{series[0]['date']} → {series[-1]['date']}）"
            f"TXO ATM Call+Put 加總。最新 ({latest['date']})："
            f"近期 {latest['opt_near_cp_sum']:.0f} 點（到期 {latest['opt_near_expiry']}）、"
            f"次遠期 {latest['opt_far_cp_sum']:.0f} 點。"
        ),
        "tags":            ["選擇權", "TXO", "價平和", "隱波"],
        "source_pipeline": "taifex + twse",
        "url":             "reports/options-atm.html",
        "stats": [
            {"label": "近期 C+P",  "value": f"{latest['opt_near_cp_sum']:.0f}", "color": "neutral"},
            {"label": "次遠期 C+P","value": f"{latest['opt_far_cp_sum']:.0f}" if latest['opt_far_cp_sum'] else "—", "color": "neutral"},
            {"label": "區間",       "value": f"{len(series)} 日", "color": "neutral"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != "options-atm"]
    manifest["entries"].append(entry)
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
    print(f"[OK] manifest.json — options-atm entry refreshed")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
