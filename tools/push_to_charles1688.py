"""
push_to_charles1688.py
=======================
Mirror Lynus' sectors + taiex content to charles1688.com's 每日推播 board.

Skips news + stocks (copyright concerns per user).

What it pushes:
    - sectors daily / weekly / rotation  (HTML body, Lynus editorial style)
    - taiex 3 charts (market-pulse / futures-basis / options-atm) as
      interactive ECharts via the tg-push-board v1.2 chart shortcode.

Usage:
    python tools/push_to_charles1688.py --charts        # push 3 taiex charts only
    python tools/push_to_charles1688.py --sectors       # push all sectors history
    python tools/push_to_charles1688.py --all           # both
    python tools/push_to_charles1688.py --clear         # nuke all tg_push first
    python tools/push_to_charles1688.py --rebuild       # clear + push all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"C:\Users\birdk\watchdog")
from web_push import post, clear_all  # noqa: E402

LYNUS_ROOT = Path(r"E:\Lynus")
INDUSTRY_REPORTS = Path(r"E:\industry_map\reports")

# ── Color tokens — light editorial (matches plugin v1.2.1 CSS) ──
PALETTE = {
    "ink":      "#1a1612",   # 深棕主字
    "ink_dim":  "#5a4f3e",
    "gold":     "#a8853a",   # 暗金（淺底友善）
    "goldSft":  "#c9a86c",
    "red":      "#c84a4a",
    "green":    "#3a8b54",
    "rule":     "rgba(26,22,18,0.12)",
    "ruleGold": "rgba(168,133,58,0.45)",
    "bg":       "#ffffff",
}


# ─────────────────────────────────────────────────────────
# Chart spec builders — produce ECharts option dicts
# ─────────────────────────────────────────────────────────

def _base_chart_axes(dates: list[str], y_left_name: str, y_right_name: str = "加權") -> dict:
    """Common axis + grid + zoom config shared by every chart."""
    return {
        "backgroundColor": "transparent",
        "grid":   {"left": 60, "right": 60, "top": 38, "bottom": 58},
        "tooltip": {
            "trigger": "axis",
            "backgroundColor": PALETTE["bg"],
            "borderColor": PALETTE["ruleGold"],
            "borderWidth": 1,
            "textStyle": {"color": PALETTE["ink"]},
            "axisPointer": {"type": "cross", "lineStyle": {"color": PALETTE["goldSft"]}},
        },
        "legend": {"textStyle": {"color": PALETTE["ink"]}, "top": 2, "itemGap": 24},
        "xAxis": {
            "type": "category",
            "data": dates,
            "axisLine":  {"lineStyle": {"color": PALETTE["rule"]}},
            "axisLabel": {"color": PALETTE["ink_dim"], "fontSize": 9},
            "axisTick":  {"show": False},
        },
        "yAxis": [
            {"type": "value", "name": y_left_name,
             "nameTextStyle": {"color": PALETTE["ink_dim"], "fontSize": 9},
             "axisLine":  {"lineStyle": {"color": PALETTE["rule"]}},
             "axisLabel": {"color": PALETTE["ink_dim"], "fontSize": 9},
             "splitLine": {"lineStyle": {"color": PALETTE["rule"], "type": "dashed"}}},
            {"type": "value", "name": y_right_name,
             "nameTextStyle": {"color": PALETTE["ink_dim"], "fontSize": 9},
             "axisLine":  {"lineStyle": {"color": PALETTE["rule"]}},
             "axisLabel": {"color": PALETTE["ink_dim"], "fontSize": 9},
             "splitLine": {"show": False}},
        ],
        "dataZoom": [
            {"type": "inside", "startValue": 0, "endValue": len(dates) - 1},
            {"type": "slider", "height": 18, "bottom": 8,
             "borderColor": "transparent", "backgroundColor": "transparent",
             "fillerColor": "rgba(212,175,55,0.12)",
             "handleStyle": {"color": PALETTE["gold"]},
             "textStyle":   {"color": PALETTE["ink_dim"], "fontSize": 9},
             "dataBackground": {
                 "lineStyle": {"color": PALETTE["goldSft"]},
                 "areaStyle": {"color": "rgba(212,175,55,0.08)"},
             }},
        ],
    }


def build_futures_basis_spec() -> tuple[dict, dict, list[dict], str]:
    """Returns (chart_option, latest_row, stats, summary) for futures basis."""
    data = json.loads((LYNUS_ROOT / "assets" / "futures_basis.json").read_text(encoding="utf-8"))
    dates = [d["date"] for d in data]
    spec = _base_chart_axes(dates, "基差 (點)")
    spec["legend"]["data"] = ["基差", "加權指數"]
    spec["yAxis"][0]["markLine"] = {
        "silent": True,
        "lineStyle": {"color": PALETTE["goldSft"]},
        "data": [{"yAxis": 0}],
    }
    spec["series"] = [
        {"name": "基差", "type": "line", "showSymbol": False, "sampling": "lttb",
         "lineStyle": {"width": 1, "color": PALETTE["red"]},
         "areaStyle": {"color": {
             "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
             "colorStops": [{"offset": 0, "color": "rgba(232,90,90,0.22)"},
                            {"offset": 1, "color": "rgba(232,90,90,0)"}]}},
         "data": [d["basis"] for d in data]},
        {"name": "加權指數", "type": "line", "yAxisIndex": 1,
         "showSymbol": False, "sampling": "lttb",
         "lineStyle": {"color": PALETTE["gold"], "width": 1.5},
         "data": [d["twse_close"] for d in data]},
    ]
    latest = data[-1]
    stats = [
        {"label": "最新基差", "value": f"{'+' if latest['basis']>=0 else ''}{latest['basis']:.1f} 點",
         "color": "up" if latest["basis"] >= 0 else "down"},
        {"label": "加權", "value": f"{latest['twse_close']:,.0f}", "color": "gold"},
        {"label": "區間", "value": f"{len(data)} 日", "color": "neutral"},
    ]
    summary = f"台指期 TXF1 vs 加權指數 12 年期現對照（{data[0]['date']} → {latest['date']}）"
    return spec, latest, stats, summary


def build_options_atm_spec() -> tuple[dict, dict, list[dict], str]:
    data = json.loads((LYNUS_ROOT / "assets" / "options_atm.json").read_text(encoding="utf-8"))
    dates = [d["date"] for d in data]
    spec = _base_chart_axes(dates, "價平和 (點)")
    spec["legend"]["data"] = ["近期 C+P", "次遠期 C+P", "加權指數"]
    spec["series"] = [
        {"name": "近期 C+P", "type": "line", "showSymbol": False, "sampling": "lttb",
         "lineStyle": {"width": 1.5, "color": PALETTE["red"]},
         "areaStyle": {"color": {
             "type": "linear", "x": 0, "y": 0, "x2": 0, "y2": 1,
             "colorStops": [{"offset": 0, "color": "rgba(232,90,90,0.22)"},
                            {"offset": 1, "color": "rgba(232,90,90,0)"}]}},
         "data": [d["opt_near_cp_sum"] for d in data]},
        {"name": "次遠期 C+P", "type": "line", "showSymbol": False, "sampling": "lttb",
         "lineStyle": {"width": 1.2, "color": PALETTE["goldSft"], "type": "dashed"},
         "data": [d["opt_far_cp_sum"] for d in data]},
        {"name": "加權指數", "type": "line", "yAxisIndex": 1,
         "showSymbol": False, "sampling": "lttb",
         "lineStyle": {"color": PALETTE["gold"], "width": 1.5},
         "data": [d["twse_close"] for d in data]},
    ]
    latest = data[-1]
    stats = [
        {"label": "近期 C+P", "value": f"{latest['opt_near_cp_sum']:.0f} 點", "color": "gold"},
        {"label": "次遠 C+P", "value": f"{latest['opt_far_cp_sum']:.0f} 點" if latest.get('opt_far_cp_sum') else "—",
         "color": "neutral"},
        {"label": "近期到期", "value": latest.get('opt_near_expiry', '—'), "color": "neutral"},
    ]
    summary = f"TXO 近月+次遠月 ATM Call+Put 加總（{data[0]['date']} → {latest['date']}）— 隱含波動代理"
    return spec, latest, stats, summary


def build_turnover_spec() -> tuple[dict, dict, list[dict], str]:
    """Daily TWSE+TPEx total turnover in NT$ billions."""
    raw_path = LYNUS_ROOT / "assets" / "market_pulse.json"
    if not raw_path.exists():
        return None  # type: ignore
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data or "turnover_billion" not in data[0]:
        return None  # type: ignore
    dates = [d["date"] for d in data]
    values = [d.get("turnover_billion") for d in data]
    # 5-day moving average for trend reference
    def _ma(arr, n):
        out = []
        for i in range(len(arr)):
            window = [v for v in arr[max(0, i - n + 1): i + 1] if v is not None]
            out.append(sum(window) / len(window) if window else None)
        return out
    ma5 = _ma(values, 5)

    spec = _base_chart_axes(dates, "成交額 (億)", y_right_name="")
    # Single y-axis — drop the right one to avoid a stray "—" label.
    spec["yAxis"] = [spec["yAxis"][0]]
    spec["legend"]["data"] = ["成交額", "5 日均"]
    spec["series"] = [
        {"name": "成交額", "type": "bar", "barMaxWidth": 16,
         "itemStyle": {"color": PALETTE["goldSft"]},
         "data": values},
        {"name": "5 日均", "type": "line",
         "showSymbol": False, "smooth": True,
         "lineStyle": {"color": PALETTE["gold"], "width": 1.8},
         "data": ma5},
    ]
    latest = data[-1]
    stats = [
        {"label": "最新成交額", "value": f"{latest['turnover_billion']:,.0f} 億", "color": "gold"},
        {"label": "5 日均",     "value": f"{ma5[-1]:,.0f} 億" if ma5[-1] else "—", "color": "neutral"},
        {"label": "近 {} 日".format(len(data)),
         "value": f"{min(v for v in values if v is not None):,.0f}–{max(values):,.0f}",
         "color": "neutral"},
    ]
    summary = f"TWSE + TPEx 合計成交額 + 5 日均（{data[0]['date']} → {latest['date']}）"
    return spec, latest, stats, summary


def build_market_pulse_spec() -> tuple[dict, dict, list[dict], str]:
    raw_path = LYNUS_ROOT / "assets" / "market_pulse.json"
    if not raw_path.exists():
        return None  # type: ignore
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        return None  # type: ignore

    def _sum_up(d):    return (d.get("limit_up_twse")   or 0) + (d.get("limit_up_tpex")   or 0)
    def _sum_down(d):  return (d.get("limit_down_twse") or 0) + (d.get("limit_down_tpex") or 0)

    dates = [d["date"] for d in data]
    spec = _base_chart_axes(dates, "家數", y_right_name="均幅 %")
    spec["legend"]["data"] = ["漲停", "跌停", "族群均幅"]
    spec["series"] = [
        {"name": "漲停", "type": "bar", "barMaxWidth": 14,
         "itemStyle": {"color": PALETTE["red"]},
         "data": [_sum_up(d) for d in data]},
        {"name": "跌停", "type": "bar", "barMaxWidth": 14,
         "itemStyle": {"color": PALETTE["green"]},
         "data": [-_sum_down(d) for d in data]},  # negative so the bar grows downward
        {"name": "族群均幅", "type": "line", "yAxisIndex": 1,
         "showSymbol": False, "smooth": True,
         "lineStyle": {"color": PALETTE["gold"], "width": 1.8},
         "data": [d.get("sectors_avg_pct") for d in data]},
    ]
    latest = data[-1]
    up_n   = _sum_up(latest)
    down_n = _sum_down(latest)
    avg    = latest.get("sectors_avg_pct") or 0
    stats = [
        {"label": "漲停", "value": str(up_n), "color": "up"},
        {"label": "跌停", "value": str(down_n), "color": "down"},
        {"label": "族群均幅",
         "value": f"{'+' if avg >= 0 else ''}{avg:.2f}%",
         "color": "up" if avg >= 0 else "down"},
    ]
    summary = (f"近 {len(data)} 個交易日漲跌停家數（TWSE+TPEx 合計）"
               f"+ 全族群平均漲幅（{data[0]['date']} → {latest['date']}）")
    return spec, latest, stats, summary


# ─────────────────────────────────────────────────────────
# Sectors HTML extraction — pull article body from wrapped HTML
# ─────────────────────────────────────────────────────────

_BODY_RE = re.compile(r'<article class="report-body[^"]*">(.*?)</article>', re.DOTALL)


def extract_sectors_body(html_path: Path) -> str:
    """Extract just the article body from a wrap_report HTML page."""
    html = html_path.read_text(encoding="utf-8")
    m = _BODY_RE.search(html)
    if not m:
        return ""
    body = m.group(1).strip()
    # Strip the reveal/scroll-fade classes that don't make sense outside Lynus
    body = re.sub(r'\sclass="[^"]*scroll-fade[^"]*"', "", body)
    return body


# ─────────────────────────────────────────────────────────
# Main push logic
# ─────────────────────────────────────────────────────────

def build_chip_html(data: dict, window_key: str = "1d", top_n: int = 10) -> str:
    """Render the chip concentration ranking as Lynus-styled HTML for charles1688.

    Shows top N buy + top N sell for one window (default daily).
    """
    win = next((w for w in data.get("windows", []) if w["key"] == window_key), None)
    if not win:
        return ""

    def _row(r: dict, side: str) -> str:
        pct = r.get("concentration_pct", 0)
        sign = "+" if pct >= 0 else ""
        pct_color = "#c84a4a" if pct >= 0 else "#3a8b54"
        bar_width = min(100, abs(pct) * 2)
        bar_color = "rgba(200,74,74,0.55)" if side == "buy" else "rgba(58,139,84,0.55)"
        main_val = r.get("main_buy") if side == "buy" else r.get("main_sell")

        # Price line (close + chg pct) if we have it
        price_html = ""
        close = r.get("close")
        if close is not None:
            chg_pct = r.get("chg_pct") or 0
            chg = r.get("chg") or 0
            p_color = "#c84a4a" if chg_pct >= 0 else "#3a8b54"
            p_sign = "+" if chg_pct >= 0 else ""
            price_html = (
                f'<div style="font-family:monospace;font-size:11px;color:{p_color};margin-top:2px">'
                f'收 {close:,.2f}　{p_sign}{chg:.2f} ({p_sign}{chg_pct:.2f}%)'
                f'</div>'
            )
        # Future returns strip
        future_html = ""
        if any(r.get(k) is not None for k in ("ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d")):
            def _fut(label, v):
                if v is None:
                    return f'<span style="color:#7a7066">{label} —</span>'
                color = "#c84a4a" if v >= 0 else "#3a8b54"
                sign = "+" if v >= 0 else ""
                return f'<span><span style="color:#7a7066">{label}</span> <strong style="color:{color}">{sign}{v:.1f}%</strong></span>'
            future_html = (
                '<div style="display:flex;gap:10px;flex-wrap:wrap;font-family:monospace;font-size:10px;margin-top:3px">'
                + _fut("1d", r.get("ret_1d"))
                + _fut("3d", r.get("ret_3d"))
                + _fut("5d", r.get("ret_5d"))
                + _fut("10d", r.get("ret_10d"))
                + _fut("20d", r.get("ret_20d"))
                + '</div>'
            )

        return f'''
        <tr>
          <td style="padding:8px 4px;width:30px;color:#9a8e76;font-family:monospace;text-align:right;font-size:11px;vertical-align:top">{r.get("rank", "")}</td>
          <td style="padding:8px 6px">
            <div style="font-weight:600;color:#1a1612">
              <span style="font-family:monospace;color:#a8853a;font-size:12px">{r['code']}</span>
              {r.get('name', r['code'])}
            </div>
            <div style="font-family:monospace;font-size:11px;color:#5a4f3e;margin-top:2px">主力{('買' if side == 'buy' else '賣')} {main_val:,} 張 · 成交 {r['vol']:,} 張</div>
            {price_html}
            {future_html}
            <div style="height:3px;background:rgba(26,22,18,0.08);margin-top:4px;border-radius:1px">
              <div style="height:100%;width:{bar_width}%;background:{bar_color}"></div>
            </div>
          </td>
          <td style="padding:8px 4px;text-align:right;font-family:monospace;font-weight:700;color:{pct_color};font-size:14px;white-space:nowrap;vertical-align:top">{sign}{pct:.1f}%</td>
        </tr>
        '''

    parts = []
    parts.append(f'<p style="color:#5a4f3e;font-size:13px;margin:0 0 18px">'
                 f'時段：<strong>{win["label"]}</strong>'
                 f' · 資料區間 {win["start_date"]} → {win["end_date"]}'
                 f' · 過濾成交量 &lt; 1,000 張')
    parts.append(f' · 全市場 {win["total_stocks_after_filter"]:,} 檔')
    parts.append('</p>')

    # Pre-rank lists
    buy_list = win["buy_top"][:top_n]
    sell_list = win["sell_top"][:top_n]
    for i, r in enumerate(buy_list):  r["rank"] = i + 1
    for i, r in enumerate(sell_list): r["rank"] = i + 1

    # Two-column on desktop, stacked on mobile (using a single table with side-by-side wrappers).
    parts.append('<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px">')
    # Buy column
    parts.append('<div>')
    parts.append('<h3 style="font-size:15px;color:#1a1612;border-bottom:1px solid #c9a86c;padding-bottom:6px;margin:0 0 8px">'
                 f'🔴 買超集中 Top {len(buy_list)}</h3>')
    parts.append('<table style="width:100%;border-collapse:collapse">')
    for r in buy_list:
        parts.append(_row(r, "buy"))
    parts.append('</table>')
    parts.append('</div>')
    # Sell column
    parts.append('<div>')
    parts.append('<h3 style="font-size:15px;color:#1a1612;border-bottom:1px solid #c9a86c;padding-bottom:6px;margin:0 0 8px">'
                 f'🟢 賣超集中 Top {len(sell_list)}</h3>')
    parts.append('<table style="width:100%;border-collapse:collapse">')
    for r in sell_list:
        parts.append(_row(r, "sell"))
    parts.append('</table>')
    parts.append('</div>')
    parts.append('</div>')

    # Method note
    parts.append('<p style="color:#9a8e76;font-size:11px;margin:18px 0 0;font-style:italic">'
                 '集中度 = (前 15 大買超分點淨買 − 前 15 大賣超分點淨賣) ÷ 該股總成交量。+/- 越大代表主力越集中。'
                 '</p>')

    return ''.join(parts)


def build_shareholder_div_html(data: dict, top_n: int = 5) -> str:
    """Render shareholder divergence quadrant rankings as Lynus-styled HTML."""
    QM = data["quadrant_meta"]
    rankings = data["quadrant_rankings"]
    # Order: 接盤警訊 first (most actionable warning), then consensus buy, etc.
    order = ["whale_distribute", "consensus_buy", "consensus_sell", "insider_accumulate"]

    def _fut_strip(r: dict) -> str:
        if not any(r.get(k) is not None for k in ("ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d")):
            return ""
        def _f(label, v):
            if v is None:
                return f'<span style="color:#9a8e76">{label} —</span>'
            color = "#c84a4a" if v >= 0 else "#3a8b54"
            sign = "+" if v >= 0 else ""
            return f'<span><span style="color:#9a8e76">{label}</span><strong style="color:{color}">{sign}{v:.1f}%</strong></span>'
        parts = [_f(l, r.get(k)) for l, k in [("1d", "ret_1d"), ("3d", "ret_3d"), ("5d", "ret_5d"), ("10d", "ret_10d"), ("20d", "ret_20d")]]
        return (
            '<div style="display:flex;gap:6px;flex-wrap:wrap;font-family:monospace;font-size:9px;margin-top:2px">'
            + " ".join(parts) + '</div>'
        )

    def _card_row(r: dict) -> str:
        ch = r["close_chg_pct"]
        ch_color = "#c84a4a" if ch >= 0 else "#3a8b54"
        ch_sign = "+" if ch >= 0 else ""
        p400 = r["pct_400up_delta"]
        p400_sign = "+" if p400 >= 0 else ""
        p400_color = "#a8853a" if p400 >= 0 else "#7a7066"
        h1k = r.get("holders_1000up_delta", 0)
        h1k_sign = "+" if h1k >= 0 else ""
        return (
            f'<tr>'
            f'<td style="padding:6px 4px;font-family:monospace;font-size:11px;color:#a8853a;vertical-align:top">{r["code"]}</td>'
            f'<td style="padding:6px 6px;color:#1a1612;font-size:13px">{r["name"]}'
            f'<div style="font-family:monospace;font-size:10px;color:#7a7066;margin-top:2px">'
            f'1000 張人數 {h1k_sign}{h1k}</div>'
            + _fut_strip(r)
            + f'</td>'
            f'<td style="padding:6px 4px;text-align:right;font-family:monospace;font-weight:700;color:{ch_color};font-size:13px;white-space:nowrap;vertical-align:top">{ch_sign}{ch:.1f}%</td>'
            f'<td style="padding:6px 4px;text-align:right;font-family:monospace;font-size:11px;color:{p400_color};white-space:nowrap;vertical-align:top">{p400_sign}{p400:.2f} pp</td>'
            f'</tr>'
        )

    def _card(qkey: str) -> str:
        meta = QM[qkey]
        rows = rankings.get(qkey, [])[:top_n]
        if not rows:
            return ""
        body = "".join(_card_row(r) for r in rows)
        return (
            f'<div style="border:1px solid #d4c4a8;padding:14px 14px 10px;background:#faf6ed">'
            f'<div style="border-bottom:1px solid {meta["color"]};padding-bottom:8px;margin-bottom:10px">'
            f'<div style="font-size:14px;font-weight:700;color:{meta["color"]}">{meta["icon"]} {meta["label"]}</div>'
            f'<div style="font-size:11px;color:#7a7066;margin-top:2px">{meta["desc"]}</div></div>'
            f'<table style="width:100%;border-collapse:collapse">'
            f'<thead><tr style="font-size:10px;color:#9a8e76;border-bottom:1px dashed #d4c4a8">'
            f'<th style="text-align:left;padding:4px 4px">代號</th>'
            f'<th style="text-align:left;padding:4px 4px">名稱</th>'
            f'<th style="text-align:right;padding:4px 4px">股價</th>'
            f'<th style="text-align:right;padding:4px 4px">大戶</th>'
            f'</tr></thead>'
            f'<tbody>{body}</tbody>'
            f'</table></div>'
        )

    cards = "".join(_card(q) for q in order)
    intro = (
        f'<p style="color:#5a4f3e;font-size:13px;margin:0 0 14px">'
        f'資料源：集保 TDCC 週對週 · {data["tdcc_prev"]} → {data["tdcc_latest"]}'
        f' · 全市場 {data["stats"]["total_stocks"]:,} 檔（已排除 ETF）'
        f'</p>'
    )
    grid = (
        f'<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">{cards}</div>'
    )
    note = (
        f'<p style="color:#9a8e76;font-size:11px;margin:14px 0 0;font-style:italic">'
        f'「大戶」= 400 張以上持股人數比例。「接盤警訊」= 股價漲但大戶減碼（主力可能倒貨）。'
        f'「內部佈局」= 股價跌但大戶逆勢加碼（可能提早卡位）。'
        f'</p>'
    )
    return intro + grid + note


def build_tri_lamp_html(data: dict, top_n: int = 8) -> str:
    """Render tri-source lamp groups as charles1688 light-themed HTML."""
    def lamp(v: int) -> str:
        color = "#c84a4a" if v > 0 else "#3a8b54" if v < 0 else "#c0b89e"
        return f'<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{color};margin-right:3px"></span>'

    def num_html(v: float, digits: int, suffix: str = "") -> str:
        color = "#c84a4a" if v > 0 else "#3a8b54" if v < 0 else "#7a7066"
        sign = "+" if v >= 0 else ""
        return f'<span style="color:{color};font-weight:600">{sign}{v:.{digits}f}{suffix}</span>'

    def group_section(title: str, color: str, desc: str, rows: list, fallback: str) -> str:
        if not rows:
            return (
                f'<div style="margin:0 0 20px"><h3 style="font-size:14px;color:{color};border-bottom:1px solid {color};padding-bottom:6px;margin:0 0 8px">{title}</h3>'
                f'<p style="color:#7a7066;font-size:11px;font-style:italic;margin:8px 0 0">{fallback}</p></div>'
            )
        def _fut_cell(v):
            if v is None:
                return '<td style="padding:6px 4px;text-align:right;font-family:monospace;color:#9a8e76;font-size:10px">—</td>'
            c = "#c84a4a" if v >= 0 else "#3a8b54"
            sign = "+" if v >= 0 else ""
            return f'<td style="padding:6px 4px;text-align:right;font-family:monospace;color:{c};font-size:10px;font-weight:600">{sign}{v:.1f}%</td>'
        items = []
        for r in rows[:top_n]:
            ch = r.get("chg")
            chp = r.get("chg_pct")
            close = r.get("close")
            close_str = f"{close:,.2f}" if close is not None else "—"
            chg_str = "—" if ch is None else f'<span style="color:{"#c84a4a" if ch >= 0 else "#3a8b54"}">{"+" if ch >= 0 else ""}{ch:.2f}</span>'
            chgpct_str = "—" if chp is None else f'<span style="color:{"#c84a4a" if chp >= 0 else "#3a8b54"}">{"+" if chp >= 0 else ""}{chp:.2f}%</span>'
            items.append(
                f'<tr>'
                f'<td style="padding:6px 4px;white-space:nowrap">{lamp(r["lamp1"])}{lamp(r["lamp2"])}{lamp(r["lamp3"])}</td>'
                f'<td style="padding:6px 4px;font-family:monospace;font-size:11px;color:#a8853a">{r["code"]}</td>'
                f'<td style="padding:6px 6px;font-size:13px;color:#1a1612">{r["name"]}</td>'
                f'<td style="padding:6px 4px;text-align:right;font-family:monospace">{num_html(r["concentration_pct"], 1, "%")}</td>'
                f'<td style="padding:6px 4px;text-align:right;font-family:monospace">{num_html(r["pct_400up_delta"], 2, "pp")}</td>'
                f'<td style="padding:6px 4px;text-align:right;font-family:monospace">{num_html(r["total_lots"], 0)}</td>'
                f'<td style="padding:6px 4px;text-align:right;font-family:monospace;font-size:11px">{close_str}</td>'
                f'<td style="padding:6px 4px;text-align:right;font-family:monospace;font-size:11px">{chgpct_str}</td>'
                + _fut_cell(r.get("ret_1d"))
                + _fut_cell(r.get("ret_3d"))
                + _fut_cell(r.get("ret_5d"))
                + _fut_cell(r.get("ret_10d"))
                + _fut_cell(r.get("ret_20d"))
                + f'</tr>'
            )
        return (
            f'<div style="margin:0 0 20px">'
            f'<h3 style="font-size:14px;color:{color};border-bottom:1px solid {color};padding-bottom:6px;margin:0 0 6px">{title}</h3>'
            f'<p style="color:#7a7066;font-size:11px;margin:0 0 8px">{desc}</p>'
            f'<table style="width:100%;border-collapse:collapse;font-size:12px">'
            f'<thead><tr style="font-size:10px;color:#9a8e76;border-bottom:1px dashed #d4c4a8">'
            f'<th style="padding:4px 4px;text-align:left">燈</th>'
            f'<th style="padding:4px 4px;text-align:left">代號</th>'
            f'<th style="padding:4px 4px;text-align:left">名稱</th>'
            f'<th style="padding:4px 4px;text-align:right">集中度</th>'
            f'<th style="padding:4px 4px;text-align:right">大戶 ΔΔ</th>'
            f'<th style="padding:4px 4px;text-align:right">法人 5d</th>'
            f'<th style="padding:4px 4px;text-align:right">收盤</th>'
            f'<th style="padding:4px 4px;text-align:right">漲跌%</th>'
            f'<th style="padding:4px 4px;text-align:right">1日後</th>'
            f'<th style="padding:4px 4px;text-align:right">3日後</th>'
            f'<th style="padding:4px 4px;text-align:right">5日後</th>'
            f'<th style="padding:4px 4px;text-align:right">10日後</th>'
            f'<th style="padding:4px 4px;text-align:right">20日後</th>'
            f'</tr></thead><tbody>'
            + "".join(items)
            + '</tbody></table></div>'
        )

    intro = (
        f'<p style="color:#5a4f3e;font-size:13px;margin:0 0 12px">'
        f'分點集中度 5d × 大股東 400 張週對週 × 三大法人 5d 合計 · 全市場 {data["stats"]["total_merged"]:,} 檔（已排除 ETF）'
        f'</p>'
    )
    return intro + group_section(
        "🔴 三線同買 (強訊號)",
        "#c84a4a",
        "分點主力買 + 大戶加碼 + 法人買 — 三盞同色為強多訊號",
        data["triple_buy"],
        f"（本期無三線同買名單，{data['stats']['triple_buy_count']} 檔）",
    ) + group_section(
        "🟢 三線同賣 (強訊號)",
        "#3a8b54",
        "分點主力賣 + 大戶減碼 + 法人賣 — 三盞同色為強空訊號",
        data["triple_sell"],
        f"（本期無三線同賣名單，{data['stats']['triple_sell_count']} 檔）",
    ) + group_section(
        "🟡 兩線偏多 (次強訊號)",
        "#c9a86c",
        "三燈中兩燈紅一燈灰 — 共識偏多但未滿",
        data["partial_buy"],
        "（本期無兩線偏多名單）",
    ) + group_section(
        "🟡 兩線偏空 (次強訊號)",
        "#7a8b3a",
        "三燈中兩燈綠一燈灰 — 共識偏空但未滿",
        data["partial_sell"],
        "（本期無兩線偏空名單）",
    ) + (
        f'<p style="color:#9a8e76;font-size:11px;margin:18px 0 0;font-style:italic">'
        f'門檻：燈 1 集中度 ±20% · 燈 2 大戶變化 ±0.5pp · 燈 3 法人 5d 累計 ±1000 張'
        f'</p>'
    )


def push_chips_history(days: int = 30) -> int:
    """
    把 backfill 出來的 dated JSON 全部 push 成 charles1688 看板上一張張歷史卡。
    流程：
      1. clear_all(pipeline='籌碼')  ← 砍掉現有所有「籌碼」分類卡
      2. 對 chip_concentration / shareholder_divergence / tri_source_lamp 三個視圖各別：
         讀 assets/chip_history/<view>/_index.json → 取 days 個日期 → 每個日期一張卡
    """
    HISTORY_BASE = LYNUS_ROOT / "assets" / "chip_history"
    n_total = 0

    print("  [STEP 1] clear pipeline='籌碼'")
    deleted = clear_all(pipeline="籌碼")
    print(f"    deleted {deleted} old chip cards")

    # ───── chip_concentration ─────
    cc_idx = HISTORY_BASE / "chip_concentration" / "_index.json"
    if cc_idx.exists():
        cc_dates = json.loads(cc_idx.read_text(encoding="utf-8")).get("dates", [])[:days]
        print(f"  [STEP 2] chip_concentration: {len(cc_dates)} dated cards")
        for d in cc_dates:
            try:
                data = json.loads((cc_idx.parent / f"{d}.json").read_text(encoding="utf-8"))
                html = build_chip_html(data, window_key="1d", top_n=10)
                if not html:
                    continue
                daily = next((w for w in data.get("windows", []) if w["key"] == "1d"), None)
                if not daily or not daily.get("buy_top"):
                    continue
                top_buy = daily["buy_top"][0]
                top_sell = daily["sell_top"][0]
                stats = [
                    {"label": "今日 Top 買", "value": f"{top_buy['code']} +{top_buy['concentration_pct']:.0f}%", "color": "up"},
                    {"label": "今日 Top 賣", "value": f"{top_sell['code']} {top_sell['concentration_pct']:.0f}%", "color": "down"},
                    {"label": "有效股數", "value": f"{daily['total_stocks_after_filter']:,}", "color": "neutral"},
                ]
                ok = post(
                    pipeline="籌碼", subpipeline="主力進出",
                    title=f"{d} 籌碼集中度 · 分點 Top 10 排行",
                    content=html, content_type="html", ts=d,
                    stats=stats, tags=["籌碼", "分點", "集中度"],
                )
                if ok:
                    n_total += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"    [ERR] {d}: {e}")

    # ───── shareholder_divergence ─────
    sd_idx = HISTORY_BASE / "shareholder_divergence" / "_index.json"
    if sd_idx.exists():
        sd_dates = json.loads(sd_idx.read_text(encoding="utf-8")).get("dates", [])[:days]
        print(f"  [STEP 3] shareholder_divergence: {len(sd_dates)} dated cards")
        for d in sd_dates:
            try:
                data = json.loads((sd_idx.parent / f"{d}.json").read_text(encoding="utf-8"))
                html = build_shareholder_div_html(data, top_n=5)
                stats = [
                    {"label": "接盤警訊", "value": f"{data['stats']['by_quadrant']['whale_distribute']} 檔", "color": "down"},
                    {"label": "共識買進", "value": f"{data['stats']['by_quadrant']['consensus_buy']} 檔", "color": "up"},
                    {"label": "內部佈局", "value": f"{data['stats']['by_quadrant']['insider_accumulate']} 檔", "color": "neutral"},
                ]
                ok = post(
                    pipeline="籌碼", subpipeline="主力進出",
                    title=f"{d} 大股東 vs 股價背離掃描 · 4 象限 Top 5",
                    content=html, content_type="html", ts=d,
                    stats=stats, tags=["籌碼", "大股東", "TDCC", "背離"],
                )
                if ok:
                    n_total += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"    [ERR] {d}: {e}")

    # ───── tri_source_lamp ─────
    ts_idx = HISTORY_BASE / "tri_source_lamp" / "_index.json"
    if ts_idx.exists():
        ts_dates = json.loads(ts_idx.read_text(encoding="utf-8")).get("dates", [])[:days]
        print(f"  [STEP 4] tri_source_lamp: {len(ts_dates)} dated cards")
        for d in ts_dates:
            try:
                data = json.loads((ts_idx.parent / f"{d}.json").read_text(encoding="utf-8"))
                html = build_tri_lamp_html(data, top_n=8)
                s = data["stats"]
                stats = [
                    {"label": "三線同買", "value": f"{s['triple_buy_count']} 檔", "color": "up"},
                    {"label": "三線同賣", "value": f"{s['triple_sell_count']} 檔", "color": "down"},
                    {"label": "兩線偏多", "value": f"{s['partial_buy_count']} 檔", "color": "neutral"},
                ]
                ok = post(
                    pipeline="籌碼", subpipeline="主力進出",
                    title=f"{d} 三源燈號 · 分點×大股東×法人",
                    content=html, content_type="html", ts=d,
                    stats=stats, tags=["籌碼", "三源", "燈號"],
                )
                if ok:
                    n_total += 1
                time.sleep(0.2)
            except Exception as e:
                print(f"    [ERR] {d}: {e}")

    print(f"  [DONE] pushed {n_total} dated chip cards")
    return n_total


def push_tri_source_lamp() -> int:
    """Push the tri-source lamp view to charles1688."""
    json_path = LYNUS_ROOT / "assets" / "tri_source_lamp.json"
    if not json_path.exists():
        print("  [SKIP] tri_source_lamp.json not found — run build_view4_tri_source_lamp.py first")
        return 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not data.get("triple_buy") and not data.get("triple_sell") and not data.get("partial_buy") and not data.get("partial_sell"):
        print("  [SKIP] all lamp groups empty")
        return 0

    html = build_tri_lamp_html(data, top_n=8)
    s = data["stats"]
    stats = [
        {"label": "三線同買", "value": f"{s['triple_buy_count']} 檔", "color": "up"},
        {"label": "三線同賣", "value": f"{s['triple_sell_count']} 檔", "color": "down"},
        {"label": "兩線偏多", "value": f"{s['partial_buy_count']} 檔", "color": "neutral"},
    ]
    ok = post(
        pipeline="籌碼",
        subpipeline="主力進出",
        title=f"{data['broker_end']} 三源燈號 · 分點×大股東×法人",
        content=html,
        content_type="html",
        ts=data["broker_end"],
        stats=stats,
        tags=["籌碼", "三源", "燈號", "分點", "大股東", "法人"],
    )
    if ok:
        print(f"  [OK] tri-source-lamp  三線同買 {s['triple_buy_count']} 檔 / 三線同賣 {s['triple_sell_count']} 檔")
        return 1
    print("  [FAIL] tri-source-lamp push")
    return 0


def push_shareholder_divergence() -> int:
    """Push the shareholder vs price divergence view to charles1688."""
    json_path = LYNUS_ROOT / "assets" / "shareholder_divergence.json"
    if not json_path.exists():
        print("  [SKIP] shareholder_divergence.json not found — run build_view2_shareholder_divergence.py first")
        return 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    if not data.get("quadrant_rankings"):
        print("  [SKIP] empty rankings")
        return 0

    html = build_shareholder_div_html(data, top_n=5)
    stats = [
        {"label": "接盤警訊", "value": f"{data['stats']['by_quadrant']['whale_distribute']} 檔",
         "color": "down"},
        {"label": "共識買進", "value": f"{data['stats']['by_quadrant']['consensus_buy']} 檔",
         "color": "up"},
        {"label": "內部佈局", "value": f"{data['stats']['by_quadrant']['insider_accumulate']} 檔",
         "color": "neutral"},
    ]
    ok = post(
        pipeline="籌碼",
        subpipeline="主力進出",
        title=f"{data['tdcc_latest']} 大股東 vs 股價背離掃描 · 4 象限 Top 5",
        content=html,
        content_type="html",
        ts=data["tdcc_latest"],
        stats=stats,
        tags=["籌碼", "大股東", "TDCC", "背離"],
    )
    if ok:
        print(f"  [OK] shareholder-divergence  接盤警訊 {data['stats']['by_quadrant']['whale_distribute']} 檔 / 共識買 {data['stats']['by_quadrant']['consensus_buy']} 檔")
        return 1
    print("  [FAIL] shareholder-divergence push")
    return 0


def push_chip_concentration() -> int:
    """Push the chip concentration ranking (daily) to charles1688."""
    json_path = LYNUS_ROOT / "assets" / "chip_concentration.json"
    if not json_path.exists():
        print("  [SKIP] chip_concentration.json not found — run build_chip_concentration.py first")
        return 0
    data = json.loads(json_path.read_text(encoding="utf-8"))
    daily = next((w for w in data.get("windows", []) if w["key"] == "1d"), None)
    if not daily or not daily.get("buy_top"):
        print("  [SKIP] no daily data")
        return 0

    html = build_chip_html(data, window_key="1d", top_n=10)
    if not html:
        print("  [SKIP] empty html")
        return 0

    top_buy = daily["buy_top"][0]
    top_sell = daily["sell_top"][0]
    stats = [
        {"label": "今日 Top 買", "value": f"{top_buy['code']} +{top_buy['concentration_pct']:.0f}%",
         "color": "up"},
        {"label": "今日 Top 賣", "value": f"{top_sell['code']} {top_sell['concentration_pct']:.0f}%",
         "color": "down"},
        {"label": "市場有效股數", "value": f"{daily['total_stocks_after_filter']:,}", "color": "neutral"},
    ]
    ok = post(
        pipeline="籌碼",
        subpipeline="主力進出",
        title=f"{daily['end_date']} 籌碼集中度 · 分點 Top 10 排行",
        content=html,
        content_type="html",
        ts=daily["end_date"],
        stats=stats,
        tags=["籌碼", "分點", "集中度"],
    )
    if ok:
        print(f"  [OK] chip-concentration  {top_buy['code']} +{top_buy['concentration_pct']:.0f}% / "
              f"{top_sell['code']} {top_sell['concentration_pct']:.0f}%")
        return 1
    print("  [FAIL] chip-concentration push")
    return 0


def push_taiex_charts() -> int:
    """Push the 3 taiex charts. Returns number successfully pushed."""
    ok = 0
    # subpipeline: market-pulse + turnover → 大盤指標; futures + options → 期貨選擇權
    SUB = {
        "market-pulse":  "大盤指標",
        "turnover":      "大盤指標",
        "futures-basis": "期貨選擇權",
        "options-atm":   "期貨選擇權",
    }
    for name, builder, title_fmt in [
        ("market-pulse",   build_market_pulse_spec,   "市場脈動 · 漲跌停 × 全族群均幅"),
        ("turnover",       build_turnover_spec,       "成交額 · TWSE + TPEx 合計"),
        ("futures-basis",  build_futures_basis_spec,  "期貨正逆價差 · 12 年期現對照"),
        ("options-atm",    build_options_atm_spec,    "選擇權價平和 · 隱含波動代理"),
    ]:
        try:
            result = builder()
            if result is None:
                print(f"  [SKIP] {name}: no data")
                continue
            spec, latest, stats, summary = result
            success = post(
                pipeline="大盤",
                subpipeline=SUB[name],
                title=title_fmt,
                content_type="chart",
                chart_spec=spec,
                summary=summary,
                stats=stats,
                ts=latest["date"],
                tags={"market-pulse":  ["市場脈動", "漲跌停"],
                      "turnover":      ["成交額", "市場規模"],
                      "futures-basis": ["期貨", "基差", "12 年"],
                      "options-atm":   ["選擇權", "TXO", "價平和"]}[name],
            )
            if success:
                ok += 1
                print(f"  [OK] {name}  stats={len(stats)} series_points={len(spec.get('xAxis', {}).get('data', []))}")
            else:
                print(f"  [FAIL] {name}: REST returned non-2xx (check web_push.json + plugin v1.2 installed)")
        except Exception as e:
            print(f"  [ERR] {name}: {e}")
    return ok


def push_sectors_history(since: str | None = None, limit: int | None = None) -> int:
    """Push every wrapped sectors HTML found under E:\\Lynus\\reports\\YYYY-MM-DD\\
    as content_type='html' with Lynus editorial styling."""
    type_label = {"daily": "盤後快報", "weekly": "週報", "rotation": "輪動偵測"}
    pushed = 0
    files = []
    for date_dir in sorted((LYNUS_ROOT / "reports").iterdir()):
        if not date_dir.is_dir() or not re.match(r"^\d{4}-\d{2}-\d{2}$", date_dir.name):
            continue
        if since and date_dir.name < since:
            continue
        for kind in ("daily", "weekly", "rotation"):
            f = date_dir / f"sectors-{kind}.html"
            if f.exists():
                files.append((date_dir.name, kind, f))
    if limit:
        files = files[-limit:]

    print(f"  found {len(files)} sectors files to push")
    # subpipeline 對應
    SUB = {"daily": "每日盤後", "weekly": "每週週報", "rotation": "輪動偵測"}
    for d, kind, path in files:
        body = extract_sectors_body(path)
        if not body:
            print(f"  [SKIP] {d} {kind}: empty body")
            continue
        title = f"{d} 族群{type_label.get(kind, kind)}"
        try:
            ok = post(
                pipeline="族群",
                subpipeline=SUB.get(kind, ""),
                title=title,
                content=body,
                content_type="html",
                ts=d,
                tags=["族群", type_label.get(kind, kind)],
            )
            if ok:
                pushed += 1
                if pushed % 10 == 0:
                    print(f"  ... {pushed}/{len(files)}")
                time.sleep(0.3)  # be polite to WordPress
            else:
                print(f"  [FAIL] {d} {kind}")
        except Exception as e:
            print(f"  [ERR] {d} {kind}: {e}")
    return pushed


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--charts",  action="store_true", help="Push 4 taiex charts.")
    p.add_argument("--sectors", action="store_true", help="Push all sectors history.")
    p.add_argument("--chips",   action="store_true", help="Push chip concentration ranking (daily Top 10).")
    p.add_argument("--view2",   action="store_true", help="Push shareholder vs price divergence (Top 5 per quadrant).")
    p.add_argument("--view4",   action="store_true", help="Push tri-source lamp table (broker × shareholder × institutional).")
    p.add_argument("--chips-history", action="store_true",
                   help="Clear pipeline=籌碼 + push 30 天歷史 dated cards (chip + view2 + view4)")
    p.add_argument("--history-days", type=int, default=30,
                   help="--chips-history 推幾天歷史 (預設 30)")
    p.add_argument("--all",     action="store_true", help="charts + sectors + chips + view2 + view4.")
    p.add_argument("--clear",   action="store_true", help="Delete ALL tg_push first.")
    p.add_argument("--rebuild", action="store_true", help="Clear + push --all.")
    p.add_argument("--since",   type=str, default=None, help="Sectors only: skip dates before YYYY-MM-DD")
    p.add_argument("--limit",   type=int, default=None, help="Limit number of sectors pushes (debug).")
    args = p.parse_args()

    if args.rebuild:
        args.clear = True
        args.all = True
    if args.all:
        args.charts = True
        args.sectors = True
        # chips_history 已涵蓋 chip + view2 + view4 三視圖的最新 + 30 天 dated
        # 不再單獨跑 chips/view2/view4 latest（會被 chips_history clear 洗掉、浪費 API）
        args.chips_history = True

    if args.clear:
        print("=== CLEAR all tg_push ===")
        n = clear_all()
        print(f"  deleted {n} posts" if n >= 0 else "  [FAIL] clear (check token / plugin v1.2)")

    if args.charts:
        print("=== PUSH 3 taiex charts ===")
        n = push_taiex_charts()
        print(f"  pushed {n}/4 charts")

    if args.sectors:
        print("=== PUSH sectors history ===")
        n = push_sectors_history(since=args.since, limit=args.limit)
        print(f"  pushed {n} sectors reports")

    if args.chips:
        print("=== PUSH chip concentration ===")
        n = push_chip_concentration()
        print(f"  pushed {n}/1 chip rankings")

    if args.view2:
        print("=== PUSH shareholder divergence (view 2) ===")
        n = push_shareholder_divergence()
        print(f"  pushed {n}/1 shareholder divergence")

    if args.view4:
        print("=== PUSH tri-source lamp (view 4) ===")
        n = push_tri_source_lamp()
        print(f"  pushed {n}/1 tri-source lamp")

    if args.chips_history:
        print("=== PUSH chips history (clear pipeline=籌碼 + 30 天 dated cards) ===")
        n = push_chips_history(days=args.history_days)
        print(f"  pushed {n} dated chip history cards")

    if not (args.charts or args.sectors or args.chips or args.view2 or args.view4 or args.chips_history or args.clear):
        p.print_help()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
