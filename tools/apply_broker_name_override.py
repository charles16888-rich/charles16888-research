"""
apply_broker_name_override.py
==============================
Apply the broker_id → broker_name correction map to stock_chip.db.

Why we need this:
    TWSE's 證交所 broker 登記名稱 (e.g. broker_id=7008 "兆豐三重") is
    sometimes different from the 業界俗稱 used by HiStock / cmoney /
    investors (e.g. "兆豐中壢"). The override map lives at:
        E:\\Lynus\\assets\\_raw\\broker_name_override.json
    and is hand-maintained — add entries when you spot a mismatch.

When to run:
    After stock_chip_crawler refreshes broker_trading, run this script
    so the names users see match what they expect.

Usage:
    python tools/apply_broker_name_override.py            # apply, print summary
    python tools/apply_broker_name_override.py --dry-run  # preview, no write
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

LYNUS_ROOT = Path(__file__).resolve().parent.parent
OVERRIDE_PATH = LYNUS_ROOT / "assets" / "_raw" / "broker_name_override.json"
# 只讀寫 broker_trading（已拆到獨立 broker_chip.db），無跨表 join
CHIP_DB = Path(r"E:\stock_chip_crawler\broker_chip.db")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Preview without writing.")
    args = p.parse_args()

    if not OVERRIDE_PATH.exists():
        print(f"[ERR] override file not found: {OVERRIDE_PATH}")
        return 1
    if not CHIP_DB.exists():
        print(f"[ERR] DB not found: {CHIP_DB}")
        return 1

    overrides = json.loads(OVERRIDE_PATH.read_text(encoding="utf-8")).get("overrides", {})
    print(f"[INFO] override entries: {len(overrides)}")

    conn = sqlite3.connect(CHIP_DB)
    cur = conn.cursor()

    total = 0
    for broker_id, correct_name in overrides.items():
        rows = cur.execute(
            "SELECT broker_name, COUNT(*) FROM broker_trading "
            "WHERE broker_id=? AND broker_name != ? GROUP BY broker_name",
            (broker_id, correct_name),
        ).fetchall()
        if not rows:
            continue
        print(f"  broker_id={broker_id} → {correct_name}")
        for old_name, count in rows:
            print(f"    \"{old_name}\" × {count:,}")
        if args.dry_run:
            continue
        n = cur.execute(
            "UPDATE broker_trading SET broker_name=? WHERE broker_id=? AND broker_name != ?",
            (correct_name, broker_id, correct_name),
        ).rowcount
        total += n

    if not args.dry_run:
        conn.commit()
    conn.close()

    if args.dry_run:
        print("[DRY-RUN] no changes written")
    else:
        print(f"[OK] updated {total:,} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
