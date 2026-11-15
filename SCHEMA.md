# Salus Global Disease & Treatment Knowledge Base — Schema & Curation Spec

> **Salus · 愿天下再无疾病** — Open-science bilingual knowledge base covering 25,697+ human diseases (`MONDO` + `Orphanet` + `Open Targets`), WHO GHE mortality series (`2000–2023`), and deeply curated patient- & researcher-facing dossiers.

## Directory Layout
- `data/curated/*.json` — Deeply curated Chinese + English disease dossiers (`01_...`..`N_...`), partitioned cleanly by clinical domain or priority wave
- `data/salus.db` — Unified SQLite + FTS5 knowledge base rebuilt cleanly via `python scripts/build_salus_db.py`
- `web/data/*.json` — Precomputed UMAP-like 2D semantic atlas, domain hierarchy, and WHO mortality time-series streams built via `python scripts/prepare_web_atlas.py`
- `web/` — FastAPI backend (`web/server.py`) and zero-build Canvas/SVG multi-layer UI (`web/static/`)
- `scripts/` — Reproducible open-data ingestion, database builder, web atlas generator, and integrity validator

## Curated Disease Record Schema (`data/curated/<batch>.json`)
Each file MUST be valid UTF-8 JSON with this top-level structure:

```json
{
  "domain": "batch_domain_slug",
  "generated_at": "2026-11-15",
  "curator": "agent_or_contributor_name",
  "records": [
    {
      "disease_id": "MONDO:xxxxxxx or ORPHA:xxxxx or ICD10:xxx (exact canonical ID verified in salus.db/MONDO, else null)",
      "name_zh": "中文标准病名",
      "name_en": "Standard English Disease Name",
      "aliases_zh": ["常见别名/俗称（可含病友常用简称）"],
      "icd10": ["I26", "D68.5"],
      "icd11": ["BB00"],
      "category": "临床分类（如：心血管与血栓急症 / 遗传性血液病 / 恶性肿瘤 / 感染性疾病 / 神经与罕见遗传病等）",
      "course_type": "acute | chronic | acute_on_chronic | congenital_genetic",
      "lethality_speed": "hyperacute_minutes | acute_hours_days | subacute_weeks | chronic_years | low_lethality",
      "lethality_detail_zh": "通俗易懂的致死时间窗、先兆危险信号与猝发机制说明（让家属一眼判断多紧急）",
      "treatability_status": "curable | controllable | partially_manageable | symptomatic_only | in_clinical_trials | unmet_no_effective_therapy",
      "treatability_summary_zh": "目前整体能治到什么程度（是否可临床治愈、能否长期稳控、主要障碍与现实期望）",
      "epidemiology": {
        "annual_deaths_global": {
          "value": 100000,
          "range_or_note": "全球每年约死亡人数或严谨说明；若权威机构未单列死亡统计则填 null 并说明自然史病死率",
          "year": "2023",
          "source": "WHO GHE / IHME GBD / Lancet / Orphanet 等可核查出处"
        },
        "annual_deaths_china": {
          "value": null,
          "range_or_note": "中国人群年死亡估算或流调自然史说明",
          "year": "2024",
          "source": "国家卫健委 / 中华医学会指南 / 公开流调论文等"
        },
        "prevalence_global": "全球患病率/发病人群规模（附来源与年份）",
        "prevalence_china": "中国患病率/患者规模或家系检出率（附来源与年份）"
      },
      "clinical_management": {
        "acute_emergency_protocol_zh": "突发危象/急诊120一分钟关键处置提醒（含禁用避坑要点；慢性无急性危象则填 null）",
        "standard_drugs": [
          {
            "name_zh": "药物通用名",
            "name_en": "Generic Name",
            "drug_class": "类别与靶点机制（附通俗作用解释）",
            "usage_role": "一线急救 / 长期预防维持 / 靶向治疗 / 基因或核酸疗法",
            "china_nrdl_covered": true,
            "cost_estimate_cny": "中国国内常见采购/参考花费区间（如集采后参考价、年治疗费用估算；严禁写入保密地方目录名）",
            "cost_source": "来源说明（如国家集采中选公示 / 公开三甲临床价格报道）"
          }
        ],
        "non_drug_therapies_zh": [
          "日常防线、监测复查、家系遗传排查、手术/介入/生活方式避坑等（每条简明可落地）"
        ],
        "annual_total_cost_china_cny": "患者常规年度/单疗程总花费估算与长期管理提示（仅写公立医院参考花费与国家公开降价信息，禁止出现地方内部政策表或非公开文件名称）"
      },
      "research_progress": {
        "highest_pipeline_stage": "Approved_Curative | Approved_Maintenance | Phase_3 | Phase_2 | Phase_1 | Preclinical",
        "breakthroughs_and_trials_zh": "近年重要临床试验、基因编辑/siRNA/蛋白替补/早筛监测进展及 AI4LS 前沿路径（附试验代号/年份/期刊出处）",
        "key_unmet_needs_zh": "目前最核心的待攻克难题（可用 1) ...; 2) ...; 3) ... 结构化列出，系统会自动对齐分行展示）"
      },
      "sources": [
        {
          "title": "权威文献/指南/数据库标题",
          "url": "https://...",
          "publisher": "WHO / IHME / Orphanet / PubMed / NEJM / Lancet / OMIM / FDA 等",
          "year": "2024"
        }
      ]
    }
  ]
}
```

## Mandatory Quality & Integrity Checklist (Zero Hallucination Policy)
1. **Exact Backbone Linking**: Always check `salus.db` (`diseases_master`) before setting `disease_id` so canonical `MONDO:...` / `ORPHA:...` IDs match accurately (`null` only if absent from MONDO/Orphanet, e.g., environmental acute injury).
2. **Zero Fabrication**: Never invent death counts, prevalence numbers, costs, clinical trial IDs, or URLs. If global mortality is unrecorded separately (common for rare monogenic diseases), set numeric `value` to `null` and explain honestly in `range_or_note`.
3. **Patient-First Clarity & Life-Saving Actionability**:
   - Explicitly flag acute / hyperacute lethal diseases (`hyperacute_minutes` / `acute_hours_days`) and supply concrete ER warning signs & avoidance rules in `acute_emergency_protocol_zh`.
   - Include practical **family screening / cascade genetic testing** notes in `non_drug_therapies_zh` for inherited/genetic diseases.
4. **Strict De-Identification / Privacy Rule**:
   - **NEVER** include confidential regional reimbursement catalogs, local administrative documents, or legacy local policy fields anywhere in curated files, costs, or citations. Focus strictly on clinical efficacy, public procurement/hospital reference pricing, and scientific evidence.
5. **Schema Validation Gate**:
   - Every batch file MUST pass `uv run python scripts/verify_salus.py --file data/curated/<your_file>.json --curated-only` (and full `uv run python scripts/verify_salus.py` after DB rebuild) with 0 schema errors.
