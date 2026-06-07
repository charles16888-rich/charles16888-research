"""
fetch_broker_mapping.py
=======================
從 HiStock 抓所有現存 broker_id 的真實券商名稱，比對 stock_chip.db 內名稱，
輸出 mismatch report。預設 dry-run，不直接寫 DB。

Why:
    HiStock 顯示的是業界俗稱（朋友們在看的版本），但 TWSE 登記名跟業界俗稱常常
    不一致。例如 broker_id=7008 TWSE 是「兆豐三重」，HiStock 也是「兆豐-三重」（一致），
    但 broker_id=700b（小寫 b）才是「兆豐-中壢」。broker_id 大小寫敏感。
    這個工具掃描全部 DB 內 distinct broker_id，跟 HiStock 比對。

Usage:
    python tools/fetch_broker_mapping.py                  # 全跑 + 寫 mapping json + mismatch report（dry-run，不動 DB）
    python tools/fetch_broker_mapping.py --sample 10      # 只抓前 10 個測試
    python tools/fetch_broker_mapping.py --apply          # 抓完後問你確認、再寫 DB

Output:
    E:\\Lynus\\assets\\_raw\\broker_mapping_histock.json     # 全部 mapping
    E:\\Lynus\\assets\\_raw\\broker_mapping_mismatch.json    # 只有 mismatch 的部分
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests

LYNUS_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = LYNUS_ROOT / "assets" / "_raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)
# 只讀寫 broker_trading（已拆到獨立 broker_chip.db），無跨表 join
CHIP_DB = Path(r"E:\stock_chip_crawler\broker_chip.db")
MAPPING_OUT = RAW_DIR / "broker_mapping_histock.json"
MISMATCH_OUT = RAW_DIR / "broker_mapping_mismatch.json"

HISTOCK_URL = "https://histock.tw/stock/brokerprofit.aspx?bno={bid}"
TITLE_RE = re.compile(r"<title>([^<]+?)券商分點", re.UNICODE)
H1_RE = re.compile(r"<h1[^>]*>([^<]+?)券商分點", re.UNICODE)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": UA, "Accept-Language": "zh-TW,zh;q=0.9"}

REQUEST_DELAY = 0.4  # 秒；HiStock 沒 rate limit 公告，保守一點


def fetch_one(bid: str, sess: requests.Session) -> str | None:
    """回傳 HiStock 顯示的券商名稱（含 dash，例如「兆豐-中壢」），找不到則 None。"""
    url = HISTOCK_URL.format(bid=bid)
    try:
        r = sess.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        # HiStock 預設用 UTF-8，但保險檢查
        if r.encoding is None or r.encoding.lower() == "iso-8859-1":
            r.encoding = "utf-8"
        m = TITLE_RE.search(r.text) or H1_RE.search(r.text)
        if not m:
            return None
        return m.group(1).strip()
    except Exception:
        return None


def normalize(name: str) -> str:
    """比對用 normalize：移除 dash / 全形 dash / 空白。HiStock 名字常帶 dash。"""
    if not name:
        return ""
    return name.replace("-", "").replace("－", "").replace("－", "").replace(" ", "").strip()


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=0, help="只抓前 N 個 broker_id（測試用）")
    p.add_argument("--apply", action="store_true", help="抓完後問確認、寫 DB")
    p.add_argument("--start", type=int, default=0, help="從第幾個開始（resume 用）")
    args = p.parse_args()

    if not CHIP_DB.exists():
        print(f"[ERR] DB 不存在: {CHIP_DB}")
        return 1

    sys.stdout.reconfigure(encoding="utf-8")

    conn = sqlite3.connect(CHIP_DB)
    rows = conn.execute(
        """
        SELECT broker_id, broker_name, COUNT(*) as n
        FROM broker_trading
        GROUP BY broker_id, broker_name
        ORDER BY broker_id, n DESC
        """
    ).fetchall()
    # 每個 broker_id 取最多筆數那個名字（合併同 id 不同 name）
    db_map: dict[str, str] = {}
    for bid, name, n in rows:
        if bid not in db_map:
            db_map[bid] = name
    bids = list(db_map.keys())
    print(f"[INFO] DB distinct broker_id: {len(bids)}")

    if args.sample > 0:
        bids = bids[args.start : args.start + args.sample]
        print(f"[SAMPLE] 只抓 [{args.start}:{args.start + args.sample}] 共 {len(bids)}")
    elif args.start > 0:
        bids = bids[args.start :]
        print(f"[RESUME] 從 #{args.start} 開始，剩 {len(bids)}")

    sess = requests.Session()
    mapping: dict[str, dict] = {}
    mismatches: list[dict] = []
    not_found: list[str] = []

    # resume：讀既有 mapping
    if MAPPING_OUT.exists() and args.start > 0:
        existing = json.loads(MAPPING_OUT.read_text(encoding="utf-8"))
        mapping = existing.get("mapping", {})
        print(f"[RESUME] 已有 mapping {len(mapping)} 筆")

    for i, bid in enumerate(bids, 1):
        histock_name = fetch_one(bid, sess)
        db_name = db_map.get(bid, "")

        if histock_name is None:
            not_found.append(bid)
            status = "NOT_FOUND"
        elif normalize(histock_name) != normalize(db_name):
            mismatches.append({"broker_id": bid, "db": db_name, "histock": histock_name})
            status = "MISMATCH"
        else:
            status = "OK"

        mapping[bid] = {"db": db_name, "histock": histock_name}

        if i % 20 == 0 or status != "OK":
            print(f"[{i}/{len(bids)}] {bid:<5} DB={db_name:<12} HiStock={histock_name or '?':<12} {status}")

        # 每 50 筆寫一次磁碟（避免中斷掉光）
        if i % 50 == 0:
            _save(mapping, mismatches, not_found)

        time.sleep(REQUEST_DELAY)

    _save(mapping, mismatches, not_found)

    print()
    print(f"[DONE] OK: {len(mapping) - len(mismatches) - len(not_found)} / Mismatch: {len(mismatches)} / NotFound: {len(not_found)}")
    print(f"[DONE] mapping 寫到 {MAPPING_OUT}")
    print(f"[DONE] mismatch 寫到 {MISMATCH_OUT}")

    if args.apply and mismatches:
        print()
        print(f"[APPLY] 即將把 {len(mismatches)} 個 broker_id 的 broker_name 改成 HiStock 版本")
        print(f"[APPLY] 影響行數預估…")
        for m in mismatches:
            cnt = conn.execute("SELECT COUNT(*) FROM broker_trading WHERE broker_id=?", (m["broker_id"],)).fetchone()[0]
            m["affected_rows"] = cnt
        total_rows = sum(m["affected_rows"] for m in mismatches)
        print(f"[APPLY] 共 {total_rows:,} 筆要改。輸入 yes 繼續、其他放棄:")
        ans = input("> ").strip()
        if ans.lower() == "yes":
            updated = 0
            for m in mismatches:
                n = conn.execute(
                    "UPDATE broker_trading SET broker_name=? WHERE broker_id=?",
                    (m["histock"], m["broker_id"]),
                ).rowcount
                updated += n
                print(f"  {m['broker_id']:<5} {m['db']:<12} → {m['histock']:<12} ({n:,} rows)")
            conn.commit()
            print(f"[APPLY-DONE] {updated:,} rows updated")
        else:
            print("[APPLY] 放棄。DB 沒動。")

    conn.close()
    return 0


def _save(mapping: dict, mismatches: list, not_found: list) -> None:
    MAPPING_OUT.write_text(
        json.dumps(
            {"_source": "histock.tw/stock/brokerprofit.aspx", "_count": len(mapping), "mapping": mapping},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    MISMATCH_OUT.write_text(
        json.dumps(
            {
                "_doc": "DB.broker_name 與 HiStock.broker_name 不一致的 broker_id（normalize 後比對：忽略 dash 與空白）",
                "_mismatch_count": len(mismatches),
                "_not_found_count": len(not_found),
                "not_found": not_found,
                "mismatches": mismatches,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main())
