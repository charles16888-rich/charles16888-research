"""
backfill_all_views.py
======================
回填 chip_concentration / shareholder_divergence / tri_source_lamp / three_factor_ranking 的歷史 dated JSON。

跑法：
    python tools/backfill_all_views.py                 # 預設：4 視圖全跑、最近 30 個交易日
    python tools/backfill_all_views.py --days 60       # 跑近 60 天
    python tools/backfill_all_views.py --only chip     # 只跑 chip_concentration
    python tools/backfill_all_views.py --skip view2    # 跳過 view 2
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sqlite3
from pathlib import Path

LYNUS_ROOT = Path(__file__).resolve().parent.parent
CHIP_DB = Path(os.environ.get("CHIP_DB_PATH", r"E:\stock_chip_crawler\stock_chip.db"))
# 分點 broker_trading 已拆到獨立的 broker_chip.db
BROKER_DB = Path(os.environ.get("BROKER_DB_PATH", r"E:\stock_chip_crawler\broker_chip.db"))

PYTHON = sys.executable
TOOLS = LYNUS_ROOT / "tools"


def get_trading_dates(n: int | None = None) -> list[str]:
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=60)
    conn.execute(f"ATTACH DATABASE 'file:{BROKER_DB.as_posix()}?mode=ro' AS broker")
    conn.execute("CREATE TEMP VIEW IF NOT EXISTS broker_trading AS SELECT * FROM broker.broker_trading")
    rows = conn.execute("SELECT DISTINCT date FROM broker_trading ORDER BY date DESC").fetchall()
    conn.close()
    dates = [r[0] for r in rows]
    return dates[:n] if n else dates


def get_tdcc_dates_all() -> list[str]:
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=60)
    rows = conn.execute("SELECT DISTINCT date FROM tdcc_holders ORDER BY date DESC").fetchall()
    conn.close()
    return [r[0] for r in rows]


def get_institutional_dates(n: int | None = None) -> list[str]:
    conn = sqlite3.connect(f"file:{CHIP_DB}?mode=ro", uri=True, timeout=60)
    rows = conn.execute("SELECT DISTINCT date FROM institutional ORDER BY date DESC").fetchall()
    conn.close()
    dates = [r[0] for r in rows]
    return dates[:n] if n else dates


def run_builder(builder: str, end_date: str) -> bool:
    """Run a builder with --end-date X --history-only. Return True if exit 0."""
    cmd = [PYTHON, str(TOOLS / builder), "--end-date", end_date, "--history-only"]
    # 顯式傳 env，確保 CHIP_DB_PATH (snapshot 路徑) 傳到 subprocess，避免撞 crawler lock
    env = os.environ.copy()
    env["CHIP_DB_PATH"] = str(CHIP_DB)
    env["BROKER_DB_PATH"] = str(BROKER_DB)
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"  → {builder} --end-date {end_date}", flush=True)
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode == 0:
            return True
        print(f"    [FAIL] exit {result.returncode}: {(result.stdout or '')[-200:]}{(result.stderr or '')[-200:]}", flush=True)
        return False
    except subprocess.TimeoutExpired:
        print(f"    [TIMEOUT] {builder} {end_date}", flush=True)
        return False


def run_three_factor(signal_date: str) -> bool:
    cmd = [PYTHON, str(TOOLS / "build_three_factor_ranking.py"), "--signal-date", signal_date, "--history-only"]
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    print(f"  -> build_three_factor_ranking.py --signal-date {signal_date}", flush=True)
    try:
        result = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=600)
        if result.returncode == 0:
            return True
        print(f"    [FAIL] exit {result.returncode}: {(result.stdout or '')[-200:]}{(result.stderr or '')[-200:]}", flush=True)
        return False
    except subprocess.TimeoutExpired:
        print(f"    [TIMEOUT] build_three_factor_ranking.py {signal_date}", flush=True)
        return False


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=30, help="回填最近 N 個交易日（chip + view4）")
    p.add_argument("--only", type=str, default=None,
                   choices=["chip", "view2", "view4", "three_factor"], help="只跑指定一個視圖")
    p.add_argument("--skip", type=str, default=None,
                   choices=["chip", "view2", "view4", "three_factor"], help="跳過指定視圖")
    args = p.parse_args()

    sys.stdout.reconfigure(encoding="utf-8")
    if not CHIP_DB.exists():
        print(f"[ERR] {CHIP_DB} not found")
        return 1

    # 決定要跑哪些
    run_chip  = args.only in (None, "chip")  and args.skip != "chip"
    run_view2 = args.only in (None, "view2") and args.skip != "view2"
    run_view4 = args.only in (None, "view4") and args.skip != "view4"
    run_three = args.only in (None, "three_factor") and args.skip != "three_factor"

    trading_dates = get_trading_dates(args.days)
    institutional_dates = get_institutional_dates(args.days)
    tdcc_dates = get_tdcc_dates_all()
    print(f"[INFO] trading_dates: {len(trading_dates)} ({trading_dates[-1] if trading_dates else '-'} → {trading_dates[0] if trading_dates else '-'})")
    print(f"[INFO] institutional_dates: {len(institutional_dates)} ({institutional_dates[-1] if institutional_dates else '-'} → {institutional_dates[0] if institutional_dates else '-'})")
    print(f"[INFO] tdcc_dates: {len(tdcc_dates)}")

    # chip-concentration: 每個交易日
    if run_chip:
        print(f"\n=== chip_concentration ({len(trading_dates)} days) ===")
        for d in trading_dates:
            run_builder("build_chip_concentration.py", d)

    # view 4 tri-source-lamp: 每個交易日（會自動對齊到 ≤d 的 tdcc）
    if run_view4:
        print(f"\n=== tri_source_lamp ({len(trading_dates)} days) ===")
        for d in trading_dates:
            run_builder("build_view4_tri_source_lamp.py", d)

    if run_three:
        print(f"\n=== three_factor_ranking ({len(institutional_dates)} days) ===")
        for d in institutional_dates:
            run_three_factor(d)

    # view 2 shareholder-divergence: 每個 tdcc 週（不是每個交易日）
    if run_view2:
        # 至少要有 prev 才能跑，所以跳掉最舊那個
        usable_tdcc = tdcc_dates[:-1]  # exclude oldest (no prev)
        print(f"\n=== shareholder_divergence ({len(usable_tdcc)} tdcc weeks) ===")
        for d in usable_tdcc:
            run_builder("build_view2_shareholder_divergence.py", d)

    print("\n[DONE]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
