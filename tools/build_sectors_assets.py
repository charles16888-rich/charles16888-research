"""
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


# ── Process one md ─────────────────────────────────────────────────────────

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
