# 籌碼三源整合分析系統 — SPEC v1.1

**修訂**：2026-05-31 · 從 v1.0 改  
**主要改動**：**tick 抓取與大單反推全廢**（Shioaji 流量配額太緊、放棄）

---

## §0 變更紀錄（v1.0 → v1.1）

| 區塊 | v1.0 | v1.1 | 原因 |
|---|---|---|---|
| 資料源 §1.2 | ticks_raw + large_orders | **刪除** | Shioaji 一般戶 500MB/日，全市場 tick 需 2-3 GB/日，無法支撐 |
| 計算 §2.3 | 大單反推 + tick_type 自驗 | **刪除** | 同上 |
| 視圖 3 | 大單 vs 分點對照 | **刪除** | 同上 |
| 視圖 5 | 盤中分鐘大單熱力 | **刪除** | 同上 |
| 硬規則 #1 | tick_type 自驗 | **刪除** | 同上 |
| 硬規則 #4 | 股本還原必做 | 標「未還原」可上線 | user 拍板「E 直接放棄」（2026-05-31） |
| 第二源 | tick（盤中即時驗證） | **改用 institutional**（外資/投信/自營商）+ **margin_trading**（融資） | 替代失去的驗證源 |
| broker_name 來源 | 「驗證資料」（含糊） | **HiStock mapping**（已建表，2026-05-31 完成 220 個 mismatch 套用 + backup） | 機制具體化 |

---

## §1 三源資料（**改為 兩源 + 兩個輔助**）

### §1.1 分點進出 `branch_trades`（主源 ①）

**資料源**：`E:\stock_chip_crawler\stock_chip.db` 的 `broker_trading` 表  
**範圍**：2026-03-20 起（但 03-20 ~ 04-09 沒 avg_price，看 §1.5）  
**規模**：~ 5.5M rows · 831 個分點 · 每日 ~ 16 萬筆

| SPEC 欄位 | DB 欄位 | 備註 |
|---|---|---|
| date | date | YYYY-MM-DD |
| stock_code | code | |
| branch_id | broker_id | **大小寫敏感**、含 HEX 4 字（如 `700b`） |
| branch_name | broker_name | **以 HiStock 為準**（2026-05-31 已統一） |
| buy_lots | buy_vol / 1000 | DB 內單位是「股」，÷1000 轉張 |
| sell_lots | sell_vol / 1000 | 同上 |
| net_lots | net_vol / 1000 | 同上 |
| avg_buy_price | avg_buy_price | 2026-04-10 起穩定有值；單邊交易仍 NULL（合理） |
| avg_sell_price | avg_sell_price | 同上 |
| ~~buy_amount~~ | ❌ 無 | 用 `avg_buy_price × buy_lots × 1000` 估算（精度受限） |
| ~~region~~ | ❌ 無 | 解析 broker_name 末段地名建表（Phase 2） |

### §1.2 ~~ticks / 大單~~ **刪除**

### §1.3 大股東持股 `shareholder_weekly`（主源 ②）

**資料源**：`stock_chip.db` 的 `tdcc_holders`（長表）+ `daily_price`（補 close）  
**範圍**：2026-03-13 ~ 2026-05-22（11 週）

| SPEC 欄位 | 從 tdcc_holders 計算 |
|---|---|
| date | date |
| stock_code | code |
| total_holders | `SUM(holders) WHERE tier=17` |
| holders_400_600 | `SUM(holders) WHERE tier=12` |
| holders_600_800 | `SUM(holders) WHERE tier=13` |
| holders_800_1000 | `SUM(holders) WHERE tier=14` |
| holders_1000up | `SUM(holders) WHERE tier=15` |
| pct_400up | `SUM(ratio) WHERE tier IN (12,13,14,15)` |
| pct_1000up | `ratio WHERE tier=15` |
| close | JOIN `daily_price` |

→ 建 SQL view `shareholder_weekly_v` 一勞永逸

### §1.4 法人進出 `institutional`（輔助 A，替代失去的 tick）

**資料源**：`stock_chip.db` 的 `institutional` 表（34M rows，2010-至今）  
**用途**：燈號交叉驗證（分點「主力買 vs 賣」 × 法人「外資買 vs 賣」一致性檢核）

### §1.5 融資餘額 `margin_trading`（輔助 B，散戶情緒）

**資料源**：`margin_trading`（6.5M rows，2010-至今）  
**用途**：分點集中度高 + 融資爆增 = 強烈警訊（散戶追入主力倒貨）

### §1.6 股價 `daily_price`（共用）

`stock_chip.db` 的 `daily_price`（69.7M rows，2010-至今，含 OHLCV）

---

## §2 計算

### §2.1 分點集中度 — **已上線**（v1.0 保留）

公式（業界標準）：
```
main_buy  = TOP 15 買超分點 net_vol 累計
main_sell = TOP 15 賣超分點 |net_vol| 累計
concentration% = (main_buy − main_sell) / max(SUM(buy), SUM(sell)) × 100
```
過濾：累計成交量 < 1,000,000 股、買賣比例失衡（< 0.5）、|集中度| > 150% 視為 outlier drop。

### §2.2 大股東訊號

- **400 張以上人數變化（週對週、月對月）**：判讀大戶進出
- **1000 張人數變化**：超大戶進出（更早期訊號）
- **散戶人數變化**（總人數 − 大戶人數）：判讀散戶情緒
- **大戶持股比 vs 股價背離**：大戶減持但股價漲 → 散戶接盤警訊

### §2.3 ~~大單反推~~ **刪除**

### §2.4 三線交叉燈號（**改寫**）

原 v1.0 用 tick 當第二驗證源，v1.1 改用 **institutional**：

| 線 | 訊號方向 | 來源 |
|---|---|---|
| ① 分點集中度 | 主力買 / 賣 | branch_trades §2.1 |
| ② 大股東（400 張以上） | 大戶加碼 / 減碼 | shareholder_weekly §2.2 |
| ③ 三大法人 | 外資 + 投信 + 自營合計買賣超 | institutional |

三線同向 → 強訊號  
分點集中度 vs 法人方向背離 → 內部人 vs 外資博弈，需注意  
分點集中度 vs 大股東方向背離 → 主力進但大戶出 → 接盤警訊

---

## §3 視圖（**從 7 個減到 5 個**）

### 視圖 1：**個股分點熱力矩陣**（v1.0 保留）

- 列：分點（TOP 15 買 + TOP 15 賣）
- 欄：5 個時段（1d / 5d / 10d / 20d / ALL）
- 格內：net_lots + avg_buy_price + avg_sell_price（2026-04-09 以前空白）

### 視圖 2：**大股東三線背離圖**（v1.0 保留）

每股一張：
- 折線 1：close（左軸）
- 折線 2：400 張以上人數（右軸）
- 折線 3：1000 張以上人數（右軸）
- 高亮：人數下降但股價漲（背離）

### 視圖 3：~~大單 vs 分點對照~~ **刪除**

### 視圖 4：**三源混合燈號表**（v1.0 視圖 4 + 第二源換掉）

每股一行，三個燈號欄：
- 燈 1：分點集中度方向（+/−/0）
- 燈 2：大股東 400 張方向（週對週）
- 燈 3：三大法人方向（合計買賣超 / 股本%）

排序：三線同方向 + 集中度絕對值高的優先

### 視圖 5：~~盤中分鐘大單熱力~~ **刪除**

### 視圖 6：**全市場分點掃描表**（v1.0 保留）

排行榜：每個時段 TOP 50 買 / TOP 50 賣（已上線：`chip-concentration.html`）

### 視圖 7：**區域擴張看盤**（v1.0 保留、但需建地緣 mapping）

- 地圖 + 熱力：哪個地區的分點今天買最多 / 賣最多
- 依賴：解析 broker_name 末段地名（如「兆豐-中壢」→ region="中壢"）建 mapping
- **Phase 2 做**（Phase 1 不阻塞）

---

## §4 硬規則（**改寫**）

1. ~~tick_type 自驗~~ **刪除**
2. **分點 / 代碼-名稱只用驗證源**：mapping 從 HiStock 取，已建表 `broker_name_backup_20260531`（可回滾）、`broker_mapping_histock.json`、`broker_mapping_mismatch.json`
3. **change 不用 level**：人數、持股比、餘額這些都要用「週對週變化」或「月對月變化」，不能用單期絕對值
4. **股本還原 → 標「未還原」**（user 拍板放棄；TWSE 除權息表自建因子未實作）
5. **不得捏造任何數字**：缺資料就回報缺哪一塊，不要補值
6. **broker_id 大小寫敏感** + **含 HEX 4 字**（如 700b ≠ 700B），DB query 用原始 id

---

## §5 SQLite views（**Phase 1 要建**）

```sql
-- 大股東寬表 view
CREATE VIEW shareholder_weekly_v AS
SELECT
    h.date,
    h.code AS stock_code,
    MAX(CASE WHEN h.tier=17 THEN h.holders END) AS total_holders,
    MAX(CASE WHEN h.tier=12 THEN h.holders END) AS holders_400_600,
    MAX(CASE WHEN h.tier=13 THEN h.holders END) AS holders_600_800,
    MAX(CASE WHEN h.tier=14 THEN h.holders END) AS holders_800_1000,
    MAX(CASE WHEN h.tier=15 THEN h.holders END) AS holders_1000up,
    SUM(CASE WHEN h.tier IN (12,13,14,15) THEN h.ratio END) AS pct_400up,
    MAX(CASE WHEN h.tier=15 THEN h.ratio END) AS pct_1000up,
    p.close
FROM tdcc_holders h
LEFT JOIN daily_price p ON h.date = p.date AND h.code = p.code
GROUP BY h.date, h.code;

-- 分點 view（簡化命名 + 單位轉換）
CREATE VIEW branch_trades_v AS
SELECT
    date,
    code AS stock_code,
    broker_id AS branch_id,
    broker_name AS branch_name,
    buy_vol / 1000.0 AS buy_lots,
    sell_vol / 1000.0 AS sell_lots,
    net_vol / 1000.0 AS net_lots,
    avg_buy_price,
    avg_sell_price
FROM broker_trading;
```

---

## §6 Phase 1 範圍（**v1.1 簡化版**）

1. 建上面兩個 SQLite views（5 分鐘）
2. 寫 `concentration_calc.py`（§2.1，已存在）
3. 寫 `shareholder_signal.py`（§2.2，新）
4. 寫 `tri_source_lamp.py`（§2.4，新）
5. 寫 5 個視圖 HTML 模板（視圖 1/2/4/6 + 7 視 Phase 2）
6. 各視圖增量更新 + manifest 整合

預估 Phase 1 工期：**3-5 天**（不含視圖 7 + region mapping）

---

## §7 不做了的事（v1.0 → v1.1 砍）

- ❌ Shioaji ticks 抓取（流量不足）
- ❌ 大單反推 + tick_type 自驗
- ❌ 視圖 3、5（依賴 tick）
- ❌ 股本還原邏輯（user 拍板放棄）
- ❌ **2026-03-20 ~ 2026-04-09 那 6 天的歷史 avg_buy/sell_price 補抓**（2026-05-31 試跑 backfill_broker_20260320_20260409.bat，4/9 122 檔只成功 11 檔 = 9%，TWSE BSR rate-limit 擋掉冷門股；估算總工期 4-5 小時、補回資料 < 全 broker_trading 的 1%，ROI 極差）
  - **Phase 1 起點 = 2026-04-10**，前 6 天約 50 萬筆 broker_trading 仍在 DB 內，但只用於 chip-concentration（不需均價）。視圖 1 / 4 / 個股均價顯示直接 fallback 「—」
- ⏳ 視圖 7 + region mapping（延至 Phase 2，看 user 要不要做）

---

## §8 DB lock 應對機制（2026-05-31 新增）

stock_chip_crawler 沒開 WAL，當 crawler 在寫入時 Lynus 端 builder 會撞 lock。
解法已上線：

1. **builder 支援 `CHIP_DB_PATH` env var**：所有 chip_analysis builder（`build_view2_shareholder_divergence.py`、`build_chip_concentration.py`）讀此環境變數切換 DB 路徑。
2. **snapshot 機制**：用 `sqlite3.backup()` page-by-page copy 到 `E:\Lynus\_cache\stock_chip_snapshot.db`（16.7 GB / 4 分鐘），不卡 writer。
3. **使用法**：撞 lock 時改用 snapshot：
   ```powershell
   $env:CHIP_DB_PATH = 'E:\Lynus\_cache\stock_chip_snapshot.db'
   python tools/build_view2_shareholder_divergence.py
   ```
4. **長期解法**：考慮 crawler 開 WAL（`PRAGMA journal_mode=WAL`）— 但要評估對 crawler 寫入效能影響。
