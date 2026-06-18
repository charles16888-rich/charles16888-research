"""
build_view4_tri_source_lamp.py
================================
Phase 1 視圖 4：三源混合燈號表（SPEC v1.1 §3 視圖 4）。

每股一行，三個燈號：
    燈 1: 分點集中度（5d 累計）
    燈 2: 大股東 400 張以上週對週變化
    燈 3: 三大法人 5d 累計買賣超

燈值規則：
    燈 1: concentration_pct > +20 → +1（強買）; < -20 → -1（強賣）; else 0
    燈 2: pct_400up_delta > +0.5 pp → +1; < -0.5 pp → -1; else 0
    燈 3: institutional total 5d > +1000 張 → +1; < -1000 → -1; else 0

排序：
    三線同方向（|燈合|=3）的股票優先顯示，再依燈 1 絕對值排序。

Outputs:
    assets/tri_source_lamp.json
    reports/tri-source-lamp.html
    manifest.json entry under "chips"
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
    get_shareholder_wide,
    get_close_at,
    get_concentration_full_market,
    get_institutional_window,
    _ro_conn,
)

LYNUS_ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = LYNUS_ROOT / "assets" / "tri_source_lamp.json"
PAGE_OUT = LYNUS_ROOT / "reports" / "tri-source-lamp.html"
MANIFEST_PATH = LYNUS_ROOT / "manifest.json"
TPE = ZoneInfo("Asia/Taipei")

HISTORY_DIR_NAME = "tri_source_lamp"


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

EXCLUDE_PREFIX = ("00",)
TOP_N = 30  # 三線同方向買 + 三線同方向賣 各 TOP N


def get_latest_broker_date() -> str:
    with _ro_conn() as c:
        r = c.execute("SELECT MAX(date) FROM broker_trading").fetchone()
    return r[0] if r else ""


def lamp_value(v: float, pos_th: float, neg_th: float) -> int:
    if v >= pos_th:
        return 1
    if v <= neg_th:
        return -1
    return 0


def get_all_broker_dates() -> list[str]:
    """All trading days in broker_trading, newest first."""
    with _ro_conn() as c:
        rows = c.execute("SELECT DISTINCT date FROM broker_trading ORDER BY date DESC").fetchall()
    return [r[0] for r in rows]


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--end-date", type=str, default=None,
                   help="broker_trading 終點日（預設用最新交易日）")
    p.add_argument("--history-only", action="store_true",
                   help="只寫 dated JSON、不動主檔")
    args = p.parse_args()

    all_broker = get_all_broker_dates()
    if not all_broker:
        print("[ERR] broker_trading empty")
        return 1
    latest_broker = all_broker[0]

    if args.end_date:
        if args.end_date not in all_broker:
            print(f"[ERR] end-date {args.end_date} not in broker_trading dates")
            return 3
        broker_end = args.end_date
    else:
        broker_end = latest_broker

    tdcc_dates = get_tdcc_dates()
    if len(tdcc_dates) < 2:
        print("[ERR] tdcc need >= 2")
        return 1

    # 對齊 tdcc：找 <= broker_end 的 tdcc 最新 + 它的前一期
    eligible = [d for d in tdcc_dates if d <= broker_end]
    if len(eligible) < 2:
        print(f"[ERR] no tdcc pair on/before {broker_end}")
        return 4
    tdcc_latest, tdcc_prev = eligible[0], eligible[1]

    is_latest_run = (broker_end == latest_broker)
    print(f"[INFO] broker end: {broker_end}, tdcc {tdcc_prev} -> {tdcc_latest}"
          f"{' (backfill)' if not is_latest_run else ''}")

    # ── 1. concentration (5d, full market)
    print("[1/3] concentration full market 5d...")
    conc = get_concentration_full_market(broker_end, lookback_days=5)
    print(f"    -> {len(conc)} stocks")

    # ── 2. shareholder week delta
    print("[2/3] shareholder week delta...")
    s_latest = get_shareholder_wide(tdcc_latest)
    s_prev = get_shareholder_wide(tdcc_prev)
    sd = s_latest[["code", "pct_400up"]].merge(
        s_prev[["code", "pct_400up"]].rename(columns={"pct_400up": "pct_400up_prev"}),
        on="code",
    )
    sd["pct_400up_delta"] = sd["pct_400up"] - sd["pct_400up_prev"]
    print(f"    -> {len(sd)} stocks")

    # ── 3. institutional 5d
    print("[3/3] institutional 5d total...")
    inst = get_institutional_window(broker_end, lookback_days=5)
    print(f"    -> {len(inst)} stocks")

    # ── merge
    df = conc.merge(sd[["code", "pct_400up_delta"]], on="code", how="inner")
    df = df.merge(inst[["code", "foreign_lots", "trust_lots", "dealer_lots", "total_lots"]],
                  on="code", how="left").fillna(0)

    # 排除 ETF
    df = df[~df["code"].astype(str).str.startswith(EXCLUDE_PREFIX)]
    print(f"[INFO] merged: {len(df)} stocks")

    # close + 漲跌 (用 build_chip_concentration.get_price_info 拿 close+chg+chg_pct)
    from build_chip_concentration import get_price_info
    from chip_analysis.data_access import get_future_returns
    price_info = get_price_info(df["code"].tolist(), broker_end)
    df["close_latest"] = df["code"].map(lambda c: price_info.get(c, {}).get("close"))
    df["close_chg"] = df["code"].map(lambda c: price_info.get(c, {}).get("chg"))
    df["close_chg_pct"] = df["code"].map(lambda c: price_info.get(c, {}).get("chg_pct"))
    df["name"] = df["code"].map(lambda c: price_info.get(c, {}).get("name") or c)
    # 未來走勢 1/3/5/10/20 個交易日
    fr_map = get_future_returns(df["code"].tolist(), broker_end)
    df["ret_1d"]  = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_1d"))
    df["ret_3d"]  = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_3d"))
    df["ret_5d"]  = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_5d"))
    df["ret_10d"] = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_10d"))
    df["ret_20d"] = df["code"].map(lambda c: fr_map.get(c, {}).get("ret_20d"))

    # 燈號
    df["lamp1"] = df["concentration_pct"].apply(lambda v: lamp_value(v, 20, -20))
    df["lamp2"] = df["pct_400up_delta"].apply(lambda v: lamp_value(v, 0.5, -0.5))
    df["lamp3"] = df["total_lots"].apply(lambda v: lamp_value(v, 1000, -1000))
    df["lamp_sum"] = df["lamp1"] + df["lamp2"] + df["lamp3"]

    # round
    for col in ["concentration_pct", "pct_400up_delta", "total_lots", "foreign_lots",
                "trust_lots", "dealer_lots", "close_latest", "close_chg", "close_chg_pct"]:
        if col in df.columns:
            df[col] = df[col].round(2)

    # 三線同方向買 / 賣
    triple_buy  = df[df["lamp_sum"] == 3].sort_values("concentration_pct", ascending=False).head(TOP_N)
    triple_sell = df[df["lamp_sum"] == -3].sort_values("concentration_pct", ascending=True).head(TOP_N)
    # 部份同向：兩燈相同方向（lamp_sum = +2 or -2）
    partial_buy  = df[df["lamp_sum"] == 2].sort_values("concentration_pct", ascending=False).head(TOP_N)
    partial_sell = df[df["lamp_sum"] == -2].sort_values("concentration_pct", ascending=True).head(TOP_N)

    def serialize(rows):
        out = []
        for _, r in rows.iterrows():
            def _opt(col):
                v = r.get(col)
                return float(v) if pd.notna(v) else None
            out.append({
                "code": r["code"],
                "name": r["name"] or r["code"],
                "close": _opt("close_latest"),
                "chg": _opt("close_chg"),
                "chg_pct": _opt("close_chg_pct"),
                "concentration_pct": float(r["concentration_pct"]),
                "pct_400up_delta": float(r["pct_400up_delta"]),
                "total_lots": float(r["total_lots"]),
                "foreign_lots": float(r["foreign_lots"]),
                "trust_lots": float(r["trust_lots"]),
                "dealer_lots": float(r["dealer_lots"]),
                "ret_1d":  _opt("ret_1d"),
                "ret_3d":  _opt("ret_3d"),
                "ret_5d":  _opt("ret_5d"),
                "ret_10d": _opt("ret_10d"),
                "ret_20d": _opt("ret_20d"),
                "lamp1": int(r["lamp1"]),
                "lamp2": int(r["lamp2"]),
                "lamp3": int(r["lamp3"]),
                "lamp_sum": int(r["lamp_sum"]),
            })
        return out

    out = {
        "generated_at": datetime.now(TPE).isoformat(),
        "broker_end": broker_end,
        "tdcc_latest": tdcc_latest,
        "tdcc_prev": tdcc_prev,
        "stats": {
            "total_merged": len(df),
            "triple_buy_count": int((df["lamp_sum"] == 3).sum()),
            "triple_sell_count": int((df["lamp_sum"] == -3).sum()),
            "partial_buy_count": int((df["lamp_sum"] == 2).sum()),
            "partial_sell_count": int((df["lamp_sum"] == -2).sum()),
            "neutral_count": int((df["lamp_sum"].abs() <= 1).sum()),
        },
        "thresholds": {
            "lamp1_concentration_pct": [20, -20],
            "lamp2_pct_400up_delta_pp": [0.5, -0.5],
            "lamp3_total_lots": [1000, -1000],
        },
        "triple_buy":  serialize(triple_buy),
        "triple_sell": serialize(triple_sell),
        "partial_buy":  serialize(partial_buy),
        "partial_sell": serialize(partial_sell),
    }

    # Always write dated history JSON
    dated_path = LYNUS_ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / f"{broker_end}.json"
    dated_path.parent.mkdir(parents=True, exist_ok=True)
    dated_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {dated_path.relative_to(LYNUS_ROOT)} (dated)")
    _update_history_index(broker_end)

    if is_latest_run and not args.history_only:
        DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
        DATA_OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] {DATA_OUT.relative_to(LYNUS_ROOT)}")

        render_html(out)
        print(f"[OK] {PAGE_OUT.relative_to(LYNUS_ROOT)}")
        update_manifest(out)
        print(f"[OK] manifest.json — tri-source-lamp entry refreshed")
    else:
        print("[SKIP main] backfill mode — main file/manifest untouched")
    return 0


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>三源燈號 — charles16888</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    .tsl-stat-row {
      display: flex; gap: 24px; flex-wrap: wrap; margin: 14px 0 24px;
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
      letter-spacing: .12em; color: #9a9486;
    }
    .tsl-stat-row span strong { color: #e8dfd3; font-weight: 600; }
    .tsl-group {
      margin: 0 0 32px;
      border: 1px solid rgba(232,223,211,0.10);
      background: rgba(232,223,211,0.02);
    }
    .tsl-group__head {
      padding: 14px 18px;
      border-bottom: 1px solid;
    }
    .tsl-group__title {
      font-family: 'Noto Serif TC', serif; font-size: 16px; font-weight: 700;
      color: #e8dfd3; margin: 0;
    }
    .tsl-group__desc {
      font-family: 'JetBrains Mono', monospace; font-size: 10px;
      color: #6e6350; letter-spacing: .08em; margin: 4px 0 0;
    }
    .tsl-table {
      width: 100%; border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
    }
    .tsl-table th {
      text-align: left; padding: 10px 12px;
      border-bottom: 1px solid rgba(232,223,211,0.10);
      color: #9a9486; font-weight: 500; letter-spacing: .1em;
    }
    .tsl-table td {
      padding: 10px 12px;
      border-bottom: 1px dashed rgba(232,223,211,0.06);
      color: #c9c0b3; vertical-align: middle;
    }
    .tsl-code { color: #b8985c; }
    .tsl-name { font-family: 'Noto Serif TC', serif; color: #e8dfd3; font-size: 13px; }
    .tsl-lamps { display: flex; gap: 6px; align-items: center; }
    .tsl-lamp {
      width: 16px; height: 16px; border-radius: 50%;
      display: inline-block;
    }
    .tsl-lamp--pos { background: #e85a5a; box-shadow: 0 0 6px rgba(232,90,90,0.5); }
    .tsl-lamp--neg { background: #5fb87a; box-shadow: 0 0 6px rgba(95,184,122,0.5); }
    .tsl-lamp--0   { background: #3d3530; border: 1px solid rgba(232,223,211,0.10); }
    .tsl-num-pos { color: #e85a5a; font-weight: 700; }
    .tsl-num-neg { color: #5fb87a; font-weight: 700; }
    .tsl-right { text-align: right; }
    @media (max-width: 760px) {
      .tsl-table th:nth-child(7), .tsl-table td:nth-child(7),
      .tsl-table th:nth-child(8), .tsl-table td:nth-child(8) { display: none; }
    }
    .tsl-date-picker {
      background: transparent;
      border: 1px solid rgba(212,175,55,0.40);
      color: #d4af37;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
      padding: 4px 10px;
      cursor: pointer;
      letter-spacing: .1em;
    }
    .tsl-date-picker option { background: #1a1612; color: #e8dfd3; }
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
      <a href="../index.html">charles16888</a>
      <span class="breadcrumb__sep">/</span>
      <a href="../category.html?cat=chips">籌碼</a>
      <span class="breadcrumb__sep">/</span>
      <span>三源燈號</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 三源混合燈號</span>
        <span>分點 5d · 大股東 週對週 · 法人 5d</span>
        <span>切換日期：<select id="date-picker" class="tsl-date-picker" aria-label="切換歷史日期"></select></span>
      </div>
      <h1 class="report-title">三源 <em>燈號</em></h1>
      <p class="report-lead">
        分點集中度（5d 累計）× 大股東 400 張持股變化（週對週）× 三大法人 5d 合計買賣超 = 三盞燈。
        三盞同色 → 強訊號；兩盞同色 → 部份共識；不同色 → 雜訊。本表已排除 ETF。
      </p>
      <div class="tsl-stat-row" id="tsl-stats"></div>
    </section>

    <div id="tsl-groups"></div>

    <section class="report-cover reveal" style="margin-top:32px">
      <h3 style="font-family:'Noto Serif TC',serif; font-size:14px; color:#9a9486; margin:0 0 8px; letter-spacing:.05em;">燈號定義</h3>
      <p class="report-lead" style="font-size:12px;">
        🔴 紅燈（+1）：偏多 · 🟢 綠燈（−1）：偏空 · ⚪ 灰燈（0）：中性<br>
        燈 1（分點集中度）：&gt; +20% 亮紅、&lt; −20% 亮綠<br>
        燈 2（大股東 400 張）：週對週變化 &gt; +0.5pp 亮紅、&lt; −0.5pp 亮綠<br>
        燈 3（三大法人 5d 合計）：&gt; +1000 張亮紅、&lt; −1000 張亮綠
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

  const HISTORY_DIR = '../assets/chip_history/tri_source_lamp';
  const urlDate = new URLSearchParams(window.location.search).get('date');
  async function loadIndex() {
    try {
      const idx = await fetch(`${HISTORY_DIR}/_index.json?t=${Date.now()}`).then(r => r.json());
      const sel = document.getElementById('date-picker');
      sel.innerHTML = (idx.dates || []).map(d => `<option value="${d}">${d}</option>`).join('');
      const wantDate = urlDate && (idx.dates || []).includes(urlDate) ? urlDate : DATA.broker_end;
      sel.value = wantDate;
      sel.addEventListener('change', () => loadDate(sel.value));
      if (urlDate && urlDate !== DATA.broker_end) {
        await loadDate(urlDate);
      }
    } catch (e) { console.warn('[date-picker]', e); }
  }
  async function loadDate(date) {
    try {
      const data = await fetch(`${HISTORY_DIR}/${date}.json?t=${Date.now()}`).then(r => r.json());
      DATA = data;
      renderStats(); renderGroups();
    } catch (e) { console.warn('[loadDate]', date, e); }
  }
  loadIndex();

  function lampHtml(v) {
    const cls = v > 0 ? 'tsl-lamp--pos' : v < 0 ? 'tsl-lamp--neg' : 'tsl-lamp--0';
    return `<span class="tsl-lamp ${cls}" title="${v}"></span>`;
  }
  function numClass(v) {
    if (v > 0) return 'tsl-num-pos';
    if (v < 0) return 'tsl-num-neg';
    return '';
  }
  function fmtSigned(v, digits) {
    const sign = v >= 0 ? '+' : '';
    return sign + v.toFixed(digits);
  }

  function renderStats() {
    const s = DATA.stats;
    const parts = [
      `<span>總樣本：<strong>${s.total_merged.toLocaleString()}</strong></span>`,
      `<span style="color:#e85a5a">🔴🔴🔴 三線同買：<strong>${s.triple_buy_count}</strong></span>`,
      `<span style="color:#5fb87a">🟢🟢🟢 三線同賣：<strong>${s.triple_sell_count}</strong></span>`,
      `<span style="color:#d4af37">兩線偏多：<strong>${s.partial_buy_count}</strong></span>`,
      `<span style="color:#a0a55f">兩線偏空：<strong>${s.partial_sell_count}</strong></span>`,
    ];
    document.getElementById('tsl-stats').innerHTML = parts.join('');
  }

  function tableHtml(rows) {
    if (!rows.length) return '<p style="padding:16px 18px;color:#6e6350;font-size:12px;font-style:italic">此分類無符合股票</p>';
    const trs = rows.map(r => `
      <tr>
        <td><span class="tsl-lamps">${lampHtml(r.lamp1)}${lampHtml(r.lamp2)}${lampHtml(r.lamp3)}</span></td>
        <td><span class="tsl-code">${r.code}</span></td>
        <td><span class="tsl-name">${r.name}</span></td>
        <td class="tsl-right ${numClass(r.concentration_pct)}">${fmtSigned(r.concentration_pct, 1)}%</td>
        <td class="tsl-right ${numClass(r.pct_400up_delta)}">${fmtSigned(r.pct_400up_delta, 2)} pp</td>
        <td class="tsl-right ${numClass(r.total_lots)}">${fmtSigned(r.total_lots, 0)}</td>
        <td class="tsl-right" style="color:#7a7066">${fmtSigned(r.foreign_lots, 0)}</td>
        <td class="tsl-right">${r.close != null ? r.close.toFixed(2) : '—'}</td>
        <td class="tsl-right ${r.chg != null ? numClass(r.chg) : ''}">${r.chg != null ? fmtSigned(r.chg, 2) : '—'}</td>
        <td class="tsl-right ${r.chg_pct != null ? numClass(r.chg_pct) : ''}">${r.chg_pct != null ? fmtSigned(r.chg_pct, 2) + '%' : '—'}</td>
        <td class="tsl-right tsl-fut ${r.ret_1d != null ? numClass(r.ret_1d) : ''}">${r.ret_1d != null ? fmtSigned(r.ret_1d, 1) + '%' : '—'}</td>
        <td class="tsl-right tsl-fut ${r.ret_3d != null ? numClass(r.ret_3d) : ''}">${r.ret_3d != null ? fmtSigned(r.ret_3d, 1) + '%' : '—'}</td>
        <td class="tsl-right tsl-fut ${r.ret_5d != null ? numClass(r.ret_5d) : ''}">${r.ret_5d != null ? fmtSigned(r.ret_5d, 1) + '%' : '—'}</td>
        <td class="tsl-right tsl-fut ${r.ret_10d != null ? numClass(r.ret_10d) : ''}">${r.ret_10d != null ? fmtSigned(r.ret_10d, 1) + '%' : '—'}</td>
        <td class="tsl-right tsl-fut ${r.ret_20d != null ? numClass(r.ret_20d) : ''}">${r.ret_20d != null ? fmtSigned(r.ret_20d, 1) + '%' : '—'}</td>
      </tr>
    `).join('');
    return `
      <table class="tsl-table">
        <thead><tr>
          <th>燈號</th><th>代號</th><th>名稱</th>
          <th class="tsl-right">集中度</th>
          <th class="tsl-right">大戶 ΔΔ</th>
          <th class="tsl-right">法人 5d</th>
          <th class="tsl-right">外資 5d</th>
          <th class="tsl-right">收盤</th>
          <th class="tsl-right">漲跌</th>
          <th class="tsl-right">漲跌%</th>
          <th class="tsl-right" title="次日漲跌">1日後</th>
          <th class="tsl-right" title="3 個交易日後漲跌">3日後</th>
          <th class="tsl-right" title="5 個交易日後漲跌">5日後</th>
          <th class="tsl-right" title="10 個交易日後漲跌">10日後</th>
          <th class="tsl-right" title="20 個交易日後漲跌">20日後</th>
        </tr></thead>
        <tbody>${trs}</tbody>
      </table>
    `;
  }

  function renderGroups() {
    const groups = [
      {key:'triple_buy',   title:'🔴 三線同買 (Top 30)',   desc:'分點主力買 + 大戶加碼 + 法人買 — 強多訊號', color:'#e85a5a'},
      {key:'triple_sell',  title:'🟢 三線同賣 (Top 30)',   desc:'分點主力賣 + 大戶減碼 + 法人賣 — 強空訊號', color:'#5fb87a'},
      {key:'partial_buy',  title:'🟡 兩線偏多 (Top 30)',   desc:'三燈中兩燈紅一燈灰 — 共識偏多但未滿', color:'#d4af37'},
      {key:'partial_sell', title:'🟡 兩線偏空 (Top 30)',   desc:'三燈中兩燈綠一燈灰 — 共識偏空但未滿', color:'#a0a55f'},
    ];
    const html = groups.map(g => `
      <div class="tsl-group">
        <div class="tsl-group__head" style="border-bottom-color:${g.color}">
          <h2 class="tsl-group__title">${g.title}</h2>
          <p class="tsl-group__desc">${g.desc}</p>
        </div>
        ${tableHtml(DATA[g.key])}
      </div>
    `).join('');
    document.getElementById('tsl-groups').innerHTML = html;
  }

  renderStats(); renderGroups();
  </script>
</body>
</html>
"""


def render_html(out: dict) -> None:
    html = PAGE_TEMPLATE
    html = html.replace("__BROKER_END__", out["broker_end"])
    html = html.replace("__DATA_JSON__", json.dumps(out, ensure_ascii=False))
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(html, encoding="utf-8")


def update_manifest(out: dict) -> None:
    if not MANIFEST_PATH.exists():
        return
    m = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = m.get("entries", [])
    entry_id = "tri-source-lamp"
    s = out["stats"]
    new_entry = {
        "id": entry_id,
        "category": "chips",
        "title": "三源混合燈號",
        "subtitle": f"{out['broker_end']} · 三同買 {s['triple_buy_count']} / 三同賣 {s['triple_sell_count']}",
        "date": out["broker_end"],
        "url": "reports/tri-source-lamp.html",
        "tags": ["分點", "大股東", "三大法人", "燈號"],
    }
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
