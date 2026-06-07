"""Read-only data accessors over stock_chip.db.

Conventions:
- All queries open the DB in read-only mode (`mode=ro`) to avoid lock conflicts
  with the crawler (which may be running concurrently).
- `tdcc_holders` is a long-format table; helpers here pivot it into the wide
  format the SPEC expects.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pandas as pd

# Default to the live crawler DB; can be overridden via env var when the
# crawler is holding a write lock (use the snapshot in _cache/).
CHIP_DB = Path(os.environ.get("CHIP_DB_PATH", r"E:\stock_chip_crawler\stock_chip.db"))
# 分點 broker_trading 已拆到獨立的 broker_chip.db（與 stock_chip.db 各自獨立寫鎖）。
# _ro_conn() 會 ATTACH 它並建一個同名 temp view，讓現有 broker_trading 查詢無痛指向新 DB。
BROKER_DB = Path(os.environ.get("BROKER_DB_PATH", r"E:\stock_chip_crawler\broker_chip.db"))

# Tier mapping (TDCC 17 tiers, validated 2026-05-31 against 2330 data)
TIER_400_600   = 12   # 400-600 lots
TIER_600_800   = 13   # 600-800 lots
TIER_800_1000  = 14   # 800-1000 lots
TIER_1000_UP   = 15   # >1000 lots (super whales)
TIER_TOTAL     = 17   # sum of all tiers


def _ro_conn() -> sqlite3.Connection:
    # timeout=60s tolerates the crawler's brief write locks at 20:30 when
    # this is driven from the daily push bat. Set CHIP_DB_PATH to use a
    # snapshot if you need to fully decouple from the live DB.
    c = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=60)
    # 分點拆庫後，broker_trading 在 broker_chip.db；ATTACH(ro) + 同名 temp view，
    # 讓 get_concentration_full_market() 等查詢的裸名 broker_trading 指向新 DB。
    c.execute(f"ATTACH DATABASE 'file:{BROKER_DB.as_posix()}?mode=ro' AS broker")
    c.execute("CREATE TEMP VIEW IF NOT EXISTS broker_trading "
              "AS SELECT * FROM broker.broker_trading")
    return c


def get_tdcc_dates() -> list[str]:
    """Distinct dates in tdcc_holders, newest first."""
    with _ro_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT date FROM tdcc_holders ORDER BY date DESC"
        ).fetchall()
    return [r[0] for r in rows]


def get_shareholder_wide(date: str) -> pd.DataFrame:
    """Pivot tdcc_holders for one snapshot date into wide format.

    Returns columns: code, total_holders, holders_400_600, holders_600_800,
                     holders_800_1000, holders_1000up, pct_400up, pct_1000up.
    """
    with _ro_conn() as c:
        df = pd.read_sql_query(
            """
            SELECT code, tier, holders, ratio
            FROM tdcc_holders
            WHERE date = ?
            """,
            c,
            params=(date,),
        )
    if df.empty:
        return pd.DataFrame()

    # pivot tier × {holders, ratio}
    pv = df.pivot_table(
        index="code", columns="tier", values=["holders", "ratio"], aggfunc="sum"
    )

    out = pd.DataFrame(index=pv.index)
    out["total_holders"]    = pv[("holders", TIER_TOTAL)]
    out["holders_400_600"]  = pv[("holders", TIER_400_600)]
    out["holders_600_800"]  = pv[("holders", TIER_600_800)]
    out["holders_800_1000"] = pv[("holders", TIER_800_1000)]
    out["holders_1000up"]   = pv[("holders", TIER_1000_UP)]
    out["pct_400up"] = (
        pv[("ratio", TIER_400_600)].fillna(0)
        + pv[("ratio", TIER_600_800)].fillna(0)
        + pv[("ratio", TIER_800_1000)].fillna(0)
        + pv[("ratio", TIER_1000_UP)].fillna(0)
    )
    out["pct_1000up"] = pv[("ratio", TIER_1000_UP)].fillna(0)
    out = out.reset_index()
    return out


def get_close_at(date: str, codes: list[str] | None = None, lookback_days: int = 14) -> pd.DataFrame:
    """Latest close on or before `date` for each requested code.

    Strategy: pull a small date window once (date - 14d → date), use pandas
    to pick max(date) per code. Uses `idx_dp_date` for fast scan.

    Returns columns: code, date_actual, close, name.
    Skips rows with NULL close.
    """
    with _ro_conn() as c:
        df = pd.read_sql_query(
            """
            SELECT code, date AS date_actual, close, name
            FROM daily_price
            WHERE date >= date(?, ?) AND date <= ?
              AND close IS NOT NULL
            """,
            c,
            params=(date, f"-{lookback_days} days", date),
        )
    if df.empty:
        return pd.DataFrame(columns=["code", "date_actual", "close", "name"])

    # 每股取 max date 那筆
    df = df.sort_values(["code", "date_actual"]).drop_duplicates("code", keep="last")
    df["close"] = df["close"].astype(float)
    if codes:
        df = df[df["code"].isin(codes)]
    return df.reset_index(drop=True)


def get_concentration_full_market(end_date: str, lookback_days: int = 5) -> pd.DataFrame:
    """Full-market broker concentration over a rolling window ending at `end_date`.

    Mirrors compute_ranking_for_window in build_chip_concentration.py but returns
    ALL stocks (not just TOP 50). Output columns:
        code, total_buy_lots, total_sell_lots, main_buy_lots, main_sell_lots,
        total_volume_lots, concentration_pct
    """
    TOP_BROKERS_PER_SIDE = 15
    MIN_VOLUME_SHARES = 1_000_000  # 1000 lots
    SHARES_PER_LOT = 1000

    with _ro_conn() as c:
        df = pd.read_sql_query(
            """
            SELECT date, code, broker_id, broker_name, buy_vol, sell_vol, net_vol
            FROM broker_trading
            WHERE date > date(?, ?) AND date <= ?
            """,
            c,
            params=(end_date, f"-{lookback_days} days", end_date),
        )
    if df.empty:
        return pd.DataFrame()

    # 累計每股 × 分點 net_vol
    grp = df.groupby(["code", "broker_id"], as_index=False).agg(
        buy_vol=("buy_vol", "sum"),
        sell_vol=("sell_vol", "sum"),
        net_vol=("net_vol", "sum"),
    )

    results = []
    for code, sub in grp.groupby("code"):
        total_buy_shares = sub["buy_vol"].sum()
        total_sell_shares = sub["sell_vol"].sum()
        total_volume_shares = max(total_buy_shares, total_sell_shares)
        if total_volume_shares < MIN_VOLUME_SHARES:
            continue
        # asymmetric 過濾（>50%）
        if min(total_buy_shares, total_sell_shares) < total_volume_shares * 0.5:
            continue

        positives = sub[sub["net_vol"] > 0].nlargest(TOP_BROKERS_PER_SIDE, "net_vol")
        negatives = sub[sub["net_vol"] < 0].nsmallest(TOP_BROKERS_PER_SIDE, "net_vol")
        main_buy = positives["net_vol"].sum()
        main_sell = abs(negatives["net_vol"].sum())
        conc_pct = (main_buy - main_sell) / total_volume_shares * 100

        if abs(conc_pct) > 150:  # outlier drop
            continue

        results.append({
            "code": code,
            "total_buy_lots": total_buy_shares / SHARES_PER_LOT,
            "total_sell_lots": total_sell_shares / SHARES_PER_LOT,
            "main_buy_lots": main_buy / SHARES_PER_LOT,
            "main_sell_lots": main_sell / SHARES_PER_LOT,
            "total_volume_lots": total_volume_shares / SHARES_PER_LOT,
            "concentration_pct": conc_pct,
        })
    return pd.DataFrame(results)


def get_institutional_window(end_date: str, lookback_days: int = 5) -> pd.DataFrame:
    """Sum 三大法人 net over a rolling window. Returns code + foreign_lots +
    trust_lots + dealer_lots + total_lots (張)."""
    SHARES_PER_LOT = 1000
    with _ro_conn() as c:
        df = pd.read_sql_query(
            """
            SELECT code,
                   SUM(foreign_net) AS foreign_net_shares,
                   SUM(trust_net) AS trust_net_shares,
                   SUM(dealer_net) AS dealer_net_shares,
                   SUM(total_net) AS total_net_shares
            FROM institutional
            WHERE date > date(?, ?) AND date <= ?
            GROUP BY code
            """,
            c,
            params=(end_date, f"-{lookback_days} days", end_date),
        )
    if df.empty:
        return pd.DataFrame(columns=["code", "foreign_lots", "trust_lots", "dealer_lots", "total_lots"])
    df["foreign_lots"] = df["foreign_net_shares"] / SHARES_PER_LOT
    df["trust_lots"]   = df["trust_net_shares"]   / SHARES_PER_LOT
    df["dealer_lots"]  = df["dealer_net_shares"]  / SHARES_PER_LOT
    df["total_lots"]   = df["total_net_shares"]   / SHARES_PER_LOT
    return df[["code", "foreign_lots", "trust_lots", "dealer_lots", "total_lots"]]


def get_future_returns(codes: list[str], base_date: str, days_list: list[int] = None) -> dict:
    """
    對每個 code 計算從 base_date 起算後 1/3/5/10/20 個交易日的漲跌幅。

    Returns: {code: {"ret_1d": ±%, "ret_3d": ±%, "ret_5d": ±%, "ret_10d": ±%, "ret_20d": ±%}}
    若該未來日期還沒到 → 該欄位 = None
    """
    if days_list is None:
        days_list = [1, 3, 5, 10, 20]
    if not codes:
        return {}

    max_lookforward = max(days_list)
    # 限制日期上限：max_lookforward * 1.6 calendar days (含週末/休市緩衝)
    # 例如 20 個交易日 ≈ 32 個日曆日，給 1.6x 緩衝 ≈ 32 天上限
    upper_offset_days = max_lookforward * 2  # 2x 緩衝
    with _ro_conn() as c:
        c.execute("CREATE TEMP TABLE IF NOT EXISTS _fr_codes (code TEXT PRIMARY KEY)")
        c.execute("DELETE FROM _fr_codes")
        c.executemany("INSERT INTO _fr_codes(code) VALUES (?)", [(x,) for x in codes])
        df = pd.read_sql_query(
            """
            SELECT dp.code, dp.date, dp.close
            FROM daily_price dp
            JOIN _fr_codes f ON dp.code = f.code
            WHERE dp.date >= ? AND dp.date <= date(?, ?) AND dp.close IS NOT NULL
            ORDER BY dp.code, dp.date
            """,
            c,
            params=(base_date, base_date, f"+{upper_offset_days} days"),
        )

    result = {}
    for code, sub in df.groupby("code"):
        sub = sub.reset_index(drop=True)
        # 第一筆必須是 base_date 才算（不然該 code 在 base_date 沒掛牌或停牌）
        if sub.iloc[0]["date"] != base_date:
            continue
        close_base = float(sub.iloc[0]["close"])
        if close_base == 0:
            continue
        row = {}
        for n in days_list:
            if n < len(sub):
                close_n = float(sub.iloc[n]["close"])
                row[f"ret_{n}d"] = round((close_n - close_base) / close_base * 100, 2)
            else:
                row[f"ret_{n}d"] = None
        result[code] = row
    return result


def get_shareholder_week_compare(latest: str, prev: str) -> pd.DataFrame:
    """Build a single-row-per-stock dataframe comparing two tdcc snapshots,
    enriched with close at each snapshot date.

    Returns columns:
        code, name,
        close_prev, close_latest, close_chg, close_chg_pct,
        pct_400up_prev, pct_400up_latest, pct_400up_delta,
        pct_1000up_prev, pct_1000up_latest, pct_1000up_delta,
        holders_1000up_prev, holders_1000up_latest, holders_1000up_delta,
        total_holders_prev, total_holders_latest, total_holders_delta,
    """
    s_latest = get_shareholder_wide(latest)
    s_prev   = get_shareholder_wide(prev)
    if s_latest.empty or s_prev.empty:
        return pd.DataFrame()

    m = s_latest.merge(s_prev, on="code", suffixes=("_latest", "_prev"))

    m["pct_400up_delta"] = m["pct_400up_latest"] - m["pct_400up_prev"]
    m["pct_1000up_delta"] = m["pct_1000up_latest"] - m["pct_1000up_prev"]
    m["holders_1000up_delta"] = m["holders_1000up_latest"] - m["holders_1000up_prev"]
    m["total_holders_delta"] = m["total_holders_latest"] - m["total_holders_prev"]

    # join close at both dates
    codes = m["code"].tolist()
    cl_latest = get_close_at(latest, codes).rename(
        columns={"close": "close_latest", "date_actual": "date_latest_actual"}
    )
    cl_prev = get_close_at(prev, codes).rename(
        columns={"close": "close_prev", "date_actual": "date_prev_actual", "name": "name_prev"}
    )
    m = m.merge(cl_latest[["code", "close_latest", "date_latest_actual", "name"]], on="code", how="left")
    m = m.merge(cl_prev[["code", "close_prev", "date_prev_actual"]], on="code", how="left")

    m["close_chg"] = m["close_latest"] - m["close_prev"]
    m["close_chg_pct"] = (m["close_chg"] / m["close_prev"]) * 100

    return m
