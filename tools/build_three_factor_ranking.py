"""
build_three_factor_ranking.py
=============================
Build the three-factor swing ranking:

    1. Foreign investors buying for consecutive days
    2. Latest monthly revenue at a new high
    3. Strong price momentum

The output includes 1/3/5/10/20-trading-day forward returns from the signal
date when those future prices are available.

Outputs:
    assets/three_factor_ranking.json
    assets/chip_history/three_factor_ranking/<date>.json
    reports/three-factor-ranking.html
    manifest.json entry under category "chips"
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd


STOCK_DB = Path(r"E:\stock_chip_crawler\stock_chip.db")
REVENUE_DB = Path(r"E:\stock_data\mops_index.db")
ROOT = Path(__file__).resolve().parent.parent
DATA_OUT = ROOT / "assets" / "three_factor_ranking.json"
PAGE_OUT = ROOT / "reports" / "three-factor-ranking.html"
MANIFEST_PATH = ROOT / "manifest.json"
HISTORY_DIR_NAME = "three_factor_ranking"
TPE = ZoneInfo("Asia/Taipei")

TOP_N = 50
MIN_FOREIGN_STREAK = 3
MIN_AMOUNT_M = 50.0


def db_uri(path: Path) -> str:
    return f"file:{path.as_posix()}?mode=ro"


def stock_conn() -> sqlite3.Connection:
    return sqlite3.connect(db_uri(STOCK_DB), uri=True, timeout=300)


def revenue_conn() -> sqlite3.Connection:
    return sqlite3.connect(db_uri(REVENUE_DB), uri=True, timeout=120)


def get_latest_institutional_date(conn: sqlite3.Connection) -> str:
    row = conn.execute("SELECT MAX(date) FROM institutional").fetchone()
    return row[0] if row and row[0] else ""


def get_recent_institutional_dates(conn: sqlite3.Connection, end_date: str, limit: int = 30) -> list[str]:
    rows = conn.execute(
        """
        SELECT DISTINCT date
        FROM institutional
        WHERE date <= ?
        ORDER BY date DESC
        LIMIT ?
        """,
        (end_date, limit),
    ).fetchall()
    return [r[0] for r in rows]


def load_foreign_factor(conn: sqlite3.Connection, signal_date: str) -> pd.DataFrame:
    dates = get_recent_institutional_dates(conn, signal_date, 30)
    if not dates:
        return pd.DataFrame()
    oldest = dates[-1]
    df = pd.read_sql_query(
        """
        SELECT date, code, name, foreign_net, total_net
        FROM institutional
        WHERE date >= ? AND date <= ?
        """,
        conn,
        params=(oldest, signal_date),
    )
    if df.empty:
        return pd.DataFrame()

    df["foreign_lots"] = df["foreign_net"] / 1000.0
    df["total_lots"] = df["total_net"] / 1000.0
    df = df.sort_values(["code", "date"], ascending=[True, False])

    records: list[dict] = []
    for code, g in df.groupby("code", sort=False):
        g = g.reset_index(drop=True)
        if g.iloc[0]["date"] != signal_date:
            continue
        streak = 0
        for v in g["foreign_net"].tolist():
            if v > 0:
                streak += 1
            else:
                break
        records.append(
            {
                "code": code,
                "name_inst": g.iloc[0]["name"],
                "foreign_streak": int(streak),
                "foreign_5d_lots": float(g.head(5)["foreign_lots"].sum()),
                "foreign_10d_lots": float(g.head(10)["foreign_lots"].sum()),
                "foreign_buy_days_10": int((g.head(10)["foreign_net"] > 0).sum()),
                "institutional_10d_lots": float(g.head(10)["total_lots"].sum()),
            }
        )
    return pd.DataFrame(records)


def load_revenue_factor(as_of_date: str | None = None) -> tuple[pd.DataFrame, dict]:
    with revenue_conn() as conn:
        where = ""
        params: tuple[str, ...] = ()
        if as_of_date:
            where = "WHERE date(publish_time) <= date(?)"
            params = (as_of_date,)
        df = pd.read_sql_query(
            f"""
            SELECT code, name, roc_year, month, revenue_k, mom, yoy, ytd_yoy, publish_time
            FROM mops_revenue
            {where}
            """,
            conn,
            params=params,
        )
    if df.empty:
        return pd.DataFrame(), {}

    df["ym"] = df["roc_year"] * 100 + df["month"]
    df = df.sort_values(["code", "ym"])
    df["record_months"] = df.groupby("code").cumcount()
    df["prev_high"] = df.groupby("code")["revenue_k"].cummax().groupby(df["code"]).shift(1)
    df["record_margin_pct"] = (df["revenue_k"] / df["prev_high"] - 1) * 100

    latest = df.groupby("code", as_index=False).tail(1).copy()
    global_ym = int(latest["ym"].max())
    latest = latest[latest["ym"] == global_ym].copy()
    latest["revenue_new_high"] = latest["prev_high"].notna() & (latest["revenue_k"] >= latest["prev_high"])
    latest["calendar_year"] = latest["roc_year"] + 1911
    latest["revenue_period"] = latest.apply(lambda r: f"{int(r['calendar_year'])}-{int(r['month']):02d}", axis=1)

    meta = {
        "global_ym": global_ym,
        "roc_year": int(global_ym // 100),
        "month": int(global_ym % 100),
        "calendar_period": f"{int(global_ym // 100) + 1911}-{int(global_ym % 100):02d}",
        "as_of_date": as_of_date,
        "latest_stock_count": int(len(latest)),
        "new_high_count": int(latest["revenue_new_high"].sum()),
    }
    return latest, meta


def load_price_momentum(conn: sqlite3.Connection, signal_date: str, codes: list[str] | None = None) -> pd.DataFrame:
    params: list = [signal_date, signal_date]
    code_filter = ""
    if codes is not None:
        if not codes:
            return pd.DataFrame()
        conn.execute("DROP TABLE IF EXISTS _tf_codes")
        conn.execute("CREATE TEMP TABLE _tf_codes (code TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO _tf_codes(code) VALUES (?)", [(str(c),) for c in codes])
        code_filter = "JOIN _tf_codes c ON dp.code = c.code"

    df = pd.read_sql_query(
        f"""
        SELECT dp.date, dp.code, dp.name, dp.close, dp.high, dp.low, dp.volume, dp.amount
        FROM daily_price dp
        {code_filter}
        WHERE dp.date >= date(?, '-220 days')
          AND dp.date <= ?
          AND dp.close IS NOT NULL
        """,
        conn,
        params=params,
    )
    if df.empty:
        return pd.DataFrame()

    df = df[~df["code"].astype(str).str.startswith("00")].copy()
    df = df.sort_values(["code", "date"])
    records: list[dict] = []

    def pct_return(close_now: float, g: pd.DataFrame, n: int) -> float | None:
        if len(g) <= n:
            return None
        close_then = g.iloc[-1 - n]["close"]
        if pd.isna(close_then) or float(close_then) == 0:
            return None
        return (close_now / float(close_then) - 1) * 100

    for code, g in df.groupby("code", sort=False):
        g = g.reset_index(drop=True)
        if g.iloc[-1]["date"] != signal_date:
            continue
        latest = g.iloc[-1]
        close = float(latest["close"])
        tail20 = g.tail(20)
        tail60 = g.tail(60)
        ma20 = float(tail20["close"].mean()) if len(tail20) else None
        ma60 = float(tail60["close"].mean()) if len(tail60) else None
        high20 = float(tail20["high"].max()) if len(tail20) else None
        high60 = float(tail60["high"].max()) if len(tail60) else None
        low60 = float(tail60["low"].min()) if len(tail60) else None
        pos60 = None
        if high60 is not None and low60 is not None and high60 > low60:
            pos60 = (close - low60) / (high60 - low60) * 100
        close_to_high20_pct = close / high20 * 100 if high20 else None
        close_vs_ma20_pct = (close / ma20 - 1) * 100 if ma20 else None
        close_vs_ma60_pct = (close / ma60 - 1) * 100 if ma60 else None
        records.append(
            {
                "code": code,
                "name": latest["name"],
                "close": close,
                "ret_5d_prev": pct_return(close, g, 5),
                "ret_10d_prev": pct_return(close, g, 10),
                "ret_20d_prev": pct_return(close, g, 20),
                "ret_60d_prev": pct_return(close, g, 60),
                "ma20": ma20,
                "ma60": ma60,
                "high20": high20,
                "high60": high60,
                "pos60": pos60,
                "close_to_high20_pct": close_to_high20_pct,
                "close_vs_ma20_pct": close_vs_ma20_pct,
                "close_vs_ma60_pct": close_vs_ma60_pct,
                "amount_m": float(latest["amount"] or 0) / 1_000_000,
                "volume_lots": float(latest["volume"] or 0) / 1000.0,
            }
        )
    out = pd.DataFrame(records)
    if out.empty:
        return out
    out["momentum_pass"] = (
        (out["ret_20d_prev"] > 0)
        & (out["ret_60d_prev"] > 0)
        & (out["close"] > out["ma20"])
        & (out["pos60"] >= 70)
        & (out["amount_m"] >= MIN_AMOUNT_M)
    )
    return out


def get_forward_returns(codes: list[str], signal_date: str, days_list: list[int] | None = None) -> dict:
    if days_list is None:
        days_list = [1, 3, 5, 10, 20]
    if not codes:
        return {}
    max_days = max(days_list)
    upper_offset_days = max_days * 2
    with stock_conn() as conn:
        conn.execute("CREATE TEMP TABLE _codes (code TEXT PRIMARY KEY)")
        conn.executemany("INSERT INTO _codes(code) VALUES (?)", [(c,) for c in codes])
        df = pd.read_sql_query(
            """
            SELECT dp.code, dp.date, dp.close
            FROM daily_price dp
            JOIN _codes c ON dp.code = c.code
            WHERE dp.date >= ?
              AND dp.date <= date(?, ?)
              AND dp.close IS NOT NULL
            ORDER BY dp.code, dp.date
            """,
            conn,
            params=(signal_date, signal_date, f"+{upper_offset_days} days"),
        )
    out: dict[str, dict] = {}
    for code, g in df.groupby("code"):
        g = g.reset_index(drop=True)
        if g.empty or g.iloc[0]["date"] != signal_date:
            continue
        base = float(g.iloc[0]["close"])
        if base == 0:
            continue
        row = {}
        for n in days_list:
            if n < len(g):
                row[f"ret_{n}d"] = round((float(g.iloc[n]["close"]) / base - 1) * 100, 2)
            else:
                row[f"ret_{n}d"] = None
        out[code] = row
    return out


def rank_pct(s: pd.Series) -> pd.Series:
    return s.rank(pct=True).fillna(0) * 100


def clip_num(v, lo: float, hi: float) -> float:
    if pd.isna(v):
        return 0.0
    return float(max(lo, min(hi, v)))


def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["foreign_rank"] = rank_pct(df["foreign_10d_lots"])
    df["ret20_rank"] = rank_pct(df["ret_20d_prev"])
    df["ret60_rank"] = rank_pct(df["ret_60d_prev"])

    df["foreign_score"] = (
        df["foreign_streak"].clip(upper=10) / 10 * 55
        + df["foreign_rank"] * 0.30
        + df["foreign_buy_days_10"].clip(upper=10) / 10 * 15
    )
    df["revenue_score"] = df.apply(
        lambda r: min(
            100.0,
            60.0
            + clip_num(r.get("record_margin_pct"), 0, 80) * 0.25
            + clip_num(r.get("yoy"), 0, 300) * 0.10
            + clip_num(r.get("mom"), 0, 100) * 0.05
            + clip_num(r.get("ytd_yoy"), 0, 300) * 0.05,
        ),
        axis=1,
    )
    df["momentum_score"] = (
        df["ret20_rank"] * 0.35
        + df["ret60_rank"] * 0.30
        + df["pos60"].clip(lower=0, upper=100).fillna(0) * 0.25
        + df["close_to_high20_pct"].clip(lower=0, upper=100).fillna(0) * 0.10
    )
    df["score"] = df["foreign_score"] * 0.35 + df["revenue_score"] * 0.35 + df["momentum_score"] * 0.30
    return df


def serialize_rows(df: pd.DataFrame) -> list[dict]:
    def opt(r: pd.Series, col: str, ndigits: int | None = 2):
        v = r.get(col)
        if pd.isna(v):
            return None
        if ndigits is None:
            return int(v)
        return round(float(v), ndigits)

    rows = []
    for i, (_, r) in enumerate(df.iterrows(), start=1):
        rows.append(
            {
                "rank": i,
                "code": str(r["code"]),
                "name": r.get("name") or r.get("name_inst") or str(r["code"]),
                "score": opt(r, "score", 1),
                "foreign_score": opt(r, "foreign_score", 1),
                "revenue_score": opt(r, "revenue_score", 1),
                "momentum_score": opt(r, "momentum_score", 1),
                "close": opt(r, "close", 2),
                "amount_m": opt(r, "amount_m", 1),
                "foreign_streak": opt(r, "foreign_streak", None),
                "foreign_5d_lots": opt(r, "foreign_5d_lots", 0),
                "foreign_10d_lots": opt(r, "foreign_10d_lots", 0),
                "foreign_buy_days_10": opt(r, "foreign_buy_days_10", None),
                "institutional_10d_lots": opt(r, "institutional_10d_lots", 0),
                "revenue_period": r.get("revenue_period"),
                "revenue_k": opt(r, "revenue_k", 0),
                "prev_high": opt(r, "prev_high", 0),
                "record_margin_pct": opt(r, "record_margin_pct", 2),
                "mom": opt(r, "mom", 2),
                "yoy": opt(r, "yoy", 2),
                "ytd_yoy": opt(r, "ytd_yoy", 2),
                "ret_5d_prev": opt(r, "ret_5d_prev", 2),
                "ret_10d_prev": opt(r, "ret_10d_prev", 2),
                "ret_20d_prev": opt(r, "ret_20d_prev", 2),
                "ret_60d_prev": opt(r, "ret_60d_prev", 2),
                "pos60": opt(r, "pos60", 1),
                "close_to_high20_pct": opt(r, "close_to_high20_pct", 1),
                "close_vs_ma20_pct": opt(r, "close_vs_ma20_pct", 2),
                "close_vs_ma60_pct": opt(r, "close_vs_ma60_pct", 2),
                "ret_1d": opt(r, "ret_1d", 2),
                "ret_3d": opt(r, "ret_3d", 2),
                "ret_5d": opt(r, "ret_5d", 2),
                "ret_10d": opt(r, "ret_10d", 2),
                "ret_20d": opt(r, "ret_20d", 2),
            }
        )
    return rows


def build(signal_date: str | None = None) -> dict:
    with stock_conn() as conn:
        latest_inst = get_latest_institutional_date(conn)
        if not latest_inst:
            raise RuntimeError("institutional table is empty")
        signal_date = signal_date or latest_inst
        if signal_date > latest_inst:
            raise RuntimeError(f"signal date {signal_date} is later than latest institutional date {latest_inst}")

        print(f"[INFO] signal date: {signal_date}")
        print("[1/3] foreign consecutive buying...")
        foreign = load_foreign_factor(conn, signal_date)
        print(f"    -> {len(foreign)} stocks")

        print("[2/3] monthly revenue new highs...")
        revenue, revenue_meta = load_revenue_factor(signal_date)
        print(f"    -> {len(revenue)} latest revenue stocks, {revenue_meta.get('new_high_count', 0)} new highs")

        if foreign.empty or revenue.empty:
            raise RuntimeError("missing factor data")

        pre = foreign.merge(revenue, on="code", how="inner")
        pre = pre[~pre["code"].astype(str).str.startswith("00")].copy()
        pre["foreign_pass"] = (pre["foreign_streak"] >= MIN_FOREIGN_STREAK) & (pre["foreign_5d_lots"] > 0)
        pre["revenue_pass"] = pre["revenue_new_high"] == True
        candidate_codes = pre.loc[pre["foreign_pass"] & pre["revenue_pass"], "code"].astype(str).tolist()

        print("[3/3] price momentum for foreign+revenue candidates...")
        momentum = load_price_momentum(conn, signal_date, candidate_codes)
        print(f"    -> {len(momentum)} candidate stocks")

    no_candidates = not candidate_codes
    if no_candidates:
        # A zero-sized intersection is a valid market result, not missing source
        # data.  Keep the upstream counts so the empty output remains traceable.
        df = pre.copy()
        df["momentum_pass"] = False
        df["factor_count"] = df[["foreign_pass", "revenue_pass", "momentum_pass"]].sum(axis=1)
        strict = df.iloc[0:0].copy()
    else:
        if momentum.empty:
            raise RuntimeError(
                f"missing price momentum data for {len(candidate_codes)} foreign/revenue candidates"
            )
        df = pre.merge(momentum, on="code", how="inner")
        df["momentum_pass"] = df["momentum_pass"] == True
        df["factor_count"] = df[["foreign_pass", "revenue_pass", "momentum_pass"]].sum(axis=1)
        df = add_scores(df)

        strict = df[df["factor_count"] == 3].copy()
        strict = strict.sort_values(
            ["score", "foreign_streak", "ret_20d_prev", "yoy"],
            ascending=[False, False, False, False],
        ).head(TOP_N)

    forward_map = get_forward_returns(strict["code"].astype(str).tolist(), signal_date)
    for col in ["ret_1d", "ret_3d", "ret_5d", "ret_10d", "ret_20d"]:
        strict[col] = strict["code"].map(lambda c: forward_map.get(str(c), {}).get(col))

    top = strict.iloc[0] if not strict.empty else None
    out = {
        "generated_at": datetime.now(TPE).isoformat(),
        "signal_date": signal_date,
        "institutional_date": signal_date,
        "price_date": signal_date,
        "data_status": "no_candidates" if no_candidates else "ok",
        "status_note": (
            "外資連買與營收創高的交集為 0 檔；這是有效空結果，不是資料缺漏。"
            if no_candidates
            else None
        ),
        "source_counts": {
            "foreign_rows": int(len(foreign)),
            "revenue_rows": int(len(revenue)),
            "foreign_revenue_candidates": int(len(candidate_codes)),
            "momentum_rows": int(len(momentum)),
        },
        "revenue_period": revenue_meta,
        "thresholds": {
            "foreign_streak_days": MIN_FOREIGN_STREAK,
            "foreign_5d_lots": "> 0",
            "revenue": "latest month revenue >= prior available high",
            "momentum": {
                "ret_20d_prev": "> 0",
                "ret_60d_prev": "> 0",
                "close": "> ma20",
                "pos60": ">= 70",
                "amount_m": f">= {MIN_AMOUNT_M}",
            },
        },
        "stats": {
            "merged_universe": int(len(df)),
            "foreign_pass_count": int(df["foreign_pass"].sum()),
            "revenue_pass_count": int(df["revenue_pass"].sum()),
            "momentum_pass_count": int(df["momentum_pass"].sum()),
            "three_factor_count": int((df["factor_count"] == 3).sum()),
            "top_code": str(top["code"]) if top is not None else None,
            "top_name": (top.get("name") or top.get("name_inst")) if top is not None else None,
            "top_score": round(float(top["score"]), 1) if top is not None else None,
        },
        "rankings": serialize_rows(strict),
    }
    return out


def get_institutional_dates_between(start_date: str, end_date: str | None = None) -> list[str]:
    with stock_conn() as conn:
        if end_date:
            rows = conn.execute(
                """
                SELECT DISTINCT date
                FROM institutional
                WHERE date >= ? AND date <= ?
                ORDER BY date
                """,
                (start_date, end_date),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT date
                FROM institutional
                WHERE date >= ?
                ORDER BY date
                """,
                (start_date,),
            ).fetchall()
    return [r[0] for r in rows]


PAGE_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <meta name="robots" content="noindex, nofollow" />
  <title>三因子波段排行榜 — charles16888</title>
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link rel="stylesheet" href="../assets/fonts.css" />
  <link rel="stylesheet" href="../assets/style.css" />
  <style>
    body.tf-page {
      background: #f3efe6;
      color: #201914;
    }
    .tf-page .masthead {
      background: #f3efe6;
      border-bottom-color: rgba(32,25,20,0.12);
    }
    .tf-page .brand,
    .tf-page .brand__mark {
      color: #201914;
    }
    .tf-page .brand__mark em {
      color: #8b6718;
    }
    .tf-page .brand__plate,
    .tf-page .nav__link {
      color: #5f5144;
    }
    .tf-page .nav__link:hover,
    .tf-page .nav__link.is-active {
      color: #201914;
    }
    .tf-page .nav__link--disabled {
      color: #8f8376;
    }
    .tf-page .breadcrumb,
    .tf-page .breadcrumb a,
    .tf-page .report-meta-line,
    .tf-page .report-lead {
      color: #6a5843;
    }
    .tf-page .report-title {
      color: #201914;
    }
    .tf-page .report-title em {
      color: #8b6718;
    }
    .tf-meta-row {
      display: flex; flex-wrap: wrap; gap: 20px; margin: 16px 0 24px;
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
      color: #6c6257; letter-spacing: .08em;
    }
    .tf-meta-row strong { color: #201914; font-weight: 750; }
    .tf-panel {
      border: 1px solid rgba(139,103,24,0.28);
      background: #fbf8f1;
      margin-bottom: 28px;
      box-shadow: 0 10px 24px rgba(32,25,20,0.05);
    }
    .tf-panel__head {
      display: flex; align-items: end; justify-content: space-between; gap: 16px;
      padding: 16px 18px; border-bottom: 1px solid rgba(139,103,24,0.38);
    }
    .tf-panel__title {
      font-family: 'Noto Serif TC', serif; color: #201914; font-size: 17px;
      margin: 0; font-weight: 700; letter-spacing: .02em;
    }
    .tf-panel__note {
      margin: 4px 0 0; color: #615448; font-size: 11px;
      font-family: 'JetBrains Mono', monospace; letter-spacing: .06em;
    }
    .tf-table-wrap { overflow-x: auto; }
    .tf-table {
      width: 100%; min-width: 1180px; border-collapse: collapse;
      font-family: 'JetBrains Mono', monospace; font-size: 11px;
    }
    .tf-table th {
      position: sticky; top: 0; z-index: 1; background: #1a1612;
      color: #f4efe6; text-align: right; padding: 10px 11px;
      border-bottom: 1px solid rgba(232,223,211,0.12);
      font-weight: 500; letter-spacing: .08em; white-space: nowrap;
    }
    .tf-table td {
      color: #332a23; text-align: right; padding: 10px 11px;
      border-bottom: 1px dashed rgba(32,25,20,0.12); vertical-align: middle;
      white-space: nowrap;
    }
    .tf-table th:nth-child(2),
    .tf-table td:nth-child(2),
    .tf-table th:nth-child(3),
    .tf-table td:nth-child(3) { text-align: left; }
    .tf-rank { color: #776b5e; }
    .tf-code { color: #8b6718; font-weight: 800; }
    .tf-name { color: #201914; font-family: 'Noto Serif TC', serif; font-size: 13px; font-weight: 650; }
    .tf-score {
      display: inline-flex; align-items: center; justify-content: center;
      width: 42px; height: 24px; border: 1px solid rgba(139,103,24,0.45);
      color: #5f4308; background: rgba(212,175,55,0.18);
      font-weight: 750;
    }
    .tf-badges { display: inline-flex; gap: 5px; }
    .tf-badge {
      display: inline-flex; align-items: center; justify-content: center;
      height: 20px; min-width: 24px; padding: 0 6px;
      border: 1px solid rgba(32,25,20,0.18);
      color: #3a3029; background: rgba(32,25,20,0.035);
      font-size: 10px;
    }
    .tf-badge--up { color: #9f1f2d; border-color: rgba(159,31,45,0.38); background: rgba(159,31,45,0.08); }
    .tf-table td.tf-num-pos,
    .tf-table .tf-num-pos { color: #b3262d; font-weight: 800; }
    .tf-table td.tf-num-neg,
    .tf-table .tf-num-neg { color: #16733c; font-weight: 800; }
    .tf-table .tf-muted,
    .tf-muted { color: #756a5e; }
    .tf-date-picker {
      background: #fffaf0;
      border: 1px solid rgba(139,103,24,0.42);
      color: #201914;
      padding: 5px 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 11px;
    }
    .tf-date-picker option { background: #fffaf0; color: #201914; }
    @media (max-width: 760px) {
      .tf-panel__head { align-items: start; flex-direction: column; }
      .tf-table { min-width: 980px; }
    }
  </style>
</head>
<body class="tf-page">
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
          <a class="nav__link" href="../category.html?cat=calendar">行事曆</a>
          <a class="nav__link" href="../category.html?cat=txo">選擇權</a>
          <a class="nav__link is-active" href="../category.html?cat=chips">籌碼</a>
          <a class="nav__link" href="../category.html?cat=stocks">個股</a>
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
      <span>三因子波段排行榜</span>
    </nav>

    <section class="report-cover reveal reveal-d1">
      <div class="report-meta-line">
        <span><strong>籌碼</strong> · 三因子波段</span>
        <span>外資連買 · 營收創高 · 強者恆強</span>
        <span>切換日期：<select id="date-picker" class="tf-date-picker" aria-label="切換歷史日期"></select></span>
      </div>
      <h1 class="report-title">三因子 <em>波段排行榜</em></h1>
      <p class="report-lead">
        同時通過外資連日買超、最新月營收創資料庫新高、股價動能維持強勢的股票才進榜。
        表格後段附上訊號日後 1 / 3 / 5 / 10 / 20 個交易日報酬；尚未發生的未來欄位會留白。
      </p>
      <div class="tf-meta-row" id="tf-stats"></div>
    </section>

    <section class="tf-panel">
      <div class="tf-panel__head">
        <div>
          <h2 class="tf-panel__title">三因子同步 Top 50</h2>
          <p class="tf-panel__note">Score = 外資 35% + 營收 35% + 動能 30%</p>
        </div>
        <div class="tf-panel__note">Signal date <strong id="tf-date">—</strong></div>
      </div>
      <div class="tf-table-wrap" id="tf-table"></div>
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
  const HISTORY_DIR = '../assets/chip_history/three_factor_ranking';
  const urlDate = new URLSearchParams(window.location.search).get('date');

  function cls(v) {
    if (v == null) return '';
    if (v > 0) return 'tf-num-pos';
    if (v < 0) return 'tf-num-neg';
    return '';
  }
  function signed(v, digits = 1, suffix = '%') {
    if (v == null || Number.isNaN(Number(v))) return '<span class="tf-muted">—</span>';
    const n = Number(v);
    const sign = n > 0 ? '+' : '';
    return `${sign}${n.toFixed(digits)}${suffix}`;
  }
  function plain(v, digits = 0) {
    if (v == null || Number.isNaN(Number(v))) return '<span class="tf-muted">—</span>';
    return Number(v).toLocaleString('en-US', {maximumFractionDigits: digits, minimumFractionDigits: digits});
  }
  function lots(v) { return plain(v, 0); }
  function revenueYi(v) {
    if (v == null) return '<span class="tf-muted">—</span>';
    return (Number(v) / 100000).toLocaleString('en-US', {maximumFractionDigits: 1}) + ' 億';
  }
  function renderStats() {
    const s = DATA.stats || {};
    const rp = DATA.revenue_period || {};
    document.getElementById('tf-date').textContent = DATA.signal_date || '—';
    document.getElementById('tf-stats').innerHTML = [
      `<span>訊號日：<strong>${DATA.signal_date || '—'}</strong></span>`,
      `<span>營收期別：<strong>${rp.calendar_period || '—'}</strong></span>`,
      `<span>三因子同步：<strong>${s.three_factor_count || 0}</strong> 檔</span>`,
      `<span>外資條件：<strong>${s.foreign_pass_count || 0}</strong> 檔</span>`,
      `<span>營收創高：<strong>${s.revenue_pass_count || 0}</strong> 檔</span>`,
      `<span>強勢動能：<strong>${s.momentum_pass_count || 0}</strong> 檔</span>`
    ].join('');
  }
  function renderTable() {
    const rows = DATA.rankings || [];
    if (!rows.length) {
      document.getElementById('tf-table').innerHTML = '<p style="padding:18px;color:#7e756b">目前沒有同時通過三個條件的股票。</p>';
      return;
    }
    const trs = rows.map(r => `
      <tr>
        <td class="tf-rank">${r.rank}</td>
        <td><span class="tf-code">${r.code}</span></td>
        <td><span class="tf-name">${r.name}</span></td>
        <td><span class="tf-score">${plain(r.score, 1)}</span></td>
        <td><span class="tf-badges"><span class="tf-badge tf-badge--up">外</span><span class="tf-badge tf-badge--up">收</span><span class="tf-badge tf-badge--up">勢</span></span></td>
        <td>${plain(r.close, 2)}</td>
        <td class="tf-num-pos">${r.foreign_streak} 日</td>
        <td class="${cls(r.foreign_5d_lots)}">${lots(r.foreign_5d_lots)}</td>
        <td class="${cls(r.foreign_10d_lots)}">${lots(r.foreign_10d_lots)}</td>
        <td>${revenueYi(r.revenue_k)}</td>
        <td class="${cls(r.record_margin_pct)}">${signed(r.record_margin_pct, 1)}</td>
        <td class="${cls(r.yoy)}">${signed(r.yoy, 1)}</td>
        <td class="${cls(r.mom)}">${signed(r.mom, 1)}</td>
        <td class="${cls(r.ret_20d_prev)}">${signed(r.ret_20d_prev, 1)}</td>
        <td class="${cls(r.ret_60d_prev)}">${signed(r.ret_60d_prev, 1)}</td>
        <td>${plain(r.pos60, 0)}</td>
        <td class="${cls(r.ret_1d)}">${signed(r.ret_1d, 1)}</td>
        <td class="${cls(r.ret_3d)}">${signed(r.ret_3d, 1)}</td>
        <td class="${cls(r.ret_5d)}">${signed(r.ret_5d, 1)}</td>
        <td class="${cls(r.ret_10d)}">${signed(r.ret_10d, 1)}</td>
        <td class="${cls(r.ret_20d)}">${signed(r.ret_20d, 1)}</td>
      </tr>
    `).join('');
    document.getElementById('tf-table').innerHTML = `
      <table class="tf-table">
        <thead>
          <tr>
            <th>#</th><th>代號</th><th>名稱</th><th>分數</th><th>因子</th>
            <th>收盤</th><th>連買</th><th>外資5d</th><th>外資10d</th>
            <th>月營收</th><th>創高幅度</th><th>YoY</th><th>MoM</th>
            <th>前20d</th><th>前60d</th><th>60d位置</th>
            <th>1日後</th><th>3日後</th><th>5日後</th><th>10日後</th><th>20日後</th>
          </tr>
        </thead>
        <tbody>${trs}</tbody>
      </table>`;
  }
  async function loadIndex() {
    try {
      const idx = await fetch(`${HISTORY_DIR}/_index.json?t=${Date.now()}`).then(r => r.json());
      const dates = idx.dates || [];
      const sel = document.getElementById('date-picker');
      sel.innerHTML = dates.map(d => `<option value="${d}">${d}</option>`).join('');
      const wantDate = urlDate && dates.includes(urlDate) ? urlDate : DATA.signal_date;
      sel.value = wantDate;
      sel.addEventListener('change', () => loadDate(sel.value));
      if (wantDate && wantDate !== DATA.signal_date) {
        await loadDate(wantDate);
      }
    } catch (e) {
      console.warn('[three-factor date-picker]', e);
    }
  }
  async function loadDate(date) {
    try {
      DATA = await fetch(`${HISTORY_DIR}/${date}.json?t=${Date.now()}`).then(r => r.json());
      renderStats();
      renderTable();
    } catch (e) {
      console.warn('[three-factor loadDate]', date, e);
    }
  }
  renderStats();
  renderTable();
  loadIndex();
  </script>
</body>
</html>
"""


def render_html(out: dict) -> None:
    html = PAGE_TEMPLATE.replace("__DATA_JSON__", json.dumps(out, ensure_ascii=False))
    PAGE_OUT.parent.mkdir(parents=True, exist_ok=True)
    PAGE_OUT.write_text(html, encoding="utf-8")


def update_history(out: dict) -> None:
    hist_dir = ROOT / "assets" / "chip_history" / HISTORY_DIR_NAME
    hist_dir.mkdir(parents=True, exist_ok=True)
    date = out["signal_date"]
    (hist_dir / f"{date}.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    idx_path = hist_dir / "_index.json"
    dates = []
    if idx_path.exists():
        try:
            dates = json.loads(idx_path.read_text(encoding="utf-8")).get("dates", [])
        except Exception:
            dates = []
    if date not in dates:
        dates.append(date)
    dates.sort(reverse=True)
    idx_path.write_text(json.dumps({"latest": dates[0] if dates else None, "dates": dates}, ensure_ascii=False), encoding="utf-8")


def update_manifest(out: dict) -> None:
    if not MANIFEST_PATH.exists():
        return
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    stats = out["stats"]
    top = out["rankings"][0] if out["rankings"] else None
    summary = [
        f"外資連買 >= {MIN_FOREIGN_STREAK} 日、最新月營收創高、20/60 日趨勢仍強。",
        f"本次三因子同步 {stats['three_factor_count']} 檔。",
    ]
    if top:
        summary.append(
            f"榜首：{top['code']} {top['name']}，外資連買 {top['foreign_streak']} 日，"
            f"營收 YoY {top['yoy']:+.1f}%，前 20 日 {top['ret_20d_prev']:+.1f}%。"
        )
    entry = {
        "id": "three-factor-ranking",
        "category": "chips",
        "type": "ranking",
        "date": out["signal_date"],
        "time": "21:10",
        "title": "三因子波段排行榜",
        "title_em": "外資×營收×強勢",
        "summary": " ".join(summary),
        "tags": ["波段", "外資連買", "營收創高", "強者恆強", "排行榜"],
        "source_pipeline": "stock_chip_crawler+mops_index",
        "url": "reports/three-factor-ranking.html",
        "stats": [
            {"label": "三因子同步", "value": f"{stats['three_factor_count']} 檔", "color": "up"},
            {"label": "營收期別", "value": out["revenue_period"].get("calendar_period", "—"), "color": "neutral"},
            {"label": "榜首", "value": f"{top['code']} {top['score']:.1f}" if top else "—", "color": "up"},
        ],
    }
    manifest["entries"] = [e for e in manifest.get("entries", []) if e.get("id") != entry["id"]]
    manifest["entries"].append(entry)
    manifest["entries"].sort(key=lambda e: (e.get("date", ""), e.get("time", ""), e.get("id", "")), reverse=True)
    manifest["updated_at"] = datetime.now(TPE).strftime("%Y-%m-%dT%H:%M:%S+08:00")
    if manifest["entries"]:
        manifest["today"] = manifest["entries"][0]["date"]
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--signal-date", default=None, help="institutional signal date; default uses latest institutional date")
    parser.add_argument("--backfill-from", default=None, help="build dated history for institutional dates >= YYYY-MM-DD")
    parser.add_argument("--backfill-to", default=None, help="build dated history for institutional dates <= YYYY-MM-DD")
    parser.add_argument("--history-only", action="store_true", help="write dated JSON only; skip current JSON/html/manifest")
    args = parser.parse_args()

    if args.backfill_from:
        dates = get_institutional_dates_between(args.backfill_from, args.backfill_to)
        if not dates:
            print(f"[ERR] no institutional dates from {args.backfill_from} to {args.backfill_to or 'latest'}")
            return 1
        print(f"[INFO] backfill {len(dates)} signal dates: {dates[0]} -> {dates[-1]}")
        latest_out = None
        for i, d in enumerate(dates, start=1):
            print(f"[BACKFILL {i}/{len(dates)}] {d}")
            out = build(d)
            update_history(out)
            latest_out = out
            print(f"[OK] assets/chip_history/{HISTORY_DIR_NAME}/{d}.json")
        if not args.history_only and latest_out:
            DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
            DATA_OUT.write_text(json.dumps(latest_out, ensure_ascii=False), encoding="utf-8")
            render_html(latest_out)
            update_manifest(latest_out)
            print("[OK] current JSON/html/manifest refreshed from latest backfill date")
        return 0

    out = build(args.signal_date)
    update_history(out)
    print(f"[OK] assets/chip_history/{HISTORY_DIR_NAME}/{out['signal_date']}.json")
    if args.history_only:
        print("[SKIP] history-only")
        return 0

    DATA_OUT.parent.mkdir(parents=True, exist_ok=True)
    DATA_OUT.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] {DATA_OUT.relative_to(ROOT)}")
    render_html(out)
    print(f"[OK] {PAGE_OUT.relative_to(ROOT)}")
    update_manifest(out)
    print("[OK] manifest.json — three-factor-ranking entry refreshed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
