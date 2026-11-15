// Salus Patient-First & Translational Disease Dossier Renderer

const COURSE_LABELS = {
  acute: '突发急性病',
  chronic: '长期慢性病',
  acute_on_chronic: '平时平稳 · 易急性致命发作',
  congenital_genetic: '先天 / 家族遗传相关',
};

const LETHALITY_LABELS = {
  hyperacute_minutes: '高危预警：数分钟至数小时内可危及生命',
  acute_hours_days: '急症警示：数小时至数天内危及生命',
  subacute_weeks: '亚急性进展（数周内加重）',
  chronic_years: '慢性进展（以年为周期）',
  low_lethality: '直接致命风险较低（重在控制症状与长期保护）',
};

const STAGE_PLAIN_NOTES = {
  curable: '目前已有成熟方案，规范治疗后大多可实现临床治愈。',
  controllable: '虽尚无一劳永逸的“去根”疗法，但通过日常规范用药和预防避险，患者通常能长期稳控并接近常人寿命。',
  approved_therapy: '国际或国内已有获批上市的对症或靶向治疗药物，可在专科医生指导下用药缓解。',
  partially_manageable: '现有疗法能在一定程度上降低危险或缓解症状，但病情仍凶险或易复发，亟需更安全精准的方案。',
  phase3_trials: '尚无针对性特效药物正式获批，已有新药进入上市前最后一关（III 期临床试验）。',
  phase2_trials: '正在推进中期人体临床试验（II 期），检验新疗法对患者是否真正有效。',
  phase1_trials: '刚步入早期人体安全性临床试验（I 期），仍在全力探索破局中。',
  symptomatic_only: '目前缺少针对病因的专门特效药，主要靠支持护理、缓解痛苦与并发症管理。',
  unmet_no_effective_therapy: '目前医学上仍高度缺乏有效根治或控制手段，属于人类亟待共同攻克的硬骨头。',
  no_ot_drug_recorded: '全球公开靶向药物管线库中暂无对应在研药物记录（部分疾病临床主要靠外科手术、物理抢救、抗感染或基因确诊管理）。',
};

const INHERITANCE_ZH = {
  'Autosomal dominant': '常染色体显性遗传（若父母一方患病，子女约有一半概率遗传同款变异，建议直系亲属尽早排查）',
  'Autosomal recessive': '常染色体隐性遗传（父母多为表型正常的携带者；近亲或家族育龄期可考虑携带者筛查）',
  'X-linked recessive': 'X 连锁隐性遗传（多经母系携带传递，男性成员发病概率较高）',
  'X-linked dominant': 'X 连锁显性遗传（家族垂直传递特征明显，女性或男性均可累及）',
  'Mitochondrial': '线粒体母系遗传（主要随母亲卵子传递）',
  'Multigenic/multifactorial': '多基因与环境共同作用（受遗传易感性与后天生活诱因共同影响）',
  'Not applicable': '非典型遗传（多由后天环境、感染、免疫或散发性突变引起）',
  'Unknown': '遗传模式尚未完全明确（建议结合专科遗传咨询确认）',
};

const PREVALENCE_ZH = {
  '<1 / 1 000 000': '极为罕见（每百万人群中不足 1 例）',
  '1-9 / 1 000 000': '极罕见（每百万人群中约 1–9 例）',
  '1-9 / 100 000': '罕见病（每 10 万人中约 1–9 例）',
  '1-5 / 10 000': '相对罕见（每 1 万人中约 1–5 例）',
  '6-9 / 10 000': '少见病（每 1 万人中约 6–9 例）',
  '>1 / 1 000': '较常见罕见/多发病（超过千分之一）',
  'Unknown': '全球尚无统一人群患病率统计',
};

const ONSET_ZH = {
  Antenatal: '胎儿期（产前即可显现）',
  Neonatal: '新生儿期（出生后最初几周）',
  Infancy: '婴幼儿期',
  Childhood: '儿童期',
  Adolescent: '青少年时期',
  Adult: '成年期',
  Elderly: '中老年期',
  'All ages': '全年龄段均可发病（儿童到成人皆可能遇到）',
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, m => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m]));
}

const CIRCLED_NUMS = '①②③④⑤⑥⑦⑧⑨⑩';

export function renderProseHtml(text, extraClass = 'dossier-body-text') {
  const raw = String(text ?? '').trim();
  if (!raw) return '';

  // Detect sequential numbered items: "1) ...; 2) ...", "1）...；2）...", "(1)... (2)...", "（1）...（2）...", "①...②...", or newline "1. ..."
  const parenRegex = /(?:^|[\s;；,，。:：\n])(\d{1,2})\s*[)）]\s*/g;
  const fullParenRegex = /(?:^|[\s;；,，。:：\n])[（(](\d{1,2})[)）]\s*/g;
  const circleRegex = new RegExp(`([${CIRCLED_NUMS}])\\s*`, 'g');
  const dotRegex = /(?:^|[\n;；])\s*(\d{1,2})\.\s+(?=[\u4e00-\u9fffA-Za-z])/g;

  const collectMatches = (re, mapIdx) => {
    const rawMatches = [];
    let m;
    while ((m = re.exec(raw)) !== null) {
      const markerText = m[0];
      const numVal = mapIdx(m[1]);
      // Offset where delimiter before the marker ends (keep actual marker start cleanly)
      const leadDelimMatch = markerText.match(/^[\s;；,，。:：\n]+/);
      const startPos = m.index + (leadDelimMatch ? leadDelimMatch[0].length : 0);
      rawMatches.push({ startPos, bodyStart: m.index + markerText.length, numVal });
    }
    // Extract strictly contiguous sequential chain 1, 2, 3, ...
    const seqOut = [];
    let expected = 1;
    for (const item of rawMatches) {
      if (item.numVal === expected) {
        seqOut.push(item);
        expected += 1;
      }
    }
    return seqOut.length >= 2 ? seqOut : null;
  };

  let seq =
    collectMatches(parenRegex, v => Number(v))
    || collectMatches(fullParenRegex, v => Number(v))
    || collectMatches(circleRegex, ch => CIRCLED_NUMS.indexOf(ch) + 1)
    || collectMatches(dotRegex, v => Number(v));

  if (seq) {
    const intro = raw.slice(0, seq[0].startPos).replace(/[：:\s]+$/, '').trim();
    const items = seq.map((entry, idx) => {
      const endPos = idx + 1 < seq.length ? seq[idx + 1].startPos : raw.length;
      const body = raw.slice(entry.bodyStart, endPos).replace(/[\s;；]+$/, '').trim();
      return { num: entry.numVal, body };
    }).filter(x => x.body);

    const introHtml = intro ? `<p class="${extraClass}" style="margin-bottom:6px;">${esc(intro)}</p>` : '';
    const listHtml = `
      <ol class="aligned-num-list">
        ${items.map(it => `
          <li class="aligned-num-item">
            <span class="aligned-num-idx">${it.num}.</span>
            <span class="aligned-num-body">${esc(it.body)}</span>
          </li>
        `).join('')}
      </ol>
    `;
    return `${introHtml}${listHtml}`;
  }

  const lines = raw.split(/\n+/).map(l => l.trim()).filter(Boolean);
  return lines.map(l => `<p class="${extraClass}">${esc(l)}</p>`).join('');
}

function fmtNum(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return '暂无全球单列统计';
  const n = Number(v);
  if (n >= 10000) return `约 ${(n / 10000).toFixed(1)} 万人/年`;
  return `约 ${n.toLocaleString()} 人/年`;
}

function translateControlled(text, table) {
  if (!text) return '';
  const parts = String(text).split(/[,;]+/).map(s => s.trim()).filter(Boolean);
  return parts.map(p => table[p] || p).join('；');
}

export function renderDiseaseDossierHTML(detail, stageMeta, domainMap, options = {}) {
  const { node, master, curated, breakthrough_tracks = [], neighbors = [] } = detail;
  const st = (node && stageMeta[node.l3]) || { zh: '未分级', color: '#9ca3af' };
  const dom = (node && domainMap[node.l1]) || { name_zh: '医学全景', color: '#3b82f6' };

  const nameZh = curated?.name_zh || master?.name_zh || node?.name_zh || '';
  const nameEn = curated?.name_en || master?.name_en || node?.name_en || '';
  const icd10List = curated?.icd10?.length ? curated.icd10 : (master?.icd10 || []);
  const icd11List = curated?.icd11?.length ? curated.icd11 : (master?.icd11 || []);

  // 1. 危急风险或总体认识提示条
  const isHyperacute = curated?.lethality_speed === 'hyperacute_minutes' || node?.lethality === 'hyperacute_minutes';
  const isUrgent = isHyperacute || curated?.lethality_speed === 'acute_hours_days';
  let alertHtml = '';
  if (curated) {
    alertHtml = `
      <div class="alert-banner ${isUrgent ? 'alert-danger' : 'alert-calm'}">
        <div class="alert-banner-title">
          ${isHyperacute ? '🚨 高危警示 · ' : (isUrgent ? '⚠ 急症提示 · ' : '💡 病情特点 · ')}
          ${esc(COURSE_LABELS[curated.course_type] || curated.course_type)} · ${esc(LETHALITY_LABELS[curated.lethality_speed] || curated.lethality_speed)}
        </div>
        ${renderProseHtml(curated.lethality_detail_zh)}
      </div>
    `;
  } else {
    alertHtml = `
      <div class="alert-banner alert-calm">
        <div class="alert-banner-title">
          💡 疾病概览 · ${esc(dom.name_zh)}${node?.l2 ? ` · ${esc(node.l2)}` : ''}
        </div>
        <div class="dossier-body-text">${esc(STAGE_PLAIN_NOTES[node?.l3] || '已收录入全球疾病主干目录。')}</div>
      </div>
    `;
  }

  // 2. 患者与家属最关心的 4 件核心事
  const stageDesc = STAGE_PLAIN_NOTES[node?.l3] || '';
  const treatPlain = curated?.treatability_summary_zh || (
    master?.highest_clinical_stage
      ? `${stageDesc}（公开研究记录已有 ${master.approved_drug_count || 0} 种获批药物及 ${master.clinical_trial_count || 0} 项在研临床试验）。`
      : stageDesc
  );

  // 第2格：日常防线、预警信号与家人筛查
  let shieldHtml = '';
  const nonDrugs = curated?.clinical_management?.non_drug_therapies_zh || [];
  if (nonDrugs.length) {
    shieldHtml = `<ul class="plain-bullets">${nonDrugs.map(x => `<li>${esc(x)}</li>`).join('')}</ul>`;
  } else {
    const inhText = translateControlled(master?.inheritance, INHERITANCE_ZH);
    const onsetText = translateControlled(master?.age_of_onset, ONSET_ZH);
    const geneText = master?.genes?.length
      ? `已知相关检测基因包括 <strong>${esc(master.genes.slice(0, 6).join('、'))}</strong>，家族成员或疑似患者做基因排查时可重点关注。`
      : '若直系亲属曾出现类似急危情况或不明原因同类病史，建议提前告知专科医生安排体检评估。';
    shieldHtml = `
      <ul class="plain-bullets">
        ${inhText ? `<li><strong>遗传特征：</strong>${esc(inhText)}</li>` : ''}
        ${onsetText ? `<li><strong>常见起病时期：</strong>${esc(onsetText)}</li>` : ''}
        <li>${geneText}</li>
      </ul>
    `;
  }

  // 第3格：大致花费与日常治疗负担
  const gDeath = curated?.epidemiology?.annual_deaths_global;
  const cDeath = curated?.epidemiology?.annual_deaths_china;
  const prevPlain = curated?.epidemiology?.prevalence_china
    || curated?.epidemiology?.prevalence_global
    || translateControlled(master?.orphanet_prevalence, PREVALENCE_ZH)
    || '具体人口患病率暂缺全球统一单列统计';

  const costText = curated?.clinical_management?.annual_total_cost_china_cny
    || (master?.approved_drug_count
      ? `已有 ${master.approved_drug_count} 种国际临床药物（具体花费视国内上市、常规采购及具体用药方案而定）。`
      : '目前尚无统一公开的年度治疗费用统计，一般视对症支持或手术护理方式而定。');

  const kpiGridHtml = `
    <div class="patient-qa-grid">
      <div class="qa-card">
        <div class="qa-head">
          <span class="qa-num">01</span>
          <h4>现在能治到什么程度？</h4>
        </div>
        <div class="qa-highlight" style="color:${st.color}">${esc(st.zh)}</div>
        ${renderProseHtml(treatPlain, 'qa-text')}
      </div>

      <div class="qa-card">
        <div class="qa-head">
          <span class="qa-num">02</span>
          <h4>平时如何防线、监测与保护家人？</h4>
        </div>
        ${shieldHtml}
      </div>

      <div class="qa-card">
        <div class="qa-head">
          <span class="qa-num">03</span>
          <h4>每年夺走多少生命 / 有多常见？</h4>
        </div>
        <div class="qa-stat-pair">
          <div><span class="muted-hint">全球年死亡：</span><strong>${gDeath ? esc(fmtNum(gDeath.value)) : '未单列'}</strong></div>
          <div><span class="muted-hint">中国情况：</span><strong>${cDeath?.value ? esc(fmtNum(cDeath.value)) : '见下方说明'}</strong></div>
        </div>
        ${renderProseHtml(gDeath?.range_or_note || prevPlain, 'qa-text')}
      </div>

      <div class="qa-card">
        <div class="qa-head">
          <span class="qa-num">04</span>
          <h4>常规治疗与费用大概什么水平？</h4>
        </div>
        ${renderProseHtml(costText, 'qa-text')}
      </div>
    </div>
  `;

  // 3. 突发急症交给急诊医生看的提示框（真正救命用！）
  const acuteProto = curated?.clinical_management?.acute_emergency_protocol_zh
    ? `
      <div class="dossier-section emergency-doctor-box">
        <h3>🚑 如突发危象前往急诊，可将这段核心处置提示直接给接诊医生看</h3>
        ${renderProseHtml(curated.clinical_management.acute_emergency_protocol_zh)}
      </div>
    `
    : '';

  // 4. 常用治疗药物与费用参考表（通俗可读）
  let drugsPlainHtml = '';
  if (curated?.drugs?.length) {
    const rows = curated.drugs.map(d => `
      <tr>
        <td>
          <strong>${esc(d.name_zh)}</strong>
          <div class="muted-hint">${esc(d.name_en)}</div>
        </td>
        <td>
          <div>${esc(d.usage_role || '-')}</div>
          <div class="muted-hint">${esc(d.drug_class || '')}</div>
        </td>
        <td>
          <span class="tag-pill ${d.china_nrdl_covered ? 'tag-curated' : ''}">${d.china_nrdl_covered ? '常规临床常用药物' : '特药 / 费用负担较重'}</span>
          <div style="margin-top:4px;font-size:12px;">${esc(d.cost_estimate_cny || '暂无公开统一零售定额')}</div>
        </td>
      </tr>
    `).join('');
    drugsPlainHtml = `
      <div class="dossier-section">
        <h3>💊 常见治疗药物与参考费用</h3>
        <table class="data-table">
          <thead><tr><th>药物名称</th><th>日常起到什么作用</th><th>常规可得性与参考花费</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    `;
  }

  // 5. 破局方向与希望（回答：未来如何早测早防、如何走向根治）
  const unmetDesc = curated?.research_progress?.key_unmet_needs_zh || (
    node?.l3 === 'curable' || node?.l3 === 'controllable'
      ? '现有疗法已能较好保命与控制，下一步更需要降低终身监测或突发筛查门槛、让偏远及年轻无症状高危人群更早被发现。'
      : '目前尚缺能直接逆转病灶或方便居家预警的核心技术，需要在精准递送、基因修正或超早期无创监测上实现突破。'
  );
  const hopeDesc = curated?.research_progress?.breakthroughs_and_trials_zh || (
    master?.top_drugs?.length
      ? `全球医药界已围绕该病推进 ${master.clinical_trial_count || 0} 项临床试验与靶向药物探索（含 ${master.top_drugs.slice(0, 3).map(d => d.drug_name || d.name).filter(Boolean).join('、')} 等候选分子），可在下方专业科研折叠栏查看详细分子阶段。`
      : '该疾病当前高度依赖基础发病机制解码与跨疾病共性技术平台突破，是未来 AI 生命科学（AI4LS）重点攻关潜力方向之一。'
  );

  const trackButtons = breakthrough_tracks.map(t => `
    <button type="button" class="track-link-card" data-open-track="${esc(t.id)}">
      <div class="track-link-head">
        <span class="track-link-title">${esc(t.title_zh)}</span>
        <span class="track-link-arrow">查看同方向疾病 →</span>
      </div>
      <div class="track-link-sub">${esc(t.subtitle_zh)}</div>
    </button>
  `).join('');

  const directionHtml = `
    <div class="dossier-section direction-section">
      <h3>🧭 走向早监测与真正根治：目前最需要努力的方向</h3>
      <div class="direction-grid">
        <div class="dir-box">
          <div class="dir-box-label">目前还缺少的关键拼图</div>
          ${renderProseHtml(unmetDesc)}
        </div>
        <div class="dir-box">
          <div class="dir-box-label">已在路上的新疗法与希望</div>
          ${renderProseHtml(hopeDesc)}
        </div>
      </div>
      ${trackButtons ? `
        <div style="margin-top:14px;">
          <div class="muted-label" style="margin-bottom:8px;">底层原理相通——攻克以下共性技术路线将同步改变这个病：</div>
          <div class="track-links-grid">${trackButtons}</div>
        </div>
      ` : ''}
    </div>
  `;

  // 6. 专业医学细节与前沿科研文献（默认折叠，供医学及科研人群按需展开）
  let otTableHtml = '';
  if (master?.top_drugs?.length) {
    const otRows = master.top_drugs.map(d => {
      const mechs = Array.isArray(d.mechanisms) && d.mechanisms.length
        ? d.mechanisms.join('; ')
        : (d.mechanism_of_action || '');
      const mechDisplay = [mechs, d.drug_type].filter(Boolean).join(' · ') || '-';
      const stageDisplay = d.max_clinical_stage || d.max_stage || d.clinical_stage || '-';
      return `
        <tr>
          <td><strong>${esc(d.drug_name || d.name || d.drug_id)}</strong></td>
          <td>${esc(mechDisplay)}</td>
          <td><span class="tag-pill">${esc(stageDisplay)}</span></td>
        </tr>
      `;
    }).join('');
    otTableHtml = `
      <div style="margin-top:12px;">
        <div class="tech-subhead">Open Targets 靶向药物管线与作用机制详情</div>
        <table class="data-table">
          <thead><tr><th>分子名称</th><th>作用机制与分子类型</th><th>最高临床阶段</th></tr></thead>
          <tbody>${otRows}</tbody>
        </table>
      </div>
    `;
  }

  let sourcesHtml = '';
  if (curated?.sources?.length) {
    sourcesHtml = `
      <div style="margin-top:12px;">
        <div class="tech-subhead">权威医学指南与科研文献索引</div>
        <div class="source-list">
          ${curated.sources.map(s => `<a class="source-link" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">[${esc(s.publisher)} ${esc(s.year)}] ${esc(s.title)}</a>`).join('')}
        </div>
      </div>
    `;
  }

  const techAccordionHtml = `
    <details class="tech-accordion">
      <summary>🔬 专业医学编码、英文原义与前沿科研文献（面向医生与研究者，点击展开）</summary>
      <div class="tech-accordion-body">
        <div class="pill-row" style="margin-bottom:10px;">
          ${master?.mondo_id ? `<span class="tag-pill">${esc(master.mondo_id)}</span>` : ''}
          ${master?.orpha_id ? `<span class="tag-pill">${esc(master.orpha_id)}</span>` : ''}
          ${master?.omim_id ? `<span class="tag-pill">${esc(master.omim_id)}</span>` : ''}
          ${master?.efo_id ? `<span class="tag-pill">${esc(master.efo_id)}</span>` : ''}
          ${icd10List.map(c => `<span class="tag-pill">ICD-10: ${esc(c)}</span>`).join('')}
          ${icd11List.map(c => `<span class="tag-pill">ICD-11: ${esc(c)}</span>`).join('')}
          ${master?.genes?.length ? `<span class="tag-pill">关联基因: ${esc(master.genes.slice(0, 10).join(', '))}</span>` : ''}
        </div>
        ${master?.definition_en ? `<div class="tech-subhead">医学本体英文定义</div><p class="muted-text" style="margin-bottom:10px;">${esc(master.definition_en)}</p>` : ''}
        ${curated?.epidemiology?.annual_deaths_china?.range_or_note ? `<div class="tech-subhead">中国人群流行病学研究摘要</div><p class="muted-text">${esc(curated.epidemiology.annual_deaths_china.range_or_note)} (${esc(curated.epidemiology.annual_deaths_china.source || '')})</p>` : ''}
        ${otTableHtml}
        ${sourcesHtml}
      </div>
    </details>
  `;

  // 7. 邻近同类病种探索
  let neighborsHtml = '';
  if (neighbors.length) {
    neighborsHtml = `
      <div class="dossier-section">
        <h3>相近疾病（点击继续对比了解）</h3>
        <div class="neighbor-chips">
          ${neighbors.map(nb => `<button type="button" class="neighbor-chip" data-open-id="${esc(nb.id)}">${esc(nb.name_zh || nb.name_en)}</button>`).join('')}
        </div>
      </div>
    `;
  }

  return `
    <div>
      <div class="pill-row" style="margin-bottom:8px;">
        <span class="tag-pill" style="border-color:${dom.color};color:${dom.color}">${esc(dom.name_zh)}</span>
        ${node?.l2 ? `<span class="tag-pill">${esc(node.l2)}</span>` : ''}
        ${curated ? `<span class="tag-pill tag-curated">深度解读</span>` : `<span class="tag-pill">全球疾病库</span>`}
        ${isHyperacute ? `<span class="tag-pill tag-urgent">数分钟危急</span>` : ''}
      </div>

      <h2 class="dossier-hero-title">${esc(nameZh || nameEn)}</h2>
      <div class="dossier-en-sub">${esc(nameEn)} ${curated?.aliases_zh?.length ? ` · 常被称为：${esc(curated.aliases_zh.join(' / '))}` : ''}</div>

      ${options.compact ? `<div style="margin:8px 0 12px;"><button type="button" class="btn-primary" data-expand-modal="${esc(node?.id || master?.canonical_id || '')}">展开完整大屏解读档案</button></div>` : ''}

      ${alertHtml}
      ${kpiGridHtml}
      ${acuteProto}
      ${drugsPlainHtml}
      ${directionHtml}
      ${techAccordionHtml}
      ${neighborsHtml}
    </div>
  `;
}
