import importlib.util
import tempfile
import unittest
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_q2_forecast_revenue_compare.py"
spec = importlib.util.spec_from_file_location("build_q2_forecast_revenue_compare", MODULE_PATH)
q2_compare = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(q2_compare)


class Q2ForecastRevenueCompareTests(unittest.TestCase):
    def test_load_forecast_table_skips_non_forecast_tables(self):
        html = """
        <!doctype html><html><head><meta charset="utf-8"></head><body>
        <table><tr><th>狀態</th><th>代號</th></tr><tr><td>尚未完整</td><td>2330</td></tr></table>
        <table>
          <tr><th>代號</th><th>公司</th><th>樣本</th><th>營收 NT$百萬</th><th>信心</th></tr>
          <tr><td>2330</td><td>台積電</td><td>8</td><td>933,000</td><td>高</td></tr>
        </table>
        </body></html>
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "forecast.html"
            path.write_text(html, encoding="utf-8")

            forecast = q2_compare.load_forecast_table(path)

        self.assertEqual(forecast.iloc[0]["code"], "2330")
        self.assertEqual(forecast.iloc[0]["forecast_revenue_m"], 933000.0)

    def test_marks_incomplete_until_all_quarter_months_exist(self):
        forecast = pd.DataFrame(
            [
                {
                    "code": "2330",
                    "name": "台積電",
                    "sample_count": 8,
                    "forecast_revenue_m": 900_000.0,
                    "confidence": "高",
                }
            ]
        )
        revenue = pd.DataFrame(
            [
                {"code": "2330", "roc_year": 115, "month": 4, "period": "2026-04", "revenue_m": 300_000.0},
                {"code": "2330", "roc_year": 115, "month": 5, "period": "2026-05", "revenue_m": 310_000.0},
            ]
        )

        payload = q2_compare.build_comparison(forecast, revenue, latest_mops_period="2026-05")
        row = payload["rows"][0]

        self.assertEqual(row["status"], "incomplete")
        self.assertEqual(row["missing_months"], ["2026-06"])
        self.assertIsNone(row["actual_revenue_m"])
        self.assertEqual(payload["stats"]["incomplete_count"], 1)

    def test_classifies_complete_quarter_surprise(self):
        forecast = pd.DataFrame(
            [
                {"code": "1111", "name": "高於公司", "sample_count": 2, "forecast_revenue_m": 1000.0, "confidence": "高"},
                {"code": "2222", "name": "低於公司", "sample_count": 2, "forecast_revenue_m": 1000.0, "confidence": "高"},
                {"code": "3333", "name": "符合公司", "sample_count": 2, "forecast_revenue_m": 1000.0, "confidence": "高"},
            ]
        )
        revenue = pd.DataFrame(
            [
                {"code": "1111", "roc_year": 115, "month": 4, "period": "2026-04", "revenue_m": 350.0},
                {"code": "1111", "roc_year": 115, "month": 5, "period": "2026-05", "revenue_m": 350.0},
                {"code": "1111", "roc_year": 115, "month": 6, "period": "2026-06", "revenue_m": 350.0},
                {"code": "2222", "roc_year": 115, "month": 4, "period": "2026-04", "revenue_m": 300.0},
                {"code": "2222", "roc_year": 115, "month": 5, "period": "2026-05", "revenue_m": 300.0},
                {"code": "2222", "roc_year": 115, "month": 6, "period": "2026-06", "revenue_m": 300.0},
                {"code": "3333", "roc_year": 115, "month": 4, "period": "2026-04", "revenue_m": 330.0},
                {"code": "3333", "roc_year": 115, "month": 5, "period": "2026-05", "revenue_m": 330.0},
                {"code": "3333", "roc_year": 115, "month": 6, "period": "2026-06", "revenue_m": 330.0},
            ]
        )

        payload = q2_compare.build_comparison(forecast, revenue, latest_mops_period="2026-06")
        by_code = {row["code"]: row for row in payload["rows"]}

        self.assertEqual(by_code["1111"]["status"], "above")
        self.assertEqual(by_code["2222"]["status"], "below")
        self.assertEqual(by_code["3333"]["status"], "inline")
        self.assertEqual(payload["stats"]["above_count"], 1)
        self.assertEqual(payload["stats"]["below_count"], 1)
        self.assertEqual(payload["stats"]["inline_count"], 1)

    def test_keeps_announced_q2_actual_without_a_revenue_forecast(self):
        forecast = pd.DataFrame(
            [{
                "code": "4444",
                "name": "實際已公告公司",
                "sample_count": 1,
                "forecast_revenue_m": None,
                "confidence": "低",
            }]
        )
        revenue = pd.DataFrame(
            [
                {"code": "4444", "roc_year": 115, "month": 4, "period": "2026-04", "revenue_m": 100.0},
                {"code": "4444", "roc_year": 115, "month": 5, "period": "2026-05", "revenue_m": 110.0},
                {"code": "4444", "roc_year": 115, "month": 6, "period": "2026-06", "revenue_m": 120.0},
            ]
        )

        payload = q2_compare.build_comparison(forecast, revenue, latest_mops_period="2026-06")
        row = payload["rows"][0]

        self.assertEqual(row["status"], "no_forecast")
        self.assertEqual(row["actual_revenue_m"], 330.0)
        self.assertIsNone(row["surprise_m"])
        self.assertEqual(row["status_label"], "無研報營收預估（實際已公告）")
        self.assertEqual(payload["stats"]["no_forecast_announced_count"], 1)

    def test_marks_missing_mops_monthly_revenue_without_inventing_data(self):
        forecast = pd.DataFrame(
            [{
                "code": "5555",
                "name": "無月營收來源公司",
                "sample_count": 1,
                "forecast_revenue_m": None,
                "confidence": "低",
            }]
        )
        revenue = pd.DataFrame(columns=["code", "roc_year", "month", "period", "revenue_m"])

        payload = q2_compare.build_comparison(forecast, revenue, latest_mops_period="2026-06")
        row = payload["rows"][0]

        self.assertEqual(row["status"], "no_forecast")
        self.assertIsNone(row["actual_revenue_m"])
        self.assertEqual(row["status_label"], "無研報營收預估（MOPS 無月營收）")

    def test_marks_scale_mismatch_as_suspect_not_above(self):
        """Quarterly forecast smaller than half a month is OCR/unit garbage."""
        forecast = pd.DataFrame(
            [{
                "code": "8299",
                "name": "群聯",
                "sample_count": 18,
                "forecast_revenue_m": 727.0,
                "confidence": "高",
            }]
        )
        revenue = pd.DataFrame(
            [
                {"code": "8299", "roc_year": 115, "month": 4, "period": "2026-04", "revenue_m": 20207.0},
                {"code": "8299", "roc_year": 115, "month": 5, "period": "2026-05", "revenue_m": 22828.0},
                {"code": "8299", "roc_year": 115, "month": 6, "period": "2026-06", "revenue_m": 24853.0},
            ]
        )
        payload = q2_compare.build_comparison(forecast, revenue, latest_mops_period="2026-06")
        row = payload["rows"][0]
        self.assertEqual(row["status"], "suspect")
        self.assertEqual(row["status_label"], "財測異常")
        self.assertEqual(payload["stats"]["suspect_count"], 1)
        self.assertEqual(payload["stats"]["above_count"], 0)


if __name__ == "__main__":
    unittest.main()
