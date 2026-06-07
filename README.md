# charles16888 — Private Taiwan Stock Research Site

私人台股研究網站，承載多條量化/研究 pipeline 的每日輸出。
視覺風格：archival editorial（私人研究典藏感）。

---

## 架構憲法

四條原則保證網站「框架穩定、隨時可擴充」，**不可破**：

1. **`categories.json` 驅動分類** — 加分類 = 改 JSON，零 HTML 修改。
2. **`manifest.entries[]` 是平面陣列** — 首頁/分類頁/tag 頁/搜尋全是它的投影。
3. **單一外框路徑** — 所有報告必經 `tools/wrap_report.py` 套同一模板。
4. **`tools/publish.py` 是 pipeline 唯一介面** — pipeline 不准直接寫檔/動 manifest/呼叫 git。

---

## 檔案結構

```
website/
├── index.html              # 首頁（從 manifest 渲染）
├── category.html           # 分類頁路由器（讀 ?cat=xxx）
├── manifest.json           # 所有報告的平面索引（entries[]）
├── categories.json         # 分類定義（含 enabled flag）
├── reports/YYYY-MM-DD/     # 由 publish.py 寫入
├── assets/
│   ├── style.css           # design tokens + 全部 components
│   ├── main.js             # 路由 / loader / 動畫
│   └── fonts.css           # Google Fonts
├── templates/
│   └── report-wrap.html    # 報告外框模板
├── tools/
│   ├── publish.py          # pipeline 唯一 CLI
│   ├── wrap_report.py      # MD → HTML 套外框
│   └── update_manifest.py  # manifest CRUD
├── .github/workflows/
│   └── deploy.yml          # Cloudflare Pages 自動部署
└── _demo_convert.py        # demo 用，可刪
```

---

## 本地開發

```bash
cd E:/Lynus
python -m http.server 8000
# open http://localhost:8000
```

僅需 Python 3.9+。如要重新跑 demo 轉換：

```bash
pip install markdown
python _demo_convert.py
```

---

## 給 pipeline 接入：唯一介面

Pipeline 產出兩個檔案：
- `report.md` — 報告主體（純 markdown，不含 HTML）
- `meta.json` — 報告中繼資料

然後呼叫：

```bash
python tools/publish.py add --content report.md --meta meta.json
```

`publish.py` 會：
1. 用 `wrap_report.py` 套抱貝外框，寫到 `reports/YYYY-MM-DD/<cat>-<type>.html`
2. 用 `update_manifest.py` 加 entry 進 `manifest.json`
3. `git add` + `commit` + `push`（觸發 Cloudflare 自動部署；用 `--no-git` 可跳過）

### meta.json schema

```json
{
  "category":      "sectors",
  "category_name": "族群",
  "type":          "daily",
  "type_label":    "盤後快報",
  "date":          "2026-05-22",
  "time":          "20:00",
  "title":         "面板族群暴衝 9.87%，ABF 載板續強",
  "title_em":      "暴衝",
  "summary":       "盤後 74 個族群中 53 個收紅...",
  "lead":          "（detail page 用，省略則 fallback summary）",
  "tags":          ["面板", "ABF載板", "化合物半導體"],
  "source_pipeline": "industry_map",
  "stats": [
    { "label": "領漲族群", "value": "面板 +9.87%", "color": "up" },
    { "label": "成交額",   "value": "14,051 億",   "color": "neutral" },
    { "label": "漲停集中", "value": "22.0%",       "color": "up" }
  ],
  "volume": 1
}
```

`color` 接受 `up` / `down` / `neutral`（台灣慣例：紅 up、綠 down）。

### 額外的 manifest 工具

```bash
# 更新市場總覽（首頁 hero stats）
python tools/update_manifest.py market --json '{"taiex":22847,"change_pct":1.21}'

# 看 manifest 狀態
python tools/update_manifest.py show

# 重新排序
python tools/update_manifest.py rebuild

# 刪一筆
python tools/update_manifest.py remove --id 2026-05-22-sectors-daily
```

---

## 加新分類

1. 編輯 `categories.json`，新增一項：
   ```json
   {
     "id": "macro",
     "name_zh": "總經",
     "name_en": "Macro",
     "tagline_zh": "宏觀變數 × 政策訊號",
     "tagline_en": "Macro signals",
     "description": "央行、利率、匯率、原物料的研究檔案",
     "enabled": true,
     "source_pipelines": ["macro_radar"],
     "report_types": [
       { "id": "weekly", "name_zh": "週報", "name_en": "Weekly" }
     ]
   }
   ```
2. 修改 `index.html` 和 `category.html` 的 nav，把對應的 `nav__link nav__link--disabled` 改成可點擊。
   （或之後做：讓 nav 也從 categories.json 動態渲染。）
3. 把 pipeline 接到 `publish.py`，category 用 `"macro"`。

---

## 部署到 Cloudflare Pages

### 一次性設定

1. **建立 private GitHub repo** `lynus-research`（或自選名），把 `E:\Lynus\` 推上去。
2. **Cloudflare Dashboard** → Workers & Pages → Create → Pages → Connect to Git → 選 repo。
   - Framework preset: None
   - Build command: 留空
   - Build output directory: `/`
3. **取得 secrets**：
   - `CLOUDFLARE_API_TOKEN`（Dashboard → My Profile → API Tokens → Edit Cloudflare Workers template）
   - `CLOUDFLARE_ACCOUNT_ID`（Dashboard 右下角）
4. **GitHub repo** → Settings → Secrets and variables → Actions → Add:
   - `CLOUDFLARE_API_TOKEN`
   - `CLOUDFLARE_ACCOUNT_ID`
5. **Cloudflare Access** → Zero Trust → Access → Applications → Add:
   - Self-hosted, domain = `lynus-research.pages.dev`
   - Policy: Allow, criteria = Email is one of [authorized@example.com]
   - 之後每次訪問會跳 Email OTP 驗證頁。

之後 `git push` 自動觸發部署。

---

## 設計系統摘要

完整 tokens 在 `assets/style.css` 第 1 段。重點：

| Token | Value | 用途 |
|---|---|---|
| `--bg` | `#1a1612` | 主背景，深棕 |
| `--ink` | `#e8dfd3` | 主文字，米白 |
| `--gold` | `#d4af37` | 強調色（細用） |
| `--up` | `#e85a5a` | 漲（台灣紅） |
| `--down` | `#5fb87a` | 跌（台灣綠） |
| `--font-display` | Playfair Display | 標題、數字 |
| `--font-serif-tc` | Noto Serif TC | 中文內文 |
| `--font-sans` | Inter | 西文/UI |
| `--font-mono` | JetBrains Mono | spec line、數字 |

要換主題色：改 tokens，整站變色。要換字體：改 `--font-*`，所有元件自動繼承。

---

## V1 已完成

- [x] 設計系統（tokens + components）
- [x] 首頁、分類頁、報告頁三個模板
- [x] manifest / categories 結構與路由器
- [x] MD → HTML 轉換 pipeline
- [x] publish.py CLI 介面
- [x] GitHub Actions 部署 workflow
- [x] 3 份 2026-05-22 industry_map demo 報告

## 待辦（後續）

- [ ] 部署到 Cloudflare Pages（含 Access 設定）
- [ ] 接 industry_map 每日 pipeline（在 `daily_run.bat` 加 `publish.py` 呼叫）
- [ ] 歷史報告回填（~60 份 .md）
- [ ] 其他 5 個分類的 pipeline 接入
- [ ] tag 頁面 / 搜尋功能（之後再決定要不要）
