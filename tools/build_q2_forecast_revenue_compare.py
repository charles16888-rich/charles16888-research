"""
Build 2026 Q2 forecast-vs-MOPS monthly revenue comparison data.

The public forecast report keeps only anonymized derived fields. This builder
reuses the revenue consensus already rendered in the HTML table, joins MOPS
monthly revenue from mops_index.db, and emits a static JSON asset consumed by
the forecast page.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from bs4 import BeautifulSoup


ROOT = Path(__file__).resolve().parent.parent
FORECAST_REPORT = ROOT / "reports" / "q2-forecast-2026q2.html"
REVENUE_DB = Path(r"E:\stock_data\mops_index.db")
DATA_OUT = ROOT / "assets" / "q2_forecast_revenue_compare.json"
TPE = ZoneInfo("Asia/Taipei")

QUARTER = "2026Q2"
QUARTER_MONTHS = [(115, 4), (115, 5), (115, 6)]
SURPRISE_THRESHOLD_PCT = 3.0
MOPS_REVENUE_URL = "https://mops.twse.com.tw/mops/web/t05st10_ifrs"


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
    except ImportError:
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
        normalized_rows: list[list[str]] = []
        for row in body:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            elif len(row) > len(header):
                row = row[: len(header)]
            normalized_rows.append(row)
        tables.append(pd.DataFrame(normalized_rows, columns=header))
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

    out = pd.DataFrame(
        {
            "code": df["代號"].map(_code),
            "name": df["公司"].astype(str).str.strip(),
            "sample_count": df["樣本"].map(_to_int),
            "forecast_revenue_m": df["營收 NT$百萬"].map(_to_float),
            "confidence": df["信心"].astype(str).str.strip(),
        }
    )
    return out


def load_mops_revenue(
    db_path: Path = REVENUE_DB,
    months: list[tuple[int, int]] | None = None,
) -> tuple[pd.DataFrame, str | None]:
    months = months or QUARTER_MONTHS
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=120) as conn:
        latest = conn.execute(
            """
            SELECT roc_year, month
            FROM mops_revenue
            ORDER BY roc_year DESC, month DESC
            LIMIT 1
            """
        ).fetchone()
        clauses = []
        params: list[int] = []
        for roc_year, month in months:
            clauses.append("(roc_year = ? AND month = ?)")
            params.extend([roc_year, month])
        where_sql = " OR ".join(clauses)
        df = pd.read_sql_query(
            f"""
            SELECT code, name, roc_year, month, revenue_k, mom, yoy, ytd_yoy, publish_time
            FROM mops_revenue
            WHERE {where_sql}
            """,
            conn,
            params=params,
        )
    latest_period = _period_label(int(latest[0]), int(latest[1])) if latest else None
    if df.empty:
        return df, latest_period
    df["code"] = df["code"].astype(str).map(_code)
    df["period"] = df.apply(lambda r: _period_label(int(r["roc_year"]), int(r["month"])), axis=1)
    df["revenue_m"] = pd.to_numeric(df["revenue_k"], errors="coerce") / 1000.0
    return df, latest_period


# Flag OCR/unit-scale garbage so extreme ±% does not pollute above/below ranks.
# - quarterly forecast smaller than half an average month ⇒ almost always unit/OCR error
# - actual and forecast differ by >20x ⇒ scale mismatch, not a real beat/miss
SUSPECT_MIN_MONTH_RATIO = 0.5
SUSPECT_MAX_SCALE_RATIO = 20.0


def _is_suspect_forecast(
    forecast_revenue_m: float,
    actual_revenue_m: float,
    month_revenues: list[float],
) -> bool:
    if forecast_revenue_m <= 0 or actual_revenue_m is None:
        return False
    ratio = actual_revenue_m / forecast_revenue_m
    if ratio >= SUSPECT_MAX_SCALE_RATIO or ratio <= 1.0 / SUSPECT_MAX_SCALE_RATIO:
        return True
    if month_revenues:
        avg_month = sum(month_revenues) / len(month_revenues)
        if avg_month > 0 and forecast_revenue_m < avg_month * SUSPECT_MIN_MONTH_RATIO:
            return True
    return False


def _status_for(
    forecast_revenue_m: float | None,
    available_periods: set[str],
    actual_revenue_m: float | None,
    expected_periods: list[str],
    *,
    month_revenues: list[float] | None = None,
) -> tuple[str, float | None, float | None]:
    if forecast_revenue_m is None:
        return "no_forecast", None, None
    missing = [p for p in expected_periods if p not in available_periods]
    if missing:
        return "incomplete", None, None
    if actual_revenue_m is None:
        return "incomplete", None, None
    diff_m = actual_revenue_m - forecast_revenue_m
    surprise_pct = diff_m / forecast_revenue_m * 100 if forecast_revenue_m else None
    if _is_suspect_forecast(forecast_revenue_m, actual_revenue_m, month_revenues or []):
        return "suspect", diff_m, surprise_pct
    if surprise_pct is not None and surprise_pct >= SURPRISE_THRESHOLD_PCT:
        return "above", diff_m, surprise_pct
    if surprise_pct is not None and surprise_pct <= -SURPRISE_THRESHOLD_PCT:
        return "below", diff_m, surprise_pct
    return "inline", diff_m, surprise_pct


def _status_label(
    status: str,
    *,
    forecast_revenue_m: float | None,
    available_periods: set[str],
    missing_periods: list[str],
    actual_revenue_m: float | None,
) -> str:
    labels = {
        "above": "高於預期",
        "below": "低於預期",
        "inline": "符合預期",
        "incomplete": "尚未完整",
        "suspect": "財測異常",
    }
    if status != "no_forecast":
        return labels[status]
    if actual_revenue_m is not None:
        return "無研報營收預估（實際已公告）"
    if not available_periods:
        return "無研報營收預估（MOPS 無月營收）"
    if missing_periods:
        return "無研報營收預估（部分月營收）"
    return "無研報營收預估"


def build_comparison(
    forecast: pd.DataFrame,
    revenue: pd.DataFrame,
    *,
    latest_mops_period: str | None,
    quarter: str = QUARTER,
    quarter_months: list[tuple[int, int]] | None = None,
) -> dict:
    quarter_months = quarter_months or QUARTER_MONTHS
    expected_periods = [_period_label(y, m) for y, m in quarter_months]
    revenue_by_code = {
        code: rows.sort_values(["roc_year", "month"]).to_dict("records")
        for code, rows in revenue.groupby("code")
    } if not revenue.empty else {}

    rows_out: list[dict] = []
    for _, f in forecast.iterrows():
        code = str(f["code"])
        month_rows = revenue_by_code.get(code, [])
        months: dict[str, dict] = {}
        available_sum = 0.0
        publish_times = []
        for row in month_rows:
            period = str(row["period"])
            if period not in expected_periods:
                continue
            revenue_m = _to_float(row.get("revenue_m"))
            months[period] = {
                "revenue_m": revenue_m,
                "mom": _to_float(row.get("mom")),
                "yoy": _to_float(row.get("yoy")),
                "publish_time": row.get("publish_time"),
            }
            if revenue_m is not None:
                available_sum += revenue_m
            if row.get("publish_time"):
                publish_times.append(str(row.get("publish_time")))

        available_periods = set(months)
        missing_periods = [p for p in expected_periods if p not in available_periods]
        forecast_revenue_m = _to_float(f.get("forecast_revenue_m"))
        # MOPS actuals are factual data, independent of whether an anonymized
        # research report supplied a revenue forecast.  Previously this was
        # gated on forecast_revenue_m, which made fully announced companies
        # look blank in the "Q2 actual" column.
        complete_actual = (
            not missing_periods
            and all(months.get(period, {}).get("revenue_m") is not None for period in expected_periods)
        )
        actual_revenue_m = available_sum if complete_actual else None
        month_revenues = [
            float(months[period]["revenue_m"])
            for period in expected_periods
            if months.get(period, {}).get("revenue_m") is not None
        ]
        status, diff_m, surprise_pct = _status_for(
            forecast_revenue_m,
            available_periods,
            actual_revenue_m,
            expected_periods,
            month_revenues=month_revenues,
        )
        rows_out.append(
            {
                "code": code,
                "name": str(f["name"]),
                "sample_count": _to_int(f.get("sample_count")),
                "confidence": str(f.get("confidence") or ""),
                "forecast_revenue_m": forecast_revenue_m,
                "announced_revenue_m": available_sum if months else None,
                "actual_revenue_m": actual_revenue_m,
                "surprise_m": diff_m,
                "surprise_pct": surprise_pct,
                "status": status,
                "status_label": _status_label(
                    status,
                    forecast_revenue_m=forecast_revenue_m,
                    available_periods=available_periods,
                    missing_periods=missing_periods,
                    actual_revenue_m=actual_revenue_m,
                ),
                "months": months,
                "missing_months": missing_periods,
                "latest_publish_time": max(publish_times) if publish_times else None,
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
        "mops_complete_count": sum(1 for r in rows_out if r["actual_revenue_m"] is not None),
        "no_forecast_announced_count": sum(
            1 for r in rows_out
            if r["status"] == "no_forecast" and r["actual_revenue_m"] is not None
        ),
    }
    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "quarter": quarter,
        "quarter_months": expected_periods,
        "latest_mops_period": latest_mops_period,
        "surprise_threshold_pct": SURPRISE_THRESHOLD_PCT,
        "source": {
            "forecast": "reports/q2-forecast-2026q2.html anonymized consensus table",
            "mops": "E:\\stock_data\\mops_index.db mops_revenue",
            "mops_url": MOPS_REVENUE_URL,
        },
        "note": "僅追蹤匿名財測營收與 MOPS 月營收加總差異，不構成投資建議。",
        "stats": stats,
        "rows": rows_out,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Q2 forecast vs MOPS revenue comparison JSON.")
    parser.add_argument("--forecast-report", default=str(FORECAST_REPORT))
    parser.add_argument("--revenue-db", default=str(REVENUE_DB))
    parser.add_argument("--out", default=str(DATA_OUT))
    args = parser.parse_args()

    forecast = load_forecast_table(Path(args.forecast_report))
    revenue, latest = load_mops_revenue(Path(args.revenue_db))
    payload = build_comparison(forecast, revenue, latest_mops_period=latest)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {out_path}")
    print(
        "[INFO] "
        f"forecast={payload['stats']['revenue_forecast_count']} "
        f"complete={payload['stats']['complete_count']} "
        f"incomplete={payload['stats']['incomplete_count']} "
        f"latest_mops={payload['latest_mops_period']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
