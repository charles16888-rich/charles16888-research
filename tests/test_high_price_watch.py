from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_high_price_watch as hpw  # noqa: E402


class HighPriceWatchTest(unittest.TestCase):
    def test_daily_ranking_tracks_new_rank_change_and_streak(self) -> None:
        observations = {
            "2026-06-27": {
                "2330": {"code": "2330", "name": "台積電", "close": 1200, "chg_pct": 1.2, "sources": {"a"}},
                "3008": {"code": "3008", "name": "大立光", "close": 2100, "chg_pct": -0.5, "sources": {"a"}},
            },
            "2026-06-28": {
                "2330": {"code": "2330", "name": "台積電", "close": 1500, "chg_pct": 2.0, "sources": {"a", "b"}},
                "2454": {"code": "2454", "name": "聯發科", "close": 1300, "chg_pct": 3.0, "sources": {"b"}},
            },
        }
        daily = hpw.build_daily_rankings(observations, min_price=100, top_n=2)
        latest = {row["code"]: row for row in daily[-1]["rows"]}
        self.assertEqual(2, latest["2330"]["streak"])
        self.assertEqual(1, latest["2330"]["rank_change"])
        self.assertTrue(latest["2454"]["is_new"])
        self.assertEqual(["a", "b"], latest["2330"]["sources"])

    def test_assets_manifest_and_category_are_present(self) -> None:
        payload = json.loads((ROOT / "assets" / "high_price_watch.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        categories = json.loads((ROOT / "categories.json").read_text(encoding="utf-8"))

        self.assertGreaterEqual(payload["stats"]["latest_count"], 1)
        self.assertTrue(payload["matrix"])
        self.assertIn("不做投資建議", payload["source_note"])

        entry = next(e for e in manifest["entries"] if e["id"] == "high-price-watch")
        self.assertEqual("stocks", entry["category"])
        self.assertEqual("reports/high-price-watch.html", entry["url"])

        stocks = next(c for c in categories["categories"] if c["id"] == "stocks")
        self.assertTrue(any(s["id"] == "high-price" for s in stocks["subcategories"]))
        self.assertTrue(any(t["id"] == "high_price_watch" for t in stocks["report_types"]))


if __name__ == "__main__":
    unittest.main()
