import importlib.util
import sqlite3
import unittest
from pathlib import Path

import pandas as pd


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "build_foreign_sell_records.py"
spec = importlib.util.spec_from_file_location("build_foreign_sell_records", MODULE_PATH)
foreign_sell_records = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(foreign_sell_records)


def _institutional():
    return pd.DataFrame(
        [
            {
                "trade_date": "2024-01-01",
                "stock_id": "0050",
                "foreign_buy": 1_000,
                "foreign_sell": 31_000,
                "foreign_net_buy_shares": -30_000,
            },
            {
                "trade_date": "2024-01-02",
                "stock_id": "2317",
                "foreign_buy": 2_000,
                "foreign_sell": 22_000,
                "foreign_net_buy_shares": -20_000,
            },
            {
                "trade_date": "2024-01-03",
                "stock_id": "2330",
                "foreign_buy": 3_000,
                "foreign_sell": 13_000,
                "foreign_net_buy_shares": -10_000,
            },
        ]
    )


def _prices():
    dates = pd.date_range("2024-01-01", periods=70, freq="B")
    rows = []
    for code, name, base in [
        ("0050", "ETF 50", 100.0),
        ("2317", "Hon Hai", 50.0),
        ("2330", "TSMC", 600.0),
    ]:
        for i, date in enumerate(dates):
            rows.append(
                {
                    "trade_date": date,
                    "stock_id": code,
                    "name": name,
                    "close": base + i,
                }
            )
    return pd.DataFrame(rows)


def _market_flow():
    return pd.DataFrame(
        [
            {
                "trade_date": "2024-01-01",
                "foreign_buy": 100_000,
                "foreign_sell": 600_000,
                "foreign_net_buy_shares": -500_000,
                "foreign_buy_amount": 2_000_000,
                "foreign_sell_amount": 12_000_000,
                "foreign_net_amount": -10_000_000,
            },
            {
                "trade_date": "2024-01-02",
                "foreign_buy": 200_000,
                "foreign_sell": 400_000,
                "foreign_net_buy_shares": -200_000,
                "foreign_buy_amount": 50_000_000,
                "foreign_sell_amount": 100_000_000,
                "foreign_net_amount": -50_000_000,
                "amount_source": "official_twse_tpex",
            },
            {
                "trade_date": "2024-01-03",
                "foreign_buy": 500_000,
                "foreign_sell": 300_000,
                "foreign_net_buy_shares": 200_000,
                "foreign_buy_amount": 150_000_000,
                "foreign_sell_amount": 90_000_000,
                "foreign_net_amount": 60_000_000,
            },
        ]
    )


def _index_prices():
    dates = pd.date_range("2024-01-01", periods=70, freq="B")
    return pd.DataFrame(
        {"trade_date": date, "close": 17000.0 + i * 10}
        for i, date in enumerate(dates)
    )


class ForeignSellRecordTests(unittest.TestCase):
    def test_excludes_etf_and_keeps_30_60_day_returns(self):
        records = foreign_sell_records.build_records_from_frames(
            _institutional(),
            _prices(),
            top_n=2,
        )
        self.assertEqual([r["code"] for r in records], ["2317", "2330"])
        self.assertEqual(records[0]["rank"], 1)
        self.assertEqual(records[0]["foreign_net_lots"], -20.0)
        self.assertIn("ret_30d", records[0])
        self.assertIn("ret_60d", records[0])
        self.assertIsNotNone(records[0]["ret_60d"])

    def test_can_include_etf_records_when_requested(self):
        records = foreign_sell_records.build_records_from_frames(
            _institutional(),
            _prices(),
            top_n=1,
            include_etf=True,
        )
        self.assertEqual(records[0]["code"], "0050")
        self.assertEqual(records[0]["name"], "ETF 50")

    def test_builds_market_records_with_index_returns(self):
        records = foreign_sell_records.build_market_records_from_frames(
            _market_flow(),
            _index_prices(),
            top_n=2,
        )
        self.assertEqual([r["trade_date"] for r in records], ["2024-01-02", "2024-01-01"])
        self.assertEqual(records[0]["foreign_net_amount"], -50_000_000)
        self.assertEqual(records[0]["foreign_net_amount_billion"], -0.5)
        self.assertEqual(records[0]["amount_source"], "official_twse_tpex")
        self.assertIn("twse_close", records[0])
        self.assertIn("ret_60d", records[0])
        self.assertIsNotNone(records[0]["ret_60d"])

    def test_loads_market_records_from_official_db_table(self):
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            """
            CREATE TABLE institutional (
                date TEXT,
                code TEXT,
                name TEXT,
                foreign_buy INTEGER,
                foreign_sell INTEGER,
                foreign_net INTEGER
            );
            CREATE TABLE market_institutional_amounts (
                date TEXT,
                market TEXT,
                foreign_buy_amount INTEGER,
                foreign_sell_amount INTEGER,
                foreign_net_amount INTEGER,
                trust_buy_amount INTEGER,
                trust_sell_amount INTEGER,
                trust_net_amount INTEGER,
                dealer_buy_amount INTEGER,
                dealer_sell_amount INTEGER,
                dealer_net_amount INTEGER,
                total_buy_amount INTEGER,
                total_sell_amount INTEGER,
                total_net_amount INTEGER,
                source TEXT,
                fetched_at TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO institutional VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("2024-01-02", "2330", "TSMC", 1000, 3000, -2000),
                ("2024-01-02", "2317", "Hon Hai", 2000, 5000, -3000),
            ],
        )
        conn.executemany(
            "INSERT INTO market_institutional_amounts VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0, 0, 0, 0, ?, 'now')",
            [
                ("2024-01-02", "twse", 10, 50, -40, "twse_bfi82u"),
                ("2024-01-02", "tpex", 3, 8, -5, "tpex_insti_summary_prod1"),
                ("2024-01-02", "total", 13, 58, -45, "tpex+twse"),
            ],
        )

        flow = foreign_sell_records._load_market_flow_records(
            conn,
            limit=10,
            start_date=None,
            end_date=None,
        )

        self.assertEqual(len(flow), 1)
        row = flow.iloc[0]
        self.assertEqual(row["foreign_net_amount"], -45)
        self.assertEqual(row["foreign_buy_amount"], 13)
        self.assertEqual(row["foreign_sell_amount"], 58)
        self.assertEqual(row["foreign_net_buy_shares"], -5000)
        self.assertEqual(row["amount_source"], "official_twse_tpex")
        self.assertEqual(row["official_source_count"], 2)


if __name__ == "__main__":
    unittest.main()
