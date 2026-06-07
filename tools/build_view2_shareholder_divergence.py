"""
build_view2_shareholder_divergence.py
======================================
Phase 1 視圖 2：大股東 vs 股價 全市場散點 + 4 象限排行卡。

對應 SPEC v1.1 §3 視圖 2「大股東三線背離圖」全市場版。

四象限定義（X=股價週漲跌%、Y=大戶比例週變化 百分點）：
    Q1 右上  漲 + 加碼   → 共識買進 (consensus buy)
    Q2 左上  跌 + 加碼   → 內部佈局 (insider accumulation, 可能提早佈局)
    Q3 左下  跌 + 減碼   → 共識賣出 (consensus sell)
    Q4 右下  漲 + 減碼   → 接盤警訊 (whale distributing into rally)

Outputs:
    assets/shareholder_divergence.json   — full dataset
    reports/shareholder-divergence.html  — interactive ECharts page
    manifest.json entry under category "chips"
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chip_analysis.data_access import (
    get_tdcc_dates,
    get_shareholder_week_compare,
)

LYNUS_ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = LYNUS_ROOT / "assets" / "shareholder_divergence.json"
PAGE_OUT = LYNUS_ROOT / "reports" / "shareholder-divergence.html"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"
TPE = ZoneInfo("Asia/Taipei")

HISTORY_DIR_NAME = "shareholder_divergence"


def _update_history_index(latest_date: str) -> None:
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

# 過濾：排除 ETF（00 開頭）以及無效樣本
EXCLUDE_PREFIX = ("00",)

TOP_N = 15  # 每象限排行 Top N


def quadrant(close_chg_pct: float, pct_400up_delta: float) -> str:
    if close_chg_pct >= 0 and pct_400up_delta >= 0:
        return "consensus_buy"        # Q1
    if close_chg_pct <  0 and pct_400up_delta >= 0:
        return "insider_accumulate"   # Q2
    if close_chg_pct <  0 and pct_400up_delta <  0:
        return "consensus_sell"       # Q3
    return "whale_distribute"         # Q4


QUADRANT_META = {
    "consensus_buy":      {"label": "共識買進", "desc": "股價漲 + 大戶加碼", "color": "#e85a5a", "icon": "▲"},
    "insider_accumulate": {"label": "內部佈局", "desc": "股價跌但大戶逆勢加碼，內部人可能提早卡位", "color": "#d4af37", "icon": "◆"},
    "consensus_sell":     {"label": "共識賣出", "desc": "股價跌 + 大戶減碼", "color": "#5fb87a", "icon": "▼"},
    "whale_distribute":   {"label": "接盤警訊", "desc": "股價漲但大戶減碼，主力可能倒貨給散戶", "color": "#ff9933", "icon": "⚠"},
}


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--end-date", type=str, default=None,
                   help="TDCC snapshot date (e.g. 2026-05-22) to use as 'latest'. "
                        "Default = newest available.")
    p.add_argument("--history-only", action="store_true",
                   help="Only write dated history JSON; do not touch main file/manifest.")
    args = p.parse_args()

    dates = get_tdcc_dates()
    if len(dates) < 2:
        print(f"[ERR] need >= 2 tdcc snapshots, have {len(dates)}")
        return 1

    # 決定 latest + prev
    if args.end_date:
        if args.end_date not in dates:
            print(f"[ERR] end-date {args.end_date} not in tdcc dates")
            return 3
        idx = dates.index(args.end_date)
        if idx + 1 >= len(dates):
            print(f"[ERR] no prior tdcc snapshot before {args.end_date}")
            return 4
        latest, prev = dates[idx], dates[idx + 1]
    else:
        latest, prev = dates[0], dates[1]

    is_latest_run = (latest == dates[0])
    print(f"[INFO] comparing {prev} → {latest}{' (backfill)' if not is_latest_run else ''}")

    df = get_shareholder_week_compare(latest, prev)
    if df.empty:
        print("[ERR] shareholder compare returned empty")
        return 2
    print(f"[INFO] raw rows: {len(df)}")

    # 過濾：排除 ETF + 缺資料
    df = df[~df["code"].astype(str).str.startswith(EXCLUDE_PREFIX)]
    df = df.dropna(subset=["close_chg_pct", "pct_400up_delta", "close_latest"])
    print(f"[INFO] after filter (non-ETF + complete): {len(df)}")

    # 給每股算象限
    df["quadrant"] = df.apply(
        lambda r: quadrant(r["close_chg_pct"], r["pct_400up_delta"]), axis=1
    )

    # round numbers for display
    df["close_chg_pct"] = df["close_chg_pct"].round(2)
    df["pct_400up_delta"] = df["pct_400up_delta"].round(3)
    df["pct_1000up_delta"] = df["pct_1000up_delta"].round(3)
    df["close_latest"] = df["close_latest"].round(2)
    df["close_prev"] = df["close_prev"].round(2)

    # 未來走勢 1/3/5/10/20 個交易日 (以 latest tdcc 為起點)
    from chip_analysis.data_access import get_future_returns
    fr_map = get_future_returns(df["code"].tolist(), latest)
    print(f"[INFO] future returns: {len(fr_map)} codes")
    df["ret_1d"]  = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_1d"))
    df["ret_3d"]  = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_3d"))
    df["ret_5d"]  = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_5d"))
    df["ret_10d"] = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_10d"))
    df["ret_20d"] = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_20d"))

    # 散點 dataset（給 ECharts，全市場）
    scatter_points = []
    for _, r in df.iterrows():
        scatter_points.append([
            float(r["close_chg_pct"]),    # X
            float(r["pct_400up_delta"]),  # Y
            float(r["close_latest"]),     # 點 size 用
            r["code"],
            r["name"] or r["code"],
            r["quadrant"],
        ])

    # 4 象限排行
    quadrant_rankings = {}
    for q_key in QUADRANT_META:
        sub = df[df["quadrant"] == q_key].copy()
        if q_key in ("consensus_buy", "consensus_sell"):
            # 漲幅/跌幅最大 + 加碼/減碼最猛者 → 用 |X| × |Y| 排序
            sub["score"] = sub["close_chg_pct"].abs() * sub["pct_400up_delta"].abs()
            sub = sub.sort_values("score", ascending=False)
        elif q_key == "insider_accumulate":
            # 跌幅大但加碼狠 → 同樣 |X| × |Y|，但限制跌 > 2%
            sub = sub[sub["close_chg_pct"] < -2]
            sub["score"] = sub["close_chg_pct"].abs() * sub["pct_400up_delta"]
            sub = sub.sort_values("score", ascending=False)
        elif q_key == "whale_distribute":
            # 漲幅大但 1000 張大戶減最多 → 漲幅 × |1000up_delta|
            sub = sub[sub["close_chg_pct"] > 2]
            sub["score"] = sub["close_chg_pct"] * sub["pct_1000up_delta"].abs()
            sub = sub.sort_values("score", ascending=False)

        top = sub.head(TOP_N)
        def _opt(r, col):
            v = r.get(col)
            return float(v) if pd.notna(v) else None
        quadrant_rankings[q_key] = [
            {
                "code": r["code"],
                "name": r["name"] or r["code"],
                "close_prev": float(r["close_prev"]),
                "close_latest": float(r["close_latest"]),
                "close_chg_pct": float(r["close_chg_pct"]),
                "pct_400up_prev": round(float(r["pct_400up_prev"]), 2),
                "pct_400up_latest": round(float(r["pct_400up_latest"]), 2),
                "pct_400up_delta": float(r["pct_400up_delta"]),
                "pct_1000up_delta": float(r["pct_1000up_delta"]),
                "holders_1000up_delta": int(r["holders_1000up_delta"]),
                "ret_1d":  _opt(r, "ret_1d"),
                "ret_3d":  _opt(r, "ret_3d"),
                "ret_5d":  _opt(r, "ret_5d"),
                "ret_10d": _opt(r, "ret_10d"),
                "ret_20d": _opt(r, "ret_20d"),
            }
            for _, r in top.iterrows()
        ]

    out = {
        "generated_at": datetime.now(TPE).isoformat(),
        "tdcc_latest": latest,
        "tdcc_prev": prev,
        "stats": {
            "total_stocks": len(df),
            "by_quadrant": {q: int((df["quadrant"] == q).sum()) for q in QUADRANT_META},
        },
        "scatter": scatter_points,
        "quadrant_meta": QUADRANT_META,
        "quadrant_rankings": quadrant_rankings,
    }

    # Always write dated history JSON
    dated_path = LYNUS_ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / f"{latest}.json"
    dated_path.parent.mkdir(parents=True, exist_ok=True)
    dated_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {dated_path.relative_to(LYNUS_ROOT)} (dated)")
    _update_history_index(latest)

    if is_latest_run and not args.history_only:
        DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
        DATA_OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)}")

        render_html(out)
        print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)}")

        update_manifest(out)
        print(f"[OK] manifest.json — shareholder-divergence entry refreshed")
    else:
        print(f"[SKIP main] backfill mode — main file/manifest untouched")
    return 0


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>大股東 vs 股價背離掃描 — Lynus' Research</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
  <style>
    .sd-stat-row {
      display: flex; gap: 24px; flex-wrap: wrap; margin: 16px 0 24px;
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
      letter-spacing: .12em; color: #9a9486;
    }
    .sd-stat-row span strong { color: #e8dfd3; font-weight: 600; }
    #scatter-chart {
      width: 100%; height: 540px; margin: 0 0 36px;
      border: 1px solid rgba(232,223,211,0.10);
      background: rgba(232,223,211,0.02);
    }
    .sd-grid {
      display: grid; grid-template-columns: 1fr 1fr;
      gap: 24px; margin: 0 0 32px;
    }
    .sd-card {
      border: 1px solid rgba(232,223,211,0.10);
      padding: 18px 20px 12px;
      background: rgba(232,223,211,0.02);
    }
    .sd-card__head {
      display: flex; align-items: baseline; justify-content: space-between;
      margin-bottom: 6px; padding-bottom: 10px;
      border-bottom: 1px solid;
    }
    .sd-card__title {
      font-family: 'Noto Serif TC', serif; font-size: 16px; font-weight: 700;
      color: #e8dfd3; margin: 0;
    }
    .sd-card__title em { font-style: normal; }
    .sd-card__desc {
      font-family: 'JetBrains Mono', monospace; font-size: 10px;
      color: #6e6350; letter-spacing: .08em; margin: 0 0 12px;
    }
    .sd-row {
      display: grid; grid-template-columns: 28px 1fr auto;
      gap: 10px; align-items: center;
      padding: 9px 0; border-bottom: 1px dashed rgba(232,223,211,0.08);
    }
    .sd-row__rank {
      font-family: 'JetBrains Mono', monospace; font-size: 10px;
      color: #6e6350; text-align: right;
    }
    .sd-row__main {
      display: flex; flex-direction: column; gap: 2px; min-width: 0;
    }
    .sd-row__title {
      font-family: 'Noto Serif TC', serif; font-size: 14px; font-weight: 600;
      color: #e8dfd3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    }
    .sd-row__title .sd-row__code {
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
      color: #b8985c; margin-right: 6px;
    }
    .sd-row__sub {
      font-family: 'JetBrains Mono', monospace; font-size: 10px;
      color: #6a6557; letter-spacing: .05em;
    }
    .sd-row__nums {
      display: flex; flex-direction: column; align-items: flex-end; gap: 1px;
      font-family: 'JetBrains Mono', monospace;
    }
    .sd-row__price {
      font-size: 13px; font-weight: 700;
    }
    .sd-row__price.up { color: #e85a5a; }
    .sd-row__price.dn { color: #5fb87a; }
    .sd-row__pct400 {
      font-size: 10px; letter-spacing: .05em;
    }
    .sd-row__pct400.up { color: #d4af37; }
    .sd-row__pct400.dn { color: #6e6350; }
    .sd-row__future {
      display: flex; gap: 8px; flex-wrap: wrap;
      margin-top: 3px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 9px; color: #6e6350; letter-spacing: .05em;
    }
    .sd-row__future strong { font-weight: 600; }
    .sd-row__future .up { color: #e85a5a; }
    .sd-row__future .dn { color: #5fb87a; }
    @media (max-width: 760px) {
      .sd-grid { grid-template-columns: 1fr; }
      #scatter-chart { height: 420px; }
    }
    .sd-date-picker {
      background: transparent;
      border: 1px solid rgba(212,175,55,0.40);
      color: #d4af37;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 4px 10px;
      cursor: pointer;
      letter-spacing: .1em;
    }
    .sd-date-picker option { background: #1a1612; color: #e8dfd3; }
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
      <span>大股東背離</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 大股東 vs 股價</span>
        <span>__TDCC_PREV__ → __TDCC_LATEST__</span>
        <span>切換日期：<select id="date-picker" class="sd-date-picker" aria-label="切換歷史日期"></select></span>
      </div>
      <h1 class="report-title">大股東 <em>背離</em> 掃描</h1>
      <p class="report-lead">
        散點圖：每一個點 = 一檔上市櫃個股。X 軸為週對週股價漲跌幅，Y 軸為 400 張以上持股比例變化（百分點）。
        四象限分別代表共識買進（右上）、內部佈局（左上）、共識賣出（左下）、接盤警訊（右下）。
      </p>
      <div class="sd-stat-row" id="sd-stats"></div>
    </section>

    <section>
      <div id="scatter-chart"></div>
    </section>

    <section>
      <div class="sd-grid" id="sd-grid"></div>
    </section>

    <section class="report-cover reveal" style="margin-top:32px">
      <h3 style="font-family:'Noto Serif TC',serif; font-size:14px; color:#9a9486; margin:0 0 8px; letter-spacing:.05em;">資料說明</h3>
      <p class="report-lead" style="font-size:12px;">
        資料源：集保中心 TDCC 每週公布的「股權分散表」(tdcc_holders 表)，以及永豐 K 線 daily_price 表。
        400 張以上持股比例 = (400-600 + 600-800 + 800-1000 + 1000+) 四個 tier 持股比例合計。
        所有指標皆為「最新一週」對比「上一週」的變化。本表已排除 ETF（代號 00 開頭）。
      </p>
    </section>

  </main>

  <footer class="footer">
    <div class="container">
      <div id="footer-line" class="footer__row"></div>
    </div>
  </footer>

  <script src="../assets/main.js" defer></script>
  <script>
  let DATA = __DATA_JSON__;
  let Q_META = DATA.quadrant_meta;

  const HISTORY_DIR = '../assets/chip_history/shareholder_divergence';
  const urlDate = new URLSearchParams(window.location.search).get('date');
  async function loadIndex() {
    try {
      const idx = await fetch(`${HISTORY_DIR}/_index.json?t=${Date.now()}`).then(r => r.json());
      const sel = document.getElementById('date-picker');
      sel.innerHTML = (idx.dates || []).map(d => `<option value="${d}">${d}</option>`).join('');
      const wantDate = urlDate && (idx.dates || []).includes(urlDate) ? urlDate : DATA.tdcc_latest;
      sel.value = wantDate;
      sel.addEventListener('change', () => loadDate(sel.value));
      if (urlDate && urlDate !== DATA.tdcc_latest) {
        await loadDate(urlDate);
      }
    } catch (e) { console.warn('[date-picker]', e); }
  }
  async function loadDate(date) {
    try {
      const data = await fetch(`${HISTORY_DIR}/${date}.json?t=${Date.now()}`).then(r => r.json());
      DATA = data;
      Q_META = data.quadrant_meta;
      renderStats(); renderScatter(); renderGrid();
    } catch (e) { console.warn('[loadDate]', date, e); }
  }
  loadIndex();

  // ── 1. Stats line
  function renderStats() {
    const host = document.getElementById('sd-stats');
    const parts = [
      `<span>樣本：<strong>${DATA.stats.total_stocks.toLocaleString()}</strong> 檔</span>`,
    ];
    for (const [k, m] of Object.entries(Q_META)) {
      const n = DATA.stats.by_quadrant[k] || 0;
      parts.push(`<span style="color:${m.color}">${m.icon} ${m.label}：<strong>${n}</strong></span>`);
    }
    host.innerHTML = parts.join('');
  }

  // ── 2. Scatter (ECharts)
  function renderScatter() {
    const chart = echarts.init(document.getElementById('scatter-chart'), null, { renderer: 'svg' });
    const groups = {};
    for (const k of Object.keys(Q_META)) groups[k] = [];
    for (const p of DATA.scatter) {
      groups[p[5]].push({
        value: [p[0], p[1]],     // X, Y
        symbolSize: Math.max(6, Math.min(22, Math.log(p[2] + 1) * 2)),
        name: `${p[3]} ${p[4]}`,
        itemStyle: { color: Q_META[p[5]].color, opacity: 0.55 },
      });
    }
    chart.setOption({
      backgroundColor: 'transparent',
      grid: { left: 60, right: 30, top: 40, bottom: 50 },
      tooltip: {
        trigger: 'item',
        backgroundColor: 'rgba(26,22,18,0.95)',
        borderColor: '#b8985c',
        textStyle: { color: '#e8dfd3', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 },
        formatter: (p) => `${p.data.name}<br/>股價 ${p.value[0] >= 0 ? '+' : ''}${p.value[0].toFixed(2)}%<br/>大戶比例 ${p.value[1] >= 0 ? '+' : ''}${p.value[1].toFixed(3)} pp`,
      },
      legend: {
        data: Object.entries(Q_META).map(([k, m]) => ({ name: m.label, itemStyle: { color: m.color } })),
        textStyle: { color: '#9a9486', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 },
        bottom: 6,
      },
      xAxis: {
        name: '股價漲跌 %', nameLocation: 'middle', nameGap: 28,
        nameTextStyle: { color: '#9a9486', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 },
        type: 'value',
        axisLine: { lineStyle: { color: '#3d3530' } },
        axisLabel: { color: '#6e6350', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(232,223,211,0.05)' } },
      },
      yAxis: {
        name: '大戶比例變化 (百分點)', nameLocation: 'middle', nameGap: 48,
        nameTextStyle: { color: '#9a9486', fontFamily: 'JetBrains Mono, monospace', fontSize: 11 },
        type: 'value',
        axisLine: { lineStyle: { color: '#3d3530' } },
        axisLabel: { color: '#6e6350', fontFamily: 'JetBrains Mono, monospace', fontSize: 10 },
        splitLine: { lineStyle: { color: 'rgba(232,223,211,0.05)' } },
      },
      series: Object.entries(Q_META).map(([k, m]) => ({
        name: m.label,
        type: 'scatter',
        data: groups[k],
        emphasis: { itemStyle: { opacity: 1, borderColor: '#fff', borderWidth: 1 } },
      })),
      markLine: {
        silent: true,
        symbol: 'none',
        lineStyle: { color: '#3d3530', type: 'dashed' },
        data: [{ xAxis: 0 }, { yAxis: 0 }],
      },
    });
    window.addEventListener('resize', () => chart.resize());
  }

  // ── 3. 四象限排行 grid
  function renderGrid() {
    const host = document.getElementById('sd-grid');
    const order = ['whale_distribute', 'consensus_buy', 'consensus_sell', 'insider_accumulate'];
    const html = order.map(qkey => {
      const m = Q_META[qkey];
      const rows = DATA.quadrant_rankings[qkey] || [];
      const items = rows.map((r, i) => {
        const upDown = r.close_chg_pct >= 0 ? 'up' : 'dn';
        const sign = r.close_chg_pct >= 0 ? '+' : '';
        const p400Sign = r.pct_400up_delta >= 0 ? '+' : '';
        const p400Cls = r.pct_400up_delta >= 0 ? 'up' : 'dn';
        return `
          <div class="sd-row">
            <div class="sd-row__rank">${i + 1}.</div>
            <div class="sd-row__main">
              <div class="sd-row__title">
                <span class="sd-row__code">${r.code}</span>${r.name}
              </div>
              <div class="sd-row__sub">大戶 ${r.pct_400up_prev.toFixed(2)}% → ${r.pct_400up_latest.toFixed(2)}% · 1000張人數 ${r.holders_1000up_delta >= 0 ? '+' : ''}${r.holders_1000up_delta}</div>
              ${(r.ret_1d != null || r.ret_5d != null || r.ret_20d != null) ? `
              <div class="sd-row__future">
                ${[['1d', r.ret_1d], ['3d', r.ret_3d], ['5d', r.ret_5d], ['10d', r.ret_10d], ['20d', r.ret_20d]].map(([lbl, v]) => v != null ? `<span>${lbl} <strong class="${v >= 0 ? 'up' : 'dn'}">${v >= 0 ? '+' : ''}${v.toFixed(1)}%</strong></span>` : `<span>${lbl} —</span>`).join('')}
              </div>` : ''}
            </div>
            <div class="sd-row__nums">
              <div class="sd-row__price ${upDown}">${sign}${r.close_chg_pct.toFixed(2)}%</div>
              <div class="sd-row__pct400 ${p400Cls}">${p400Sign}${r.pct_400up_delta.toFixed(3)} pp</div>
            </div>
          </div>
        `;
      }).join('');
      return `
        <div class="sd-card">
          <div class="sd-card__head" style="border-bottom-color:${m.color}">
            <h2 class="sd-card__title">${m.icon} ${m.label} <em style="color:${m.color}">Top ${rows.length}</em></h2>
          </div>
          <p class="sd-card__desc">${m.desc}</p>
          ${items}
        </div>
      `;
    }).join('');
    host.innerHTML = html;
  }

  // 初始渲染
  renderStats(); renderScatter(); renderGrid();
  </script>
</body>
</html>
"""


def render_html(out: dict) -> None:
    html = PAGE_TEMPLATE
    html = html.replace("__TDCC_PREV__", out["tdcc_prev"])
    html = html.replace("__TDCC_LATEST__", out["tdcc_latest"])
    html = html.replace("__DATA_JSON__", json.dumps(out, ensure_ascii=False))
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(html, encoding="utf-8")


def update_manifest(out: dict) -> None:
    if not MANIFEST_PATH.exists():
        return
    m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = m.get("entries", [])
    entry_id = "shareholder-divergence"
    new_entry = {
        "id": entry_id,
        "category": "chips",
        "title": "大股東 vs 股價背離掃描",
        "subtitle": f"{out['tdcc_prev']} → {out['tdcc_latest']} 週對週 · {out['stats']['total_stocks']} 檔",
        "date": out["tdcc_latest"],
        "url": "reports/shareholder-divergence.html",
        "tags": ["大股東", "TDCC", "背離"],
    }
    # 替換或插入
    found = False
    for i, e in enumerate(entries):
        if e.get("id") == entry_id:
            entries[i] = new_entry
            found = True
            break
    if not found:
        entries.insert(0, new_entry)
    m["entries"] = entries
    MANIFEST_PATH.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
