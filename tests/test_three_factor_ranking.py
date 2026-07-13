from __future__ import annotations

import importlib.util
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import pandas as pd


SCRIPT = Path(__file__).resolve().parents[1] / "tools" / "build_three_factor_ranking.py"
SPEC = importlib.util.spec_from_file_location("build_three_factor_ranking_under_test", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ThreeFactorRankingTests(unittest.TestCase):
    def test_zero_factor_intersection_is_valid_traceable_output(self) -> None:
        foreign = pd.DataFrame(
            [{
                "code": "1101",
                "name_inst": "測試股",
                "foreign_streak": 1,
                "foreign_5d_lots": 10.0,
                "foreign_10d_lots": 20.0,
                "foreign_buy_days_10": 2,
                "institutional_10d_lots": 15.0,
            }]
        )
        revenue = pd.DataFrame(
            [{"code": "1101", "revenue_new_high": False}]
        )

        with (
            patch.object(MODULE, "stock_conn", return_value=nullcontext(object())),
            patch.object(MODULE, "get_latest_institutional_date", return_value="2026-07-10"),
            patch.object(MODULE, "load_foreign_factor", return_value=foreign),
            patch.object(
                MODULE,
                "load_revenue_factor",
                return_value=(revenue, {"calendar_period": "2026-06"}),
            ),
            patch.object(MODULE, "load_price_momentum", return_value=pd.DataFrame()),
        ):
            output = MODULE.build()

        self.assertEqual(output["data_status"], "no_candidates")
        self.assertEqual(output["source_counts"]["foreign_revenue_candidates"], 0)
        self.assertEqual(output["stats"]["three_factor_count"], 0)
        self.assertEqual(output["rankings"], [])

    def test_missing_momentum_rows_is_valid_traceable_output(self) -> None:
        foreign = pd.DataFrame(
            [{
                "code": "1101",
                "name_inst": "測試",
                "foreign_streak": 3,
                "foreign_5d_lots": 10.0,
                "foreign_10d_lots": 20.0,
                "foreign_buy_days_10": 3,
                "institutional_10d_lots": 15.0,
            }]
        )
        revenue = pd.DataFrame([{"code": "1101", "revenue_new_high": True}])

        with (
            patch.object(MODULE, "stock_conn", return_value=nullcontext(object())),
            patch.object(MODULE, "get_latest_institutional_date", return_value="2026-07-10"),
            patch.object(MODULE, "load_foreign_factor", return_value=foreign),
            patch.object(MODULE, "load_revenue_factor", return_value=(revenue, {})),
            patch.object(MODULE, "load_price_momentum", return_value=pd.DataFrame()),
        ):
            output = MODULE.build()

        self.assertEqual(output["data_status"], "no_price_momentum")
        self.assertEqual(output["source_counts"]["foreign_revenue_candidates"], 1)
        self.assertEqual(output["source_counts"]["momentum_rows"], 0)
        self.assertEqual(output["rankings"], [])


if __name__ == "__main__":
    unittest.main()
