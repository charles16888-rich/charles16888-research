"""
One-shot script: convert the three 2026-05-22 industry_map markdown reports
into wrapped HTML pages that match the manifest's sample entries.

Run from the website root:
    python _demo_convert.py
"""

import sys
from pathlib import Path

# Allow importing wrap_report from tools/
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "tools"))
from wrap_report import wrap_report  # noqa: E402


SRC = Path(r"E:\industry_map\reports")
DST = HERE / "reports" / "2026-05-22"

JOBS = [
    {
        "input":  SRC / "daily_2026-05-22.md",
        "output": DST / "sectors-daily.html",
        "meta": {
            "category_id": "sectors",
            "category_name": "族群",
            "type_id": "daily",
            "type_label": "盤後快報",
            "date": "2026-05-22",
            "time": "20:00",
            "title": "面板族群暴衝 9.87%，ABF 載板續強",
            "title_em": "暴衝",
            "lead": "盤後 74 個族群中 53 個收紅，平均 +1.80%。漲停股成交 3,109 億占大盤 22%，資金高度集中在科技題材。",
            "volume": 1,
            "asset_prefix": "../../",
            "stats": [
                {"label": "領漲族群", "value": "面板 +9.87%", "color": "up"},
                {"label": "成交額",   "value": "14,051 億",   "color": "neutral"},
                {"label": "漲停集中", "value": "22.0%",       "color": "up"},
            ],
        },
    },
    {
        "input":  SRC / "weekly_2026-05-22.md",
        "output": DST / "sectors-weekly.html",
        "meta": {
            "category_id": "sectors",
            "category_name": "族群",
            "type_id": "weekly",
            "type_label": "週報",
            "date": "2026-05-22",
            "time": "20:00",
            "title": "被動元件本週 +22%，記憶體逆勢失血",
            "title_em": "失血",
            "lead": "本週 5 個交易日，被動元件累積 +22.24% 領漲，ABF 載板與面板齊強；記憶體族群 -5.52% 為資金重災區。",
            "volume": 1,
            "asset_prefix": "../../",
            "stats": [
                {"label": "週領漲",   "value": "被動元件 +22.24%", "color": "up"},
                {"label": "週領跌",   "value": "記憶體 -5.52%",    "color": "down"},
                {"label": "資金焦點", "value": "電子零組件",       "color": "neutral"},
            ],
        },
    },
    {
        "input":  SRC / "rotation_2026-05-22.md",
        "output": DST / "sectors-rotation.html",
        "meta": {
            "category_id": "sectors",
            "category_name": "族群",
            "type_id": "rotation",
            "type_label": "輪動偵測",
            "date": "2026-05-22",
            "time": "20:00",
            "title": "化合物半導體與面板今日突然轉強",
            "title_em": "突然轉強",
            "lead": "對比前 5 日均，化合物半導體（+8.76%）、面板（+8.37%）、記憶體（+7.80%）三族群當日漲幅顯著高於均值，題材輪動跡象明確。",
            "volume": 1,
            "asset_prefix": "../../",
            "stats": [
                {"label": "最大轉強", "value": "化合物半導體 +8.76%", "color": "up"},
                {"label": "最大轉弱", "value": "自行車 -2.55%",       "color": "down"},
                {"label": "對比基準", "value": "5 日均",              "color": "neutral"},
            ],
        },
    },
]

def main():
    for job in JOBS:
        if not job["input"].exists():
            print(f"[MISSING] {job['input']}")
            continue
        wrap_report(job["input"], job["output"], job["meta"])
        print(f"[OK] {job['output'].relative_to(HERE)}")

if __name__ == "__main__":
    main()
