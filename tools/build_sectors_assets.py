r"""
build_sectors_assets.py
=======================
Convert industry_map's daily / weekly / rotation md reports into Lynus'
Research HTML pages under the `sectors` category.

Reads:
    E:\industry_map\reports\daily_YYYY-MM-DD.md
    E:\industry_map\reports\weekly_YYYY-MM-DD.md
    E:\industry_map\reports\rotation_YYYY-MM-DD.md
    E:\industry_map\reports\focus_YYYY-MM-DD_*.md (optional)

Writes:
    E:\Lynus\reports\YYYY-MM-DD\sectors-{type}.html
    Updates manifest.json with entries.

Idempotent — already-generated dates simply get overwritten with current data.

Usage:
    python tools/build_sectors_assets.py              # backfill ALL md
    python tools/build_sectors_assets.py --since 2026-05-26
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

INDUSTRY_MAP_REPORTS = Path(r"E:\industry_map\reports")
LYNUS_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = LYNUS_ROOT / "tools"

sys.path.insert(0, str(TOOLS_DIR))
from wrap_report import wrap_report  # noqa: E402

TPE = ZoneInfo("Asia/Taipei")

TYPE_LABEL = {
    "daily":    "盤後快報",
    "weekly":   "週報",
    "rotation": "輪動偵測",
    "focus":    "焦點深度",
}

# Strip emoji + enclosed circle numerals from md so the editorial template
# renders clean prose. Same family the wrap_report script uses.
_DECO = re.compile("[\U0001F300-\U0001FAFF☀-➿①-⓿]")


# ── meta extractors per type ──────────────────────────────────────────────

def extract_daily_meta(md: str, date: str) -> dict:
    """Pull stats + lead out of an industry_map daily report."""
    # 共 N 個族群 | 總成交額 X 億
    m = re.search(r"共\s*\*\*(\d+)\*\*\s*個族群\｜總成交額\s*\*\*([\d,\.]+)\s*億", md)
    n_sectors = m.group(1) if m else "—"
    volume    = m.group(2) if m else "—"

    # First 漲幅前 5 entry name
    first_up = re.search(r"## .*漲幅前 5.*?###\s*.\s*(\S+)\s*\+?([\d\.\-]+)%", md, re.DOTALL)
    leader_up = first_up.group(1) if first_up else ""
    leader_up_pct = first_up.group(2) if first_up else ""

    # First 跌幅前 5 entry name
    first_dn = re.search(r"## .*跌幅前 5.*?###\s*.\s*(\S+)\s*([\-\d\.]+)%", md, re.DOTALL)
    leader_dn = first_dn.group(1) if first_dn else ""
    leader_dn_pct = first_dn.group(2) if first_dn else ""

    # 漲多族群 N / 74
    m_share = re.search(r"漲多族群：\*\*(\d+)\s*/\s*(\d+)\*\*", md)
    up_count = m_share.group(1) if m_share else ""
    total    = m_share.group(2) if m_share else ""

    # 全族群平均漲幅
    m_avg = re.search(r"全族群平均漲幅：\*\*([+\-\d\.]+)%", md)
    avg = m_avg.group(1) if m_avg else ""

    # 漲停集中度
    m_conc = re.search(r"漲停集中度.*?=\s*\*\*([\d\.]+)%", md)
    conc = m_conc.group(1) if m_conc else ""

    title = f"{date} 族群盤後快報"
    title_em = (leader_up or "族群") if leader_up_pct else "盤後"
    summary = (
        f"全 {n_sectors} 族群、成交 {volume} 億；領漲 {leader_up} "
        f"+{leader_up_pct}%，領跌 {leader_dn} {leader_dn_pct}%；"
        f"漲多族群 {up_count}/{total}，全族群均漲 {avg}%。"
    )

    stats = [
        {"label": "領漲族群",   "value": f"{leader_up} +{leader_up_pct}%" if leader_up else "—", "color": "up"},
        {"label": "領跌族群",   "value": f"{leader_dn} {leader_dn_pct}%" if leader_dn else "—", "color": "down"},
        {"label": "漲停集中",   "value": f"{conc}%" if conc else "—", "color": "neutral"},
    ]
    tags = []
    for chip in [leader_up, leader_dn, "族群盤後"]:
        if chip and chip not in tags:
            tags.append(chip)

    return {"title": title, "title_em": title_em, "summary": summary, "stats": stats, "tags": tags}


def extract_weekly_meta(md: str, date: str) -> dict:
    """Weekly cumulative — top 5 sectors, top trading focus."""
    # # 📈 族群週報 2026-05-22 ~ 2026-05-28（5 交易日）
    m_range = re.search(r"族群週報\s*(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", md)
    rng = f"{m_range.group(1)} ~ {m_range.group(2)}" if m_range else date

    # First top-gainer
    up = re.search(r"5 日累計漲幅前 5.*?###\s*\d+\.\s*(\S+)\s*\+?([\d\.\-]+)%", md, re.DOTALL)
    leader_up = up.group(1) if up else ""
    leader_up_pct = up.group(2) if up else ""

    # First top-loser
    dn = re.search(r"5 日累計跌幅前 5.*?###\s*\d+\.\s*(\S+)\s*([\-\d\.]+)%", md, re.DOTALL)
    leader_dn = dn.group(1) if dn else ""
    leader_dn_pct = dn.group(2) if dn else ""

    # First 資金焦點
    money = re.search(r"日均成交額前 5.*?###\s*\d+\.\s*(\S+)", md, re.DOTALL)
    money_leader = money.group(1) if money else ""

    title = f"{date} 族群週報"
    title_em = leader_up or "週報"
    summary = (
        f"涵蓋 {rng}。週領漲 {leader_up} +{leader_up_pct}%，"
        f"週領跌 {leader_dn} {leader_dn_pct}%；資金焦點 {money_leader}。"
    )

    stats = [
        {"label": "週領漲", "value": f"{leader_up} +{leader_up_pct}%" if leader_up else "—", "color": "up"},
        {"label": "週領跌", "value": f"{leader_dn} {leader_dn_pct}%" if leader_dn else "—", "color": "down"},
        {"label": "資金焦點", "value": money_leader or "—", "color": "neutral"},
    ]
    tags = [c for c in (leader_up, leader_dn, money_leader, "週報") if c]

    return {"title": title, "title_em": title_em, "summary": summary, "stats": stats, "tags": tags}


def extract_rotation_meta(md: str, date: str) -> dict:
    """Rotation — sudden strong / sudden weak relative to N-day average."""
    # First 突然轉強
    up = re.search(r"突然轉強前 5.*?###\s*\d+\.\s*(\S+)\s*差距\s*\*\*\+?([\d\.\-]+)%", md, re.DOTALL)
    leader_up = up.group(1) if up else ""
    leader_up_pct = up.group(2) if up else ""

    # First 突然轉弱
    dn = re.search(r"突然轉弱前 5.*?###\s*\d+\.\s*(\S+)\s*差距\s*\*\*([\-\d\.]+)%", md, re.DOTALL)
    leader_dn = dn.group(1) if dn else ""
    leader_dn_pct = dn.group(2) if dn else ""

    title = f"{date} 類股輪動偵測"
    title_em = leader_up or "輪動"
    summary = (
        f"最大轉強 {leader_up} +{leader_up_pct}%（vs 5 日均），"
        f"最大轉弱 {leader_dn} {leader_dn_pct}%。"
    )

    stats = [
        {"label": "最大轉強", "value": f"{leader_up} +{leader_up_pct}%" if leader_up else "—", "color": "up"},
        {"label": "最大轉弱", "value": f"{leader_dn} {leader_dn_pct}%" if leader_dn else "—", "color": "down"},
        {"label": "對比基準", "value": "5 日均", "color": "neutral"},
    ]
    tags = [c for c in (leader_up, leader_dn, "輪動") if c]

    return {"title": title, "title_em": title_em, "summary": summary, "stats": stats, "tags": tags}


def extract_focus_meta(md: str, date: str, slug: str) -> dict:
    """Focus deep-dive on one sector."""
    title = f"{date} {slug} 焦點"
    title_em = slug
    summary = f"族群焦點深度分析：{slug}。逐日表現 + 成員清單。"
    stats = []
    tags = [slug, "焦點"]
    return {"title": title, "title_em": title_em, "summary": summary, "stats": stats, "tags": tags}


META_EXTRACTORS = {
    "daily":    extract_daily_meta,
    "weekly":   extract_weekly_meta,
    "rotation": extract_rotation_meta,
}



_WEEKLY_DATA_RE = re.compile(r"<!--\s*sector-weekly-data\s*(\{.*?\})\s*-->", re.DOTALL)


def _h(value) -> str:
    return html.escape(str(value), quote=True)


def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _fmt_pct(value) -> str:
    return f"{_num(value):+.2f}%"


def _fmt_money(value) -> str:
    v = _num(value)
    if v <= 0:
        return "&#8212;"
    return f"{v / 1e8:.1f} &#x5104;"


def _tone_class(value) -> str:
    v = _num(value)
    if v > 0:
        return "num-up"
    if v < 0:
        return "num-down"
    return "num-neutral"


def _heat_style(value) -> str:
    v = max(-20.0, min(20.0, _num(value)))
    alpha = 0.14 + min(abs(v) / 20.0, 1.0) * 0.5
    if v >= 0:
        return f"--heat-bg: rgba(200, 48, 48, {alpha:.3f}); --heat-border: rgba(200, 48, 48, 0.42);"
    return f"--heat-bg: rgba(29, 140, 74, {alpha:.3f}); --heat-border: rgba(29, 140, 74, 0.42);"


def _rhythm_html(row: dict) -> str:
    marks = []
    for value in row.get("rhythm", []):
        if value > 0:
            marks.append('<span class="sector-rhythm__dot sector-rhythm__dot--up">&uarr;</span>')
        elif value < 0:
            marks.append('<span class="sector-rhythm__dot sector-rhythm__dot--down">&darr;</span>')
        else:
            marks.append('<span class="sector-rhythm__dot sector-rhythm__dot--flat">&middot;</span>')
    return '<span class="sector-rhythm" aria-label="5 &#x65e5;&#x7bc0;&#x594f;">' + "".join(marks) + "</span>"


def _member_summary(row: dict, preview=6) -> str:
    members = row.get("members") or []
    prefix = "&#x6210;&#x5206;&#x80a1;&#xff1a;"
    if not members:
        return prefix + "&#8212;"
    shown = "&#x3001;".join(_h(m) for m in members[:preview])
    if len(members) > preview:
        return f"{prefix}{shown}... &#x5171; {len(members)} &#x6a94;"
    return f"{prefix}{shown}"


def _member_details(row: dict) -> str:
    members = row.get("members") or []
    if not members:
        return '<p class="sector-card__members">&#x6210;&#x5206;&#x80a1;&#xff1a;&#8212;</p>'
    chips = "".join(f'<span>{_h(m)}</span>' for m in members)
    return ('<details class="sector-members">' f'<summary>{_member_summary(row)}</summary>' f'<div class="sector-members__chips">{chips}</div>' '</details>')


def _tag_html(row: dict, turnover_pct: float) -> str:
    tags = []
    chg = _num(row.get("cum_chg"))
    n_stocks = int(row.get("n_stocks") or len(row.get("members") or []) or 0)
    if chg >= 3:
        tags.append("&#x50f9;&#x5f37;")
    elif chg <= -3:
        tags.append("&#x50f9;&#x5f31;")
    else:
        tags.append("&#x4e2d;&#x6027;&#x9707;&#x76ea;")
    if turnover_pct >= 80:
        tags.append("&#x91cf;&#x5927;")
    elif turnover_pct >= 55:
        tags.append("&#x6210;&#x4ea4;&#x4e2d;&#x9ad8;")
    else:
        tags.append("&#x91cf;&#x80fd;&#x666e;&#x901a;")
    if chg < 0 and turnover_pct >= 80:
        tags.append("&#x91cf;&#x5927;&#x50f9;&#x8dcc;")
    elif chg > 0 and turnover_pct >= 80:
        tags.append("&#x50f9;&#x91cf;&#x9f4a;&#x63da;")
    if n_stocks <= 5:
        tags.append("&#x6210;&#x5206;&#x96c6;&#x4e2d;")
    return "".join(f'<span>{t}</span>' for t in tags[:4])


def _treemap(items: list[dict], x=0.0, y=0.0, w=100.0, h=100.0) -> list[tuple[dict, float, float, float, float]]:
    items = [i for i in items if _num(i.get("avg_turnover")) > 0]
    if not items:
        return []
    if len(items) == 1:
        return [(items[0], x, y, w, h)]
    total = sum(_num(i.get("avg_turnover")) for i in items)
    acc = 0.0
    split = 1
    for idx, item in enumerate(items, 1):
        acc += _num(item.get("avg_turnover"))
        split = idx
        if acc >= total / 2:
            break
    a, b = items[:split], items[split:]
    a_total = sum(_num(i.get("avg_turnover")) for i in a)
    if not b:
        return [(i, x, y, w / len(items), h) for i in items]
    if w >= h:
        aw = w * a_total / total
        return _treemap(a, x, y, aw, h) + _treemap(b, x + aw, y, w - aw, h)
    ah = h * a_total / total
    return _treemap(a, x, y, w, ah) + _treemap(b, x, y + ah, w, h - ah)


def _sector_card(row: dict, rank: int, turnover_pct: float, variant: str) -> str:
    tone = _tone_class(row.get("cum_chg"))
    return f"""
    <article class="sector-card sector-card--{variant}">
      <div class="sector-card__head"><span class="sector-card__rank">#{rank}</span><strong>{_h(row.get("name", ""))}</strong><em class="{tone}">{_fmt_pct(row.get("cum_chg"))}</em></div>
      <div class="sector-card__meta"><span>&#x65e5;&#x5747;&#x6210;&#x4ea4;&#x984d; <strong>{_fmt_money(row.get("avg_turnover"))}</strong></span><span>5 &#x65e5;&#x7bc0;&#x594f; {_rhythm_html(row)}</span><span>&#x6f32;/&#x8dcc;&#x65e5; <strong>{int(row.get("up_days", 0))}/{int(row.get("down_days", 0))}</strong></span></div>
      {_member_details(row)}<div class="sector-tags">{_tag_html(row, turnover_pct)}</div>
    </article>"""


def _render_enhanced_weekly_md(md_text: str) -> str:
    match = _WEEKLY_DATA_RE.search(md_text)
    if not match:
        return md_text
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return _WEEKLY_DATA_RE.sub("", md_text)
    sectors = data.get("sectors") or []
    if not sectors:
        return _WEEKLY_DATA_RE.sub("", md_text)
    sectors = sorted(sectors, key=lambda r: _num(r.get("cum_chg")), reverse=True)
    total = len(sectors)
    turnovers = sorted((_num(r.get("avg_turnover")) for r in sectors), reverse=True)
    turnover_sum = sum(turnovers) or 1.0
    turnover_ranked = sorted(sectors, key=lambda r: _num(r.get("avg_turnover")))
    turnover_pct = {id(r): (i / max(total - 1, 1)) * 100 for i, r in enumerate(turnover_ranked)}
    up_count = sum(1 for r in sectors if _num(r.get("cum_chg")) > 0)
    down_count = sum(1 for r in sectors if _num(r.get("cum_chg")) < 0)
    chgs = sorted(_num(r.get("cum_chg")) for r in sectors)
    mid = total // 2
    median = chgs[mid] if total % 2 else (chgs[mid - 1] + chgs[mid]) / 2
    concentration = sum(turnovers[:5]) / turnover_sum * 100
    strongest = max(sectors, key=lambda r: _num(r.get("cum_chg")))
    weakest = min(sectors, key=lambda r: _num(r.get("cum_chg")))
    money = max(sectors, key=lambda r: _num(r.get("avg_turnover")))
    if median > 1 and up_count / total >= 0.6:
        conclusion = "&#x5e02;&#x5834;&#x504f;&#x591a;&#x64f4;&#x6563;&#xff0c;&#x5f37;&#x52e2;&#x4e0d;&#x662f;&#x53ea;&#x96c6;&#x4e2d;&#x5728;&#x5c11;&#x6578;&#x65cf;&#x7fa4;&#x3002;"
    elif median < -1 and down_count / total >= 0.6:
        conclusion = "&#x5e02;&#x5834;&#x504f;&#x7a7a;&#x4fee;&#x6b63;&#xff0c;&#x8cc7;&#x91d1;&#x7126;&#x9ede;&#x8207;&#x50f9;&#x683c;&#x8868;&#x73fe;&#x51fa;&#x73fe;&#x5206;&#x6b67;&#x3002;"
    elif concentration >= 45:
        conclusion = "&#x6210;&#x4ea4;&#x984d;&#x9ad8;&#x5ea6;&#x96c6;&#x4e2d;&#xff0c;&#x9069;&#x5408;&#x512a;&#x5148;&#x89c0;&#x5bdf;&#x5927;&#x578b;&#x8cc7;&#x91d1;&#x5340;&#x7684;&#x5f37;&#x5f31;&#x8b8a;&#x5316;&#x3002;"
    else:
        conclusion = "&#x76e4;&#x9762;&#x9707;&#x76ea;&#x5206;&#x6b67;&#xff0c;&#x65cf;&#x7fa4;&#x8f2a;&#x52d5;&#x901f;&#x5ea6;&#x9ad8;&#x65bc;&#x6574;&#x9ad4;&#x8da8;&#x52e2;&#x3002;"
    bins = [(-10**9, -15, "<-15%"), (-15, -10, "-15~-10%"), (-10, -5, "-10~-5%"), (-5, 0, "-5~0%"), (0, 5, "0~5%"), (5, 10, "5~10%"), (10, 10**9, ">10%")]
    dist_rows, max_bin = [], 1
    for lo, hi, label in bins:
        count = sum(1 for r in sectors if _num(r.get("cum_chg")) >= lo and _num(r.get("cum_chg")) < hi)
        max_bin = max(max_bin, count)
        dist_rows.append((label, count))
    dist_html = "".join(f'<div class="sector-dist__row"><span>{_h(label)}</span><div><i style="width:{count / max_bin * 100:.1f}%"></i></div><em>{count}</em></div>' for label, count in dist_rows)
    gainers = sectors[:5]
    losers = sorted(sectors, key=lambda r: _num(r.get("cum_chg")))[:5]
    max_abs = max(abs(_num(r.get("cum_chg"))) for r in gainers + losers) or 1.0
    diverging_rows = []
    for i in range(5):
        g, l = gainers[i], losers[i]
        diverging_rows.append(f"""<div class="sector-rank-row"><div class="sector-rank-row__left"><span>{_h(l.get("name"))}</span><i style="width:{abs(_num(l.get("cum_chg"))) / max_abs * 100:.1f}%"></i><em class="num-down">{_fmt_pct(l.get("cum_chg"))}&#xff5c;{_fmt_money(l.get("avg_turnover"))}</em></div><div class="sector-rank-row__right"><span>{_h(g.get("name"))}</span><i style="width:{abs(_num(g.get("cum_chg"))) / max_abs * 100:.1f}%"></i><em class="num-up">{_fmt_pct(g.get("cum_chg"))}&#xff5c;{_fmt_money(g.get("avg_turnover"))}</em></div></div>""")
    max_members = max((int(r.get("n_stocks") or len(r.get("members") or []) or 1) for r in sectors), default=1)
    scatter_abs = max(max(abs(_num(r.get("cum_chg"))) for r in sectors), 5.0)
    label_names = {r["name"] for r in gainers[:2] + losers[:2] + sorted(sectors, key=lambda r: _num(r.get("avg_turnover")), reverse=True)[:5]}
    points = []
    for r in sectors:
        x = (_num(r.get("cum_chg")) + scatter_abs) / (2 * scatter_abs) * 100
        y = turnover_pct[id(r)]
        n = int(r.get("n_stocks") or len(r.get("members") or []) or 1)
        size = 10 + (n / max_members) ** 0.5 * 22
        label = f'<span>{_h(r.get("name"))}</span>' if r.get("name") in label_names else ""
        tone = "up" if _num(r.get("cum_chg")) > 0 else ("down" if _num(r.get("cum_chg")) < 0 else "flat")
        points.append(f'<b class="sector-scatter__point sector-scatter__point--{tone}" style="left:{x:.2f}%; bottom:{y:.2f}%; width:{size:.1f}px; height:{size:.1f}px" title="{_h(r.get("name"))} {_fmt_pct(r.get("cum_chg"))}&#xff5c;{_fmt_money(r.get("avg_turnover"))}">{label}</b>')
    heat_items = sorted(sectors, key=lambda r: _num(r.get("avg_turnover")), reverse=True)[:18]
    tiles = []
    for r, x, y, w, h in _treemap(heat_items):
        tiles.append(f"""<div class="sector-treemap__tile" style="left:{x:.3f}%; top:{y:.3f}%; width:{w:.3f}%; height:{h:.3f}%; {_heat_style(r.get("cum_chg"))}"><strong>{_h(r.get("name"))}</strong><span>{_fmt_pct(r.get("cum_chg"))}</span><em>{_fmt_money(r.get("avg_turnover"))}</em></div>""")
    money_top = sorted(sectors, key=lambda r: _num(r.get("avg_turnover")), reverse=True)[:5]
    card_groups = [("&#x6f32;&#x5e45;&#x524d; 5", gainers, "up"), ("&#x8dcc;&#x5e45;&#x524d; 5", losers, "down"), ("&#x8cc7;&#x91d1;&#x7126;&#x9ede;&#x524d; 5", money_top, "money")]
    cards_html = "".join(f'<section class="sector-card-group"><h3>{title}</h3><div class="sector-card-grid">' + "".join(_sector_card(r, i, turnover_pct[id(r)], variant) for i, r in enumerate(rows, 1)) + '</div></section>' for title, rows, variant in card_groups)
    h1 = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    title = h1.group(0) if h1 else f"# &#x65cf;&#x7fa4;&#x9031;&#x5831; {data.get('end_date', '')}"
    html_block = f"""
<section class="sector-weekly"><section class="sector-thermo"><div class="sector-section-head"><span>&#x4e00;&#x3001;&#x672c;&#x9031;&#x5e02;&#x5834;&#x7e3d;&#x89bd;</span><h2>&#x672c;&#x9031;&#x65cf;&#x7fa4;&#x6eab;&#x5ea6;&#x8a08;</h2><p>{conclusion}</p></div><div class="sector-thermo__grid"><div><span>&#x6db5;&#x84cb;&#x65cf;&#x7fa4;</span><strong>{total}</strong></div><div><span>&#x4e0a;&#x6f32;&#x65cf;&#x7fa4;</span><strong class="num-up">{up_count} / {total}</strong></div><div><span>&#x4e0b;&#x8dcc;&#x65cf;&#x7fa4;</span><strong class="num-down">{down_count} / {total}</strong></div><div><span>5 &#x65e5;&#x6f32;&#x8dcc;&#x5e45;&#x4e2d;&#x4f4d;&#x6578;</span><strong class="{_tone_class(median)}">{_fmt_pct(median)}</strong></div><div><span>&#x6210;&#x4ea4;&#x984d;&#x96c6;&#x4e2d;&#x5ea6;</span><strong>{concentration:.1f}%</strong></div><div><span>&#x6700;&#x5f37;&#x65cf;&#x7fa4;</span><strong class="num-up">{_h(strongest.get('name'))} {_fmt_pct(strongest.get('cum_chg'))}</strong></div><div><span>&#x6700;&#x5f31;&#x65cf;&#x7fa4;</span><strong class="num-down">{_h(weakest.get('name'))} {_fmt_pct(weakest.get('cum_chg'))}</strong></div><div><span>&#x8cc7;&#x91d1;&#x7126;&#x9ede;</span><strong>{_h(money.get('name'))} {_fmt_money(money.get('avg_turnover'))}</strong></div></div></section>
<section class="sector-panel"><div class="sector-section-head"><span>&#x4e8c;&#x3001;&#x65cf;&#x7fa4;&#x6f32;&#x8dcc;&#x5206;&#x5e03;</span><h2>74 &#x65cf;&#x7fa4;&#x5206;&#x5e03;&#x8207;&#x5f37;&#x5f31;&#x6392;&#x884c;</h2></div><div class="sector-two-col"><div class="sector-dist">{dist_html}</div><div class="sector-rank-chart"><div class="sector-rank-chart__labels"><span>&#x8dcc;&#x5e45;&#x65cf;&#x7fa4;</span><span>&#x6f32;&#x5e45;&#x65cf;&#x7fa4;</span></div>{''.join(diverging_rows)}</div></div></section>
<section class="sector-panel"><div class="sector-section-head"><span>&#x4e09;&#x3001;&#x50f9;&#x91cf;&#x56db;&#x8c61;&#x9650;</span><h2>&#x50f9;&#x91cf;&#x56db;&#x8c61;&#x9650;&#x5716;</h2></div><div class="sector-scatter" aria-label="&#x50f9;&#x91cf;&#x56db;&#x8c61;&#x9650;&#x5716;"><div class="sector-scatter__axis sector-scatter__axis--x"></div><div class="sector-scatter__axis sector-scatter__axis--y"></div><span class="sector-scatter__q sector-scatter__q1">&#x50f9;&#x91cf;&#x9f4a;&#x63da;<br>&#x4e3b;&#x7dda;&#x5019;&#x9078;</span><span class="sector-scatter__q sector-scatter__q2">&#x91cf;&#x5927;&#x50f9;&#x8dcc;<br>&#x64a4;&#x9000;&#x6216;&#x5206;&#x6b67;</span><span class="sector-scatter__q sector-scatter__q3">&#x6f32;&#x5f37;&#x91cf;&#x5c0f;<br>&#x77ed;&#x7dda;&#x8f2a;&#x52d5;</span><span class="sector-scatter__q sector-scatter__q4">&#x5f31;&#x52e2;&#x51b7;&#x9580;<br>&#x66ab;&#x975e;&#x7126;&#x9ede;</span>{''.join(points)}</div></section>
<section class="sector-panel"><div class="sector-section-head"><span>&#x56db;&#x3001;&#x8cc7;&#x91d1;&#x71b1;&#x529b;&#x5716;</span><h2>&#x65e5;&#x5747;&#x6210;&#x4ea4;&#x984d; Treemap</h2></div><div class="sector-treemap">{''.join(tiles)}</div></section>
<section class="sector-panel"><div class="sector-section-head"><span>&#x4e94;&#x3001;&#x5f37;&#x5f31;&#x65cf;&#x7fa4;&#x5361;&#x7247;</span><h2>&#x5f37;&#x5f31;&#x8207;&#x8cc7;&#x91d1;&#x7126;&#x9ede;</h2></div>{cards_html}</section>
<section class="sector-panel sector-observation"><div class="sector-section-head"><span>&#x516d;&#x3001;&#x7814;&#x7a76;&#x89c0;&#x5bdf;</span><h2>&#x672c;&#x9031;&#x89c0;&#x5bdf;</h2></div><ul><li>&#x672c;&#x9031;&#x4e3b;&#x7dda;&#xff1a;{_h(strongest.get('name'))} &#x662f;&#x6700;&#x5f37;&#x65cf;&#x7fa4;&#xff0c;5 &#x65e5;&#x7d2f;&#x8a08; {_fmt_pct(strongest.get('cum_chg'))}&#x3002;</li><li>&#x8cc7;&#x91d1;&#x64a4;&#x9000;&#x5340;&#xff1a;{_h(weakest.get('name'))} &#x8207;&#x9ad8;&#x6210;&#x4ea4;&#x4e0b;&#x8dcc;&#x65cf;&#x7fa4;&#x9700;&#x512a;&#x5148;&#x89c0;&#x5bdf;&#x662f;&#x5426;&#x6b62;&#x7a69;&#x3002;</li><li>&#x8cc7;&#x91d1;&#x7126;&#x9ede;&#xff1a;{_h(money.get('name'))} &#x65e5;&#x5747;&#x6210;&#x4ea4;&#x984d; {_fmt_money(money.get('avg_turnover'))}&#xff0c;&#x524d; 5 &#x5927;&#x65cf;&#x7fa4;&#x4f54; {concentration:.1f}%&#x3002;</li><li>&#x4e0b;&#x9031;&#x8ffd;&#x8e64;&#xff1a;&#x7559;&#x610f;&#x53f3;&#x4e0a;&#x8c61;&#x9650;&#x662f;&#x5426;&#x64f4;&#x6563;&#xff0c;&#x6216;&#x5de6;&#x4e0a;&#x8c61;&#x9650;&#x7684;&#x5927;&#x578b;&#x96fb;&#x5b50;&#x65cf;&#x7fa4;&#x662f;&#x5426;&#x5ef6;&#x7e8c;&#x4fee;&#x6b63;&#x3002;</li></ul></section></section>
"""
    return title + "\n\n" + html_block


def process_md(md_path: Path) -> dict | None:
    """Read one md, build HTML, return manifest entry meta."""
    name = md_path.stem  # e.g. daily_2026-05-28
    parts = name.split("_", 1)
    type_id = parts[0]

    if type_id not in TYPE_LABEL:
        return None

    if type_id == "focus":
        # focus_YYYY-MM-DD_<slug>
        m = re.match(r"focus_(\d{4}-\d{2}-\d{2})_(.+)", name)
        if not m:
            return None
        date, slug = m.group(1), m.group(2)
        meta_extra = extract_focus_meta(md_path.read_text(encoding="utf-8"), date, slug)
        url_slug = f"sectors-focus-{slug}"
    else:
        m = re.match(r"(daily|weekly|rotation)_(\d{4}-\d{2}-\d{2})", name)
        if not m:
            return None
        date = m.group(2)
        md_text = md_path.read_text(encoding="utf-8")
        meta_extra = META_EXTRACTORS[type_id](md_text, date)
        url_slug = f"sectors-{type_id}"

    out_path = LYNUS_ROOT / "reports" / date / f"{url_slug}.html"
    wrap_meta = {
        "category_id":   "sectors",
        "category_name": "族群",
        "type_id":       type_id,
        "type_label":    TYPE_LABEL[type_id],
        "date":          date,
        "time":          "20:00",
        "title":         meta_extra["title"],
        "title_em":      meta_extra["title_em"],
        "lead":          meta_extra["summary"],
        "volume":        1,
        "asset_prefix":  "../../",
        "stats":         meta_extra["stats"],
    }

    # wrap_report's clean step strips emoji already; we still pre-clean so the
    # markdown converter doesn't trip on enclosed alphanumerics like ❶❷.
    tmp_md = TOOLS_DIR / f"_sectors_tmp_{type_id}_{date}.md"
    cleaned = _DECO.sub("", md_path.read_text(encoding="utf-8"))
    if type_id == "weekly":
        cleaned = _render_enhanced_weekly_md(cleaned)
    tmp_md.write_text(cleaned, encoding="utf-8")

    try:
        wrap_report(tmp_md, out_path, wrap_meta)
    finally:
        tmp_md.unlink(missing_ok=True)

    return {
        "id":              f"{date}-sectors-{type_id}" + (f"-{url_slug.split('-', 2)[-1]}" if type_id == "focus" else ""),
        "category":        "sectors",
        "type":            type_id,
        "date":            date,
        "time":            "20:00",
        "title":           meta_extra["title"],
        "title_em":        meta_extra["title_em"],
        "summary":         meta_extra["summary"],
        "tags":            meta_extra["tags"],
        "source_pipeline": "industry_map",
        "url":             f"reports/{date}/{url_slug}.html",
        "stats":           meta_extra["stats"],
    }


# ── Manifest update ────────────────────────────────────────────────────────

def update_manifest(entries: list[dict]) -> None:
    path = LYNUS_ROOT / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))

    # Drop existing sector entries that we're regenerating, keep others.
    new_ids = {e["id"] for e in entries}
    kept = [e for e in manifest.get("entries", []) if e.get("id") not in new_ids]
    manifest["entries"] = kept + entries
    manifest["entries"].sort(
        key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")),
        reverse=True,
    )
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[OK] manifest — sectors entries updated: {len(entries)}, total: {len(manifest['entries'])}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None, help="Only process md with date >= YYYY-MM-DD")
    args = p.parse_args()

    if not INDUSTRY_MAP_REPORTS.exists():
        print(f"[ERR] {INDUSTRY_MAP_REPORTS} not found")
        return 1

    mds = sorted(INDUSTRY_MAP_REPORTS.glob("*.md"))
    if args.since:
        mds = [m for m in mds if _extract_date(m.stem) >= args.since]

    print(f"[INFO] Processing {len(mds)} md files from industry_map/reports/")

    entries: list[dict] = []
    failed = 0
    for md_path in mds:
        try:
            meta = process_md(md_path)
            if meta:
                entries.append(meta)
        except Exception as e:
            failed += 1
            print(f"[ERR] {md_path.name}: {e}")

    print(f"[OK] {len(entries)} HTML pages built, {failed} failures")
    if entries:
        update_manifest(entries)

    return 0 if failed == 0 else 2


def _extract_date(stem: str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", stem)
    return m.group(1) if m else "0000-00-00"


if __name__ == "__main__":
    sys.exit(main())
