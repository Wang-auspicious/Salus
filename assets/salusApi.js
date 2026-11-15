// Salus Unified Data Client
// Supports both live FastAPI backend (/api/*) and 100% static CDN bundle (window.SALUS_STATIC_DATA_BASE)

const UTF8_ENCODER = new TextEncoder();

function fnv1aBucket(key) {
  const bytes = UTF8_ENCODER.encode(String(key));
  let h = 0x811c9dc5;
  for (let i = 0; i < bytes.length; i += 1) {
    h ^= bytes[i];
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return (h & 0xff).toString(16).padStart(2, '0');
}

function getStaticBase() {
  const raw = typeof window !== 'undefined' ? window.SALUS_STATIC_DATA_BASE : '';
  return raw ? String(raw).replace(/\/+$/, '') : '';
}

export function isStaticMode() {
  return Boolean(getStaticBase());
}

const _state = {
  mapPoints: null,
  idx: null,
  nodeLookup: null,
  nodeByCurated: null,
  searchExtraPromise: null,
  searchExtra: null,
  idAliasesPromise: null,
  idAliases: null,
  shardCache: new Map(),
};

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`HTTP ${res.status} loading ${url}`);
  }
  return res.json();
}

function buildStaticIndexes(ptsRes) {
  _state.mapPoints = ptsRes;
  const fields = ptsRes.fields || [];
  const idx = {};
  fields.forEach((f, i) => { idx[f] = i; });
  _state.idx = idx;

  const lookup = new Map();
  const byCurated = new Map();
  for (const r of ptsRes.rows || []) {
    const item = {
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
    };
    lookup.set(item.id, item);
    if (item.curated_id !== null && item.curated_id !== undefined) {
      byCurated.set(Number(item.curated_id), item);
    }
  }
  _state.nodeLookup = lookup;
  _state.nodeByCurated = byCurated;
}

async function ensureSearchExtra() {
  if (_state.searchExtra) return _state.searchExtra;
  if (!_state.searchExtraPromise) {
    const base = getStaticBase();
    _state.searchExtraPromise = fetchJson(`${base}/search_extra.json`).then((data) => {
      _state.searchExtra = data;
      return data;
    });
  }
  return _state.searchExtraPromise;
}

async function ensureIdAliases() {
  if (_state.idAliases) return _state.idAliases;
  if (!_state.idAliasesPromise) {
    const base = getStaticBase();
    _state.idAliasesPromise = fetchJson(`${base}/id_aliases.json`).then((data) => {
      _state.idAliases = data || {};
      return _state.idAliases;
    });
  }
  return _state.idAliasesPromise;
}

export async function bootstrapAll() {
  const base = getStaticBase();
  if (!base) {
    const [bootstrap, mapPoints, flow, roadmap] = await Promise.all([
      fetchJson('/api/bootstrap'),
      fetchJson('/api/map-points'),
      fetchJson('/api/flow'),
      fetchJson('/api/roadmap'),
    ]);
    return { bootstrap, mapPoints, flow, roadmap };
  }

  const [bootstrap, mapPoints, flow, roadmap] = await Promise.all([
    fetchJson(`${base}/bootstrap.json`),
    fetchJson(`${base}/map_points.json`),
    fetchJson(`${base}/flow.json`),
    fetchJson(`${base}/roadmap.json`),
  ]);
  buildStaticIndexes(mapPoints);
  // Preload lightweight search & alias indexes without delaying first paint
  ensureSearchExtra().catch(() => {});
  ensureIdAliases().catch(() => {});
  return { bootstrap, mapPoints, flow, roadmap };
}

export async function sliceDiseases({
  domain = null,
  subcluster = null,
  stage = null,
  chip = 'all',
  q = null,
  offset = 0,
  limit = 50,
} = {}) {
  const base = getStaticBase();
  if (!base) {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (domain) params.set('domain', domain);
    if (subcluster) params.set('subcluster', subcluster);
    if (stage) params.set('stage', stage);
    if (chip && chip !== 'all') params.set('chip', chip);
    if (q) params.set('q', q);
    return fetchJson(`/api/slice?${params}`);
  }

  const atlas = _state.mapPoints;
  const idx = _state.idx;
  const qNorm = String(q || '').trim().toLowerCase();
  let curatedBlobs = null;
  if (qNorm) {
    const extra = await ensureSearchExtra().catch(() => null);
    curatedBlobs = extra?.curated_blobs || null;
  }

  const matched = [];
  const stageCounts = {};
  const courseCounts = {};
  let rareCount = 0;
  let curatedCount = 0;

  for (const r of atlas.rows || []) {
    if (domain && r[idx.l1] !== domain) continue;
    if (subcluster && r[idx.l2] !== subcluster) continue;
    const l3 = r[idx.l3];
    if (stage && l3 !== stage) continue;

    const isCur = r[idx.is_curated] || 0;
    const isRare = r[idx.is_rare] || 0;
    const course = r[idx.course];
    const lethality = r[idx.lethality];

    if (chip === 'curated' && !isCur) continue;
    else if (chip === 'hyperacute' && lethality !== 'hyperacute_minutes') continue;
    else if (
      chip === 'acute'
      && !(course === 'acute' || course === 'acute_on_chronic' || lethality === 'hyperacute_minutes' || lethality === 'acute_hours_days')
    ) continue;
    else if (chip === 'chronic' && course !== 'chronic') continue;
    else if (chip === 'rare' && !isRare) continue;
    else if (chip === 'treatable' && !['curable', 'controllable', 'approved_therapy'].includes(l3)) continue;
    else if (chip === 'trials' && !['phase3_trials', 'phase2_trials', 'phase1_trials'].includes(l3)) continue;
    else if (chip === 'unmet' && !['symptomatic_only', 'unmet_no_effective_therapy', 'no_ot_drug_recorded'].includes(l3)) continue;

    if (qNorm) {
      const id = r[idx.id] || '';
      const extraText = curatedBlobs && curatedBlobs[id] ? ` ${curatedBlobs[id]}` : '';
      const hay = `${r[idx.name_zh] || ''} ${r[idx.name_en] || ''} ${id} ${r[idx.icd10] || ''} ${r[idx.orpha_id] || ''}${extraText}`.toLowerCase();
      if (!hay.includes(qNorm)) continue;
    }

    stageCounts[l3] = (stageCounts[l3] || 0) + 1;
    if (course) courseCounts[course] = (courseCounts[course] || 0) + 1;
    rareCount += isRare;
    curatedCount += isCur;
    matched.push(r);
  }

  matched.sort((a, b) => {
    const curDiff = (b[idx.is_curated] || 0) - (a[idx.is_curated] || 0);
    if (curDiff !== 0) return curDiff;
    const lA = a[idx.lethality] === 'hyperacute_minutes' ? 3 : (a[idx.lethality] === 'acute_hours_days' ? 2 : 0);
    const lB = b[idx.lethality] === 'hyperacute_minutes' ? 3 : (b[idx.lethality] === 'acute_hours_days' ? 2 : 0);
    if (lB !== lA) return lB - lA;
    const scoreA = (Number(a[idx.drugs]) || 0) * 10 + (Number(a[idx.trials]) || 0);
    const scoreB = (Number(b[idx.drugs]) || 0) * 10 + (Number(b[idx.trials]) || 0);
    if (scoreB !== scoreA) return scoreB - scoreA;
    return String(a[idx.name_en] || '').localeCompare(String(b[idx.name_en] || ''));
  });

  const total = matched.length;
  const pageRows = matched.slice(offset, offset + limit);
  const items = pageRows.map((r) => ({
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

  return {
    total,
    offset,
    limit,
    stats: {
      curated_count: curatedCount,
      rare_count: rareCount,
      stages: stageCounts,
      courses: courseCounts,
    },
    items,
  };
}

export async function searchDiseases(query, limit = 30) {
  const q = String(query || '').trim();
  if (!q) return { query: '', count: 0, results: [] };

  const base = getStaticBase();
  if (!base) {
    return fetchJson(`/api/search?q=${encodeURIComponent(q)}&limit=${limit}`);
  }

  const extra = await ensureSearchExtra().catch(() => ({}));
  const curatedMeta = extra?.curated || [];
  const masterMeta = extra?.master_meta || {};
  const lookup = _state.nodeLookup;
  const qLower = q.toLowerCase();
  const qUpper = q.toUpperCase();
  const tokens = qLower.replace(/:/g, ' ').split(/\s+/).filter(Boolean);

  const results = [];
  const seen = new Set();

  // 1. Curated dossiers first (matching name, aliases, ICD-10, disease ID, drugs, genes, summary)
  for (const c of curatedMeta) {
    if (results.length >= limit) break;
    if (c.search.includes(qLower)) {
      const node = lookup.get(c.id);
      if (node && !seen.has(node.id)) {
        seen.add(node.id);
        results.push({
          ...node,
          match_source: 'curated_dossier',
          treatability_status: c.treatability_status,
        });
      }
    }
  }

  // 2. Direct ID / ICD / Gene / bilingual name match across 25,697 nodes
  if (results.length < limit) {
    const directHits = [];
    for (const node of lookup.values()) {
      if (seen.has(node.id)) continue;
      const m = masterMeta[node.id]; // [mondo_id, omim_id, icd11, genes]
      const exactIdHit =
        node.id.toUpperCase() === qUpper
        || (node.orpha_id && node.orpha_id.toUpperCase() === qUpper)
        || (m && ((m[0] && m[0].toUpperCase() === qUpper) || (m[1] && m[1].toUpperCase() === qUpper)));

      let hit = exactIdHit;
      if (!hit) {
        const hay = `${node.name_zh || ''} ${node.name_en || ''} ${node.icd10 || ''} ${m ? `${m[2] || ''} ${m[3] || ''}` : ''}`.toLowerCase();
        hit = hay.includes(qLower);
      }
      if (hit) {
        directHits.push({ node, exactIdHit });
      }
    }

    directHits.sort((a, b) => {
      if (a.exactIdHit !== b.exactIdHit) return a.exactIdHit ? -1 : 1;
      const curDiff = (b.node.is_curated || 0) - (a.node.is_curated || 0);
      if (curDiff !== 0) return curDiff;
      const drugsDiff = (b.node.drugs || 0) - (a.node.drugs || 0);
      if (drugsDiff !== 0) return drugsDiff;
      return (b.node.trials || 0) - (a.node.trials || 0);
    });

    for (const h of directHits) {
      if (results.length >= limit) break;
      seen.add(h.node.id);
      results.push({ ...h.node, match_source: 'master_direct' });
    }
  }

  // 3. Multi-token fallback (equivalent to FTS5 AND query across curated + master fields)
  if (results.length < limit && tokens.length > 1) {
    const curatedBlobs = extra?.curated_blobs || {};
    const tokenHits = [];
    for (const node of lookup.values()) {
      if (seen.has(node.id)) continue;
      const m = masterMeta[node.id];
      const blob = curatedBlobs[node.id] || '';
      const hay = `${node.id} ${node.name_zh || ''} ${node.name_en || ''} ${node.l2 || ''} ${node.icd10 || ''} ${node.orpha_id || ''} ${m ? m.join(' ') : ''} ${blob}`.toLowerCase();
      if (tokens.every((t) => hay.includes(t))) {
        tokenHits.push(node);
      }
    }
    tokenHits.sort((a, b) => ((b.is_curated || 0) - (a.is_curated || 0)) || ((b.drugs || 0) - (a.drugs || 0)));
    for (const node of tokenHits) {
      if (results.length >= limit) break;
      seen.add(node.id);
      results.push({ ...node, match_source: 'static_tokens' });
    }
  }

  return { query: q, count: results.length, results };
}

function computeStaticNeighbors(node) {
  if (!node || !_state.mapPoints) return [];
  const atlas = _state.mapPoints;
  const idx = _state.idx;
  const nx = node.x;
  const ny = node.y;
  const cands = [];

  for (const r of atlas.rows || []) {
    if (r[idx.id] === node.id) continue;
    if (r[idx.l1] === node.l1 && r[idx.l2] === node.l2) {
      const dx = r[idx.x] - nx;
      const dy = r[idx.y] - ny;
      const score = dx * dx + dy * dy - (r[idx.is_curated] || 0) * 800.0;
      cands.push([score, r]);
    }
  }
  cands.sort((a, b) => a[0] - b[0]);
  return cands.slice(0, 12).map(([, r]) => ({
    id: r[idx.id],
    name_en: r[idx.name_en],
    name_zh: r[idx.name_zh],
    icd10: r[idx.icd10],
    l3: r[idx.l3],
    is_curated: r[idx.is_curated],
    lethality: r[idx.lethality],
    x: r[idx.x],
    y: r[idx.y],
  }));
}

export async function getDiseaseDetail(diseaseId) {
  const base = getStaticBase();
  if (!base) {
    return fetchJson(`/api/disease/${encodeURIComponent(diseaseId)}`);
  }

  const aliases = await ensureIdAliases().catch(() => ({}));
  const canonicalKey = aliases[diseaseId] || diseaseId;
  const bucket = fnv1aBucket(canonicalKey);

  let shard = _state.shardCache.get(bucket);
  if (!shard) {
    shard = await fetchJson(`${base}/diseases/${bucket}.json`);
    _state.shardCache.set(bucket, shard);
  }
  const entry = shard[canonicalKey];
  if (!entry) {
    throw new Error(`Disease not found in static shard: ${diseaseId}`);
  }

  const node = entry.node || _state.nodeLookup.get(canonicalKey) || null;
  return {
    ...entry,
    node,
    neighbors: entry.neighbors || computeStaticNeighbors(node),
  };
}
