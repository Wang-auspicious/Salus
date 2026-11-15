import { renderProseHtml } from './dossierView.js';

// Mode 4: Cross-Disease Common Breakthrough Tracks & Dynamic Research Updates

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

const TRACK_STAGE_NOTE = {
  curable: '可治愈',
  controllable: '可长期控制',
  approved_therapy: '已有获批药',
  partially_manageable: '部分可控仍高危',
  phase3_trials: '后期临床试验',
  phase2_trials: '中期临床试验',
  phase1_trials: '早期临床试验',
  symptomatic_only: '对症支持',
  unmet_no_effective_therapy: '急需突破',
};

export class RoadmapView {
  constructor(callbacks) {
    this.cb = callbacks;
    this.data = null;
    this.activeTrackId = null;
    this.searchQuery = '';
  }

  init(data) {
    this.data = data;
    this.activeTrackId = data?.tracks?.[0]?.id || 'early_monitoring';

    const inp = document.getElementById('roadmap-search');
    if (inp) {
      let t = null;
      inp.addEventListener('input', () => {
        clearTimeout(t);
        t = setTimeout(() => {
          this.searchQuery = inp.value.trim().toLowerCase();
          this.renderUpdates();
        }, 160);
      });
    }

    this.render();
  }

  focusTrack(trackId) {
    if (!this.data) return;
    const found = (this.data.tracks || []).find(t => t.id === trackId);
    if (found) {
      this.activeTrackId = found.id;
      this.renderTrackSection();
    }
  }

  render() {
    if (!this.data) return;
    this.renderKpis();
    this.renderTrackSection();
    this.renderUpdates();
  }

  renderKpis() {
    const el = document.getElementById('roadmap-pipeline-badges');
    if (!el) return;
    const st = this.data.stage_totals || {};
    const inTrials = (st.phase3_trials || 0) + (st.phase2_trials || 0) + (st.phase1_trials || 0);
    const treatable = (st.curable || 0) + (st.controllable || 0) + (st.approved_therapy || 0);
    const unmet = (st.unmet_no_effective_therapy || 0) + (st.symptomatic_only || 0) + (st.no_ot_drug_recorded || 0);

    el.innerHTML = `
      <div class="roadmap-kpi-chip">
        <div class="rk-num" style="color:#10b981;">${treatable.toLocaleString()}</div>
        <div class="rk-lbl">已有控制或治疗药物</div>
      </div>
      <div class="roadmap-kpi-chip">
        <div class="rk-num" style="color:#a855f7;">${inTrials.toLocaleString()}</div>
        <div class="rk-lbl">新药正在临床闯关</div>
      </div>
      <div class="roadmap-kpi-chip">
        <div class="rk-num" style="color:#ef4444;">${unmet.toLocaleString()}</div>
        <div class="rk-lbl">尚缺特效靶向药路径</div>
      </div>
    `;
  }

  renderTrackSection() {
    const listEl = document.getElementById('roadmap-tracks-list');
    const detailEl = document.getElementById('roadmap-track-detail');
    if (!listEl || !detailEl) return;

    const tracks = this.data.tracks || [];
    const active = tracks.find(t => t.id === this.activeTrackId) || tracks[0];
    if (!active) return;

    listEl.innerHTML = tracks.map(t => `
      <button type="button" class="track-selector-btn ${t.id === active.id ? 'active' : ''}" data-track-id="${esc(t.id)}" style="--track-col:${esc(t.color)};">
        <div class="ts-top">
          <span class="dot" style="background:${esc(t.color)}"></span>
          <span class="ts-title">${esc(t.title_zh)}</span>
        </div>
        <div class="ts-meta">关联 ${t.matched_total_count.toLocaleString()} 种疾病（深度档案 ${t.curated_count} 种）</div>
      </button>
    `).join('');

    listEl.querySelectorAll('[data-track-id]').forEach(btn => {
      btn.addEventListener('click', () => {
        this.activeTrackId = btn.dataset.trackId;
        this.renderTrackSection();
      });
    });

    const diseasePills = (active.featured_diseases || []).map(d => `
      <button type="button" class="roadmap-disease-pill" data-open-id="${esc(d.id)}">
        <span>${esc(d.name_zh || d.name_en)}</span>
        ${d.lethality === 'hyperacute_minutes' ? `<span class="rd-badge-urgent">极危急</span>` : ''}
      </button>
    `).join('');

    const litSignalsHtml = (active.literature_signals || []).map(ls => `
      <div class="lit-signal-item">
        <div class="lit-signal-head">
          <button type="button" class="link-plain" data-open-id="${esc(ls.id)}"><strong>${esc(ls.name_zh)}</strong> (${esc(ls.name_en)})</button>
          <span class="tag-pill">${esc(ls.pipeline_stage || '研发中')}</span>
        </div>
        <p class="muted-text" style="margin:4px 0 6px;">${esc(ls.breakthrough_zh)}</p>
        ${(ls.sources || []).length ? `
          <div class="source-list">
            ${ls.sources.map(s => `<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">[${esc(s.publisher)} ${esc(s.year)}] ${esc(s.title)}</a>`).join('')}
          </div>
        ` : ''}
      </div>
    `).join('');

    detailEl.innerHTML = `
      <div class="track-detail-box">
        <h3 class="serif-subtitle" style="margin-bottom:4px;">${esc(active.title_zh)}</h3>
        <div class="muted-text" style="margin-bottom:12px;">${esc(active.subtitle_zh)}</div>

        <div class="track-block">
          <div class="tb-label">💔 这些病共同面对的现实痛点</div>
          ${renderProseHtml(active.painpoint_zh)}
        </div>

        <div class="track-block">
          <div class="tb-label">🛠 我们应该集中突破什么关键技术</div>
          ${renderProseHtml(active.direction_zh)}
        </div>

        <div class="track-block">
          <div class="tb-label">🤖 面向 AI 与生命科学交叉研究者的切入点</div>
          ${renderProseHtml(active.ai4ls_entry_zh)}
        </div>

        <div class="track-block">
          <div class="tb-label">🤝 如果攻克这条路线，以下典型疾病有望被同步守住（点击看详情）</div>
          <div class="roadmap-disease-pills">${diseasePills}</div>
        </div>

        <details class="tech-accordion" style="margin-top:12px;">
          <summary>📄 前沿论文与具体临床突破细节（主要供科研人员追踪参考，点击展开 ${active.literature_signals?.length || 0} 条）</summary>
          <div class="tech-accordion-body">${litSignalsHtml || '<p class="muted-text">暂无额外摘录。</p>'}</div>
        </details>
      </div>
    `;

    detailEl.querySelectorAll('[data-open-id]').forEach(el => {
      el.addEventListener('click', () => this.cb.onOpenDossier(el.dataset.openId));
    });
  }

  renderUpdates() {
    const feedEl = document.getElementById('roadmap-updates-feed');
    const metaEl = document.getElementById('roadmap-update-meta');
    if (!feedEl) return;

    const all = this.data.live_updates || [];
    const filtered = this.searchQuery
      ? all.filter(u => `${u.name_zh} ${u.name_en} ${u.summary_zh} ${u.unmet_zh}`.toLowerCase().includes(this.searchQuery))
      : all;

    if (metaEl) {
      metaEl.textContent = `当前收录 ${this.data.curated_count} 项重点研发动态追踪（匹配 ${filtered.length} 条）`;
    }

    const snapshotsHtml = `
      <div class="snapshot-strip">
        ${(this.data.data_snapshots || []).map(sn => `
          <div class="snap-box">
            <div class="snap-name">${esc(sn.name)}</div>
            <div class="snap-meta">${esc(sn.version)} · <strong>${esc(sn.count)}</strong></div>
          </div>
        `).join('')}
      </div>
    `;

    const cardsHtml = filtered.map(u => {
      const stLbl = TRACK_STAGE_NOTE[u.treatability_status] || u.treatability_status || '';
      return `
        <div class="update-card">
          <div class="update-card-top">
            <div>
              <button type="button" class="link-plain update-title" data-open-id="${esc(u.id)}">${esc(u.name_zh)}</button>
              <span class="muted-hint" style="margin-left:6px;">${esc(u.name_en)}</span>
            </div>
            <div style="display:flex;gap:5px;align-items:center;">
              ${u.lethality_speed === 'hyperacute_minutes' ? `<span class="tag-pill tag-urgent">突发致命高危</span>` : ''}
              <span class="tag-pill tag-curated">${esc(stLbl)}</span>
            </div>
          </div>

          <div class="update-body">
            <div class="update-row"><span class="ur-key">最新方向希望：</span>${renderProseHtml(u.summary_zh, 'update-prose-p')}</div>
            ${u.unmet_zh ? `<div class="update-row muted-text"><span class="ur-key">仍待攻克：</span>${renderProseHtml(u.unmet_zh, 'update-prose-p')}</div>` : ''}
          </div>

          ${(u.sources || []).length ? `
            <details class="mini-lit-details">
              <summary>查看原始学术文献出处 (${u.sources.length})</summary>
              <div class="source-list" style="margin-top:6px;">
                ${u.sources.map(s => `<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">[${esc(s.publisher)} ${esc(s.year)}] ${esc(s.title)}</a>`).join('')}
              </div>
            </details>
          ` : ''}
        </div>
      `;
    }).join('') || '<div class="muted-text" style="padding:18px;">没有匹配到对应关键词的更新记录。</div>';

    feedEl.innerHTML = snapshotsHtml + cardsHtml;
    feedEl.querySelectorAll('[data-open-id]').forEach(el => {
      el.addEventListener('click', () => this.cb.onOpenDossier(el.dataset.openId));
    });
  }
}
