// Scime-Atlas-inspired Interactive WHO Mortality Flow & Year Snapshot Engine (2000-2023)
import { sliceDiseases } from './salusApi.js';

const STREAM_PALETTE = [
  '#ef4444', '#f97316', '#eab308', '#10b981', '#06b6d4',
  '#3b82f6', '#8b5cf6', '#e64980', '#14b8a6', '#f59e0b',
  '#ec4899', '#6366f1', '#22c55e', '#fb7185', '#38bdf8',
];

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

export class FlowMortalityView {
  constructor(callbacks) {
    this.cb = callbacks;
    this.flowData = null;
    this.loc = 'WRLD';      // WRLD | CHN
    this.year = 2023;
    this.scale = 'pct';     // pct | deaths
    this.parentCode = '0';  // '0' (All Causes -> shows L1); L1 code -> shows L2; L2 code -> shows L3
    this.searchQuery = '';
    this.hoveredCode = null;
  }

  init(flowData) {
    this.flowData = flowData;
    this._bindEvents();
    this.render();
  }

  _bindEvents() {
    document.getElementById('flow-year-prev').addEventListener('click', () => {
      if (this.year > 2000) { this.year--; this.render(); }
    });
    document.getElementById('flow-year-next').addEventListener('click', () => {
      if (this.year < 2023) { this.year++; this.render(); }
    });
    document.querySelectorAll('.loc-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.loc-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.loc = btn.dataset.loc;
        this.render();
      });
    });
    document.querySelectorAll('.scale-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.scale-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.scale = btn.dataset.scale;
        this.render();
      });
    });
    document.getElementById('flow-search').addEventListener('input', (e) => {
      this.searchQuery = e.target.value.trim().toLowerCase();
      this.render();
    });
  }

  _getLocData() {
    return this.flowData?.locations?.[this.loc] || null;
  }

  _getCurrentItems() {
    const locData = this._getLocData();
    if (!locData) return [];
    const causes = locData.causes;
    const yIdx = this.year - 2000;

    let children = [];
    if (this.searchQuery) {
      children = causes.filter(c =>
        c.level >= 1 && (
          (c.name_en || '').toLowerCase().includes(this.searchQuery) ||
          (c.name_zh || '').toLowerCase().includes(this.searchQuery)
        )
      );
    } else if (this.parentCode === '0') {
      // Top-level overview shows Level 2 major disease groups for crisp visual richness (like Scime's 10-20 streams),
      // while also letting user step via Level 1 or Level 2
      children = causes.filter(c => c.level === 2);
    } else {
      children = causes.filter(c => c.parent_code === this.parentCode);
      if (!children.length) {
        // Leaf cause selected: show itself + sibling causes under same parent
        const self = causes.find(c => c.code === this.parentCode);
        if (self) children = [self];
      }
    }

    children.sort((a, b) => (locData.deaths[b.code]?.[yIdx] || 0) - (locData.deaths[a.code]?.[yIdx] || 0));
    return children.slice(0, 14);
  }

  _getBreadcrumbs() {
    const locData = this._getLocData();
    if (!locData) return [];
    const byCode = Object.fromEntries(locData.causes.map(c => [c.code, c]));
    const trail = [];
    let cur = byCode[this.parentCode];
    while (cur) {
      trail.unshift(cur);
      cur = cur.parent_code ? byCode[cur.parent_code] : null;
    }
    return trail;
  }

  render() {
    if (!this.flowData) return;
    const locData = this._getLocData();
    if (!locData) return;

    document.getElementById('flow-year-display').textContent = String(this.year);
    const yIdx = this.year - 2000;
    const prevIdx = Math.max(0, yIdx - 1);
    const totalDeathsYear = locData.deaths['0']?.[yIdx] || 1;
    const locLabel = this.loc === 'WRLD' ? '全球' : '中国';

    const trail = this._getBreadcrumbs();
    const curNode = trail[trail.length - 1];
    document.getElementById('snapshot-heading').textContent = `${this.year} 年${locLabel}${curNode && curNode.code !== '0' ? ' · ' + (curNode.name_zh || curNode.name_en) : '主要死因分布'}`;
    document.getElementById('snapshot-subtext').textContent =
      `${this.year} 年${locLabel}总死亡 ${Math.round(totalDeathsYear).toLocaleString()} 人（WHO GHE）。右侧显示占总死亡比重及较 ${Math.max(2000, this.year - 1)} 年变化。`;

    // Breadcrumbs
    const bcEl = document.getElementById('flow-breadcrumbs');
    bcEl.innerHTML = trail.map((c, idx) => {
      const isLast = idx === trail.length - 1;
      const label = c.code === '0' ? '全部死因 All Causes' : (c.name_zh || c.name_en);
      return `${idx > 0 ? '<span>›</span>' : ''}<button type="button" class="crumb-btn ${isLast ? 'current' : ''}" data-flow-code="${esc(c.code)}">${esc(label)}</button>`;
    }).join('');
    bcEl.querySelectorAll('[data-flow-code]').forEach(btn => {
      btn.addEventListener('click', () => {
        this.searchQuery = '';
        document.getElementById('flow-search').value = '';
        this.parentCode = btn.dataset.flowCode;
        this.render();
      });
    });

    const items = this._getCurrentItems();
    const colorMap = {};
    items.forEach((c, i) => { colorMap[c.code] = STREAM_PALETTE[i % STREAM_PALETTE.length]; });

    // 1. Render Left Leaderboard (Scime Flow snapshot bars)
    const lbEl = document.getElementById('flow-leaderboard');
    const maxVal = Math.max(0.01, ...items.map(c => locData.pct[c.code]?.[yIdx] || 0));
    lbEl.innerHTML = items.map(c => {
      const pctNow = locData.pct[c.code]?.[yIdx] || 0;
      const pctPrev = locData.pct[c.code]?.[prevIdx] || 0;
      const delta = pctNow - pctPrev;
      const deathsNow = locData.deaths[c.code]?.[yIdx] || 0;
      const deltaStr = this.year === 2000 ? '' : (delta >= 0 ? `<span class="delta-pos">+${delta.toFixed(1)}</span>` : `<span class="delta-neg">${delta.toFixed(1)}</span>`);
      const widthPct = Math.max(2, (pctNow / maxVal) * 100);
      const col = colorMap[c.code];
      const title = c.name_zh ? `${c.name_zh} · ${c.name_en}` : c.name_en;
      return `
        <div class="leader-item" data-drill-code="${esc(c.code)}">
          <div class="leader-row">
            <span class="leader-name" title="${esc(title)}">${esc(c.name_zh || c.name_en)}</span>
            <span class="leader-nums">
              ${this.scale === 'deaths' ? `${Math.round(deathsNow).toLocaleString()}` : `${pctNow.toFixed(1)}%`}
              ${deltaStr}
            </span>
          </div>
          <div class="leader-bar-track">
            <div class="leader-bar-fill" style="width:${widthPct.toFixed(1)}%;background:${col};"></div>
          </div>
        </div>
      `;
    }).join('');

    lbEl.querySelectorAll('[data-drill-code]').forEach(el => {
      el.addEventListener('click', () => this._drillOrFocus(el.dataset.drillCode));
    });

    // 2. Render 2000-2023 Year Matrix
    const ymEl = document.getElementById('flow-year-matrix');
    ymEl.innerHTML = this.flowData.years.map(y =>
      `<button type="button" class="ym-cell ${y === this.year ? 'active' : ''}" data-yr="${y}">${String(y).slice(2)}</button>`
    ).join('');
    ymEl.querySelectorAll('[data-yr]').forEach(btn => {
      btn.addEventListener('click', () => { this.year = Number(btn.dataset.yr); this.render(); });
    });

    // 3. Render SVG Streamgraph
    this._renderStreamgraph(items, colorMap, locData);

    // 4. Render Related Curated Dossier Cards underneath
    this._renderRelatedCards(items);
  }

  _drillOrFocus(code) {
    const locData = this._getLocData();
    if (!locData) return;
    const hasKids = locData.causes.some(c => c.parent_code === code);
    if (hasKids) {
      this.searchQuery = '';
      document.getElementById('flow-search').value = '';
      this.parentCode = code;
      this.render();
    } else {
      const target = locData.causes.find(c => c.code === code);
      if (target) this.cb.onSearchTerm(target.name_zh || target.name_en);
    }
  }

  _renderStreamgraph(items, colorMap, locData) {
    const svg = document.getElementById('flow-svg');
    const tooltip = document.getElementById('flow-tooltip');
    const W = 960;
    const H = 360;
    const padL = 48;
    const padR = 20;
    const padT = 18;
    const padB = 34;
    const plotW = W - padL - padR;
    const plotH = H - padT - padB;
    const years = this.flowData.years;
    const seriesKey = this.scale === 'deaths' ? 'deaths' : 'pct';

    if (!items.length) {
      svg.innerHTML = `<text x="480" y="180" text-anchor="middle" fill="#6b7280" font-size="14">无匹配的死因类别</text>`;
      return;
    }

    // Stacked totals across 24 years
    const totals = years.map((_, yi) => items.reduce((acc, c) => acc + (locData[seriesKey][c.code]?.[yi] || 0), 0));
    const maxTotal = Math.max(1e-6, ...totals);

    // Compute stacked paths (bottom-up)
    const cumBottom = new Array(years.length).fill(0);
    const layers = items.slice().reverse().map(c => {
      const vals = years.map((_, yi) => locData[seriesKey][c.code]?.[yi] || 0);
      const y0 = [...cumBottom];
      const y1 = cumBottom.map((b, yi) => b + vals[yi]);
      for (let yi = 0; yi < years.length; yi++) cumBottom[yi] = y1[yi];
      return { cause: c, vals, y0, y1 };
    });

    const xOf = yi => padL + (yi / (years.length - 1)) * plotW;
    const yOf = val => padT + plotH - (val / maxTotal) * plotH;
    const isLight = document.documentElement.dataset.theme === 'light';
    const dividerCol = isLight ? '#ffffff' : '#111111';
    const gridCol = isLight ? 'rgba(15,23,42,0.09)' : 'rgba(255,255,255,0.07)';
    const axisCol = isLight ? '#475569' : '#9ca3af';
    const yAxisCol = isLight ? '#64748b' : '#6b7280';
    const cursorCol = isLight ? '#0f172a' : '#ffffff';

    let pathsHtml = '';
    layers.forEach(layer => {
      const c = layer.cause;
      const col = colorMap[c.code];
      let d = `M ${xOf(0).toFixed(1)} ${yOf(layer.y1[0]).toFixed(1)}`;
      for (let yi = 1; yi < years.length; yi++) {
        d += ` L ${xOf(yi).toFixed(1)} ${yOf(layer.y1[yi]).toFixed(1)}`;
      }
      for (let yi = years.length - 1; yi >= 0; yi--) {
        d += ` L ${xOf(yi).toFixed(1)} ${yOf(layer.y0[yi]).toFixed(1)}`;
      }
      d += ' Z';
      pathsHtml += `<path class="stream-layer" data-code="${esc(c.code)}" d="${d}" fill="${col}" fill-opacity="0.82" stroke="${dividerCol}" stroke-width="0.8" style="cursor:pointer;transition:fill-opacity 0.15s;"></path>`;
    });

    // Year gridlines & labels
    let axesHtml = '';
    [2000, 2004, 2008, 2012, 2016, 2020, 2023].forEach(yr => {
      const yi = yr - 2000;
      const x = xOf(yi);
      axesHtml += `<line x1="${x}" y1="${padT}" x2="${x}" y2="${padT + plotH}" stroke="${gridCol}" stroke-dasharray="3 3" />`;
      axesHtml += `<text x="${x}" y="${H - 10}" text-anchor="middle" fill="${axisCol}" font-size="11">${yr}</text>`;
    });

    // Selected year indicator line
    const selX = xOf(this.year - 2000);
    axesHtml += `<line x1="${selX}" y1="${padT}" x2="${selX}" y2="${padT + plotH}" stroke="${cursorCol}" stroke-width="1.4" stroke-opacity="0.8" />`;

    // Y axis max label
    const maxLabel = this.scale === 'deaths' ? `${(maxTotal / 10000).toFixed(0)}万` : `${maxTotal.toFixed(0)}%`;
    axesHtml += `<text x="${padL - 6}" y="${padT + 10}" text-anchor="end" fill="${yAxisCol}" font-size="10.5">${maxLabel}</text>`;
    axesHtml += `<text x="${padL - 6}" y="${padT + plotH}" text-anchor="end" fill="${yAxisCol}" font-size="10.5">0</text>`;

    svg.innerHTML = pathsHtml + axesHtml;

    svg.querySelectorAll('.stream-layer').forEach(el => {
      el.addEventListener('click', () => this._drillOrFocus(el.dataset.code));
      el.addEventListener('mouseenter', () => {
        svg.querySelectorAll('.stream-layer').forEach(p => {
          p.setAttribute('fill-opacity', p === el ? '0.96' : '0.35');
        });
      });
      el.addEventListener('mouseleave', () => {
        svg.querySelectorAll('.stream-layer').forEach(p => p.setAttribute('fill-opacity', '0.82'));
        tooltip.classList.add('hidden');
      });
      el.addEventListener('mousemove', (e) => {
        const rect = svg.getBoundingClientRect();
        const relX = ((e.clientX - rect.left) / rect.width) * W;
        const yi = Math.min(23, Math.max(0, Math.round(((relX - padL) / plotW) * (years.length - 1))));
        const code = el.dataset.code;
        const c = items.find(x => x.code === code);
        if (!c) return;
        const dVal = locData.deaths[code]?.[yi] || 0;
        const pVal = locData.pct[code]?.[yi] || 0;
        tooltip.innerHTML = `
          <div style="font-weight:600;color:var(--text-strong);">${esc(c.name_zh || c.name_en)} (${years[yi]})</div>
          <div style="color:var(--text-secondary);font-size:11px;margin-bottom:4px;">${esc(c.name_en)}</div>
          <div>年死亡人数：<strong>${Math.round(dVal).toLocaleString()}</strong> 人</div>
          <div>占总死亡比：<strong>${pVal.toFixed(2)}%</strong></div>
          <div style="color:var(--accent-blue);font-size:11px;margin-top:4px;">点击此色带继续细看原因</div>
        `;
        tooltip.style.left = `${Math.min(window.innerWidth - 240, e.clientX + 14)}px`;
        tooltip.style.top = `${Math.min(window.innerHeight - 130, e.clientY + 12)}px`;
        tooltip.classList.remove('hidden');
      });
    });
  }

  async _renderRelatedCards(items) {
    const grid = document.getElementById('flow-related-cards');
    const cntEl = document.getElementById('flow-cards-count');
    let res;
    try {
      res = await sliceDiseases({ chip: 'curated', limit: 9 });
    } catch {
      return;
    }
    const list = res.items || [];
    cntEl.textContent = `重点解读病种 (${list.length} / ${res.total})`;
    grid.innerHTML = list.map(d => `
      <div class="disease-card-row" data-open-id="${esc(d.id)}">
        <div class="disease-row-top">
          <div>
            <div class="disease-row-title">${esc(d.name_zh || d.name_en)}</div>
            <div class="disease-row-en">${esc(d.name_en)}</div>
          </div>
          ${d.deaths_g ? `<span class="tag-pill">约 ${Math.round(d.deaths_g).toLocaleString()} 死/年</span>` : ''}
        </div>
        <div class="pill-row">
          <span class="tag-pill tag-curated">深度解读</span>
          ${d.lethality === 'hyperacute_minutes' ? `<span class="tag-pill tag-urgent">数分钟突发高危</span>` : ''}
        </div>
      </div>
    `).join('');
    grid.querySelectorAll('[data-open-id]').forEach(el => {
      el.addEventListener('click', () => this.cb.onOpenDossier(el.dataset.openId));
    });
  }
}
