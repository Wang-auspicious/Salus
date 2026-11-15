import { AtlasMapEngine } from './mapCanvas.js';
import { FlowMortalityView } from './flowChart.js';
import { DirectoryView } from './directoryPolicy.js';
import { RoadmapView } from './roadmapView.js';
import { renderDiseaseDossierHTML } from './dossierView.js';

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

class SalusApp {
  constructor() {
    this.mode = 'map';
    this.chip = 'all';
    this.selectedDomain = null;
    this.selectedSubcluster = null;
    this.selectedStage = '';
    this.selectedDiseaseId = null;

    this.hierarchy = null;
    this.domainMap = {};
    this.stageMeta = {};
    this.summary = {};
    this.nodeIndex = new Map();

    this.mapEngine = new AtlasMapEngine(
      document.getElementById('atlas-canvas'),
      document.getElementById('map-tooltip'),
      {
        onSelectDomain: (domId) => this.selectDomain(domId),
        onSelectSubcluster: (domId, sub) => this.selectSubcluster(domId, sub),
        onSelectNode: (node) => this.selectDiseaseOnMap(node),
      }
    );

    this.flowView = new FlowMortalityView({
      onOpenDossier: (id) => this.openDossierModal(id),
      onSearchTerm: (q) => this.locateAndFly(q),
    });

    this.dirView = new DirectoryView({
      onOpenDossier: (id) => this.openDossierModal(id),
    });

    this.roadmapView = new RoadmapView({
      onOpenDossier: (id) => this.openDossierModal(id),
    });
  }

  _initTheme() {
    const sunSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4.2"/><path d="M12 2.5v2.2M12 19.3v2.2M4.93 4.93l1.56 1.56M17.51 17.51l1.56 1.56M2.5 12h2.2M19.3 12h2.2M4.93 19.07l1.56-1.56M17.51 6.49l1.56-1.56"/></svg>`;
    const moonSvg = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20.354 15.354A8.5 8.5 0 0 1 8.646 3.646 8.5 8.5 0 1 0 20.354 15.354z"/></svg>`;
    const saved = localStorage.getItem('salus_theme') || 'dark';
    const apply = (theme) => {
      document.documentElement.dataset.theme = theme;
      const btn = document.getElementById('btn-theme-toggle');
      if (btn) {
        btn.innerHTML = theme === 'light' ? moonSvg : sunSvg;
        const lbl = theme === 'light' ? '切换为深色夜景模式' : '切换为浅色白底模式';
        btn.setAttribute('title', lbl);
        btn.setAttribute('aria-label', lbl);
      }
    };
    apply(saved);
    document.getElementById('btn-theme-toggle')?.addEventListener('click', () => {
      const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light';
      localStorage.setItem('salus_theme', next);
      apply(next);
      if (this.flowView?.flowData) this.flowView.render();
    });
  }

  switchMode(m) {
    this.mode = m;
    document.querySelectorAll('.mode-pill').forEach(b => b.classList.toggle('active', b.dataset.mode === m));
    document.querySelectorAll('.view-panel').forEach(p => p.classList.toggle('active', p.id === `view-${m}`));
    if (m === 'map') {
      setTimeout(() => this.mapEngine.resize(), 40);
    }
  }

  async start() {
    this._initTheme();
    this._bindChrome();

    // Load bootstrap + map points + flow + roadmap in parallel
    const [bootRes, ptsRes, flowRes, roadRes] = await Promise.all([
      fetch('/api/bootstrap').then(r => r.json()),
      fetch('/api/map-points').then(r => r.json()),
      fetch('/api/flow').then(r => r.json()),
      fetch('/api/roadmap').then(r => r.json()),
    ]);

    this.hierarchy = bootRes.hierarchy;
    this.summary = bootRes.summary;
    this.stageMeta = this.hierarchy.stage_meta || {};
    (this.hierarchy.domains || []).forEach(d => { this.domainMap[d.id] = d; });

    const cntAllEl = document.getElementById('cnt-all');
    if (cntAllEl && this.hierarchy.total_diseases) {
      cntAllEl.textContent = Number(this.hierarchy.total_diseases).toLocaleString();
    }
    const cntCurEl = document.getElementById('cnt-curated');
    if (cntCurEl && roadRes?.curated_count !== undefined) {
      cntCurEl.textContent = Number(roadRes.curated_count).toLocaleString();
    }

    // Build client lookup index
    const fields = ptsRes.fields;
    const idIdx = fields.indexOf('id');
    const xIdx = fields.indexOf('x');
    const yIdx = fields.indexOf('y');
    const l1Idx = fields.indexOf('l1');
    const l2Idx = fields.indexOf('l2');
    const l3Idx = fields.indexOf('l3');
    const enIdx = fields.indexOf('name_en');
    const zhIdx = fields.indexOf('name_zh');
    ptsRes.rows.forEach(r => {
      this.nodeIndex.set(r[idIdx], {
        id: r[idIdx], x: r[xIdx], y: r[yIdx],
        l1: r[l1Idx], l2: r[l2Idx], l3: r[l3Idx],
        name_en: r[enIdx], name_zh: r[zhIdx],
      });
    });

    this.mapEngine.setData(ptsRes, this.hierarchy);
    this.flowView.init(flowRes);
    this.dirView.init(this.hierarchy);
    this.roadmapView.init(roadRes);

    this.renderAreasAccordion();
    await this.refreshRightDrawer();
  }

  _bindChrome() {
    document.querySelectorAll('.mode-pill').forEach(btn => {
      btn.addEventListener('click', () => this.switchMode(btn.dataset.mode));
    });
    document.querySelectorAll('.header-nav [data-nav]').forEach(btn => {
      btn.addEventListener('click', () => this.switchMode(btn.dataset.nav));
    });
    document.getElementById('brand-home').addEventListener('click', (e) => {
      e.preventDefault();
      this.switchMode('map');
      this.resetMapView();
    });

    // Filter chips
    document.querySelectorAll('.filter-chip').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.filter-chip').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.chip = btn.dataset.chip;
        this.mapEngine.setFilter({
          domain: this.selectedDomain,
          subcluster: this.selectedSubcluster,
          stage: this.selectedStage,
          chip: this.chip,
        });
        this.dirView.setChip(this.chip);
        this.refreshRightDrawer();
      });
    });

    // Left collapse
    document.getElementById('btn-toggle-left').addEventListener('click', () => {
      const panel = document.getElementById('map-left-panel');
      const collapsed = panel.classList.toggle('collapsed');
      document.getElementById('btn-toggle-left').textContent = collapsed ? '+' : '−';
    });

    // Right drawer close
    document.getElementById('btn-close-right').addEventListener('click', () => {
      document.getElementById('map-right-drawer').classList.add('closed');
    });

    // Zoom & reset buttons
    document.getElementById('btn-zoom-in').addEventListener('click', () => this.mapEngine.zoomBy(1.35));
    document.getElementById('btn-zoom-out').addEventListener('click', () => this.mapEngine.zoomBy(0.74));
    document.getElementById('btn-back-overview').addEventListener('click', () => this.resetMapView());
    document.getElementById('btn-reset-map').addEventListener('click', () => this.resetMapView());

    // Bottom stage scrubber
    document.querySelectorAll('.stage-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.stage-pill').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        this.selectedStage = btn.dataset.stage;
        this.mapEngine.setFilter({
          domain: this.selectedDomain,
          subcluster: this.selectedSubcluster,
          stage: this.selectedStage,
          chip: this.chip,
        });
        this.refreshRightDrawer();
      });
    });

    // Search & Locate box
    const inp = document.getElementById('locate-input');
    const dd = document.getElementById('search-dropdown');
    let sTimer = null;
    inp.addEventListener('input', () => {
      clearTimeout(sTimer);
      const q = inp.value.trim();
      if (!q) { dd.classList.add('hidden'); return; }
      sTimer = setTimeout(async () => {
        const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=12`).then(r => r.json());
        if (!res.results?.length) {
          dd.innerHTML = `<div class="search-hit"><div class="search-hit-title">未找到匹配 "${esc(q)}" 的疾病</div></div>`;
          dd.classList.remove('hidden');
          return;
        }
        dd.innerHTML = res.results.map(r => `
          <div class="search-hit" data-hit-id="${esc(r.id)}">
            <div class="search-hit-title">${esc(r.name_zh || r.name_en)}</div>
            <div class="search-hit-meta">
              <span>${esc(r.name_en)}</span>
              ${r.icd10 ? `<span>ICD-10: ${esc(r.icd10)}</span>` : ''}
              ${r.is_curated ? `<span style="color:var(--accent-emerald);font-weight:600;">深度解读</span>` : ''}
            </div>
          </div>
        `).join('');
        dd.classList.remove('hidden');
        dd.querySelectorAll('[data-hit-id]').forEach(el => {
          el.addEventListener('click', () => {
            dd.classList.add('hidden');
            const node = this.nodeIndex.get(el.dataset.hitId);
            if (node) this.selectDiseaseOnMap(node, true);
          });
        });
      }, 180);
    });

    document.getElementById('locate-form').addEventListener('submit', (e) => {
      e.preventDefault();
      dd.classList.add('hidden');
      const q = inp.value.trim();
      if (q) this.locateAndFly(q);
    });

    document.querySelectorAll('.example-pill').forEach(btn => {
      btn.addEventListener('click', () => {
        inp.value = btn.dataset.q;
        this.locateAndFly(btn.dataset.q);
      });
    });

    document.addEventListener('click', (e) => {
      if (!e.target.closest('.hero-card')) dd.classList.add('hidden');
    });

    // Modals
    document.getElementById('btn-close-modal').addEventListener('click', () => {
      document.getElementById('disease-modal').classList.add('hidden');
    });
    document.getElementById('disease-modal').addEventListener('click', (e) => {
      if (e.target.id === 'disease-modal') e.target.classList.add('hidden');
    });
    document.getElementById('btn-modal-locate').addEventListener('click', () => {
      if (!this.selectedDiseaseId) return;
      document.getElementById('disease-modal').classList.add('hidden');
      this.switchMode('map');
      const node = this.nodeIndex.get(this.selectedDiseaseId);
      if (node) this.selectDiseaseOnMap(node, true);
    });

    document.getElementById('btn-about').addEventListener('click', () => {
      document.getElementById('about-modal').classList.remove('hidden');
    });
    document.getElementById('btn-close-about').addEventListener('click', () => {
      document.getElementById('about-modal').classList.add('hidden');
    });
    document.getElementById('about-modal').addEventListener('click', (e) => {
      if (e.target.id === 'about-modal') e.target.classList.add('hidden');
    });
  }

  async locateAndFly(q) {
    document.querySelectorAll('.mode-pill').forEach(b => b.classList.toggle('active', b.dataset.mode === 'map'));
    document.querySelectorAll('.view-panel').forEach(p => p.classList.toggle('active', p.id === 'view-map'));
    setTimeout(() => this.mapEngine.resize(), 20);

    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}&limit=5`).then(r => r.json());
    if (res.results && res.results.length) {
      const hit = res.results[0];
      const node = this.nodeIndex.get(hit.id) || hit;
      this.selectDiseaseOnMap(node, true);
    }
  }

  resetMapView() {
    this.selectedDomain = null;
    this.selectedSubcluster = null;
    this.selectedDiseaseId = null;
    this.mapEngine.setBeacon(null);
    this.mapEngine.setFilter({
      domain: null,
      subcluster: null,
      stage: this.selectedStage,
      chip: this.chip,
    });
    this.mapEngine.resetCamera(true);
    this.renderAreasAccordion();
    this.refreshRightDrawer();
  }

  selectDomain(domId) {
    this.selectedDomain = domId;
    this.selectedSubcluster = null;
    this.selectedDiseaseId = null;
    this.mapEngine.setBeacon(null);
    this.mapEngine.setFilter({
      domain: domId,
      subcluster: null,
      stage: this.selectedStage,
      chip: this.chip,
    });
    const d = this.domainMap[domId];
    if (d) this.mapEngine.flyTo(d.cx, d.cy, 1.35);
    this.renderAreasAccordion();
    this.refreshRightDrawer();
  }

  selectSubcluster(domId, subName) {
    this.selectedDomain = domId;
    this.selectedSubcluster = subName;
    this.selectedDiseaseId = null;
    this.mapEngine.setBeacon(null);
    this.mapEngine.setFilter({
      domain: domId,
      subcluster: subName,
      stage: this.selectedStage,
      chip: this.chip,
    });
    const d = this.domainMap[domId];
    const sc = d?.subclusters?.find(s => s.name === subName);
    if (sc) this.mapEngine.flyTo(sc.cx, sc.cy, 2.35);
    this.renderAreasAccordion();
    this.refreshRightDrawer();
  }

  async selectDiseaseOnMap(node, fly = false) {
    this.selectedDomain = node.l1;
    this.selectedSubcluster = node.l2;
    this.selectedDiseaseId = node.id;
    this.mapEngine.setFilter({
      domain: node.l1,
      subcluster: node.l2,
      stage: this.selectedStage,
      chip: this.chip,
    });
    this.mapEngine.setBeacon(node);
    if (fly) this.mapEngine.flyTo(node.x, node.y, 3.2);
    this.renderAreasAccordion();
    await this.renderRightDossier(node.id);
  }

  renderAreasAccordion() {
    const listEl = document.getElementById('areas-list');
    if (!this.hierarchy) return;
    document.getElementById('areas-total-badge').textContent = this.hierarchy.total_diseases.toLocaleString();

    listEl.innerHTML = (this.hierarchy.domains || []).map(d => {
      const isActive = this.selectedDomain === d.id;
      const subsHtml = isActive ? `
        <div class="sub-list">
          ${(d.subclusters || []).map(sc => `
            <div class="sub-row ${this.selectedSubcluster === sc.name ? 'active' : ''}" data-dom="${esc(d.id)}" data-sub="${esc(sc.name)}">
              <span>${esc(sc.name)}</span>
              <span class="tabular">${sc.count.toLocaleString()}</span>
            </div>
          `).join('')}
        </div>
      ` : '';

      return `
        <div>
          <div class="area-row ${isActive ? 'active' : ''}" data-dom-pick="${esc(d.id)}">
            <div class="area-row-left">
              <i class="dot" style="background:${d.color}"></i>
              <span class="area-name">${esc(d.name_zh)}</span>
            </div>
            <span class="area-stats">${d.count.toLocaleString()} (${d.share_pct}%)</span>
          </div>
          ${subsHtml}
        </div>
      `;
    }).join('');

    listEl.querySelectorAll('[data-dom-pick]').forEach(el => {
      el.addEventListener('click', () => {
        const did = el.dataset.domPick;
        if (this.selectedDomain === did && !this.selectedSubcluster) {
          this.resetMapView();
        } else {
          this.selectDomain(did);
        }
      });
    });

    listEl.querySelectorAll('[data-sub]').forEach(el => {
      el.addEventListener('click', (e) => {
        e.stopPropagation();
        this.selectSubcluster(el.dataset.dom, el.dataset.sub);
      });
    });
  }

  _renderMapBreadcrumbs() {
    const bc = document.getElementById('map-breadcrumbs');
    const dom = this.selectedDomain ? this.domainMap[this.selectedDomain] : null;
    const parts = [
      `<button type="button" class="crumb-btn ${!dom ? 'current' : ''}" data-bc="root">全部疾病 (25,697)</button>`,
    ];
    if (dom) {
      parts.push(`<span>›</span><button type="button" class="crumb-btn ${!this.selectedSubcluster && !this.selectedDiseaseId ? 'current' : ''}" data-bc="domain" style="color:${dom.color}">${esc(dom.name_zh)}</button>`);
    }
    if (this.selectedSubcluster) {
      parts.push(`<span>›</span><button type="button" class="crumb-btn ${!this.selectedDiseaseId ? 'current' : ''}" data-bc="sub">${esc(this.selectedSubcluster)}</button>`);
    }
    if (this.selectedDiseaseId) {
      const n = this.nodeIndex.get(this.selectedDiseaseId);
      parts.push(`<span>›</span><span class="crumb-btn current">${esc(n?.name_zh || n?.name_en || this.selectedDiseaseId)}</span>`);
    }
    bc.innerHTML = parts.join('');
    bc.querySelector('[data-bc="root"]')?.addEventListener('click', () => this.resetMapView());
    bc.querySelector('[data-bc="domain"]')?.addEventListener('click', () => this.selectDomain(this.selectedDomain));
    bc.querySelector('[data-bc="sub"]')?.addEventListener('click', () => this.selectSubcluster(this.selectedDomain, this.selectedSubcluster));
  }

  async refreshRightDrawer() {
    const drawer = document.getElementById('map-right-drawer');
    drawer.classList.remove('closed');
    this._renderMapBreadcrumbs();

    const params = new URLSearchParams({ limit: '45' });
    if (this.selectedDomain) params.set('domain', this.selectedDomain);
    if (this.selectedSubcluster) params.set('subcluster', this.selectedSubcluster);
    if (this.selectedStage) params.set('stage', this.selectedStage);
    if (this.chip && this.chip !== 'all') params.set('chip', this.chip);

    const data = await fetch(`/api/slice?${params}`).then(r => r.json());
    const body = document.getElementById('map-drawer-body');
    const stCounts = data.stats?.stages || {};
    const total = Math.max(1, data.total);

    // Stacked progress bar (Curable -> Controllable -> Approved -> Phase 3/2/1 -> Symptomatic/Unmet/Unrecorded)
    const orderedKeys = Object.keys(stCounts).sort((a, b) => (this.stageMeta[a]?.order || 99) - (this.stageMeta[b]?.order || 99));
    const barSegs = orderedKeys.map(k => {
      const pct = (stCounts[k] * 100) / total;
      const col = this.stageMeta[k]?.color || '#6b7280';
      return `<div class="stacked-seg" style="width:${pct.toFixed(2)}%;background:${col};" title="${esc(this.stageMeta[k]?.zh || k)}: ${stCounts[k]}"></div>`;
    }).join('');

    const legendItems = orderedKeys.map(k => {
      const m = this.stageMeta[k] || { zh: k, color: '#6b7280' };
      return `<span class="stage-legend-item" data-pick-stage="${esc(k)}"><i class="dot" style="background:${m.color}"></i>${esc(m.zh)} ${stCounts[k].toLocaleString()}</span>`;
    }).join('');

    const title = this.selectedSubcluster || (this.selectedDomain ? this.domainMap[this.selectedDomain]?.name_zh : '人类疾病治疗希望概览');
    const subCountNote = `当前共 ${data.total.toLocaleString()} 种疾病（深度解读档案 ${data.stats.curated_count} 种 · 遗传/罕见病 ${data.stats.rare_count.toLocaleString()} 种）`;

    const cardsHtml = (data.items || []).map(item => {
      const sm = this.stageMeta[item.l3] || { zh: item.l3, color: '#9ca3af' };
      const deathsHint = item.deaths_g ? `每年全球约 ${Math.round(item.deaths_g).toLocaleString()} 人离世` : '';
      return `
        <div class="disease-card-row" data-card-id="${esc(item.id)}">
          <div class="disease-row-top">
            <div>
              <div class="disease-row-title">${esc(item.name_zh || item.name_en)}</div>
              <div class="disease-row-en">${esc(item.name_en)}</div>
            </div>
            <span class="tag-pill" style="border-color:${sm.color};color:${sm.color}">${esc(sm.zh)}</span>
          </div>
          <div class="pill-row">
            ${item.is_curated ? `<span class="tag-pill tag-curated">深度解读</span>` : ''}
            ${item.lethality === 'hyperacute_minutes' ? `<span class="tag-pill tag-urgent">数分钟突发高危</span>` : ''}
            ${deathsHint ? `<span class="tag-pill">${esc(deathsHint)}</span>` : ''}
            ${item.drugs ? `<span class="tag-pill">已有 ${item.drugs} 项对应药物</span>` : ''}
          </div>
        </div>
      `;
    }).join('');

    body.innerHTML = `
      <div class="telemetry-card">
        <div style="display:flex;justify-content:space-between;align-items:baseline;">
          <h3 class="serif-subtitle">${esc(title)}</h3>
          <span class="badge-muted">${data.total.toLocaleString()}</span>
        </div>
        <p class="muted-text" style="margin-top:4px;">${esc(subCountNote)}</p>
        <div class="stacked-bar">${barSegs}</div>
        <div class="stage-legend-grid">${legendItems}</div>
      </div>
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:8px;">点击任意疾病卡片，用通俗语言看懂风险预警、日常防线、参考费用与突破方向：</div>
      ${cardsHtml || '<div class="muted-text">当前筛选条件下暂无匹配疾病。</div>'}
    `;

    body.querySelectorAll('[data-pick-stage]').forEach(el => {
      el.addEventListener('click', () => {
        const st = el.dataset.pickStage;
        this.selectedStage = this.selectedStage === st ? '' : st;
        document.querySelectorAll('.stage-pill').forEach(b => b.classList.toggle('active', b.dataset.stage === this.selectedStage));
        this.mapEngine.setFilter({
          domain: this.selectedDomain,
          subcluster: this.selectedSubcluster,
          stage: this.selectedStage,
          chip: this.chip,
        });
        this.refreshRightDrawer();
      });
    });

    body.querySelectorAll('[data-card-id]').forEach(el => {
      el.addEventListener('click', () => {
        const node = this.nodeIndex.get(el.dataset.cardId);
        if (node) this.selectDiseaseOnMap(node, true);
        else this.openDossierModal(el.dataset.cardId);
      });
    });
  }

  async renderRightDossier(diseaseId) {
    const drawer = document.getElementById('map-right-drawer');
    drawer.classList.remove('closed');
    this._renderMapBreadcrumbs();

    const body = document.getElementById('map-drawer-body');
    body.innerHTML = `<div class="muted-text" style="padding:20px;">正在加载完整疾病档案...</div>`;
    const detail = await fetch(`/api/disease/${encodeURIComponent(diseaseId)}`).then(r => r.json());

    body.innerHTML = `
      <div style="margin-bottom:10px;">
        <button type="button" class="btn-ghost" id="btn-back-slice">‹ 返回当前亚群列表</button>
      </div>
      ${renderDiseaseDossierHTML(detail, this.stageMeta, this.domainMap, { compact: true })}
    `;

    document.getElementById('btn-back-slice')?.addEventListener('click', () => {
      this.selectedDiseaseId = null;
      this.mapEngine.setBeacon(null);
      this.refreshRightDrawer();
    });

    body.querySelector('[data-expand-modal]')?.addEventListener('click', (e) => {
      this.openDossierModal(e.currentTarget.dataset.expandModal);
    });

    body.querySelectorAll('[data-open-id]').forEach(el => {
      el.addEventListener('click', () => {
        const node = this.nodeIndex.get(el.dataset.openId);
        if (node) this.selectDiseaseOnMap(node, true);
        else this.openDossierModal(el.dataset.openId);
      });
    });

    body.querySelectorAll('[data-open-track]').forEach(el => {
      el.addEventListener('click', () => {
        this.switchMode('roadmap');
        this.roadmapView.focusTrack(el.dataset.openTrack);
      });
    });
  }

  async openDossierModal(diseaseId) {
    this.selectedDiseaseId = diseaseId;
    const modal = document.getElementById('disease-modal');
    const content = document.getElementById('modal-content');
    const bc = document.getElementById('modal-breadcrumbs');
    modal.classList.remove('hidden');
    content.innerHTML = `<div class="muted-text">正在读取档案...</div>`;

    const detail = await fetch(`/api/disease/${encodeURIComponent(diseaseId)}`).then(r => r.json());
    const n = detail.node;
    const dom = n ? this.domainMap[n.l1] : null;
    bc.innerHTML = `
      <span>全部疾病</span>
      ${dom ? `<span>›</span><span style="color:${dom.color}">${esc(dom.name_zh)}</span>` : ''}
      ${n?.l2 ? `<span>›</span><span>${esc(n.l2)}</span>` : ''}
      <span>›</span><span class="crumb-btn current">${esc(detail.curated?.name_zh || detail.master?.name_zh || detail.master?.name_en)}</span>
    `;
    content.innerHTML = renderDiseaseDossierHTML(detail, this.stageMeta, this.domainMap, { compact: false });
    content.querySelectorAll('[data-open-id]').forEach(el => {
      el.addEventListener('click', () => this.openDossierModal(el.dataset.openId));
    });
    content.querySelectorAll('[data-open-track]').forEach(el => {
      el.addEventListener('click', () => {
        modal.classList.add('hidden');
        this.switchMode('roadmap');
        this.roadmapView.focusTrack(el.dataset.openTrack);
      });
    });
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const app = new SalusApp();
  app.start().catch(err => {
    console.error('Salus init failed:', err);
    document.getElementById('map-drawer-body').innerHTML = `<div class="alert-banner">初始化失败: ${esc(err.message)}</div>`;
  });
});
