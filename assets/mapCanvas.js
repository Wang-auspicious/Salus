// High-DPI Interactive 25,697-Disease Spatial Map Engine (Scime Atlas style)

export class AtlasMapEngine {
  constructor(canvas, tooltipEl, callbacks) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false });
    this.tooltip = tooltipEl;
    this.cb = callbacks;

    this.nodes = [];
    this.domainColors = {};
    this.domains = [];
    this.stageMeta = {};

    // Camera state in world coords [-1000, 1000]
    this.cam = { x: 0, y: 0, zoom: 0.42 };
    this.targetCam = null;
    this.animId = null;

    // Selection & filtering state
    this.selectedDomain = null;
    this.selectedSubcluster = null;
    this.selectedStage = '';
    this.activeChip = 'all';
    this.beaconNode = null;
    this.hoveredNode = null;
    this.labelBoxes = [];

    // Spatial hash cell size for O(1) hover & click hit-testing across 25,697 points
    this.cellSize = 28;
    this.grid = new Map();

    // Pointer interaction
    this.dragging = false;
    this.dragMoved = false;
    this.lastPtr = { x: 0, y: 0 };
    this.pulsePhase = 0;

    this._bindEvents();
    this._startLoop();
  }

  setData(nodesPayload, hierarchy) {
    const fields = nodesPayload.fields;
    const idx = {};
    fields.forEach((f, i) => { idx[f] = i; });

    this.domains = hierarchy.domains || [];
    this.stageMeta = hierarchy.stage_meta || {};
    this.domainColors = {};
    this.domains.forEach(d => { this.domainColors[d.id] = d.color; });

    this.nodes = nodesPayload.rows.map(r => ({
      id: r[idx.id],
      x: r[idx.x],
      y: r[idx.y],
      l1: r[idx.l1],
      l2: r[idx.l2],
      l3: r[idx.l3],
      name_en: r[idx.name_en],
      name_zh: r[idx.name_zh],
      icd10: r[idx.icd10],
      orpha_id: r[idx.orpha_id],
      is_curated: r[idx.is_curated],
      is_rare: r[idx.is_rare],
      course: r[idx.course],
      lethality: r[idx.lethality],
      deaths_g: r[idx.deaths_g],
      drugs: r[idx.drugs],
      trials: r[idx.trials],
      curated_id: r[idx.curated_id],
    }));

    this.grid.clear();
    for (let i = 0; i < this.nodes.length; i++) {
      const n = this.nodes[i];
      const gx = Math.floor(n.x / this.cellSize);
      const gy = Math.floor(n.y / this.cellSize);
      const key = `${gx},${gy}`;
      if (!this.grid.has(key)) this.grid.set(key, []);
      this.grid.get(key).push(n);
    }

    this.resize();
    this.resetCamera(false);
  }

  setFilter({ domain = null, subcluster = null, stage = '', chip = 'all' }) {
    this.selectedDomain = domain;
    this.selectedSubcluster = subcluster;
    this.selectedStage = stage;
    this.activeChip = chip;
  }

  setBeacon(node) {
    this.beaconNode = node || null;
  }

  nodeMatchesFilters(n) {
    if (this.selectedDomain && n.l1 !== this.selectedDomain) return false;
    if (this.selectedSubcluster && n.l2 !== this.selectedSubcluster) return false;
    if (this.selectedStage && n.l3 !== this.selectedStage) return false;
    switch (this.activeChip) {
      case 'curated': return !!n.is_curated;
      case 'hyperacute': return n.lethality === 'hyperacute_minutes';
      case 'acute': return n.course === 'acute' || n.course === 'acute_on_chronic' || n.lethality === 'hyperacute_minutes' || n.lethality === 'acute_hours_days';
      case 'chronic': return n.course === 'chronic';
      case 'rare': return !!n.is_rare;
      case 'treatable': return n.l3 === 'curable' || n.l3 === 'controllable' || n.l3 === 'approved_therapy';
      case 'trials': return n.l3 === 'phase3_trials' || n.l3 === 'phase2_trials' || n.l3 === 'phase1_trials';
      case 'unmet': return n.l3 === 'symptomatic_only' || n.l3 === 'unmet_no_effective_therapy' || n.l3 === 'no_ot_drug_recorded';
      default: return true;
    }
  }

  resize() {
    const rect = this.canvas.parentElement.getBoundingClientRect();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.width = Math.max(300, rect.width);
    this.height = Math.max(300, rect.height);
    this.canvas.width = Math.floor(this.width * dpr);
    this.canvas.height = Math.floor(this.height * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  resetCamera(animate = true) {
    const baseZoom = Math.min(this.width || 1200, this.height || 800) / 1720;
    const target = { x: -40, y: 0, zoom: Math.max(0.28, baseZoom) };
    if (animate) {
      this.targetCam = target;
    } else {
      this.cam = { ...target };
      this.targetCam = null;
    }
  }

  flyTo(wx, wy, targetZoom = 1.6) {
    this.targetCam = { x: wx, y: wy, zoom: targetZoom };
  }

  zoomBy(factor) {
    const next = Math.min(8.0, Math.max(0.22, this.cam.zoom * factor));
    this.targetCam = { x: this.cam.x, y: this.cam.y, zoom: next };
  }

  worldToScreen(wx, wy) {
    return {
      x: (wx - this.cam.x) * this.cam.zoom + this.width * 0.52,
      y: (wy - this.cam.y) * this.cam.zoom + this.height * 0.48,
    };
  }

  screenToWorld(sx, sy) {
    return {
      x: (sx - this.width * 0.52) / this.cam.zoom + this.cam.x,
      y: (sy - this.height * 0.48) / this.cam.zoom + this.cam.y,
    };
  }

  pickNodeAt(sx, sy) {
    const w = this.screenToWorld(sx, sy);
    const hitRadius = Math.max(8, 12 / this.cam.zoom);
    const minGx = Math.floor((w.x - hitRadius) / this.cellSize);
    const maxGx = Math.floor((w.x + hitRadius) / this.cellSize);
    const minGy = Math.floor((w.y - hitRadius) / this.cellSize);
    const maxGy = Math.floor((w.y + hitRadius) / this.cellSize);

    let best = null;
    let bestScore = Infinity;
    for (let gx = minGx; gx <= maxGx; gx++) {
      for (let gy = minGy; gy <= maxGy; gy++) {
        const list = this.grid.get(`${gx},${gy}`);
        if (!list) continue;
        for (let i = 0; i < list.length; i++) {
          const n = list[i];
          const dx = n.x - w.x;
          const dy = n.y - w.y;
          const d2 = dx * dx + dy * dy;
          if (d2 <= hitRadius * hitRadius) {
            const matchBoost = this.nodeMatchesFilters(n) ? 0 : 120;
            const curatedBoost = n.is_curated ? -16 : 0;
            const score = d2 + matchBoost + curatedBoost;
            if (score < bestScore) {
              bestScore = score;
              best = n;
            }
          }
        }
      }
    }
    return best;
  }

  pickLabelAt(sx, sy) {
    for (let i = this.labelBoxes.length - 1; i >= 0; i--) {
      const b = this.labelBoxes[i];
      if (sx >= b.x && sx <= b.x + b.w && sy >= b.y && sy <= b.y + b.h) {
        return b;
      }
    }
    return null;
  }

  _bindEvents() {
    window.addEventListener('resize', () => this.resize());

    this.canvas.addEventListener('pointerdown', (e) => {
      this.dragging = true;
      this.dragMoved = false;
      this.lastPtr = { x: e.clientX, y: e.clientY };
      this.targetCam = null;
    });

    window.addEventListener('pointermove', (e) => {
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;

      if (this.dragging) {
        const dx = e.clientX - this.lastPtr.x;
        const dy = e.clientY - this.lastPtr.y;
        if (Math.abs(dx) > 2 || Math.abs(dy) > 2) this.dragMoved = true;
        this.cam.x -= dx / this.cam.zoom;
        this.cam.y -= dy / this.cam.zoom;
        this.lastPtr = { x: e.clientX, y: e.clientY };
        this.tooltip.classList.add('hidden');
        return;
      }

      if (e.target !== this.canvas) {
        this.hoveredNode = null;
        this.tooltip.classList.add('hidden');
        return;
      }

      const node = this.pickNodeAt(sx, sy);
      this.hoveredNode = node;
      if (node) {
        this.canvas.style.cursor = 'pointer';
        this._showTooltip(node, e.clientX, e.clientY);
      } else {
        const lbl = this.pickLabelAt(sx, sy);
        this.canvas.style.cursor = lbl ? 'pointer' : 'grab';
        this.tooltip.classList.add('hidden');
      }
    });

    window.addEventListener('pointerup', (e) => {
      if (!this.dragging) return;
      const wasDrag = this.dragMoved;
      this.dragging = false;
      if (wasDrag || e.target !== this.canvas) return;

      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;

      // Priority 1: Check floating cluster label click for smooth layer drill-down
      const lbl = this.pickLabelAt(sx, sy);
      if (lbl) {
        if (lbl.kind === 'domain') this.cb.onSelectDomain(lbl.domainId);
        else if (lbl.kind === 'subcluster') this.cb.onSelectSubcluster(lbl.domainId, lbl.subcluster);
        return;
      }

      // Priority 2: Individual disease point click
      const node = this.pickNodeAt(sx, sy);
      if (node) {
        this.cb.onSelectNode(node);
      }
    });

    this.canvas.addEventListener('wheel', (e) => {
      e.preventDefault();
      this.targetCam = null;
      const rect = this.canvas.getBoundingClientRect();
      const sx = e.clientX - rect.left;
      const sy = e.clientY - rect.top;
      const before = this.screenToWorld(sx, sy);
      const factor = e.deltaY < 0 ? 1.15 : 0.87;
      this.cam.zoom = Math.min(8.0, Math.max(0.22, this.cam.zoom * factor));
      const after = this.screenToWorld(sx, sy);
      this.cam.x += before.x - after.x;
      this.cam.y += before.y - after.y;
    }, { passive: false });
  }

  _showTooltip(node, cx, cy) {
    const st = this.stageMeta[node.l3] || { zh: node.l3, color: '#9ca3af' };
    const title = node.name_zh ? `${node.name_zh}` : node.name_en;
    const sub = node.name_zh ? node.name_en : (node.icd10 || node.id);
    const urgent = node.lethality === 'hyperacute_minutes' ? `<span class="tag-pill tag-urgent">数分钟/超急性致命</span>` : '';
    const curated = node.is_curated ? `<span class="tag-pill tag-curated">深度档案</span>` : '';
    const stagePill = `<span class="tag-pill" style="border-color:${st.color};color:${st.color}">${st.zh}</span>`;
    this.tooltip.innerHTML = `
      <div class="map-tooltip-title">${this._esc(title)}</div>
      <div class="map-tooltip-sub">${this._esc(sub)} · ${this._esc(node.l2)}</div>
      <div class="map-tooltip-badges">${curated}${urgent}${stagePill}</div>
    `;
    this.tooltip.style.left = `${Math.min(window.innerWidth - 330, cx + 14)}px`;
    this.tooltip.style.top = `${Math.min(window.innerHeight - 110, cy + 14)}px`;
    this.tooltip.classList.remove('hidden');
  }

  _esc(s) {
    return String(s || '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
  }

  _startLoop() {
    const frame = () => {
      this.pulsePhase = (this.pulsePhase + 0.045) % (Math.PI * 2);
      if (this.targetCam) {
        this.cam.x += (this.targetCam.x - this.cam.x) * 0.14;
        this.cam.y += (this.targetCam.y - this.cam.y) * 0.14;
        this.cam.zoom += (this.targetCam.zoom - this.cam.zoom) * 0.14;
        if (
          Math.abs(this.targetCam.x - this.cam.x) < 0.25 &&
          Math.abs(this.targetCam.y - this.cam.y) < 0.25 &&
          Math.abs(this.targetCam.zoom - this.cam.zoom) < 0.004
        ) {
          this.cam = { ...this.targetCam };
          this.targetCam = null;
        }
      }
      this._render();
      this.animId = requestAnimationFrame(frame);
    };
    this.animId = requestAnimationFrame(frame);
  }

  _render() {
    const ctx = this.ctx;
    const w = this.width || 1200;
    const h = this.height || 800;
    const isLight = document.documentElement.dataset.theme === 'light';

    // Background galaxy surface
    ctx.fillStyle = isLight ? '#f4f6fa' : '#111111';
    ctx.fillRect(0, 0, w, h);

    if (!this.nodes.length) return;

    const hasFocus = !!(this.selectedDomain || this.selectedSubcluster || this.selectedStage || this.activeChip !== 'all');
    const zoom = this.cam.zoom;
    const baseR = Math.max(1.25, Math.min(3.6, 1.45 * Math.sqrt(zoom)));

    // Pass 1: Dimmed non-matching background points
    if (hasFocus) {
      for (let i = 0; i < this.nodes.length; i++) {
        const n = this.nodes[i];
        if (this.nodeMatchesFilters(n)) continue;
        const p = this.worldToScreen(n.x, n.y);
        if (p.x < -10 || p.x > w + 10 || p.y < -10 || p.y > h + 10) continue;
        ctx.fillStyle = isLight ? 'rgba(100, 116, 139, 0.16)' : 'rgba(110, 118, 134, 0.13)';
        ctx.fillRect(p.x - 1, p.y - 1, 2, 2);
      }
    }

    // Pass 2: Active matching points
    for (let i = 0; i < this.nodes.length; i++) {
      const n = this.nodes[i];
      if (hasFocus && !this.nodeMatchesFilters(n)) continue;
      const p = this.worldToScreen(n.x, n.y);
      if (p.x < -14 || p.x > w + 14 || p.y < -14 || p.y > h + 14) continue;

      const color = this.domainColors[n.l1] || '#9ca3af';
      const r = n.is_curated ? baseR * 1.9 : baseR;
      ctx.fillStyle = color;
      ctx.globalAlpha = n.is_curated ? 0.96 : (hasFocus ? (isLight ? 0.88 : 0.85) : (isLight ? 0.72 : 0.62));
      ctx.beginPath();
      ctx.arc(p.x, p.y, r, 0, Math.PI * 2);
      ctx.fill();

      // Subtle ring on curated dossiers when focused or zoomed in
      if (n.is_curated && (zoom > 0.72 || hasFocus)) {
        ctx.strokeStyle = n.lethality === 'hyperacute_minutes'
          ? (isLight ? '#dc2626' : '#fca5a5')
          : (isLight ? 'rgba(15, 23, 42, 0.45)' : 'rgba(255,255,255,0.65)');
        ctx.lineWidth = n.lethality === 'hyperacute_minutes' ? 1.4 : 0.9;
        ctx.globalAlpha = 0.85;
        ctx.stroke();
      }
    }
    ctx.globalAlpha = 1;

    // Pass 3: Hovered highlight
    if (this.hoveredNode) {
      const p = this.worldToScreen(this.hoveredNode.x, this.hoveredNode.y);
      ctx.strokeStyle = isLight ? '#0f172a' : '#ffffff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, Math.max(6, baseR * 2.8), 0, Math.PI * 2);
      ctx.stroke();
    }

    // Pass 4: Pulsing Radar Beacon on Located / Selected Disease
    if (this.beaconNode) {
      const p = this.worldToScreen(this.beaconNode.x, this.beaconNode.y);
      const pulseR = 12 + Math.sin(this.pulsePhase) * 6;
      ctx.strokeStyle = isLight ? 'rgba(15, 23, 42, 0.9)' : 'rgba(255, 255, 255, 0.85)';
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 7.5, 0, Math.PI * 2);
      ctx.stroke();

      ctx.strokeStyle = isLight ? 'rgba(37, 99, 235, 0.7)' : 'rgba(59, 130, 246, 0.55)';
      ctx.lineWidth = 1.6;
      ctx.beginPath();
      ctx.arc(p.x, p.y, pulseR, 0, Math.PI * 2);
      ctx.stroke();

      const lbl = this.beaconNode.name_zh || this.beaconNode.name_en;
      ctx.font = '600 12.5px "Inter", "Noto Sans SC", sans-serif';
      const tw = ctx.measureText(lbl).width;
      const bx = p.x - tw / 2 - 10;
      const by = p.y + 16;
      ctx.fillStyle = isLight ? 'rgba(255, 255, 255, 0.96)' : 'rgba(17, 17, 20, 0.92)';
      this._roundRect(ctx, bx, by, tw + 20, 24, 6);
      ctx.fill();
      ctx.strokeStyle = isLight ? 'rgba(15, 23, 42, 0.2)' : 'rgba(255, 255, 255, 0.25)';
      ctx.lineWidth = 1;
      ctx.stroke();
      ctx.fillStyle = isLight ? '#0f172a' : '#ffffff';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(lbl, p.x, by + 12);
    }

    // Pass 5: Multi-layer Floating Cluster Labels
    this._renderFloatingLabels(ctx, w, h, zoom, isLight);
  }

  _renderFloatingLabels(ctx, w, h, zoom, isLight = false) {
    this.labelBoxes = [];
    const showSubclusters = !!(this.selectedDomain || zoom >= 1.15);

    if (!showSubclusters) {
      // Level 1: 14 Macro Domains
      ctx.font = '600 12.5px "Inter", "Noto Sans SC", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      for (const d of this.domains) {
        const p = this.worldToScreen(d.cx, d.cy);
        if (p.x < 120 || p.x > w - 40 || p.y < 30 || p.y > h - 40) continue;
        const text = `${d.name_zh} · ${d.count.toLocaleString()}`;
        const tw = ctx.measureText(text).width;
        const boxW = tw + 18;
        const boxH = 24;
        const bx = p.x - boxW / 2;
        const by = p.y - boxH / 2;

        // Anti-collision skip if overlapping an already drawn label
        if (this._collides(bx, by, boxW, boxH)) continue;

        ctx.fillStyle = isLight ? 'rgba(255, 255, 255, 0.92)' : 'rgba(17, 17, 20, 0.78)';
        this._roundRect(ctx, bx, by, boxW, boxH, 999);
        ctx.fill();
        ctx.strokeStyle = d.color;
        ctx.globalAlpha = isLight ? 0.82 : 0.55;
        ctx.lineWidth = 1.1;
        ctx.stroke();
        ctx.globalAlpha = 1;

        ctx.fillStyle = isLight ? '#0f172a' : '#f3f4f6';
        ctx.fillText(text, p.x, p.y);
        this.labelBoxes.push({ x: bx, y: by, w: boxW, h: boxH, kind: 'domain', domainId: d.id });
      }
    } else {
      // Level 2: Subclusters of selected domain (or visible domains when zoomed)
      const targetDomains = this.selectedDomain
        ? this.domains.filter(d => d.id === this.selectedDomain)
        : this.domains;
      ctx.font = '600 12px "Inter", "Noto Sans SC", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';

      for (const d of targetDomains) {
        for (const sc of d.subclusters || []) {
          const p = this.worldToScreen(sc.cx, sc.cy);
          if (p.x < 60 || p.x > w - 40 || p.y < 24 || p.y > h - 40) continue;
          const active = this.selectedSubcluster === sc.name;
          const text = `${sc.name} (${sc.count})`;
          const tw = ctx.measureText(text).width;
          const boxW = tw + 16;
          const boxH = 23;
          const bx = p.x - boxW / 2;
          const by = p.y - boxH / 2;
          if (!active && this._collides(bx, by, boxW, boxH)) continue;

          ctx.fillStyle = active
            ? (isLight ? 'rgba(37, 99, 235, 0.95)' : 'rgba(59, 130, 246, 0.25)')
            : (isLight ? 'rgba(255, 255, 255, 0.92)' : 'rgba(17, 17, 20, 0.84)');
          this._roundRect(ctx, bx, by, boxW, boxH, 6);
          ctx.fill();
          ctx.strokeStyle = active ? (isLight ? '#1d4ed8' : '#ffffff') : d.color;
          ctx.globalAlpha = active ? 0.95 : (isLight ? 0.85 : 0.65);
          ctx.lineWidth = active ? 1.4 : 1;
          ctx.stroke();
          ctx.globalAlpha = 1;

          ctx.fillStyle = active ? '#ffffff' : (isLight ? '#0f172a' : d.color);
          ctx.fillText(text, p.x, p.y);
          this.labelBoxes.push({ x: bx, y: by, w: boxW, h: boxH, kind: 'subcluster', domainId: d.id, subcluster: sc.name });
        }
      }
    }
  }

  _collides(x, y, w, h) {
    for (let i = 0; i < this.labelBoxes.length; i++) {
      const b = this.labelBoxes[i];
      if (x < b.x + b.w + 6 && x + w + 6 > b.x && y < b.y + b.h + 4 && y + h + 4 > b.y) {
        return true;
      }
    }
    return false;
  }

  _roundRect(ctx, x, y, w, h, r) {
    const rad = Math.min(r, w / 2, h / 2);
    ctx.beginPath();
    ctx.moveTo(x + rad, y);
    ctx.arcTo(x + w, y, x + w, y + h, rad);
    ctx.arcTo(x + w, y + h, x, y + h, rad);
    ctx.arcTo(x, y + h, x, y, rad);
    ctx.arcTo(x, y, x + w, y, rad);
    ctx.closePath();
  }
}
