"""
Build a public MOPS revenue-event watchlist against the anonymized Q2 forecast list.

This intentionally uses only public MOPS event metadata plus the already-public
anonymous forecast JSON. It does not expose broker names, source report names, or
research-institution provenance.
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


ROOT = Path(__file__).resolve().parent.parent
FORECAST_ASSET = ROOT / "assets" / "q2_forecast_revenue_compare.json"
MOPS_DB = Path(r"E:\stock_data\mops_index.db")
DATA_OUT = ROOT / "assets" / "mops_revenue_forecast_watch.json"
TPE = ZoneInfo("Asia/Taipei")

QUARTER = "2026Q2"
JUNE_PERIOD = "2026-06"
PRIOR_PERIODS = ("2026-04", "2026-05")
SURPRISE_THRESHOLD_PCT = 3.0
MOPS_EVENT_URL = "https://mops.twse.com.tw/mops/web/t05st01"


def _code(value) -> str:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if text.isdigit():
        return text.zfill(4)
    return text


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


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def is_explicit_june_revenue(text: str) -> bool:
    compact = _compact(text)
    patterns = (
        r"115年0?6月",
        r"115/0?6(?:\D|$)",
        r"0?6月(?:份)?(?:自結|合併|營業)?營收",
        r"營收[^。；;]{0,20}0?6月",
    )
    return "營收" in compact and any(re.search(pattern, compact) for pattern in patterns)


def extract_revenue_m(text: str) -> float | None:
    candidates = (
        r"(?:合併)?營收(?:淨額)?[^0-9]{0,40}([0-9][0-9,]*(?:\.[0-9]+)?)\s*億元?",
        r"營業收入[^0-9]{0,40}([0-9][0-9,]*(?:\.[0-9]+)?)\s*百萬",
    )
    for pattern in candidates:
        match = re.search(pattern, text or "")
        if not match:
            continue
        value = _to_float(match.group(1))
        if value is None:
            continue
        return round(value * 100, 3) if "億" in pattern else round(value, 3)
    return None


def short_summary(title: str, content: str, *, limit: int = 110) -> str:
    title = re.sub(r"\s+", " ", title or "").strip()
    if title:
        return title[:limit]
    content = re.sub(r"\s+", " ", content or "").strip()
    return content[:limit]


def load_forecast_index(path: Path) -> tuple[dict[str, dict], dict]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    rows = payload.get("rows") or []
    return {_code(row.get("code")): row for row in rows if row.get("code")}, payload


def load_revenue_events(db_path: Path, event_date: str) -> list[dict]:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    db_uri = f"file:///{db_path.as_posix()}?immutable=1"
    start = f"{event_date} 00:00:00"
    end = f"{event_date} 23:59:59"
    with sqlite3.connect(db_uri, uri=True, timeout=30) as conn:
        rows = conn.execute(
            """
            SELECT id, code, name, category, event_title, content, publish_time
            FROM mops_events
            WHERE publish_time >= ? AND publish_time <= ?
            ORDER BY publish_time DESC, id DESC
            """,
            (start, end),
        ).fetchall()
    out = []
    for row in rows:
        event = {
            "event_id": row[0],
            "code": _code(row[1]),
            "name": row[2],
            "category": row[3],
            "title": row[4],
            "content": row[5],
            "publish_time": row[6],
        }
        text = " ".join(str(event.get(key) or "") for key in ("category", "title", "content"))
        if "營收" in text:
            out.append(event)
    return out


def _prior_q2_revenue_m(forecast_row: dict | None) -> tuple[float | None, list[str]]:
    if not forecast_row:
        return None, list(PRIOR_PERIODS)
    months = forecast_row.get("months") or {}
    total = 0.0
    missing = []
    for period in PRIOR_PERIODS:
        revenue_m = _to_float((months.get(period) or {}).get("revenue_m"))
        if revenue_m is None:
            missing.append(period)
        else:
            total += revenue_m
    return (round(total, 3), missing)


def _status_for(
    *,
    matched: bool,
    explicit_june: bool,
    june_revenue_m: float | None,
    forecast_revenue_m: float | None,
    prior_missing: list[str],
    partial_q2_revenue_m: float | None,
) -> tuple[str, str, float | None, float | None]:
    if not matched:
        return "not_in_forecast", "未在財測名單", None, None
    if not explicit_june:
        return "watch", "有財測名單 / 非6月營收", None, None
    if june_revenue_m is None:
        return "needs_review", "6月公告 / 待抽數字", None, None
    if forecast_revenue_m is None:
        return "announced_no_forecast", "有6月公告 / 無營收財測", None, None
    if prior_missing or partial_q2_revenue_m is None:
        return "partial", "有6月公告 / 待前月補齊", None, None
    diff_m = partial_q2_revenue_m - forecast_revenue_m
    surprise_pct = diff_m / forecast_revenue_m * 100 if forecast_revenue_m else None
    if surprise_pct is not None and surprise_pct >= SURPRISE_THRESHOLD_PCT:
        return "above", "高於財測", round(diff_m, 3), round(surprise_pct, 3)
    if surprise_pct is not None and surprise_pct <= -SURPRISE_THRESHOLD_PCT:
        return "below", "低於財測", round(diff_m, 3), round(surprise_pct, 3)
    return "inline", "接近財測", round(diff_m, 3), round(surprise_pct, 3) if surprise_pct is not None else None


def build_payload(events: list[dict], forecast_index: dict[str, dict], forecast_payload: dict, event_date: str) -> dict:
    rows = []
    for event in events:
        code = _code(event.get("code"))
        forecast_row = forecast_index.get(code)
        text = " ".join(str(event.get(key) or "") for key in ("category", "title", "content"))
        explicit_june = is_explicit_june_revenue(text)
        june_revenue_m = extract_revenue_m(text) if explicit_june else None
        prior_q2_revenue_m, prior_missing = _prior_q2_revenue_m(forecast_row)
        forecast_revenue_m = _to_float((forecast_row or {}).get("forecast_revenue_m"))
        partial_q2_revenue_m = None
        if june_revenue_m is not None and prior_q2_revenue_m is not None:
            partial_q2_revenue_m = round(prior_q2_revenue_m + june_revenue_m, 3)
        status, status_label, surprise_m, surprise_pct = _status_for(
            matched=forecast_row is not None,
            explicit_june=explicit_june,
            june_revenue_m=june_revenue_m,
            forecast_revenue_m=forecast_revenue_m,
            prior_missing=prior_missing,
            partial_q2_revenue_m=partial_q2_revenue_m,
        )
        rows.append(
            {
                "event_id": event.get("event_id"),
                "code": code,
                "name": event.get("name"),
                "publish_time": event.get("publish_time"),
                "category": event.get("category"),
                "summary": short_summary(str(event.get("title") or ""), str(event.get("content") or "")),
                "explicit_june_revenue": explicit_june,
                "june_revenue_m": june_revenue_m,
                "matched_forecast_list": forecast_row is not None,
                "forecast_name": (forecast_row or {}).get("name"),
                "sample_count": (forecast_row or {}).get("sample_count"),
                "confidence": (forecast_row or {}).get("confidence"),
                "forecast_revenue_m": forecast_revenue_m,
                "prior_q2_revenue_m": prior_q2_revenue_m,
                "partial_q2_revenue_m": partial_q2_revenue_m,
                "prior_missing_months": prior_missing,
                "surprise_m": surprise_m,
                "surprise_pct": surprise_pct,
                "status": status,
                "status_label": status_label,
            }
        )

    status_order = {
        "above": 0,
        "below": 1,
        "inline": 2,
        "partial": 3,
        "announced_no_forecast": 4,
        "needs_review": 5,
        "watch": 6,
        "not_in_forecast": 7,
    }
    rows.sort(key=lambda item: (status_order.get(item["status"], 99), item["publish_time"] or "", item["code"]), reverse=False)
    stats = {
        "event_count": len(rows),
        "matched_forecast_count": sum(1 for row in rows if row["matched_forecast_list"]),
        "explicit_june_revenue_count": sum(1 for row in rows if row["explicit_june_revenue"]),
        "comparable_count": sum(1 for row in rows if row["status"] in ("above", "below", "inline")),
        "watch_count": sum(1 for row in rows if row["status"] == "watch"),
        "no_forecast_count": sum(1 for row in rows if row["status"] == "announced_no_forecast"),
    }
    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "event_date": event_date,
        "quarter": QUARTER,
        "forecast_generated_at": forecast_payload.get("generated_at"),
        "forecast_latest_mops_period": forecast_payload.get("latest_mops_period"),
        "source": {
            "events": "E:\\stock_data\\mops_index.db mops_events",
            "forecast": "assets/q2_forecast_revenue_compare.json anonymized consensus rows",
            "mops_url": MOPS_EVENT_URL,
        },
        "note": "僅追蹤公開 MOPS 營收公告與匿名財測名單是否交集，不公開研究機構來源，不構成投資建議。",
        "stats": stats,
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build MOPS revenue event watchlist vs anonymized forecast list.")
    parser.add_argument("--date", default=datetime.now(TPE).date().isoformat(), help="MOPS event date, YYYY-MM-DD.")
    parser.add_argument("--forecast-asset", default=str(FORECAST_ASSET))
    parser.add_argument("--mops-db", default=str(MOPS_DB))
    parser.add_argument("--out", default=str(DATA_OUT))
    args = parser.parse_args()

    forecast_index, forecast_payload = load_forecast_index(Path(args.forecast_asset))
    events = load_revenue_events(Path(args.mops_db), args.date)
    payload = build_payload(events, forecast_index, forecast_payload, args.date)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] {out}")
    print(
        "[INFO] "
        f"events={payload['stats']['event_count']} "
        f"matched={payload['stats']['matched_forecast_count']} "
        f"explicit_june={payload['stats']['explicit_june_revenue_count']} "
        f"comparable={payload['stats']['comparable_count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
