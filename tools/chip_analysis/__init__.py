"""籌碼三源分析模組 (Phase 1 of SPEC v1.1).

Data sources:
- E:\\stock_chip_crawler\\stock_chip.db (read-only)
  - broker_trading       (分點進出, 主源 1)
  - tdcc_holders         (大股東持股, 主源 2, 17 tiers, weekly)
  - daily_price          (close, 共用)
  - institutional        (外資/投信/自營, 輔助)
  - margin_trading       (融資餘額, 輔助)

This package does NOT modify stock_chip.db schema.
All access is through SQLite `mode=ro` URI.
"""
