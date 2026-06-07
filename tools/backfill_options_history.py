"""
backfill_options_history.py
============================
Back-fill the market_pulse_daily table with TXO ATM Call+Put history fetched
direct from TAIFEX's 選擇權每日簡表 (optDailyMarketReport).

Why: build_options_chart.py reads market_pulse_daily, but that table only
contains the past ~23 days (since industry_map started writing). We want the
same multi-year history view we now have for the futures basis chart.

Source: industry_map's market_pulse.py already speaks fluent TAIFEX — we
reuse its helpers, but with a compatibility shim because TAIFEX changed the
options table layout sometime in 2025 (added a 契約最後交易日 column).

Run:
    python tools/backfill_options_history.py --years 3
    python tools/backfill_options_history.py --start 2023-01-01
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

# Pull in industry_map's TAIFEX/TWSE helpers.
INDUSTRY_DIR = Path(r"E:\industry_map")
sys.path.insert(0, str(INDUSTRY_DIR))
from market_pulse import _fetch_taifex, fetch_twse_index_history, TAIFEX_OPT_URL  # noqa: E402

DB = Path(r"E:\industry_map\sector_daily.db")


def calc_settle_date(expiry: str) -> date | None:
    """Estimate TXO contract settlement date from expiry code.

    Codes:
        202406        — monthly (3rd Wed of June 2024)
        202406F1      — monthly (same as bare YYYYMM)
        202406W1..W5  — weekly (Nth Wednesday of June 2024)
    The first Wednesday of the month is W1; W3 = monthly = F1.
    """
    if not expiry or len(expiry) < 6:
        return None
    try:
        y, m = int(expiry[:4]), int(expiry[4:6])
    except ValueError:
        return None
    first = date(y, m, 1)
    # weekday(): Mon=0, Wed=2
    first_wed = first + timedelta(days=(2 - first.weekday()) % 7)
    suffix = expiry[6:].upper()
    if not suffix or suffix.startswith("F"):
        return first_wed + timedelta(weeks=2)   # monthly = 3rd Wed
    if suffix.startswith("W"):
        try:
            n = int(suffix[1:])
        except ValueError:
            return first_wed
        return first_wed + timedelta(weeks=n - 1)
    return first_wed


def fetch_options_atm_compat(query_date: str, ref_index: float) -> list[dict]:
    """TAIFEX TXO ATM Call+Put for one date.

    Layout-tolerant version (industry_map's stock one assumes the post-2025
    20-column schema and silently returns 0 rows for older dates).
        - 20-col (current):   [2]=last_trade_day [3]=strike [4]=cp [9]=close
        - 19-col (≤ ~2024):   [2]=strike [3]=cp [8]=close (no last_trade_day)
    Returns up to 2 contracts (近月 + 次遠月)."""
    df = _fetch_taifex(TAIFEX_OPT_URL, "TXO", query_date)
    if df is None:
        return []
    df.columns = list(range(len(df.columns)))
    ncols = df.shape[1]
    if ncols >= 20:
        strike_col, cp_col, close_col, last_day_col = 3, 4, 9, 2
    else:
        strike_col, cp_col, close_col, last_day_col = 2, 3, 8, None

    df["__code"] = df[0].astype(str).str.strip()
    df = df[df["__code"] == "TXO"].copy()
    if df.empty:
        return []
    df["__expiry"] = df[1].astype(str).str.strip()
    df["__strike"] = pd.to_numeric(df[strike_col], errors="coerce")
    df["__cp"]     = df[cp_col].astype(str).str.strip()
    df["__settle"] = pd.to_numeric(df[close_col], errors="coerce")

    if last_day_col is not None:
        df["__last_trade_day"] = pd.to_numeric(df[last_day_col], errors="coerce")
        qd = int(query_date.replace("/", ""))
        df = df[df["__last_trade_day"] > qd]
        if df.empty:
            return []
        contracts = df.sort_values("__last_trade_day")["__expiry"].drop_duplicates().tolist()[:2]
    else:
        # Legacy: TAIFEX pre-2026 table has no 最後交易日 column, so we estimate
        # the settlement date from the expiry code and sort by that. String
        # sort puts 202406 (= monthly F1, 3rd Wed) before 202406W1 (1st Wed),
        # which gets the near/far pair backwards.
        q_d = datetime.strptime(query_date, "%Y/%m/%d").date()
        ranked = []
        for ctr in df["__expiry"].drop_duplicates():
            sd = calc_settle_date(str(ctr))
            if sd is None or sd <= q_d:
                continue
            ranked.append((sd, ctr))
        ranked.sort()
        contracts = [c for _, c in ranked[:2]]

    out: list[dict] = []
    for ctr in contracts:
        sub = df[df["__expiry"] == ctr]
        strikes = sorted(sub["__strike"].dropna().unique())
        if not strikes:
            continue
        atm = min(strikes, key=lambda s: abs(s - ref_index))
        c_row = sub[(sub["__strike"] == atm) & (sub["__cp"] == "Call")]
        p_row = sub[(sub["__strike"] == atm) & (sub["__cp"] == "Put")]
        c = float(c_row["__settle"].iloc[0]) if not c_row.empty and pd.notna(c_row["__settle"].iloc[0]) else None
        p = float(p_row["__settle"].iloc[0]) if not p_row.empty and pd.notna(p_row["__settle"].iloc[0]) else None
        if c is None or p is None:
            continue
        last_day = int(sub["__last_trade_day"].iloc[0]) if last_day_col is not None else None
        out.append({
            "expiry":        ctr,
            "last_trade_day": last_day,
            "strike":        atm,
            "call":          c,
            "put":           p,
            "cp_sum":        c + p,
        })
    return out


def fetch_one_day(d: date, twse_close: float) -> tuple[date, float, list[dict]]:
    """Worker: fetch options ATM for one day. Returns (date, twse_close, opts)."""
    qd = d.strftime("%Y/%m/%d")
    try:
        opts = fetch_options_atm_compat(qd, twse_close)
    except Exception as e:
        print(f"[WARN] {d}: {e}")
        opts = []
    return d, twse_close, opts


def existing_dates(conn: sqlite3.Connection) -> set[str]:
    """Dates that already have a non-null opt_near_cp_sum — skip those."""
    rows = conn.execute(
        "SELECT date FROM market_pulse_daily WHERE opt_near_cp_sum IS NOT NULL"
    ).fetchall()
    return {r[0] for r in rows}


def upsert_options(conn: sqlite3.Connection, d: date, twse_close: float,
                   opts: list[dict]) -> bool:
    """Insert a row (or fill in option fields on an existing row)."""
    d_str = d.strftime("%Y-%m-%d")
    near = opts[0] if len(opts) >= 1 else {}
    far  = opts[1] if len(opts) >= 2 else {}

    # Check if row exists.
    existing = conn.execute(
        "SELECT 1 FROM market_pulse_daily WHERE date = ?", (d_str,)
    ).fetchone()

    if existing:
        conn.execute(
            """
            UPDATE market_pulse_daily SET
                twse_close          = COALESCE(twse_close, ?),
                opt_near_expiry     = ?,
                opt_near_last_trade_day = ?,
                opt_near_strike     = ?,
                opt_near_call       = ?,
                opt_near_put        = ?,
                opt_near_cp_sum     = ?,
                opt_far_expiry      = ?,
                opt_far_last_trade_day = ?,
                opt_far_strike      = ?,
                opt_far_call        = ?,
                opt_far_put         = ?,
                opt_far_cp_sum      = ?,
                updated_at          = datetime('now', 'localtime')
            WHERE date = ?
            """,
            (
                twse_close,
                near.get("expiry"), near.get("last_trade_day"), near.get("strike"),
                near.get("call"), near.get("put"), near.get("cp_sum"),
                far.get("expiry"), far.get("last_trade_day"), far.get("strike"),
                far.get("call"), far.get("put"), far.get("cp_sum"),
                d_str,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO market_pulse_daily (
                date, twse_close,
                opt_near_expiry, opt_near_last_trade_day, opt_near_strike,
                opt_near_call, opt_near_put, opt_near_cp_sum,
                opt_far_expiry, opt_far_last_trade_day, opt_far_strike,
                opt_far_call, opt_far_put, opt_far_cp_sum,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            """,
            (
                d_str, twse_close,
                near.get("expiry"), near.get("last_trade_day"), near.get("strike"),
                near.get("call"), near.get("put"), near.get("cp_sum"),
                far.get("expiry"), far.get("last_trade_day"), far.get("strike"),
                far.get("call"), far.get("put"), far.get("cp_sum"),
            ),
        )
    return True


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--years", type=int, default=3,
                   help="Backfill this many years back from today (default 3).")
    p.add_argument("--start", type=str, default=None,
                   help="Explicit start date YYYY-MM-DD (overrides --years).")
    p.add_argument("--workers", type=int, default=4,
                   help="Parallel TAIFEX requests (default 4 — keep modest).")
    p.add_argument("--force", action="store_true",
                   help="Re-fetch dates that already have data.")
    args = p.parse_args()

    today = date.today()
    if args.start:
        start_d = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start_d = date(today.year - args.years, today.month, today.day)

    print(f"[INFO] Range: {start_d} -> {today}")

    # 1. Fetch TWSE index history (one shot, multi-month inside)
    print("[INFO] Fetching TWSE index history ...")
    idx_df = fetch_twse_index_history(start_d, today)
    idx_map = dict(zip(idx_df["date"], idx_df["close"]))
    print(f"[INFO] TWSE: {len(idx_map)} trading days")

    if not idx_map:
        print("[ERR] No TWSE data — aborting")
        return 1

    # 2. Filter dates already done
    conn = sqlite3.connect(DB, timeout=10)
    skip = set() if args.force else existing_dates(conn)
    targets = sorted(d for d in idx_map if d.strftime("%Y-%m-%d") not in skip)
    print(f"[INFO] Already have {len(skip)} dates — fetching {len(targets)} new ones")

    if not targets:
        print("[INFO] Nothing to do.")
        return 0

    # 3. Parallel fetch options for each date
    print(f"[INFO] Fetching options with {args.workers} workers ...")
    t0 = time.time()
    done = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(fetch_one_day, d, idx_map[d]): d for d in targets}
        for f in as_completed(futures):
            d, twse, opts = f.result()
            if opts:
                upsert_options(conn, d, twse, opts)
                done += 1
            else:
                failed += 1
            if (done + failed) % 50 == 0:
                conn.commit()
                rate = (done + failed) / (time.time() - t0)
                remaining = (len(targets) - done - failed) / max(rate, 0.01)
                print(f"  [{done + failed}/{len(targets)}] ok={done} fail={failed} "
                      f"rate={rate:.1f}/s eta={remaining/60:.1f}min")
    conn.commit()
    conn.close()

    print(f"[DONE] {done} dates upserted, {failed} failed ({time.time()-t0:.0f}s total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
