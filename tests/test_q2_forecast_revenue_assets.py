from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Q2ForecastRevenueAssetTests(unittest.TestCase):
    def test_q2_compare_asset_has_mops_revenue_join(self) -> None:
        payload = json.loads((ROOT / "assets" / "q2_forecast_revenue_compare.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["quarter"], "2026Q2")
        self.assertIn("2026-06", payload["quarter_months"])
        self.assertGreaterEqual(payload["stats"]["total_companies"], 500)
        self.assertGreaterEqual(payload["stats"]["revenue_forecast_count"], 400)
        self.assertGreaterEqual(payload["latest_mops_period"], "2026-06")
        self.assertTrue(
            any("2026-06" in (row.get("months") or {}) or "2026-06" in row.get("missing_months", []) for row in payload["rows"])
        )

    def test_mops_watch_asset_uses_current_shape(self) -> None:
        payload = json.loads((ROOT / "assets" / "mops_revenue_forecast_watch.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["quarter"], "2026Q2")
        self.assertRegex(payload["event_date"], r"^\d{4}-\d{2}-\d{2}$")
        for key in ("event_count", "matched_forecast_count", "explicit_june_revenue_count", "comparable_count"):
            self.assertIn(key, payload["stats"])

    def test_forecast_page_fetches_compare_and_watch_assets(self) -> None:
        page = (ROOT / "reports" / "q2-forecast-2026q2.html").read_text(encoding="utf-8")
        self.assertIn("q2_forecast_revenue_compare.json", page)
        self.assertIn("mops_revenue_forecast_watch.json", page)
        self.assertIn("q2-mops-events", page)


if __name__ == "__main__":
    unittest.main()
