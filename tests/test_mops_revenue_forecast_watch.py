import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_mops_revenue_forecast_watch.py"
spec = importlib.util.spec_from_file_location("build_mops_revenue_forecast_watch", MODULE_PATH)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class MopsRevenueForecastWatchTests(unittest.TestCase):
    def test_extracts_explicit_june_revenue_in_millions(self):
        text = "公告本公司自行結算115年06月合併營收約新台幣11.35億元。"
        self.assertTrue(watch.is_explicit_june_revenue(text))
        self.assertEqual(watch.extract_revenue_m(text), 1135.0)

    def test_build_payload_marks_announced_no_forecast(self):
        events = [
            {
                "event_id": "mops_2402",
                "code": "2402",
                "name": "毅嘉",
                "category": "營收公告",
                "title": "公告本公司自行結算115年06月合併營收約新台幣11.35億元。",
                "content": "",
                "publish_time": "2026-07-01 18:07:00",
            }
        ]
        forecast = {"2402": {"code": "2402", "name": "毅嘉", "forecast_revenue_m": None, "months": {}}}
        payload = watch.build_payload(events, forecast, {"generated_at": "x"}, "2026-07-01")
        self.assertEqual(payload["stats"]["event_count"], 1)
        self.assertEqual(payload["stats"]["matched_forecast_count"], 1)
        self.assertEqual(payload["rows"][0]["status"], "announced_no_forecast")

    def test_build_payload_compares_when_prior_months_exist(self):
        events = [
            {
                "event_id": "mops_1111",
                "code": "1111",
                "name": "測試",
                "category": "營收公告",
                "title": "公告本公司115年06月合併營收約新台幣5億元。",
                "content": "",
                "publish_time": "2026-07-01 18:07:00",
            }
        ]
        forecast = {
            "1111": {
                "code": "1111",
                "name": "測試",
                "forecast_revenue_m": 1000.0,
                "months": {
                    "2026-04": {"revenue_m": 250.0},
                    "2026-05": {"revenue_m": 250.0},
                },
            }
        }
        payload = watch.build_payload(events, forecast, {"generated_at": "x"}, "2026-07-01")
        row = payload["rows"][0]
        self.assertEqual(row["partial_q2_revenue_m"], 1000.0)
        self.assertEqual(row["status"], "inline")


if __name__ == "__main__":
    unittest.main()
