// Mode 3: 4-Tier Patient-Friendly Hierarchical Disease Directory

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

export class DirectoryView {
  constructor(callbacks) {
    this.cb = callbacks;
    this.hierarchy = null;
    this.l1 = null;
    this.l2 = null;
    this.l3 = null;
    this.search = '';
    this.chip = 'all';
  }

  init(hierarchy) {
    this.hierarchy = hierarchy;
    this.l1 = hierarchy.domains?.[0]?.id || null;
    const firstDom = hierarchy.domains?.[0];
    this.l2 = firstDom?.subclusters?.[0]?.name || null;
    this.l3 = '';

    const inp = document.getElementById('dir-search-input');
    let timer = null;
    inp.addEventListener('input', () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        this.search = inp.value.trim();
        this.loadL4();
      }, 180);
    });

    this.render();
  }

  setChip(chip) {
    this.chip = chip;
    if (this.hierarchy) this.loadL4();
  }

  render() {
    if (!this.hierarchy) return;
    const domains = this.hierarchy.domains || [];
    const stageMeta = this.hierarchy.stage_meta || {};
    const domObj = domains.find(d => d.id === this.l1) || domains[0];
    if (!domObj) return;

    // Breadcrumbs
    const bc = document.getElementById('dir-breadcrumbs');
    bc.innerHTML = `
      <span class="crumb-btn">全部疾病 (${this.hierarchy.total_diseases.toLocaleString()})</span>
      <span>›</span>
      <span class="crumb-btn current" style="color:${domObj.color}">${esc(domObj.name_zh)}</span>
      ${this.l2 ? `<span>›</span><span class="crumb-btn current">${esc(this.l2)}</span>` : ''}
      ${this.l3 ? `<span>›</span><span class="crumb-btn current">${esc(stageMeta[this.l3]?.zh || this.l3)}</span>` : ''}
    `;

    // Col 1: 14 Domains
    const c1 = document.getElementById('dir-col-l1');
    c1.innerHTML = domains.map(d => `
      <div class="miller-item ${d.id === this.l1 ? 'active' : ''}" data-l1="${esc(d.id)}">
        <span style="display:flex;align-items:center;gap:7px;min-width:0;">
          <i class="dot" style="background:${d.color}"></i>
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(d.name_zh)}</span>
        </span>
        <span class="muted-hint tabular">${d.count.toLocaleString()}</span>
      </div>
    `).join('');
    c1.querySelectorAll('[data-l1]').forEach(el => {
      el.addEventListener('click', () => {
        this.l1 = el.dataset.l1;
        const d = domains.find(x => x.id === this.l1);
        this.l2 = d?.subclusters?.[0]?.name || null;
        this.l3 = '';
        this.render();
      });
    });

    // Col 2: Subclusters
    const c2 = document.getElementById('dir-col-l2');
    const subs = domObj.subclusters || [];
    c2.innerHTML = `
      <div class="miller-item ${!this.l2 ? 'active' : ''}" data-l2="">
        <span>全部病种大类 (${domObj.count.toLocaleString()})</span>
      </div>
      ${subs.map(s => `
        <div class="miller-item ${s.name === this.l2 ? 'active' : ''}" data-l2="${esc(s.name)}">
          <span>${esc(s.name)}</span>
          <span class="muted-hint tabular">${s.count.toLocaleString()}</span>
        </div>
      `).join('')}
    `;
    c2.querySelectorAll('[data-l2]').forEach(el => {
      el.addEventListener('click', () => {
        this.l2 = el.dataset.l2 || null;
        this.l3 = '';
        this.render();
      });
    });

    // Col 3: Treatability Stages
    const c3 = document.getElementById('dir-col-l3');
    const subObj = subs.find(s => s.name === this.l2);
    const stageCounts = subObj ? subObj.stages : domObj.stages;
    const orderedKeys = Object.keys(stageCounts).sort((a, b) => (stageMeta[a]?.order || 99) - (stageMeta[b]?.order || 99));
    c3.innerHTML = `
      <div class="miller-item ${!this.l3 ? 'active' : ''}" data-l3="">
        <span>全部可治程度</span>
      </div>
      ${orderedKeys.map(k => {
        const sm = stageMeta[k] || { zh: k, color: '#9ca3af' };
        return `
          <div class="miller-item ${this.l3 === k ? 'active' : ''}" data-l3="${esc(k)}">
            <span style="display:flex;align-items:center;gap:7px;">
              <i class="dot" style="background:${sm.color}"></i>
              <span>${esc(sm.zh)}</span>
            </span>
            <span class="muted-hint tabular">${(stageCounts[k] || 0).toLocaleString()}</span>
          </div>
        `;
      }).join('')}
    `;
    c3.querySelectorAll('[data-l3]').forEach(el => {
      el.addEventListener('click', () => {
        this.l3 = el.dataset.l3 || '';
        this.render();
      });
    });

    this.loadL4();
  }

  async loadL4() {
    const c4 = document.getElementById('dir-col-l4');
    const cntEl = document.getElementById('dir-l4-count');
    const params = new URLSearchParams({ limit: '80' });
    if (this.l1) params.set('domain', this.l1);
    if (this.l2) params.set('subcluster', this.l2);
    if (this.l3) params.set('stage', this.l3);
    if (this.chip && this.chip !== 'all') params.set('chip', this.chip);
    if (this.search) params.set('q', this.search);

    const res = await fetch(`/api/slice?${params}`);
    const data = await res.json();
    cntEl.textContent = `${data.total.toLocaleString()} 种`;
    const stageMeta = this.hierarchy?.stage_meta || {};

    c4.innerHTML = (data.items || []).map(item => {
      const sm = stageMeta[item.l3] || { zh: item.l3, color: '#9ca3af' };
      const deathsText = item.deaths_g
        ? `每年全球约 ${Math.round(item.deaths_g).toLocaleString()} 人离世`
        : (item.is_rare ? '罕见病/家族遗传关注' : '');
      return `
        <div class="disease-card-row" data-open-id="${esc(item.id)}">
          <div class="disease-row-top">
            <div>
              <div class="disease-row-title">${esc(item.name_zh || item.name_en)}</div>
              <div class="disease-row-en">${esc(item.name_en)}</div>
            </div>
            <span class="tag-pill" style="border-color:${sm.color};color:${sm.color}">${esc(sm.zh)}</span>
          </div>
          <div class="pill-row">
            ${item.is_curated ? `<span class="tag-pill tag-curated">深度解读</span>` : ''}
            ${item.lethality === 'hyperacute_minutes' ? `<span class="tag-pill tag-urgent">突发数分钟致命</span>` : ''}
            ${deathsText ? `<span class="tag-pill">${esc(deathsText)}</span>` : ''}
            ${item.drugs ? `<span class="tag-pill">已有 ${item.drugs} 项对应药物</span>` : ''}
          </div>
        </div>
      `;
    }).join('') || `<div class="muted-text" style="padding:18px;">当前筛选下暂无疾病记录。</div>`;

    c4.querySelectorAll('[data-open-id]').forEach(el => {
      el.addEventListener('click', () => this.cb.onOpenDossier(el.dataset.openId));
    });
  }
}
