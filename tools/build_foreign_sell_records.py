"""
build_foreign_sell_records.py
=============================
Build the all-time foreign-investor single-day sell records table.

This is a tracking/statistics view only. It ranks historical
institutional.foreign_net sell events and records the stock's later
1/3/5/10/20/30/60-trading-day path from the event close.

Outputs:
    assets/foreign_sell_records.json
    reports/foreign-sell-records.html
    assets/chip_history/foreign_sell_records/<as_of_date>.json
    manifest.json entry under category "chips"
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from chip_analysis.data_access import CHIP_DB  # noqa: E402


ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = ROOT / "assets" / "foreign_sell_records.json"
PAGE_OUT = ROOT / "reports" / "foreign-sell-records.html"
MANIFEST_PATH = ROOT / "manifest.json"
FUTURES_BASIS_PATH = ROOT / "assets" / "futures_basis.json"
OFFICIAL_MARKET_FLOW_PATH = ROOT / "assets" / "official_market_foreign_amounts.json"
HISTORY_DIR_NAME = "foreign_sell_records"
TPE = ZoneInfo("Asia/Taipei")

DEFAULT_HORIZONS = [1, 3, 5, 10, 20, 30, 60]
DEFAULT_TOP_N = 20
MARKET_CANDIDATE_MULTIPLIER = 10
MARKET_CANDIDATE_MIN = 200
SHARES_PER_LOT = 1000
EXCLUDE_PREFIX = ("00",)


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except (OSError, ValueError):
        return str(path)


def build_records_from_frames(
    institutional: pd.DataFrame,
    prices: pd.DataFrame,
    *,
    top_n: int = DEFAULT_TOP_N,
    horizons: list[int] | None = None,
    include_etf: bool = False,
) -> list[dict]:
    """Return ranked historical foreign sell records from in-memory frames."""

    horizons = horizons or DEFAULT_HORIZONS
    inst = _normalize_institutional(institutional)
    px = _normalize_prices(prices)

    inst = inst[inst["foreign_net_buy_shares"] < 0].copy()
    if not include_etf:
        inst = inst[~inst["stock_id"].astype(str).str.startswith(EXCLUDE_PREFIX)]
    inst = inst.sort_values(
        ["foreign_net_buy_shares", "trade_date", "stock_id"],
        ascending=[True, True, True],
    ).head(top_n)

    if inst.empty:
        return []

    prices_by_code = {code: sub.sort_values("trade_date").reset_index(drop=True) for code, sub in px.groupby("stock_id")}
    records: list[dict] = []
    for rank, (_, event) in enumerate(inst.iterrows(), start=1):
        code = str(event["stock_id"])
        event_date = pd.Timestamp(event["trade_date"]).normalize()
        sub = prices_by_code.get(code, pd.DataFrame())
        price_row = _event_price_row(sub, event_date)
        close = _to_float(price_row.get("close") if price_row is not None else None)
        name = _first_text(
            price_row.get("name") if price_row is not None else None,
            event.get("name"),
            code,
        )

        row = {
            "rank": rank,
            "trade_date": event_date.date().isoformat(),
            "code": code,
            "name": name,
            "foreign_net_shares": _to_int(event.get("foreign_net_buy_shares")),
            "foreign_net_lots": _to_lots(event.get("foreign_net_buy_shares")),
            "foreign_buy_lots": _to_lots(event.get("foreign_buy")),
            "foreign_sell_lots": _to_lots(event.get("foreign_sell")),
            "close": close,
            "price_date_actual": price_row.get("trade_date").date().isoformat() if price_row is not None else None,
        }
        row.update(_future_returns_from_prices(sub, event_date, close, horizons))
        records.append(row)
    return records


def build_market_records_from_frames(
    market_flow: pd.DataFrame,
    index_prices: pd.DataFrame,
    *,
    top_n: int = DEFAULT_TOP_N,
    horizons: list[int] | None = None,
) -> list[dict]:
    """Return ranked all-market foreign sell-amount records with later TAIEX returns."""

    horizons = horizons or DEFAULT_HORIZONS
    flow = _normalize_market_flow(market_flow)
    px = _normalize_index_prices(index_prices)

    flow = flow[flow["foreign_net_amount"] < 0].copy()
    flow = flow.sort_values(
        ["foreign_net_amount", "trade_date"],
        ascending=[True, True],
    ).head(top_n)

    if flow.empty:
        return []

    records: list[dict] = []
    for rank, (_, event) in enumerate(flow.iterrows(), start=1):
        event_date = pd.Timestamp(event["trade_date"]).normalize()
        price_row = _event_price_row(px, event_date)
        close = _to_float(price_row.get("close") if price_row is not None else None)

        row = {
            "rank": rank,
            "trade_date": event_date.date().isoformat(),
            "foreign_net_shares": _to_int(event.get("foreign_net_buy_shares")),
            "foreign_net_lots": _to_lots(event.get("foreign_net_buy_shares")),
            "foreign_buy_lots": _to_lots(event.get("foreign_buy")),
            "foreign_sell_lots": _to_lots(event.get("foreign_sell")),
            "foreign_net_amount": _to_rounded_amount(event.get("foreign_net_amount")),
            "foreign_net_amount_billion": _to_billion(event.get("foreign_net_amount")),
            "foreign_buy_amount": _to_rounded_amount(event.get("foreign_buy_amount")),
            "foreign_sell_amount": _to_rounded_amount(event.get("foreign_sell_amount")),
            "amount_estimate_source_rows": _to_int(event.get("source_rows")),
            "amount_estimate_priced_rows": _to_int(event.get("priced_rows")),
            "amount_source": _first_text(event.get("amount_source"), "estimated_vwap"),
            "twse_foreign_net_amount": _to_rounded_amount(event.get("twse_foreign_net_amount")),
            "tpex_foreign_net_amount": _to_rounded_amount(event.get("tpex_foreign_net_amount")),
            "twse_close": close,
            "index_date_actual": price_row.get("trade_date").date().isoformat() if price_row is not None else None,
        }
        row.update(_future_returns_from_prices(px, event_date, close, horizons))
        records.append(row)
    return records


def _normalize_institutional(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "date": "trade_date",
        "code": "stock_id",
        "foreign_net": "foreign_net_buy_shares",
    }
    out = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns}).copy()
    required = {"trade_date", "stock_id", "foreign_net_buy_shares"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"institutional is missing required columns: {sorted(missing)}")
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    for col in ("foreign_net_buy_shares", "foreign_buy", "foreign_sell"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["trade_date", "stock_id", "foreign_net_buy_shares"])


def _normalize_market_flow(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {
        "date": "trade_date",
        "foreign_net": "foreign_net_buy_shares",
        "foreign_net_value": "foreign_net_amount",
    }
    out = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns}).copy()
    required = {"trade_date", "foreign_net_amount"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"market_flow is missing required columns: {sorted(missing)}")
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    for col in ("foreign_net_buy_shares", "foreign_buy", "foreign_sell"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ("foreign_net_amount", "foreign_buy_amount", "foreign_sell_amount"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    for col in ("twse_foreign_net_amount", "tpex_foreign_net_amount"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "amount_source" not in out.columns:
        out["amount_source"] = "estimated_vwap"
    for col in ("source_rows", "priced_rows"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["trade_date", "foreign_net_amount"])


def _normalize_prices(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {"date": "trade_date", "code": "stock_id"}
    out = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns}).copy()
    required = {"trade_date", "stock_id", "close"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"prices is missing required columns: {sorted(missing)}")
    if "name" not in out.columns:
        out["name"] = pd.NA
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    out["stock_id"] = out["stock_id"].astype(str).str.strip()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.drop_duplicates(["stock_id", "trade_date"], keep="last")
    return out.sort_values(["stock_id", "trade_date"]).reset_index(drop=True)


def _normalize_index_prices(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {"date": "trade_date", "twse_close": "close"}
    out = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns}).copy()
    required = {"trade_date", "close"}
    missing = required.difference(out.columns)
    if missing:
        raise ValueError(f"index_prices is missing required columns: {sorted(missing)}")
    out["trade_date"] = pd.to_datetime(out["trade_date"]).dt.normalize()
    out["close"] = pd.to_numeric(out["close"], errors="coerce")
    out = out.dropna(subset=["trade_date", "close"])
    out = out.drop_duplicates(["trade_date"], keep="last")
    return out.sort_values("trade_date").reset_index(drop=True)


def _event_price_row(prices: pd.DataFrame, event_date: pd.Timestamp) -> pd.Series | None:
    if prices.empty:
        return None
    match = prices[prices["trade_date"] == event_date]
    if match.empty:
        return None
    return match.iloc[0]


def _future_returns_from_prices(
    prices: pd.DataFrame,
    event_date: pd.Timestamp,
    close: float | None,
    horizons: list[int],
) -> dict[str, float | None]:
    out = {f"ret_{h}d": None for h in horizons}
    if prices.empty or close is None or close == 0:
        return out
    matches = prices.index[prices["trade_date"] == event_date].tolist()
    if not matches:
        return out
    pos = matches[0]
    for horizon in horizons:
        target_pos = pos + horizon
        if target_pos >= len(prices):
            continue
        target_close = _to_float(prices.iloc[target_pos].get("close"))
        if target_close is None:
            continue
        out[f"ret_{horizon}d"] = round((target_close - close) / close * 100, 2)
    return out


def _to_lots(value: object) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return round(number / SHARES_PER_LOT, 2)


def _to_float(value: object) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _to_int(value: object) -> int | None:
    number = _to_float(value)
    if number is None:
        return None
    return int(number)


def _to_rounded_amount(value: object) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return round(number, 2)


def _to_billion(value: object) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    return round(number / 100_000_000, 2)


def _format_billion(value: object) -> str:
    number = _to_billion(value)
    if number is None:
        return "-"
    return f"{number:,.1f}億"


def _first_text(*values: object) -> str:
    for value in values:
        if value is not None and not pd.isna(value):
            text = str(value).strip()
            if text:
                return text
    return ""


def _connect_ro() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=300)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _date_bounds(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    row = conn.execute("SELECT MIN(date), MAX(date) FROM institutional").fetchone()
    return (row[0], row[1]) if row else (None, None)


def _load_top_events(
    conn: sqlite3.Connection,
    *,
    top_n: int,
    start_date: str | None,
    end_date: str | None,
    include_etf: bool,
) -> pd.DataFrame:
    candidate_limit = max(top_n * 50, 200)
    max_candidate_limit = 1_000_000
    while True:
        raw = pd.read_sql_query(
            """
            SELECT date AS trade_date,
                   code AS stock_id,
                   name,
                   foreign_buy,
                   foreign_sell,
                   foreign_net AS foreign_net_buy_shares
            FROM institutional INDEXED BY idx_inst_fnet
            WHERE foreign_net < 0
            ORDER BY foreign_net ASC
            LIMIT ?
            """,
            conn,
            params=[candidate_limit],
            parse_dates=["trade_date"],
        )
        filtered = raw
        if start_date:
            filtered = filtered[filtered["trade_date"] >= pd.Timestamp(start_date)]
        if end_date:
            filtered = filtered[filtered["trade_date"] <= pd.Timestamp(end_date)]
        if not include_etf:
            filtered = filtered[~filtered["stock_id"].astype(str).str.startswith(EXCLUDE_PREFIX)]
        filtered = filtered.sort_values(
            ["foreign_net_buy_shares", "trade_date", "stock_id"],
            ascending=[True, True, True],
        ).head(top_n)
        if len(filtered) >= top_n or len(raw) < candidate_limit or candidate_limit >= max_candidate_limit:
            return filtered.reset_index(drop=True)
        candidate_limit = min(candidate_limit * 2, max_candidate_limit)


def _load_prices_for_events(conn: sqlite3.Connection, events: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    if events.empty:
        return pd.DataFrame(columns=["trade_date", "stock_id", "name", "close"])
    codes = sorted(events["stock_id"].astype(str).unique())
    placeholders = ",".join("?" for _ in codes)
    min_event = pd.to_datetime(events["trade_date"]).min().date().isoformat()
    max_event = pd.to_datetime(events["trade_date"]).max().date().isoformat()
    max_calendar_days = max(horizons) * 2
    params: list[object] = [*codes, min_event, max_event, f"+{max_calendar_days} days"]
    return pd.read_sql_query(
        f"""
        SELECT date AS trade_date,
               code AS stock_id,
               name,
               close
        FROM daily_price
        WHERE code IN ({placeholders})
          AND date >= ?
          AND date <= date(?, ?)
          AND close IS NOT NULL
        ORDER BY code, date
        """,
        conn,
        params=params,
        parse_dates=["trade_date"],
    )


def _load_market_flow_records(
    conn: sqlite3.Connection,
    *,
    limit: int,
    start_date: str | None,
    end_date: str | None,
) -> pd.DataFrame:
    has_official_table = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = 'market_institutional_amounts'
        """
    ).fetchone()
    columns = [
        "trade_date",
        "foreign_buy",
        "foreign_sell",
        "foreign_net_buy_shares",
        "foreign_buy_amount",
        "foreign_sell_amount",
        "foreign_net_amount",
        "source_rows",
        "priced_rows",
        "amount_source",
        "twse_foreign_buy_amount",
        "twse_foreign_sell_amount",
        "twse_foreign_net_amount",
        "tpex_foreign_buy_amount",
        "tpex_foreign_sell_amount",
        "tpex_foreign_net_amount",
        "official_source_count",
    ]
    if not has_official_table:
        return pd.DataFrame(columns=columns)

    where: list[str] = ["m.market = 'total'", "m.foreign_net_amount < 0"]
    params: list[object] = []
    if start_date:
        where.append("m.date >= ?")
        params.append(start_date)
    if end_date:
        where.append("m.date <= ?")
        params.append(end_date)
    where_sql = "WHERE " + " AND ".join(where)
    return pd.read_sql_query(
        f"""
        WITH daily AS (
            SELECT date AS trade_date,
                   SUM(foreign_buy) AS foreign_buy,
                   SUM(foreign_sell) AS foreign_sell,
                   SUM(foreign_net) AS foreign_net_buy_shares,
                   COUNT(*) AS source_rows
            FROM institutional
            GROUP BY date
         )
        SELECT m.date AS trade_date,
               d.foreign_buy,
               d.foreign_sell,
               d.foreign_net_buy_shares,
               m.foreign_buy_amount,
               m.foreign_sell_amount,
               m.foreign_net_amount,
               d.source_rows,
               (
                 CASE WHEN twse.market IS NOT NULL THEN 1 ELSE 0 END +
                 CASE WHEN tpex.market IS NOT NULL THEN 1 ELSE 0 END
               ) AS priced_rows,
               'official_twse_tpex' AS amount_source,
               twse.foreign_buy_amount AS twse_foreign_buy_amount,
               twse.foreign_sell_amount AS twse_foreign_sell_amount,
               twse.foreign_net_amount AS twse_foreign_net_amount,
               tpex.foreign_buy_amount AS tpex_foreign_buy_amount,
               tpex.foreign_sell_amount AS tpex_foreign_sell_amount,
               tpex.foreign_net_amount AS tpex_foreign_net_amount,
               (
                 CASE WHEN twse.market IS NOT NULL THEN 1 ELSE 0 END +
                 CASE WHEN tpex.market IS NOT NULL THEN 1 ELSE 0 END
               ) AS official_source_count
        FROM market_institutional_amounts m
        LEFT JOIN daily d
               ON d.trade_date = m.date
        LEFT JOIN market_institutional_amounts twse
               ON twse.date = m.date
              AND twse.market = 'twse'
        LEFT JOIN market_institutional_amounts tpex
               ON tpex.date = m.date
              AND tpex.market = 'tpex'
        {where_sql}
        ORDER BY m.foreign_net_amount ASC
        LIMIT ?
        """,
        conn,
        params=[*params, limit],
        parse_dates=["trade_date"],
    )


def _with_official_market_amounts(market_flow: pd.DataFrame) -> pd.DataFrame:
    if market_flow.empty:
        return market_flow

    out = market_flow.copy()
    dates = [pd.Timestamp(x).date().isoformat() for x in out["trade_date"]]
    official = _load_official_market_amount_cache()
    changed = False

    for trade_date in dates:
        if trade_date not in official:
            row = _fetch_official_market_amount(trade_date)
            if row:
                official[trade_date] = row
                changed = True
                # Keep requests gentle; daily runs usually fetch only one new date.
                time.sleep(0.08)

    if changed:
        _write_official_market_amount_cache(official)

    for col in (
        "twse_foreign_buy_amount",
        "twse_foreign_sell_amount",
        "twse_foreign_net_amount",
        "tpex_foreign_buy_amount",
        "tpex_foreign_sell_amount",
        "tpex_foreign_net_amount",
        "official_source_count",
    ):
        out[col] = pd.NA
    out["amount_source"] = "estimated_vwap"

    for idx, row in out.iterrows():
        trade_date = pd.Timestamp(row["trade_date"]).date().isoformat()
        official_row = official.get(trade_date)
        if not official_row:
            continue
        net = _to_float(official_row.get("foreign_net_amount"))
        buy = _to_float(official_row.get("foreign_buy_amount"))
        sell = _to_float(official_row.get("foreign_sell_amount"))
        if net is None or buy is None or sell is None:
            continue
        out.at[idx, "foreign_buy_amount"] = buy
        out.at[idx, "foreign_sell_amount"] = sell
        out.at[idx, "foreign_net_amount"] = net
        out.at[idx, "amount_source"] = official_row.get("source", "official_twse_tpex")
        for key in (
            "twse_foreign_buy_amount",
            "twse_foreign_sell_amount",
            "twse_foreign_net_amount",
            "tpex_foreign_buy_amount",
            "tpex_foreign_sell_amount",
            "tpex_foreign_net_amount",
            "official_source_count",
        ):
            out.at[idx, key] = official_row.get(key)

    return out


def _load_official_market_amount_cache() -> dict[str, dict]:
    if not OFFICIAL_MARKET_FLOW_PATH.exists():
        return {}
    try:
        raw = json.loads(OFFICIAL_MARKET_FLOW_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = raw.get("dates", raw) if isinstance(raw, dict) else {}
    return rows if isinstance(rows, dict) else {}


def _write_official_market_amount_cache(rows: dict[str, dict]) -> None:
    OFFICIAL_MARKET_FLOW_PATH.parent.mkdir(parents=True, exist_ok=True)
    ordered = {key: rows[key] for key in sorted(rows)}
    payload = {
        "updated_at": datetime.now(TPE).isoformat(),
        "source": "TWSE BFI82U + TPEx insti/summary official foreign buy/sell amount",
        "dates": ordered,
    }
    OFFICIAL_MARKET_FLOW_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fetch_official_market_amount(trade_date: str) -> dict | None:
    compact = trade_date.replace("-", "")
    slash = trade_date.replace("-", "/")
    twse = _fetch_twse_market_amount(compact)
    tpex = _fetch_tpex_market_amount(slash)
    rows = [row for row in (twse, tpex) if row]
    if not rows:
        return None
    buy = sum(_to_float(row.get("foreign_buy_amount")) or 0 for row in rows)
    sell = sum(_to_float(row.get("foreign_sell_amount")) or 0 for row in rows)
    net = sum(_to_float(row.get("foreign_net_amount")) or 0 for row in rows)
    out = {
        "trade_date": trade_date,
        "foreign_buy_amount": buy,
        "foreign_sell_amount": sell,
        "foreign_net_amount": net,
        "source": "official_twse_tpex" if twse and tpex else ("official_twse" if twse else "official_tpex"),
        "official_source_count": len(rows),
        "fetched_at": datetime.now(TPE).isoformat(),
    }
    if twse:
        out.update(
            {
                "twse_foreign_buy_amount": twse.get("foreign_buy_amount"),
                "twse_foreign_sell_amount": twse.get("foreign_sell_amount"),
                "twse_foreign_net_amount": twse.get("foreign_net_amount"),
            }
        )
    if tpex:
        out.update(
            {
                "tpex_foreign_buy_amount": tpex.get("foreign_buy_amount"),
                "tpex_foreign_sell_amount": tpex.get("foreign_sell_amount"),
                "tpex_foreign_net_amount": tpex.get("foreign_net_amount"),
            }
        )
    return out


def _fetch_twse_market_amount(compact_date: str) -> dict | None:
    url = f"https://www.twse.com.tw/rwd/zh/fund/BFI82U?dayDate={compact_date}&type=day&response=json"
    data = _fetch_json_url(url)
    if not data or data.get("stat") != "OK":
        return None
    for row in data.get("data", []):
        label = str(row[0]).strip() if row else ""
        if label == "外資及陸資(不含外資自營商)":
            return {
                "foreign_buy_amount": _parse_int(row[1]),
                "foreign_sell_amount": _parse_int(row[2]),
                "foreign_net_amount": _parse_int(row[3]),
            }
    return None


def _fetch_tpex_market_amount(slash_date: str) -> dict | None:
    url = f"https://www.tpex.org.tw/www/zh-tw/insti/summary?date={slash_date}&type=Daily&prod=1&response=json"
    data = _fetch_json_url(url)
    if not data or str(data.get("stat", "")).lower() != "ok":
        return None
    tables = data.get("tables", [])
    if not tables:
        return None
    preferred = None
    fallback = None
    for row in tables[0].get("data", []):
        label = str(row[0]).replace("\u3000", "").strip() if row else ""
        if label == "外資及陸資合計":
            preferred = row
            break
        if label == "外資及陸資(不含自營商)":
            fallback = row
    row = preferred or fallback
    if not row:
        return None
    return {
        "foreign_buy_amount": _parse_int(row[1]),
        "foreign_sell_amount": _parse_int(row[2]),
        "foreign_net_amount": _parse_int(row[3]),
    }


def _fetch_json_url(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        return None


def _parse_int(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).replace(",", "").replace("+", "").strip()
    if text in {"", "--", "---"}:
        return 0
    return int(text)


def _load_index_prices() -> pd.DataFrame:
    if not FUTURES_BASIS_PATH.exists():
        return pd.DataFrame(columns=["trade_date", "close"])
    rows = json.loads(FUTURES_BASIS_PATH.read_text(encoding="utf-8"))
    return pd.DataFrame(
        {
            "trade_date": row.get("date"),
            "close": row.get("twse_close"),
        }
        for row in rows
        if row.get("date") and row.get("twse_close") is not None
    )


def build_payload(
    records: list[dict],
    *,
    market_records: list[dict] | None = None,
    as_of_date: str | None,
    source_start_date: str | None,
    source_end_date: str | None,
    top_n: int,
    horizons: list[int],
    include_etf: bool,
) -> dict:
    market_records = market_records or []
    return {
        "generated_at": datetime.now(TPE).isoformat(),
        "as_of_date": as_of_date,
        "source_start_date": source_start_date,
        "source_end_date": source_end_date,
        "top_n": top_n,
        "horizons": horizons,
        "include_etf": include_etf,
        "source": "stock_chip.db institutional.foreign_net; stock returns from daily_price close; market foreign amount from stock_chip.db market_institutional_amounts official TWSE BFI82U + TPEx insti/summary daily amount; market returns from futures_basis.twse_close",
        "note": "僅追蹤外資單日賣超事件後走勢，不構成投資建議。",
        "rankings": {
            "top10": records[:10],
            "top20": records[:20],
        },
        "market_rankings": {
            "top10": market_records[:10],
            "top20": market_records[:20],
        },
        "records": records,
        "market_records": market_records,
    }


def _update_history_index(as_of_date: str) -> None:
    idx_path = ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / "_index.json"
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    existing = []
    if idx_path.exists():
        try:
            existing = json.loads(idx_path.read_text(encoding="utf-8")).get("dates", [])
        except Exception:
            pass
    if as_of_date not in existing:
        existing.append(as_of_date)
    existing.sort(reverse=True)
    idx_path.write_text(
        json.dumps({"latest": existing[0] if existing else None, "dates": existing}, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Build all-time foreign sell records.")
    parser.add_argument("--top-n", type=int, default=DEFAULT_TOP_N, help="Number of records to keep. Default: 20.")
    parser.add_argument("--start-date", default=None, help="Optional institutional date lower bound.")
    parser.add_argument("--end-date", default=None, help="Optional institutional date upper bound.")
    parser.add_argument("--include-etf", action="store_true", help="Include 00xx ETF/product codes.")
    parser.add_argument("--history-only", action="store_true", help="Only write dated JSON; do not touch main file/manifest.")
    args = parser.parse_args()

    if not CHIP_DB.exists():
        print(f"[ERR] {CHIP_DB} not found")
        return 1
    if args.top_n <= 0:
        print("[ERR] --top-n must be positive")
        return 2

    horizons = DEFAULT_HORIZONS
    with _connect_ro() as conn:
        source_start, source_end = _date_bounds(conn)
        events = _load_top_events(
            conn,
            top_n=args.top_n,
            start_date=args.start_date,
            end_date=args.end_date,
            include_etf=args.include_etf,
        )
        prices = _load_prices_for_events(conn, events, horizons)
        market_candidate_limit = max(args.top_n * MARKET_CANDIDATE_MULTIPLIER, MARKET_CANDIDATE_MIN)
        market_flow = _load_market_flow_records(
            conn,
            limit=market_candidate_limit,
            start_date=args.start_date,
            end_date=args.end_date,
        )
    index_prices = _load_index_prices()

    records = build_records_from_frames(
        events,
        prices,
        top_n=args.top_n,
        horizons=horizons,
        include_etf=args.include_etf,
    )
    market_records = build_market_records_from_frames(
        market_flow,
        index_prices,
        top_n=args.top_n,
        horizons=horizons,
    )
    as_of_date = args.end_date or source_end
    payload = build_payload(
        records,
        market_records=market_records,
        as_of_date=as_of_date,
        source_start_date=args.start_date or source_start,
        source_end_date=args.end_date or source_end,
        top_n=args.top_n,
        horizons=horizons,
        include_etf=args.include_etf,
    )

    if as_of_date is None:
        print("[ERR] institutional has no date range")
        return 3

    dated_path = ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME / f"{as_of_date}.json"
    dated_path.parent.mkdir(parents=True, exist_ok=True)
    dated_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {_display_path(dated_path)} (dated)")
    _update_history_index(as_of_date)

    is_latest_run = as_of_date == source_end
    if is_latest_run and not args.history_only:
        DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
        DATA_OUT.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"[OK] {_display_path(DATA_OUT)}")
        render_html(payload)
        print(f"[OK] {_display_path(PAGE_OUT)}")
        update_manifest(payload)
        print("[OK] manifest.json — foreign-sell-records entry refreshed")
    else:
        print("[SKIP main] backfill mode — main file/manifest untouched")
    return 0


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>外資史上賣超紀錄 — charles16888</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    .fsr-page .report-cover { padding-bottom: 42px; }
    .fsr-page .report-lead {
      max-width: 760px;
      color: #2f251a;
      font-size: 17px;
      line-height: 1.85;
    }
    .fsr-tabs {
      display: flex; gap: 8px; margin: 18px 0 18px; flex-wrap: wrap;
      font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: .08em;
    }
    .fsr-mode-tabs { margin-top: 0; }
    .fsr-tab,
    .fsr-mode-tab {
      min-height: 38px;
      padding: 9px 18px;
      border: 1px solid rgba(115,80,0,0.36);
      background: #fffaf0;
      color: #342b20;
      cursor: pointer;
      transition: all .15s;
      font: inherit;
      font-weight: 700;
    }
    .fsr-tab:hover,
    .fsr-mode-tab:hover { color: #15110d; border-color: #735000; }
    .fsr-tab.is-active,
    .fsr-mode-tab.is-active { background: #735000; color: #fffdf8; border-color: #735000; }
    .fsr-table-wrap {
      overflow-x: auto;
      border: 1px solid rgba(115,80,0,0.28);
      background: #fffaf0;
      margin: 0 0 32px;
      box-shadow: 0 14px 32px rgba(21,17,13,0.06);
    }
    .fsr-table {
      width: 100%;
      min-width: 1080px;
      border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
    }
    .fsr-table th {
      text-align: left;
      padding: 12px 14px;
      color: #fffdf8;
      background: #2b2118;
      font-weight: 800;
      letter-spacing: .06em;
      border-bottom: 2px solid rgba(115,80,0,0.58);
      white-space: nowrap;
    }
    .fsr-table td {
      padding: 12px 14px;
      color: #22180f;
      border-bottom: 1px solid rgba(21,17,13,0.10);
      white-space: nowrap;
      font-weight: 650;
    }
    .fsr-table tbody tr:nth-child(even) td { background: rgba(115,80,0,0.045); }
    .fsr-table tbody tr:hover td { background: rgba(212,175,55,0.15); }
    .fsr-rank { color: #5b4b39; text-align: right; }
    .fsr-code { color: #735000; font-weight: 850; }
    .fsr-name { font-family: 'Noto Serif TC', serif; color: #15110d; font-size: 14px; font-weight: 800; }
    .fsr-right { text-align: right; }
    .fsr-neg { color: #0f7a43; font-weight: 900; }
    .fsr-pos { color: #d03845; font-weight: 900; }
    .fsr-muted { color: #7a6d5c; font-weight: 650; }
    .fsr-stat-row {
      display: flex; gap: 18px; flex-wrap: wrap; margin: 20px 0 0;
      font-family: 'JetBrains Mono', monospace; font-size: 12px; letter-spacing: .06em; color: #4b4032;
    }
    .fsr-stat-row span {
      border: 1px solid rgba(115,80,0,0.22);
      background: rgba(255,250,240,0.86);
      padding: 8px 10px;
    }
    .fsr-stat-row strong { color: #15110d; font-weight: 900; }
    @media (max-width: 760px) {
      .fsr-page .report-lead { font-size: 15px; }
      .fsr-table { min-width: 980px; font-size: 11px; }
    }
  </style>
</head>
<body class="fsr-page" data-page="report">
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
          <a class="nav__link" href="../category.html?cat=txo">選擇權</a>
          <a class="nav__link is-active" href="../category.html?cat=chips">籌碼</a>
          <a class="nav__link" href="../category.html?cat=research">研報統計</a>
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
      <span>外資賣超紀錄</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 外資史上賣超</span>
        <span>__SOURCE_RANGE__</span>
        <span>更新至 __AS_OF_DATE__</span>
      </div>
      <h1 class="report-title">外資史上<em>賣超</em>紀錄</h1>
      <p class="report-lead">
        以 institutional.foreign_net 單日淨買賣超排序，分成「個股」與「大盤」兩組紀錄。
        個股表追蹤事件股票後續走勢；大盤表使用 TWSE 與 TPEx 官方三大法人買賣金額彙總表的外資淨買賣金額，
        追蹤加權指數後續 1/3/5/10/20/30/60 個交易日漲跌幅。
        本表只做追蹤與統計，不做投資建議。
      </p>
      <div class="fsr-stat-row" id="fsr-stats"></div>
    </section>

    <section>
      <div class="fsr-tabs fsr-mode-tabs" aria-label="切換紀錄類型">
        <button class="fsr-mode-tab is-active" type="button" data-mode="stock">個股紀錄</button>
        <button class="fsr-mode-tab" type="button" data-mode="market">大盤紀錄</button>
      </div>
      <div class="fsr-tabs">
        <button class="fsr-tab is-active" type="button" data-limit="10">TOP 10</button>
        <button class="fsr-tab" type="button" data-limit="20">TOP 20</button>
      </div>
      <div class="fsr-table-wrap" id="fsr-table"></div>
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
  const horizons = DATA.horizons || [1,3,5,10,20,30,60];
  let currentMode = 'stock';
  let currentLimit = 10;

  function cls(v) {
    if (v == null) return 'fsr-muted';
    return v >= 0 ? 'fsr-pos' : 'fsr-neg';
  }
  function fmt(v, digits = 1, suffix = '') {
    if (v == null) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}${Number(v).toFixed(digits)}${suffix}`;
  }
  function fmtLots(v) {
    if (v == null) return '—';
    return `${Math.round(v).toLocaleString()} 張`;
  }
  function fmtBillion(v) {
    if (v == null) return '—';
    const n = Number(v) / 100000000;
    const sign = n >= 0 ? '+' : '';
    return `${sign}${n.toLocaleString(undefined, {minimumFractionDigits: 1, maximumFractionDigits: 1})} 億`;
  }
  function amountSourceLabel(source) {
    if (source === 'official_twse_tpex') return '官方金額（上市+上櫃）';
    if (source === 'official_twse') return '官方金額（上市）';
    if (source === 'official_tpex') return '官方金額（上櫃）';
    return '估算成交額';
  }
  function currentRecords() {
    return currentMode === 'market' ? (DATA.market_records || []) : (DATA.records || []);
  }
  function renderStats() {
    const r = currentRecords();
    const top = r[0] || {};
    if (currentMode === 'market') {
      document.getElementById('fsr-stats').innerHTML = [
        `<span>紀錄筆數：<strong>${r.length}</strong></span>`,
        `<span>最大賣超金額：<strong>${top.trade_date || '—'} ${top.foreign_net_amount != null ? fmtBillion(top.foreign_net_amount) : ''}</strong></span>`,
        `<span>口徑：<strong>${amountSourceLabel(top.amount_source)}</strong></span>`,
        `<span>追蹤標的：<strong>加權指數 1/3/5/10/20/30/60 日</strong></span>`
      ].join('');
      return;
    }
    document.getElementById('fsr-stats').innerHTML = [
      `<span>紀錄筆數：<strong>${r.length}</strong></span>`,
      `<span>最大賣超：<strong>${top.code || '—'} ${top.foreign_net_lots != null ? fmtLots(top.foreign_net_lots) : ''}</strong></span>`,
      `<span>走勢欄位：<strong>${horizons.join('/')} 日</strong></span>`
    ].join('');
  }
  function renderStock(rows) {
    const futureHeads = horizons.map(h => `<th class="fsr-right">${h}日後</th>`).join('');
    const body = rows.map(r => {
      const futureCells = horizons.map(h => {
        const v = r[`ret_${h}d`];
        return `<td class="fsr-right ${cls(v)}">${fmt(v, 1, '%')}</td>`;
      }).join('');
      return `<tr>
        <td class="fsr-rank">${r.rank}</td>
        <td>${r.trade_date}</td>
        <td><span class="fsr-code">${r.code}</span></td>
        <td><span class="fsr-name">${r.name}</span></td>
        <td class="fsr-right fsr-neg">${fmtLots(r.foreign_net_lots)}</td>
        <td class="fsr-right">${r.close != null ? Number(r.close).toFixed(2) : '—'}</td>
        ${futureCells}
      </tr>`;
    }).join('');
    document.getElementById('fsr-table').innerHTML = `<table class="fsr-table">
      <thead><tr>
        <th class="fsr-right">#</th><th>日期</th><th>代號</th><th>名稱</th>
        <th class="fsr-right">外資淨賣超</th><th class="fsr-right">事件收盤</th>${futureHeads}
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }
  function renderMarket(rows) {
    const futureHeads = horizons.map(h => `<th class="fsr-right">${h}日後</th>`).join('');
    const body = rows.map(r => {
      const futureCells = horizons.map(h => {
        const v = r[`ret_${h}d`];
        return `<td class="fsr-right ${cls(v)}">${fmt(v, 1, '%')}</td>`;
      }).join('');
      return `<tr>
        <td class="fsr-rank">${r.rank}</td>
        <td>${r.trade_date}</td>
        <td class="fsr-right fsr-neg">${fmtBillion(r.foreign_net_amount)}</td>
        <td class="fsr-right">${r.twse_close != null ? Number(r.twse_close).toLocaleString(undefined, {maximumFractionDigits: 2}) : '—'}</td>
        ${futureCells}
      </tr>`;
    }).join('');
    document.getElementById('fsr-table').innerHTML = `<table class="fsr-table">
      <thead><tr>
        <th class="fsr-right">#</th><th>日期</th>
        <th class="fsr-right">大盤外資淨賣超金額</th><th class="fsr-right">加權收盤</th>${futureHeads}
      </tr></thead>
      <tbody>${body}</tbody>
    </table>`;
  }
  function render(limit = currentLimit) {
    currentLimit = limit;
    const rows = currentRecords().slice(0, currentLimit);
    renderStats();
    if (currentMode === 'market') renderMarket(rows);
    else renderStock(rows);
  }
  document.querySelectorAll('.fsr-mode-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      currentMode = btn.dataset.mode;
      document.querySelectorAll('.fsr-mode-tab').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      render(currentLimit);
    });
  });
  document.querySelectorAll('.fsr-tab').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.fsr-tab').forEach(b => b.classList.remove('is-active'));
      btn.classList.add('is-active');
      render(Number(btn.dataset.limit));
    });
  });
  render(currentLimit);
  </script>
</body>
</html>
"""


def render_html(out: dict) -> None:
    source_range = f"{out.get('source_start_date') or '-'} -> {out.get('source_end_date') or '-'}"
    rendered = (
        PAGE_TEMPLATE
        .replace("__DATA_JSON__", json.dumps(out, ensure_ascii=False))
        .replace("__SOURCE_RANGE__", source_range)
        .replace("__AS_OF_DATE__", out.get("as_of_date") or "-")
    )
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(rendered, encoding="utf-8")


def update_manifest(out: dict) -> None:
    if not MANIFEST_PATH.exists():
        return
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    records = out.get("records", [])
    market_records = out.get("market_records", [])
    top = records[0] if records else None
    market_top = market_records[0] if market_records else None
    summary = (
        f"外資單日淨賣超史上 Top {min(20, len(records))}，並加入大盤外資淨賣超金額 Top {min(20, len(market_records))}。"
        f"追蹤事件後 {'/'.join(str(x) for x in out.get('horizons', []))} 日走勢。"
    )
    if top:
        summary += f" 最大紀錄：{top['trade_date']} {top['code']} {top['name']} {top['foreign_net_lots']:,.0f} 張。"
    if market_top:
        summary += f" 大盤最大金額：{market_top['trade_date']} {_format_billion(market_top.get('foreign_net_amount'))}。"

    entry = {
        "id": "foreign-sell-records",
        "category": "chips",
        "type": "ranking",
        "date": out.get("as_of_date"),
        "time": "21:05",
        "title": "外資史上賣超紀錄 · 個股 / 大盤金額 Top 20",
        "title_em": "賣超",
        "summary": summary,
        "tags": ["籌碼", "外資", "賣超", "成交額", "事件追蹤"],
        "source_pipeline": "stock_chip_crawler",
        "url": f"reports/foreign-sell-records.html?v={out.get('as_of_date', '').replace('-', '')}-official",
        "stats": [
            {
                "label": "個股最大",
                "value": f"{top['code']} {top['foreign_net_lots']:,.0f}張" if top else "-",
                "color": "down",
            },
            {
                "label": "大盤金額",
                "value": _format_billion(market_top.get("foreign_net_amount")) if market_top else "-",
                "color": "down",
            },
            {"label": "走勢", "value": "1-60日", "color": "neutral"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != entry["id"]]
    manifest["entries"].append(entry)
    manifest["entries"].sort(key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")), reverse=True)
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
