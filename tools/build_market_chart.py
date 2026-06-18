"""
build_market_chart.py
=====================
Extract daily market pulse from industry_map daily_*.md reports and emit:
    1) assets/market_pulse.json   — daily series
    2) reports/market-pulse.html  — standalone ECharts page

Series extracted per trading day:
    - limit_up_twse, limit_up_tpex     (漲停家數)
    - limit_down_twse, limit_down_tpex (跌停家數)
    - turnover_billion                 (總成交額，億)
    - sectors_up                       (漲多族群數)
    - sectors_avg_pct                  (全族群平均漲幅，% × 100)
    - up_concentration_pct             (漲停集中度，%)

Usage:
    python tools/build_market_chart.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

INDUSTRY_MAP_REPORTS = Path(r"E:\industry_map\reports")
LYNUS_ROOT = Path(__file__).resolve().parent.parent
DATA_OUT  = LYNUS_ROOT / "assets" / "market_pulse.json"
PAGE_OUT  = LYNUS_ROOT / "reports" / "market-pulse.html"

TPE = ZoneInfo("Asia/Taipei")


# ── md parsing ─────────────────────────────────────────────────────────────

# Pattern: "| TPEX | 866 | 291 | 512 | 63 | 34 | 2 | 3804.6 億 |"
_MARKET_ROW = re.compile(
    r"\|\s*(TPEX|TWSE)\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*\d+\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*([\d,\.]+)\s*億",
    re.IGNORECASE,
)
_CONC_UP    = re.compile(r"漲停集中度.*?=\s*\*\*([\d\.]+)%", re.DOTALL)
_TOTAL_VOL  = re.compile(r"總成交額\s*\*\*([\d,\.]+)\s*億")
_AVG_UP_PCT = re.compile(r"全族群平均漲幅：\*\*([+\-\d\.]+)%")
_SECT_UP    = re.compile(r"漲多族群：\*\*(\d+)\s*/\s*(\d+)")


def parse_daily(md_text: str) -> dict | None:
    """Return dict of metrics or None if the file isn't parseable."""
    out = {
        "limit_up_twse": 0, "limit_up_tpex": 0,
        "limit_down_twse": 0, "limit_down_tpex": 0,
        "turnover_billion": 0.0,
        "up_concentration_pct": 0.0,
        "sectors_avg_pct": 0.0,
        "sectors_up": 0,
        "sectors_total": 0,
    }

    matches = _MARKET_ROW.findall(md_text)
    if len(matches) < 2:
        return None  # 沒抓到完整 TWSE+TPEX 兩列

    for market, lup, ldn, _vol in matches:
        m = market.lower()
        out[f"limit_up_{m}"]   = int(lup)
        out[f"limit_down_{m}"] = int(ldn)

    m = _TOTAL_VOL.search(md_text)
    if m: out["turnover_billion"] = float(m.group(1).replace(",", ""))

    m = _CONC_UP.search(md_text)
    if m: out["up_concentration_pct"] = float(m.group(1))

    m = _AVG_UP_PCT.search(md_text)
    if m: out["sectors_avg_pct"] = float(m.group(1))

    m = _SECT_UP.search(md_text)
    if m:
        out["sectors_up"]    = int(m.group(1))
        out["sectors_total"] = int(m.group(2))

    return out


# ── ECharts page (vanilla HTML — styled to match Lynus tokens) ────────────

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>市場脈動 — charles16888</title>

  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
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
          <a class="nav__link nav__link--disabled" href="#" aria-disabled="true">選擇權</a>
          <a class="nav__link nav__link--disabled" href="#" aria-disabled="true">籌碼</a>
          <a class="nav__link nav__link--disabled" href="#" aria-disabled="true">個股</a>
          <a class="nav__link" href="../category.html?cat=news">新聞</a>
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
      <span>市場脈動</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>大盤</strong> · 市場脈動</span>
        <span id="market-date-range">{date_range}</span>
        <span>{days} 個交易日</span>
      </div>
      <h1 class="report-title">市場脈動 · <em>家數圖</em></h1>
      <p class="report-lead">漲跌停家數、總成交額、族群平均漲幅 — 一張圖看大盤每日呼吸節奏。</p>

      <div class="stat-row stat-row--3">
        <div class="stat">
          <div class="stat__label">資料起訖</div>
          <div class="stat__value stat__value--sm num-neutral">{date_range}</div>
        </div>
        <div class="stat">
          <div class="stat__label">資料筆數</div>
          <div class="stat__value stat__value--sm num-neutral">{days}</div>
        </div>
        <div class="stat">
          <div class="stat__label">資料源</div>
          <div class="stat__value stat__value--sm num-neutral">industry_map</div>
        </div>
      </div>
    </section>

    <section class="market-charts">
      <h2 class="market-chart-title">漲跌停家數 × 全族群平均漲幅</h2>
      <div id="chart-limits" class="market-chart"></div>

      <h2 class="market-chart-title">總成交額 × 漲停集中度</h2>
      <div id="chart-volume" class="market-chart"></div>
    </section>

  </main>

  <footer class="footer">
    <div class="container">
      <div id="footer-line" class="footer__row"></div>
    </div>
  </footer>

  <script src="../assets/main.js" defer></script>
  <script>
  // Inline data so the chart works without an extra fetch.
  const MARKET_DATA = {data_json};

  // Lynus' tokens for the chart palette.
  const ink     = '#e8dfd3';
  const inkDim  = '#6e6350';
  const gold    = '#d4af37';
  const goldSft = '#b8985c';
  const up      = '#e85a5a';
  const down    = '#5fb87a';
  const rule    = 'rgba(232,223,211,0.10)';
  const ruleGold= 'rgba(212,175,55,0.45)';
  const bg      = '#1a1612';

  function commonAxis() {{
    return {{
      type: 'category',
      data: MARKET_DATA.map(d => d.date),
      axisLine: {{ lineStyle: {{ color: rule }} }},
      axisLabel: {{ color: inkDim, fontSize: 10, fontFamily: 'JetBrains Mono, monospace' }},
      axisTick: {{ show: false }},
      splitLine: {{ show: false }},
    }};
  }}

  // ── Chart 1: limit-up/down bars + sector avg-pct line ───────────────
  const chart1 = echarts.init(document.getElementById('chart-limits'), null, {{ renderer: 'svg' }});
  chart1.setOption({{
    backgroundColor: 'transparent',
    grid: {{ left: 50, right: 50, top: 50, bottom: 60 }},
    tooltip: {{
      trigger: 'axis',
      backgroundColor: bg,
      borderColor: ruleGold,
      borderWidth: 1,
      textStyle: {{ color: ink, fontFamily: 'Inter' }},
      axisPointer: {{ type: 'cross', lineStyle: {{ color: goldSft }} }},
    }},
    legend: {{
      data: ['漲停家數', '跌停家數', '全族群平均漲幅'],
      textStyle: {{ color: ink, fontFamily: 'Inter' }},
      top: 8,
      itemGap: 28,
    }},
    xAxis: commonAxis(),
    yAxis: [
      {{
        type: 'value',
        name: '家數',
        nameTextStyle: {{ color: inkDim, fontSize: 10, padding: [0, 0, 0, -10] }},
        axisLine: {{ lineStyle: {{ color: rule }} }},
        axisLabel: {{ color: inkDim, fontSize: 10 }},
        splitLine: {{ lineStyle: {{ color: rule, type: 'dashed' }} }},
      }},
      {{
        type: 'value',
        name: '% 平均',
        nameTextStyle: {{ color: inkDim, fontSize: 10, padding: [0, -10, 0, 0] }},
        axisLine: {{ lineStyle: {{ color: rule }} }},
        axisLabel: {{ color: inkDim, fontSize: 10, formatter: '{{value}}%' }},
        splitLine: {{ show: false }},
      }},
    ],
    dataZoom: [
      {{ type: 'inside', start: 0, end: 100 }},
      {{
        type: 'slider', height: 24, bottom: 8,
        borderColor: 'transparent',
        backgroundColor: 'transparent',
        fillerColor: 'rgba(212,175,55,0.12)',
        handleStyle: {{ color: gold }},
        moveHandleStyle: {{ color: gold }},
        textStyle: {{ color: inkDim, fontFamily: 'JetBrains Mono', fontSize: 9 }},
        dataBackground: {{
          lineStyle: {{ color: goldSft }},
          areaStyle: {{ color: 'rgba(212,175,55,0.08)' }},
        }},
      }},
    ],
    series: [
      {{
        name: '漲停家數',
        type: 'bar',
        stack: 'limit-up',
        data: MARKET_DATA.map(d => d.limit_up_twse + d.limit_up_tpex),
        itemStyle: {{ color: up }},
        barMaxWidth: 12,
      }},
      {{
        name: '跌停家數',
        type: 'bar',
        stack: 'limit-down',
        data: MARKET_DATA.map(d => -(d.limit_down_twse + d.limit_down_tpex)),
        itemStyle: {{ color: down }},
        barMaxWidth: 12,
      }},
      {{
        name: '全族群平均漲幅',
        type: 'line',
        yAxisIndex: 1,
        data: MARKET_DATA.map(d => d.sectors_avg_pct),
        smooth: true,
        lineStyle: {{ color: gold, width: 2 }},
        itemStyle: {{ color: gold }},
        symbolSize: 5,
        areaStyle: {{
          color: {{
            type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              {{ offset: 0, color: 'rgba(212,175,55,0.18)' }},
              {{ offset: 1, color: 'rgba(212,175,55,0)' }},
            ],
          }},
        }},
      }},
    ],
  }});

  // ── Chart 2: turnover + limit-up concentration ─────────────────────
  const chart2 = echarts.init(document.getElementById('chart-volume'), null, {{ renderer: 'svg' }});
  chart2.setOption({{
    backgroundColor: 'transparent',
    grid: {{ left: 60, right: 60, top: 50, bottom: 60 }},
    tooltip: {{
      trigger: 'axis',
      backgroundColor: bg,
      borderColor: ruleGold,
      borderWidth: 1,
      textStyle: {{ color: ink, fontFamily: 'Inter' }},
      axisPointer: {{ type: 'cross', lineStyle: {{ color: goldSft }} }},
    }},
    legend: {{
      data: ['總成交額(億)', '漲停集中度(%)'],
      textStyle: {{ color: ink }},
      top: 8,
      itemGap: 28,
    }},
    xAxis: commonAxis(),
    yAxis: [
      {{
        type: 'value', name: '億 NT$',
        nameTextStyle: {{ color: inkDim, fontSize: 10 }},
        axisLine: {{ lineStyle: {{ color: rule }} }},
        axisLabel: {{ color: inkDim, fontSize: 10 }},
        splitLine: {{ lineStyle: {{ color: rule, type: 'dashed' }} }},
      }},
      {{
        type: 'value', name: '%',
        nameTextStyle: {{ color: inkDim, fontSize: 10 }},
        axisLine: {{ lineStyle: {{ color: rule }} }},
        axisLabel: {{ color: inkDim, fontSize: 10, formatter: '{{value}}%' }},
        splitLine: {{ show: false }},
        max: 50,
      }},
    ],
    dataZoom: [
      {{ type: 'inside', start: 0, end: 100 }},
      {{
        type: 'slider', height: 24, bottom: 8,
        borderColor: 'transparent', backgroundColor: 'transparent',
        fillerColor: 'rgba(212,175,55,0.12)',
        handleStyle: {{ color: gold }}, moveHandleStyle: {{ color: gold }},
        textStyle: {{ color: inkDim, fontFamily: 'JetBrains Mono', fontSize: 9 }},
        dataBackground: {{
          lineStyle: {{ color: goldSft }},
          areaStyle: {{ color: 'rgba(212,175,55,0.08)' }},
        }},
      }},
    ],
    series: [
      {{
        name: '總成交額(億)',
        type: 'bar',
        data: MARKET_DATA.map(d => d.turnover_billion),
        itemStyle: {{ color: 'rgba(184,152,92,0.55)' }},
        barMaxWidth: 12,
      }},
      {{
        name: '漲停集中度(%)',
        type: 'line',
        yAxisIndex: 1,
        data: MARKET_DATA.map(d => d.up_concentration_pct),
        smooth: true,
        lineStyle: {{ color: up, width: 2 }},
        itemStyle: {{ color: up }},
        symbolSize: 5,
      }},
    ],
  }});

  // Re-fit charts on resize / orientation change
  function resizeAll() {{ chart1.resize(); chart2.resize(); }}
  window.addEventListener('resize', resizeAll);
  </script>
</body>
</html>
"""


# ── main ───────────────────────────────────────────────────────────────────

def main() -> int:
    mds = sorted(INDUSTRY_MAP_REPORTS.glob("daily_*.md"))
    if not mds:
        print(f"[ERR] no daily_*.md found in {INDUSTRY_MAP_REPORTS}")
        return 1

    series: list[dict] = []
    for md_path in mds:
        m = re.search(r"daily_(\d{4}-\d{2}-\d{2})", md_path.stem)
        if not m:
            continue
        date = m.group(1)
        parsed = parse_daily(md_path.read_text(encoding="utf-8"))
        if parsed is None:
            continue
        parsed["date"] = date
        series.append(parsed)

    if not series:
        print("[ERR] parsed no data")
        return 2

    # date-sort
    series.sort(key=lambda d: d["date"])

    # 1) json
    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(series, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)} — {len(series)} rows")

    # 2) html with inline data
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    rendered = PAGE_TEMPLATE.format(
        data_json=json.dumps(series, ensure_ascii=False),
        date_range=f"{series[0]['date']} → {series[-1]['date']}",
        days=len(series),
    )
    PAGE_OUT.write_text(rendered, encoding="utf-8")
    print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)} — chart page written")

    # 3) Enable taiex category + add single manifest entry pointing to the
    # live chart page. We use a fixed id so each rebuild replaces itself
    # rather than littering archive with 'one entry per build' noise.
    cats_path = LYNUS_ROOT / "categories.json"
    cats = json.loads(cats_path.read_text(encoding="utf-8"))
    for c in cats["categories"]:
        if c["id"] == "taiex":
            c["enabled"] = True
            if "industry_map" not in c.get("source_pipelines", []):
                c.setdefault("source_pipelines", []).append("industry_map")
            c["report_types"] = [{"id": "pulse", "name_zh": "市場脈動", "name_en": "Market Pulse"}]
    cats_path.write_text(json.dumps(cats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] categories.json — taiex enabled")

    manifest_path = LYNUS_ROOT / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    latest = series[-1]
    entry = {
        "id":              "market-pulse",
        "category":        "taiex",
        "type":            "pulse",
        "date":            latest["date"],
        "time":            "20:00",
        "title":           f"市場脈動 · 漲跌停 × 全族群均幅",
        "title_em":        "脈動",
        "summary":         (
            f"近 {len(series)} 個交易日漲跌停家數、總成交額、族群平均漲幅、漲停集中度。"
            f"最新 ({latest['date']})：漲停 {latest['limit_up_twse'] + latest['limit_up_tpex']}、"
            f"跌停 {latest['limit_down_twse'] + latest['limit_down_tpex']}、"
            f"全族群均幅 {latest['sectors_avg_pct']:+.2f}%。"
        ),
        "tags":            ["市場脈動", "漲跌停", "成交額"],
        "source_pipeline": "industry_map",
        "url":             "reports/market-pulse.html",
        "stats": [
            {"label": "漲停",   "value": str(latest["limit_up_twse"] + latest["limit_up_tpex"]), "color": "up"},
            {"label": "跌停",   "value": str(latest["limit_down_twse"] + latest["limit_down_tpex"]), "color": "down"},
            {"label": "成交額", "value": f"{latest['turnover_billion']:,.0f} 億", "color": "neutral"},
        ],
    }
    # Replace prior entry with same id
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != "market-pulse"]
    manifest["entries"].append(entry)
    manifest["entries"].sort(
        key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")),
        reverse=True,
    )
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] manifest.json — market-pulse entry refreshed")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
