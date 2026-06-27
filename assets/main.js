/* ============================================================
   charles16888 — Main JS
   Responsibilities:
     1. Load manifest.json + categories.json
     2. Render index / category / report pages from data
     3. Drive page-load staggered reveal animation
     4. Drive scroll-triggered fade via IntersectionObserver
   ============================================================ */

(function () {
  'use strict';

  // ---------- Globals ----------
  const STATE = {
    manifest: null,
    categories: null,
    page: document.body.dataset.page || 'index',
  };

  const ROMAN = ['', 'I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII'];
  const TYPE_LABELS = {
    daily: '盤後快報',
    weekly: '週報',
    rotation: '輪動偵測',
    focus: '焦點深度',
    pulse: '市場脈動',
    options_weekly: '選擇權週報',
    options_annual: '年度合併',
    deep_card_full: '深度卡 · 完整版',
    deep_card_lite: '深度卡 · 簡版',
    ranking: '排行榜',
    forecast: '財測彙總',
    event: '事件雷達',
    mops_daily: '重大訊息',
    news_digest: '新聞匯整',
  };

  // ---------- Utilities ----------

  function el(tag, attrs = {}, children = []) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') node.className = v;
      else if (k === 'html') node.innerHTML = v;
      else if (k.startsWith('on')) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v);
    }
    for (const child of [].concat(children)) {
      if (child == null) continue;
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    }
    return node;
  }

  function $(sel, root = document) { return root.querySelector(sel); }
  function $$(sel, root = document) { return Array.from(root.querySelectorAll(sel)); }

  function fmtNum(n, opts = {}) {
    if (n == null || isNaN(n)) return '—';
    const { digits = 0, sign = false } = opts;
    const v = Number(n).toFixed(digits);
    return (sign && n > 0 ? '+' : '') + Number(v).toLocaleString('en-US');
  }

  function pad2(n) { return String(n).padStart(2, '0'); }

  function parseDate(yyyymmdd) {
    const [y, m, d] = yyyymmdd.split('-').map(Number);
    return { y, m, d, roman: ROMAN[m] || String(m) };
  }

  // Highlight the title_em substring inside title with <em>...</em>
  function emphasizeTitle(title, titleEm) {
    if (!titleEm) return escapeHTML(title);
    const idx = title.indexOf(titleEm);
    if (idx < 0) return escapeHTML(title);
    return [
      escapeHTML(title.slice(0, idx)),
      '<em>', escapeHTML(titleEm), '</em>',
      escapeHTML(title.slice(idx + titleEm.length))
    ].join('');
  }

  function escapeHTML(s) {
    return String(s)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ---------- Data ----------

  async function loadData() {
    // Use root-absolute paths so deeply-nested pages (e.g. reports/YYYY-MM-DD/foo.html)
    // resolve the same JSON file as the index. Works on python -m http.server and
    // on Cloudflare Pages root deployment.
    const bust = '?t=' + Date.now();
    const [m, c] = await Promise.all([
      fetch('/manifest.json' + bust).then(r => r.ok ? r.json() : null).catch(() => null),
      fetch('/categories.json' + bust).then(r => r.ok ? r.json() : null).catch(() => null),
    ]);
    STATE.manifest = m;
    STATE.categories = c;
  }

  function entriesByCategory(catId) {
    if (!STATE.manifest) return [];
    return STATE.manifest.entries
      .filter(e => e.category === catId)
      .sort((a, b) => b.date.localeCompare(a.date) || (b.time || '').localeCompare(a.time || ''));
  }

  function countByCategory(catId) {
    return entriesByCategory(catId).length;
  }

  function getCategory(catId) {
    if (!STATE.categories) return null;
    return STATE.categories.categories.find(c => c.id === catId);
  }

  function latestEntries(limit = 5) {
    if (!STATE.manifest) return [];
    return [...STATE.manifest.entries]
      .sort((a, b) => b.date.localeCompare(a.date) || (b.time || '').localeCompare(a.time || ''))
      .slice(0, limit);
  }

  // ---------- Page: Index ----------

  function renderIndex() {
    if (!STATE.manifest || !STATE.categories) return;

    renderCover();
    renderCoverStats();
    renderDashboard();
    renderTodayReports();
    renderCategoryIndex();
    renderFooter();
  }

  function renderCover() {
    const m = STATE.manifest;
    const d = parseDate(m.today);
    const dateLine = `${d.y} · ${d.roman} · ${pad2(d.d)}`;

    const specEl = $('#cover-spec');
    if (specEl) {
      specEl.innerHTML = `
        <span class="spec-line">${dateLine}</span>
        <span class="spec-line spec-line--gold">VOLUME ${pad2(m.volume_number || 1)}</span>
        <span class="spec-line">MARKET EDITION</span>
      `;
    }

    const dekEl = $('#cover-dek');
    if (dekEl && m.market_summary) {
      const ms = m.market_summary;
      const leader = ms.leading_sector
        ? `領漲族群 <strong style="color:var(--gold-soft)">${escapeHTML(ms.leading_sector)}</strong> +${fmtNum(ms.leading_sector_pct, { digits: 2 })}%。`
        : '';
      dekEl.innerHTML = `本日盤後 ${ms.industries_count} 個族群中 ${ms.industries_up} 個收紅，全市場成交 ${fmtNum(ms.volume_billion, { digits: 0 })} 億。${leader}`;
    }
  }

  function renderCoverStats() {
    const ms = STATE.manifest.market_summary;
    if (!ms) return;
    const host = $('#cover-stats');
    if (!host) return;

    const stats = [
      { label: '族群', value: fmtNum(ms.industries_count), sub: `↑ ${ms.industries_up} / ↓ ${ms.industries_count - ms.industries_up}` },
      { label: '全族群均幅', value: (ms.industries_avg_pct >= 0 ? '+' : '') + fmtNum(ms.industries_avg_pct, { digits: 2 }) + '%',
        color: ms.industries_avg_pct >= 0 ? 'up' : 'down',
        sub: `中位 ${(ms.industries_median_pct >= 0 ? '+' : '') + fmtNum(ms.industries_median_pct, { digits: 2 })}%` },
      { label: '成交額', value: fmtNum(ms.volume_billion, { digits: 0 }), sub: '億 NT$' },
      { label: '漲停集中', value: fmtNum(ms.limit_up_concentration_pct, { digits: 1 }) + '%',
        sub: `TWSE ${ms.twse_up}↑ / ${ms.twse_down}↓` },
    ];

    host.innerHTML = stats.map(s => `
      <div class="stat">
        <div class="stat__label">${escapeHTML(s.label)}</div>
        <div class="stat__value ${s.color === 'up' ? 'num-up' : s.color === 'down' ? 'num-down' : ''}">${s.value}</div>
        <div class="stat__sub">${escapeHTML(s.sub || '')}</div>
      </div>
    `).join('');
  }

  function renderDashboard() {
    const cover = $('.cover');
    if (!cover) return;

    let section = $('#dashboard-section');
    if (!section) {
      section = el('section', {
        id: 'dashboard-section',
        class: 'section section--dashboard scroll-fade',
        'aria-labelledby': 'dashboard-title'
      }, [
        el('div', { class: 'section__head' }, [
          el('div', { class: 'section__label' }, [
            el('span', { class: 'section__marker', 'aria-hidden': 'true' }),
            el('h2', { id: 'dashboard-title', class: 'section__title', html: '市場<em>儀表板</em>' })
          ]),
          el('span', { id: 'dashboard-meta', class: 'section__meta' })
        ]),
        el('div', { id: 'dashboard-grid', class: 'dashboard-grid' })
      ]);
      cover.insertAdjacentElement('afterend', section);
    }

    const categories = STATE.categories.categories.filter(c => c.enabled !== false);
    const preferred = ['taiex', 'sectors', 'chips', 'txo', 'mops', 'news', 'stocks', 'research'];
    const ordered = [
      ...preferred.map(id => categories.find(c => c.id === id)).filter(Boolean),
      ...categories.filter(c => !preferred.includes(c.id))
    ];

    const cards = ordered
      .map(c => {
        const entries = entriesByCategory(c.id);
        return { category: c, entry: entries[0], count: entries.length };
      })
      .filter(x => x.entry)
      .slice(0, 7);

    const meta = $('#dashboard-meta');
    if (meta) meta.textContent = `${STATE.manifest.today} · ${cards.length} SIGNALS`;

    const host = $('#dashboard-grid');
    if (!host) return;
    host.innerHTML = cards.map(renderDashboardCard).join('');
  }

  function renderDashboardCard(item) {
    const { category, entry, count } = item;
    const typeLabel = TYPE_LABELS[entry.type] || entry.type || '更新';
    const dateText = [entry.date, entry.time].filter(Boolean).join(' ');
    const summary = entry.summary || dashboardFallbackSummary(entry);
    const tags = (entry.tags || []).slice(0, 4);

    return `
      <a class="dashboard-card dashboard-card--${escapeHTML(category.id)}" href="${escapeHTML(entry.url)}">
        <div class="dashboard-card__kicker">
          <span>${escapeHTML(category.name_zh)}</span>
          <span>${escapeHTML(typeLabel)}</span>
        </div>
        <h3 class="dashboard-card__title">${emphasizeTitle(entry.title, entry.title_em)}</h3>
        <p class="dashboard-card__summary">${escapeHTML(summary)}</p>
        <div class="dashboard-card__foot">
          <span>${escapeHTML(dateText || entry.date || '')}</span>
          <span>${count} entries</span>
        </div>
        ${tags.length ? `<div class="dashboard-card__tags">${tags.map(t => `<span>${escapeHTML(t)}</span>`).join('')}</div>` : ''}
      </a>
    `;
  }

  function dashboardFallbackSummary(entry) {
    const tags = (entry.tags || []).slice(0, 3).join(' / ');
    if (entry.category === 'chips' && entry.id === 'tri-source-lamp') {
      return '分點主力、大股東與三大法人三線同向訊號，適合先看共識再看個股。';
    }
    if (entry.category === 'stocks') {
      return '個股深度卡彙整基本資料、題材事件與追蹤欄位，作為研究入口。';
    }
    if (entry.category === 'mops') {
      return '重大訊息依事件類型、重大性與例行/非例行狀態整理，先看高影響事件。';
    }
    return tags ? `最新標籤：${tags}` : '最新資料已歸檔，點入查看完整表格與圖表。';
  }

  function renderTodayReports() {
    const todayEntries = STATE.manifest.entries
      .filter(e => e.date === STATE.manifest.today)
      .sort((a, b) => (b.time || '').localeCompare(a.time || ''));

    const headEl = $('#today-section-meta');
    if (headEl) {
      headEl.textContent = `${STATE.manifest.today} · ${todayEntries.length} ENTRIES`;
    }

    const major = todayEntries[0];
    const minors = todayEntries.slice(1);

    const majorHost = $('#today-major');
    if (major && majorHost) {
      majorHost.innerHTML = renderMajorCard(major);
    }

    const minorHost = $('#today-minor');
    if (minorHost) {
      minorHost.innerHTML = minors.map(renderMinorCard).join('');
      if (minors.length === 0) {
        minorHost.innerHTML = `<div class="placeholder">No additional entries today</div>`;
      }
    }
  }

  function renderMajorCard(entry) {
    const cat = getCategory(entry.category);
    const typeLabel = TYPE_LABELS[entry.type] || entry.type;
    const catName = cat ? cat.name_zh : entry.category;

    return `
      <a class="report-major" href="${escapeHTML(entry.url)}">
        <div class="report-major__spec">
          <span class="spec-line spec-line--gold">${escapeHTML(catName)} · ${escapeHTML(typeLabel)}</span>
          <span class="spec-line">${escapeHTML(entry.date)} ${escapeHTML(entry.time || '')}</span>
        </div>
        <h2 class="report-major__title">${emphasizeTitle(entry.title, entry.title_em)}</h2>
        <p class="report-major__summary">${escapeHTML(entry.summary || '')}</p>
        ${renderStats(entry.stats)}
        <div class="report-major__tags">${(entry.tags || []).map(t => `<span class="tag">${escapeHTML(t)}</span>`).join('')}</div>
        <div class="report-major__cta">閱讀全文</div>
      </a>
    `;
  }

  function renderMinorCard(entry) {
    const cat = getCategory(entry.category);
    const typeLabel = TYPE_LABELS[entry.type] || entry.type;
    const firstStat = (entry.stats && entry.stats[0]) || null;
    return `
      <a class="report-minor" href="${escapeHTML(entry.url)}">
        <div class="report-minor__spec">
          <span class="spec-line spec-line--gold">${escapeHTML(cat ? cat.name_zh : entry.category)} · ${escapeHTML(typeLabel)}</span>
        </div>
        <h3 class="report-minor__title">${emphasizeTitle(entry.title, entry.title_em)}</h3>
        ${firstStat ? `<div class="report-minor__stat"><strong>${escapeHTML(firstStat.label)}</strong>　${escapeHTML(firstStat.value)}</div>` : ''}
      </a>
    `;
  }

  function renderStats(stats) {
    if (!stats || stats.length === 0) return '';
    return `
      <div class="report-major__stats">
        ${stats.map(s => `
          <div class="stat">
            <div class="stat__label">${escapeHTML(s.label)}</div>
            <div class="stat__value stat__value--sm ${s.color === 'up' ? 'num-up' : s.color === 'down' ? 'num-down' : 'num-neutral'}">${escapeHTML(s.value)}</div>
          </div>
        `).join('')}
      </div>
    `;
  }

  function renderCategoryIndex() {
    const host = $('#cat-index');
    if (!host) return;

    const cats = STATE.categories.categories;
    host.innerHTML = cats.map((c, idx) => {
      const count = countByCategory(c.id);
      const disabled = !c.enabled || count === 0;
      const href = disabled ? '#' : `category.html?cat=${encodeURIComponent(c.id)}`;
      return `
        <a class="cat-row ${disabled ? 'is-disabled' : ''}" href="${href}">
          <div class="cat-row__num">№ ${String(idx + 1).padStart(2, '0')}</div>
          <div class="cat-row__name">
            <span class="cat-row__name-zh">${escapeHTML(c.name_zh)}</span>
            <span class="cat-row__name-en">${escapeHTML(c.name_en)}</span>
          </div>
          <div class="cat-row__desc">${escapeHTML(c.description || '')}</div>
          <div class="cat-row__count">
            ${disabled
              ? '<span>FORTHCOMING</span>'
              : `<strong>${count}</strong> ENTRIES`}
          </div>
          <div class="cat-row__arrow">${disabled ? '·' : '→'}</div>
        </a>
      `;
    }).join('');
  }

  // ---------- Page: Category ----------

  function matchSubcategory(entry, subcat) {
    if (!subcat || !subcat.match_id_prefix || subcat.match_id_prefix.length === 0) {
      return false;
    }
    const id = entry.id || '';
    // 支援兩種 entry.id 結構：
    //   1. 純 type 開頭：market-pulse / chip-concentration / tri-source-lamp
    //   2. 日期前綴：2026-05-29-sectors-weekly / 2026-05-30-news-daily
    return subcat.match_id_prefix.some(prefix => {
      if (id === prefix) return true;
      if (id.startsWith(prefix)) return true;
      // 日期前綴模式：YYYY-MM-DD-<prefix>...
      if (/^\d{4}-\d{2}-\d{2}-/.test(id) && id.indexOf('-' + prefix) === 10) return true;
      return false;
    });
  }

  function filterBySubcategory(entries, cat, subId) {
    if (!subId || !cat.subcategories || cat.subcategories.length === 0) {
      return entries;
    }
    const subcat = cat.subcategories.find(s => s.id === subId);
    if (!subcat) return entries;
    return entries.filter(e => matchSubcategory(e, subcat));
  }

  function renderSubNav(cat, currentSubId, allEntries) {
    const host = document.getElementById('sub-nav');
    if (!host) return;
    if (!cat.subcategories || cat.subcategories.length === 0) {
      host.innerHTML = '';
      host.classList.add('hidden');
      return;
    }
    host.classList.remove('hidden');

    // 統計每個次分類的 entry 數
    const counts = {};
    cat.subcategories.forEach(s => {
      counts[s.id] = allEntries.filter(e => matchSubcategory(e, s)).length;
    });
    const totalCount = allEntries.length;

    const allActive = !currentSubId ? ' is-active' : '';
    const buttons = [
      `<a class="sub-nav__tab${allActive}" href="category.html?cat=${encodeURIComponent(cat.id)}" data-sub="">全部 <span class="sub-nav__count">${totalCount}</span></a>`
    ];
    cat.subcategories.forEach(s => {
      const active = s.id === currentSubId ? ' is-active' : '';
      const count = counts[s.id] || 0;
      buttons.push(
        `<a class="sub-nav__tab${active}" href="category.html?cat=${encodeURIComponent(cat.id)}&amp;sub=${encodeURIComponent(s.id)}" data-sub="${escapeHTML(s.id)}">`
        + `${escapeHTML(s.name_zh)} <span class="sub-nav__count">${count}</span>`
        + `</a>`
      );
    });
    host.innerHTML = buttons.join('');
  }

  function renderCategoryPage() {
    if (!STATE.manifest || !STATE.categories) return;

    const params = new URLSearchParams(window.location.search);
    const catId = params.get('cat') || 'sectors';
    const subId = params.get('sub') || '';
    const cat = getCategory(catId);

    if (!cat) {
      $('#cat-cover').innerHTML = '<p class="muted">Category not found.</p>';
      return;
    }

    const subcat = subId && cat.subcategories
      ? cat.subcategories.find(s => s.id === subId)
      : null;

    const titleSuffix = subcat ? ` · ${subcat.name_zh}` : '';
    document.title = `${cat.name_zh}${titleSuffix} · ${cat.name_en} — charles16888`;

    // Breadcrumb
    const bc = $('#breadcrumb-cat');
    if (bc) bc.textContent = cat.name_zh + (subcat ? ' / ' + subcat.name_zh : '');

    // Cover
    const allEntries = entriesByCategory(catId);
    const entries = filterBySubcategory(allEntries, cat, subId);
    const oldest = entries.length ? entries[entries.length - 1].date : '—';
    const newest = entries.length ? entries[0].date : '—';

    $('#cat-cover').innerHTML = `
      <div class="cat-cover__spec">
        <span class="spec-line spec-line--gold">№ ${String(STATE.categories.categories.findIndex(x => x.id === catId) + 1).padStart(2, '0')}</span>
        <span class="spec-line">${entries.length} ENTRIES${subcat ? ' (' + escapeHTML(subcat.name_zh) + ')' : ''}</span>
        <span class="spec-line">${oldest === newest ? oldest : oldest + ' → ' + newest}</span>
      </div>
      <h1 class="cat-cover__title">${escapeHTML(cat.name_zh)}<em> · ${escapeHTML(cat.name_en)}</em></h1>
      <p class="cat-cover__subtitle">${escapeHTML(cat.tagline_en || '')}</p>
      <p class="cat-cover__dek">${escapeHTML(cat.description || '')}${cat.tagline_zh ? '　·　' + escapeHTML(cat.tagline_zh) : ''}</p>
    `;

    // Sub nav（次分類 tabs）
    renderSubNav(cat, subId, allEntries);

    // Archive (group by month)
    renderCategoryArchive(catId, entries);

    // News-only: search & filter panel
    if (catId === 'news') {
      setupNewsSearch().catch(e => console.error('[news-search]', e));
    }
  }

  // ---------- News search & filter ----------

  const NEWS_PAGE_SIZE = 50;
  const NEWS_WORKER_URL = 'https://lynus-search.ehulordking.workers.dev/search';
  const NEWS_STATE = {
    index: null,
    mode: 'keyword',          // 'keyword' | 'ai'
    q: '',
    sub: new Set(),
    sent: new Set(),
    reg: new Set(),
    evt: new Set(),
    shown: NEWS_PAGE_SIZE,
    aiResults: null,          // last AI search results
    aiQuery: '',              // last AI query
    aiLoading: false,
  };

  async function setupNewsSearch() {
    const panel = $('#news-search');
    if (!panel) return;
    panel.innerHTML = '<div class="muted" style="padding:24px 0;font-family:var(--font-mono);font-size:11px;letter-spacing:.15em">LOADING NEWS INDEX…</div>';
    panel.classList.remove('hidden');

    try {
      const r = await fetch('/assets/news_index.json');
      NEWS_STATE.index = await r.json();
    } catch (e) {
      console.error('[news] index load failed', e);
      panel.innerHTML = '<div class="muted">無法載入新聞索引</div>';
      return;
    }

    buildSearchPanel(panel, NEWS_STATE.index.articles);
    attachSearchListeners();
  }

  function buildSearchPanel(host, articles) {
    const topSubs = topNCounts(articles, 'sub', 12);
    const topRegs = topNCounts(articles, 'reg', 8);
    const topEvts = topNCounts(articles, 'evt', 8);

    host.innerHTML = `
      <div class="search-mode-tabs">
        <button class="search-mode-tab is-active" data-mode="keyword" type="button">
          <span class="search-mode-tab__title">一般搜尋</span>
          <span class="search-mode-tab__sub">標題 / 公司 / 股號比對</span>
        </button>
        <button class="search-mode-tab" data-mode="ai" type="button">
          <span class="search-mode-tab__title">AI 智能搜尋</span>
          <span class="search-mode-tab__sub">語意理解 · 找概念相關</span>
        </button>
      </div>

      <!-- Keyword mode -->
      <div id="news-mode-keyword" class="search-mode-pane">
        <div class="search-input-wrap">
          <input id="news-q" class="search-input" type="text" autocomplete="off"
                 placeholder="搜尋標題、公司、股號…" />
          <button id="news-clear" class="search-clear" type="button">CLEAR ALL</button>
        </div>
        <div class="filter-groups">
          ${renderChipGroup('主題', 'sub', topSubs)}
          <div class="filter-group">
            <div class="filter-group__label">傾向</div>
            <div class="filter-chips">
              <button class="filter-chip" data-group="sent" data-value="利多" type="button">利多</button>
              <button class="filter-chip" data-group="sent" data-value="利空" type="button">利空</button>
              <button class="filter-chip" data-group="sent" data-value="中性" type="button">中性</button>
            </div>
          </div>
          ${renderChipGroup('地區', 'reg', topRegs)}
          ${renderChipGroup('事件', 'evt', topEvts)}
        </div>
      </div>

      <!-- AI mode -->
      <div id="news-mode-ai" class="search-mode-pane hidden">
        <div class="search-input-wrap">
          <input id="news-ai-q" class="search-input" type="text" autocomplete="off"
                 placeholder="自然語言提問，例：AI 算力供應鏈、台積電擴產動向、半導體出口管制…" />
          <button id="news-ai-go" class="search-clear is-visible" type="button">SEARCH ↵</button>
        </div>
        <div class="search-ai-hint">
          找概念相關的新聞（不限字面比對）。每次查詢約 1-2 秒。
        </div>
      </div>
    `;
  }

  function renderChipGroup(label, key, items) {
    const chips = items.map(([v, n]) =>
      `<button class="filter-chip" data-group="${key}" data-value="${escapeHTML(v)}" type="button">${escapeHTML(v)}<span class="filter-chip__count">${n}</span></button>`
    ).join('');
    return `
      <div class="filter-group">
        <div class="filter-group__label">${label}</div>
        <div class="filter-chips">${chips}</div>
      </div>
    `;
  }

  function topNCounts(articles, key, n) {
    const m = new Map();
    for (const a of articles) {
      const arr = a[key];
      if (!arr) continue;
      for (const v of arr) {
        m.set(v, (m.get(v) || 0) + 1);
      }
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, n);
  }

  function attachSearchListeners() {
    // Mode tabs
    $$('.search-mode-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        const mode = tab.dataset.mode;
        if (mode === NEWS_STATE.mode) return;
        NEWS_STATE.mode = mode;
        $$('.search-mode-tab').forEach(t => t.classList.toggle('is-active', t === tab));
        $('#news-mode-keyword').classList.toggle('hidden', mode !== 'keyword');
        $('#news-mode-ai').classList.toggle('hidden', mode !== 'ai');
        performNewsSearch();
      });
    });

    // AI search
    const aiInput = $('#news-ai-q');
    const aiGo = $('#news-ai-go');
    const runAi = () => {
      const q = aiInput.value.trim();
      if (!q || q === NEWS_STATE.aiQuery) return;
      runAiSearch(q);
    };
    aiGo.addEventListener('click', runAi);
    aiInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') runAi();
    });

    const input = $('#news-q');
    const clearBtn = $('#news-clear');

    let debounce;
    input.addEventListener('input', () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        NEWS_STATE.q = input.value.trim().toLowerCase();
        clearBtn.classList.toggle('is-visible', isFilterActive());
        NEWS_STATE.shown = NEWS_PAGE_SIZE;
        performNewsSearch();
      }, 150);
    });

    $$('.filter-chip').forEach(chip => {
      chip.addEventListener('click', () => {
        const group = chip.dataset.group;
        const value = chip.dataset.value;
        const set = NEWS_STATE[group];
        if (set.has(value)) set.delete(value);
        else set.add(value);
        chip.classList.toggle('is-active');
        clearBtn.classList.toggle('is-visible', isFilterActive());
        NEWS_STATE.shown = NEWS_PAGE_SIZE;
        performNewsSearch();
      });
    });

    clearBtn.addEventListener('click', () => {
      input.value = '';
      NEWS_STATE.q = '';
      NEWS_STATE.sub.clear();
      NEWS_STATE.sent.clear();
      NEWS_STATE.reg.clear();
      NEWS_STATE.evt.clear();
      $$('.filter-chip.is-active').forEach(c => c.classList.remove('is-active'));
      clearBtn.classList.remove('is-visible');
      NEWS_STATE.shown = NEWS_PAGE_SIZE;
      performNewsSearch();
    });
  }

  function isFilterActive() {
    return Boolean(
      NEWS_STATE.q ||
      NEWS_STATE.sub.size ||
      NEWS_STATE.sent.size ||
      NEWS_STATE.reg.size ||
      NEWS_STATE.evt.size
    );
  }

  function performNewsSearch() {
    const archive = $('#archive-section');
    const resultsHost = $('#news-results');

    // AI mode is event-driven (only fires on submit), not on this tick.
    if (NEWS_STATE.mode === 'ai') {
      const hasAi = NEWS_STATE.aiResults || NEWS_STATE.aiLoading;
      if (archive) archive.classList.toggle('hidden', !!hasAi);
      if (resultsHost) resultsHost.classList.toggle('hidden', !hasAi);
      if (hasAi) renderAiResults(resultsHost);
      return;
    }

    if (!isFilterActive()) {
      if (archive) archive.classList.remove('hidden');
      if (resultsHost) resultsHost.classList.add('hidden');
      return;
    }

    if (archive) archive.classList.add('hidden');
    if (resultsHost) resultsHost.classList.remove('hidden');

    const q = NEWS_STATE.q;
    const matched = NEWS_STATE.index.articles.filter(a => {
      if (q) {
        const hay = (a.title + ' ' + (a.co || []).join(' ') + ' ' + (a.sk || []).join(' ')).toLowerCase();
        if (!hay.includes(q)) return false;
      }
      if (NEWS_STATE.sub.size && !(a.sub || []).some(v => NEWS_STATE.sub.has(v))) return false;
      if (NEWS_STATE.sent.size && !NEWS_STATE.sent.has(a.sent)) return false;
      if (NEWS_STATE.reg.size && !(a.reg || []).some(v => NEWS_STATE.reg.has(v))) return false;
      if (NEWS_STATE.evt.size && !(a.evt || []).some(v => NEWS_STATE.evt.has(v))) return false;
      return true;
    });

    renderNewsResults(matched, resultsHost);
  }

  async function runAiSearch(q) {
    NEWS_STATE.aiLoading = true;
    NEWS_STATE.aiQuery = q;
    NEWS_STATE.aiResults = null;
    performNewsSearch();   // render loading state

    try {
      const url = NEWS_WORKER_URL + '?q=' + encodeURIComponent(q) + '&k=30';
      const r = await fetch(url);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const data = await r.json();
      NEWS_STATE.aiResults = data;
    } catch (e) {
      NEWS_STATE.aiResults = { error: String(e) };
    } finally {
      NEWS_STATE.aiLoading = false;
      performNewsSearch();
    }
  }

  function renderAiResults(host) {
    if (NEWS_STATE.aiLoading) {
      host.innerHTML = '<div class="search-empty">AI 思考中…</div>';
      return;
    }
    const data = NEWS_STATE.aiResults;
    if (!data) return;
    if (data.error) {
      host.innerHTML = `<div class="search-empty">搜尋失敗：${escapeHTML(data.error)}</div>`;
      return;
    }
    const matches = data.matches || [];
    if (matches.length === 0) {
      host.innerHTML = '<div class="search-empty">沒有找到符合的新聞</div>';
      return;
    }

    let html = `
      <div class="search-results__meta">
        AI 智能搜尋 · 「${escapeHTML(NEWS_STATE.aiQuery)}」 ·
        <strong>${matches.length}</strong>則語意相關
      </div>
    `;
    for (const m of matches) {
      const parts = [];
      if (m.regions && m.regions.length) parts.push(m.regions.slice(0, 3).join(' / '));
      if (m.companies && m.companies.length) parts.push(m.companies.slice(0, 3).join(' / '));
      if (m.stock_codes && m.stock_codes.length) parts.push(m.stock_codes.map(c => '#' + c).join(' '));
      if (m.sub_category && m.sub_category.length) parts.push(m.sub_category.slice(0, 2).join(' / '));
      if (m.sentiment) parts.push('傾向：' + m.sentiment);
      parts.push(m.date || '');

      // m.id from Vectorize is "art-3692" — peel the prefix and jump straight
      // to the matching anchor inside that day's digest (main.js's
      // setupNewsAnchorAutoExpand auto-opens the <details> on arrival).
      const articleNum = String(m.id || '').replace(/^art-/, '');
      const localUrl = `reports/${encodeURIComponent(m.date || '')}/news-daily.html#art-${articleNum}`;
      const externalUrl = m.url || '';

      html += `
        <article class="search-article">
          <h4 class="search-article__title">
            <a href="${escapeHTML(localUrl)}">${escapeHTML(m.title || '')}</a>
            ${externalUrl ? `<a class="search-article__ext" href="${escapeHTML(externalUrl)}" target="_blank" rel="noopener noreferrer" title="開啟 digitimes 原文">↗ 原文</a>` : ''}
            <span class="search-article__score">相關度 ${m.score.toFixed(2)}</span>
          </h4>
          <div class="search-article__meta">${escapeHTML(parts.filter(Boolean).join(' · '))}</div>
        </article>
      `;
    }
    host.innerHTML = html;
  }

  function renderNewsResults(matched, host) {
    if (matched.length === 0) {
      host.innerHTML = '<div class="search-empty">沒有找到符合條件的新聞</div>';
      return;
    }

    const shown = matched.slice(0, NEWS_STATE.shown);

    // Group by date
    const byDate = new Map();
    for (const a of shown) {
      if (!byDate.has(a.date)) byDate.set(a.date, []);
      byDate.get(a.date).push(a);
    }

    const totalShown = shown.length;
    const totalMatched = matched.length;

    let html = `
      <div class="search-results__meta">
        搜尋結果 ·<strong>${totalMatched}</strong>則新聞${totalShown < totalMatched ? ` · 顯示 ${totalShown}` : ''}
      </div>
    `;

    for (const [date, articles] of byDate) {
      html += `<div class="search-day">
        <h3 class="search-day__date">${escapeHTML(date)}<span class="search-day__count">${articles.length} entries</span></h3>
        ${articles.map(renderArticleCard).join('')}
      </div>`;
    }

    if (totalShown < totalMatched) {
      html += `
        <div class="search-load-more">
          <button id="news-load-more" class="search-load-more__btn" type="button">
            載入更多 · 還剩 ${totalMatched - totalShown}
          </button>
        </div>
      `;
    }

    host.innerHTML = html;

    const loadMore = $('#news-load-more');
    if (loadMore) {
      loadMore.addEventListener('click', () => {
        NEWS_STATE.shown += NEWS_PAGE_SIZE;
        performNewsSearch();
      });
    }
  }

  function renderArticleCard(a) {
    const parts = [];
    if (a.reg && a.reg.length) parts.push(a.reg.slice(0, 3).join(' / '));
    if (a.co && a.co.length) parts.push(a.co.slice(0, 3).join(' / '));
    if (a.sk && a.sk.length) parts.push(a.sk.slice(0, 5).map(c => '#' + c).join(' '));
    if (a.evt && a.evt.length) parts.push(a.evt.slice(0, 2).join(' / '));
    if (a.sent) parts.push('傾向：' + a.sent);

    // Search result links straight to the article's anchor inside that day's
    // digest — the per-article standalone pages have been retired to keep the
    // deployment file count low. setupNewsAnchorAutoExpand handles auto-open.
    const localUrl = `reports/${encodeURIComponent(a.date)}/news-daily.html#art-${a.id}`;
    const externalUrl = a.url || '';
    return `
      <article class="search-article">
        <h4 class="search-article__title">
          <a href="${escapeHTML(localUrl)}">${escapeHTML(a.title)}</a>
          ${externalUrl ? `<a class="search-article__ext" href="${escapeHTML(externalUrl)}" target="_blank" rel="noopener noreferrer" title="開啟 digitimes 原文">↗ 原文</a>` : ''}
        </h4>
        <div class="search-article__meta">${escapeHTML(parts.join(' · '))}</div>
      </article>
    `;
  }

  function renderCategoryArchive(catId, entries) {
    const host = $('#cat-archive');
    if (!host) return;

    if (entries.length === 0) {
      host.innerHTML = `<div class="placeholder">No entries yet — pipeline pending</div>`;
      return;
    }

    // Group by YYYY-MM
    const groups = {};
    entries.forEach(e => {
      const key = e.date.slice(0, 7); // YYYY-MM
      (groups[key] = groups[key] || []).push(e);
    });

    const sortedKeys = Object.keys(groups).sort((a, b) => b.localeCompare(a));

    host.innerHTML = sortedKeys.map(key => {
      const [y, m] = key.split('-').map(Number);
      const roman = ROMAN[m] || String(m);
      const items = groups[key];
      return `
        <section class="month-group scroll-fade">
          <div class="month-group__head">
            <h2 class="month-group__name"><span class="roman">${roman}</span>${y} · ${pad2(m)}</h2>
            <span class="month-group__count">${items.length} ENTRIES</span>
          </div>
          ${items.map(e => renderReportRow(e)).join('')}
        </section>
      `;
    }).join('');
  }

  function renderReportRow(entry) {
    const typeLabel = TYPE_LABELS[entry.type] || entry.type;
    return `
      <a class="report-row" href="${escapeHTML(entry.url)}">
        <div class="report-row__date">${escapeHTML(entry.date)}</div>
        <h3 class="report-row__title">${emphasizeTitle(entry.title, entry.title_em)}</h3>
        <div class="report-row__type">${escapeHTML(typeLabel)}</div>
        <div class="report-row__arrow">→</div>
      </a>
    `;
  }

  // ---------- Footer ----------

  function renderFooter() {
    const f = $('#footer-line');
    if (!f) return;
    const m = STATE.manifest || {};
    const updated = m.updated_at ? m.updated_at.slice(0, 16).replace('T', ' ') : '—';
    f.innerHTML = `
      <span class="footer__brand">charles16888</span>
      <span>MARKET EDITION · VOLUME ${pad2(m.volume_number || 1)}</span>
      <span>UPDATED ${escapeHTML(updated)}</span>
    `;
  }

  // ---------- Motion ----------

  function setupRevealOnLoad() {
    // Add class after a tick so transitions trigger reliably
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.body.classList.add('is-loaded');
      });
    });
  }

  function setupScrollFade() {
    const items = $$('.scroll-fade');
    if (!items.length || !('IntersectionObserver' in window)) {
      items.forEach(i => i.classList.add('is-visible'));
      return;
    }
    const io = new IntersectionObserver((entries) => {
      entries.forEach(en => {
        if (en.isIntersecting) {
          en.target.classList.add('is-visible');
          io.unobserve(en.target);
        }
      });
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
    items.forEach(i => io.observe(i));

    // Safety net: anything still hidden after 2.5s (programmatic scroll,
    // full-page screenshot tools, very tall pages on slow networks) gets
    // forced visible so content is never permanently invisible.
    setTimeout(() => {
      $$('.scroll-fade:not(.is-visible)').forEach(el => el.classList.add('is-visible'));
    }, 2500);
  }

  // ---------- Init ----------

  async function init() {
    try {
      await loadData();
    } catch (e) {
      console.error('[antigravity] loadData failed:', e);
    }

    try {
      if (STATE.page === 'index') {
        renderIndex();
      } else if (STATE.page === 'category') {
        renderCategoryPage();
        renderFooter();
      } else if (STATE.page === 'report') {
        renderFooter();
      }
    } catch (e) {
      console.error('[antigravity] render failed:', e);
    }

    // News pages: when user jumps to #art-N (from the TOC or external link),
    // auto-open the matching <details class="news-body"> so they see the
    // body immediately instead of having to click "展開內文" again.
    setupNewsAnchorAutoExpand();

    // Floating "↑" button (all pages) — appears once user scrolls down.
    setupBackToTop();

    // Always run reveal — even if data load / render failed, static content
    // (masthead, breadcrumb, wrapped report body) should still become visible.
    setupRevealOnLoad();
    window.requestAnimationFrame(() => setupScrollFade());
  }

  function setupBackToTop() {
    // Inject the styles once for both the inline ↑ links inside news body
    // and the floating circular button anchored to the viewport.
    const style = document.createElement('style');
    style.textContent = `
      .back-to-top {
        text-align: right; margin: 12px 0 32px;
        font-family: 'JetBrains Mono', monospace;
        font-size: 11px; letter-spacing: .12em;
      }
      .back-to-top a {
        color: #6e6350; text-decoration: none;
        border-bottom: 1px dashed rgba(232,223,211,0.18);
        padding-bottom: 2px;
      }
      .back-to-top a:hover { color: #d4af37; border-color: #d4af37; }

      .back-to-top-btn {
        position: fixed; right: 24px; bottom: 24px;
        width: 44px; height: 44px; border-radius: 50%;
        border: 1px solid rgba(212,175,55,0.45);
        background: rgba(26,22,18,0.88); color: #d4af37;
        font-size: 18px; line-height: 1; cursor: pointer;
        opacity: 0; pointer-events: none;
        transition: opacity .25s, transform .15s, background .15s;
        backdrop-filter: blur(8px);
        -webkit-backdrop-filter: blur(8px);
        z-index: 100;
      }
      .back-to-top-btn.is-visible { opacity: 1; pointer-events: auto; }
      .back-to-top-btn:hover {
        background: #d4af37; color: #1a1612; transform: translateY(-2px);
      }
      @media (max-width: 600px) {
        .back-to-top-btn { right: 16px; bottom: 16px; width: 40px; height: 40px; }
      }
    `;
    document.head.appendChild(style);

    // Floating button — show after 600px scroll, click goes to top.
    const btn = document.createElement('button');
    btn.className = 'back-to-top-btn';
    btn.setAttribute('aria-label', '回到頂部');
    btn.type = 'button';
    btn.innerHTML = '↑';
    btn.addEventListener('click', () => {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    document.body.appendChild(btn);
    window.addEventListener('scroll', () => {
      btn.classList.toggle('is-visible', window.scrollY > 600);
    }, { passive: true });
  }

  function setupNewsAnchorAutoExpand() {
    function openTargetArticle() {
      const hash = window.location.hash;
      if (!hash || !hash.startsWith('#art-')) return;
      const anchor = document.getElementById(hash.slice(1));
      if (!anchor) return;
      // The body <details> is the next sibling element after the anchor heading.
      let el = anchor.nextElementSibling;
      while (el && !(el.tagName === 'DETAILS' && el.classList.contains('news-body'))) {
        el = el.nextElementSibling;
      }
      if (el) {
        el.open = true;
        // Re-scroll so the heading lands at the top after expansion shifts layout.
        setTimeout(() => anchor.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60);
      }
    }
    window.addEventListener('hashchange', openTargetArticle);
    // Run once at load in case the URL already has a #art-N hash.
    openTargetArticle();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
