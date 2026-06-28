from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_TYPES = {
    "tw_ex_rights",
    "tw_ex_dividend",
    "tw_dividend",
    "taiwan_macro",
    "taiwan_import_export",
    "taiwan_cpi",
    "taiwan_gdp",
    "taiwan_central_bank_rate_decision",
}
NOISY_EVENT_IDS = {
    "tw-trading-week-2026-06-29",
    "tw-mops-material-news-window-2026-06-29",
}


class FinancialCalendarAssetsTest(unittest.TestCase):
    def test_calendar_category_and_manifest_entry_exist(self) -> None:
        categories = json.loads((ROOT / "categories.json").read_text(encoding="utf-8"))
        manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
        self.assertTrue(any(c["id"] == "calendar" and c["enabled"] for c in categories["categories"]))
        entry = next(e for e in manifest["entries"] if e["id"] == "financial-calendar")
        self.assertEqual("calendar", entry["category"])
        self.assertEqual("reports/financial-calendar.html", entry["url"])
        self.assertIn("市場行事曆", entry["title"])
        self.assertNotIn("金十", json.dumps(entry, ensure_ascii=False))

    def test_calendar_events_are_normalized_and_exclusions_hold(self) -> None:
        payload = json.loads((ROOT / "assets" / "calendar_events.json").read_text(encoding="utf-8"))
        payload_text = json.dumps(payload, ensure_ascii=False)
        keys = [e["event_key"] for e in payload["events"]]
        ids = {e["id"] for e in payload["events"]}
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(payload["events"])
        self.assertGreaterEqual(len(payload["events"]), 45)
        self.assertNotIn("2025-05", payload_text)
        self.assertNotIn("金十", payload_text)
        self.assertTrue(EXCLUDED_TYPES.issubset(set(payload["excluded_types"])))
        self.assertFalse(set(e["event_type"] for e in payload["events"]) & EXCLUDED_TYPES)
        self.assertFalse(ids & NOISY_EVENT_IDS)
        tw_events = [e for e in payload["events"] if e["market"] == "TW"]
        self.assertGreaterEqual(len(tw_events), 3)
        self.assertTrue(any(e.get("metadata", {}).get("focusGroups") for e in tw_events))
        for event in payload["events"]:
            self.assertIn(event["market"], {"US", "TW", "GLOBAL"})
            self.assertIn(event["importance"], {1, 2, 3})
            self.assertTrue(event["source_id"])
            self.assertTrue(event["source_url"])
            self.assertTrue(event["last_seen_at"])
            self.assertTrue(event["raw_hash"])
            self.assertTrue(event["event_date_local"])
            self.assertIn("actual", event)
            self.assertIn("forecast", event)
            self.assertIn("previous", event)
            if event["market"] == "US":
                self.assertEqual("America/New_York", event["timezone"])
            if event["event_time_local"]:
                self.assertTrue(event["event_time_utc"])
                self.assertIn("taipei_display_time", event)

    def test_frontend_hooks_exist(self) -> None:
        index = (ROOT / "index.html").read_text(encoding="utf-8")
        page = (ROOT / "reports" / "financial-calendar.html").read_text(encoding="utf-8")
        main_js = (ROOT / "assets" / "main.js").read_text(encoding="utf-8")
        self.assertIn('id="calendar-widget-section"', index)
        self.assertIn('id="calendar-list"', page)
        self.assertIn("calendar-table__head", page)
        self.assertIn("renderEventRow", page)
        self.assertIn("台北時間", page)
        self.assertIn("前值", page)
        self.assertIn("公布", page)
        self.assertNotIn("Timezone:", page)
        self.assertNotIn("Last seen:", page)
        self.assertNotIn("2025-05", page)
        self.assertNotIn("來源：", page)
        self.assertNotIn("<strong>Source</strong>", page)
        self.assertIn("renderMarketEventsWidget", main_js)


if __name__ == "__main__":
    unittest.main()
