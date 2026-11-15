#!/usr/bin/env python3
"""
Salus Global Disease & Treatment Atlas — Web Backend (FastAPI + SQLite FTS5).
Serves the interactive Scime-Atlas-inspired multi-layer frontend and live database APIs.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "salus.db"
SUMMARY_PATH = PROJECT_ROOT / "data" / "salus_summary.json"
WEB_DIR = Path(__file__).resolve().parent
DATA_DIR = WEB_DIR / "data"
STATIC_DIR = WEB_DIR / "static"

app = FastAPI(title="Salus Global Disease Atlas", version="1.1.0")
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Cache precomputed JSON files in memory for sub-millisecond serving
_CACHE: dict[str, Any] = {}


def _scrub_policy_text(text: str | None) -> str | None:
    return text


def _scrub_nested(obj: Any) -> Any:
    return obj


def _clean_sources(sources: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return sources or []


BREAKTHROUGH_TRACKS: list[dict[str, Any]] = [
    {
        "id": "early_monitoring",
        "title_zh": "睡梦与突发危象的实时监测预警",
        "subtitle_zh": "在数分钟至数小时黄金窗到来前，提前发出异常信号",
        "color": "#ef4444",
        "painpoint_zh": "以遗传性易栓症、急性肺栓塞、主动脉夹层、恶性心律失常和脑出血为代表的急症，平时毫无征兆，往往在睡梦中或静息状态下几分钟内危及生命——家人根本来不及反应。",
        "direction_zh": "需要发展连续无创可穿戴微循环/血氧/心电多模态传感器、血液微凝血活性快速试纸，结合 AI 在异常触发前几小时识别微血栓与高危信号并立即叫醒预警。",
        "ai4ls_entry_zh": "时序多模态生理异常检测 + 血液高凝状态前置生物标志物发现 + 低成本家系表型筛查算法。",
    },
    {
        "id": "gene_root_repair",
        "title_zh": "单基因遗传缺陷与缺失蛋白的源头修复",
        "subtitle_zh": "从“提心吊胆终身服药/等待突发”走向“一次修复致病根源”",
        "color": "#a855f7",
        "painpoint_zh": "许多遗传病并非细胞坏了，只是基因编码少了关键一环（如 SERPINC1 缺少抗凝血酶“刹车蛋白”、SMN1 缺乏、凝血因子或酶缺陷），让患者自出生起就暴露在高危或渐进衰退中。",
        "direction_zh": "推进高精度体内 CRISPR 碱基/先导编辑、低免疫原性肝/神经靶向病毒载体（AAV）与脂质纳米颗粒（LNP）递送，以及患者自体干细胞精准修复移植，让身体自己恢复生产正常蛋白。",
        "ai4ls_entry_zh": "AI 指导低脱靶编辑器设计、肝脏/心肌/神经组织特异启动子预测、蛋白功能残基致病性建模。",
    },
    {
        "id": "targeted_delivery",
        "title_zh": "跨生理屏障递送与 RNA / 蛋白稳态重塑",
        "subtitle_zh": "把药真正送进大脑、心脏与深层致密病灶",
        "color": "#3b82f6",
        "painpoint_zh": "神经退行性疾病（渐冻症 ALS、阿尔茨海默病、帕金森）、胶质母细胞瘤与纤维化器官病往往不是没发现靶点，而是大分子药进不去血脑屏障，或普通小分子误伤全身。",
        "direction_zh": "突破跨血脑屏障转运体载体、GalNAc-siRNA / ASO 精密肝脏与中枢基因沉默调控、靶向蛋白降解嵌合体（PROTAC）及虚拟细胞药效预测技术。",
        "ai4ls_entry_zh": "可穿屏障肽/纳米载体生成式设计、RNA 序列稳定与毒性预测、多组学虚拟细胞扰动响应仿真。",
    },
    {
        "id": "emergency_organ_rescue",
        "title_zh": "急诊极危重期的快速再通与器官细胞保护",
        "subtitle_zh": "把“几分钟决定生死”的抢救窗口拓宽成“几小时可救回”",
        "color": "#f59e0b",
        "painpoint_zh": "对急性心梗、脑梗死、脓毒性休克、急性肝衰竭与热射病而言，哪怕送到急诊室，微血管再灌注损伤、细胞因子风暴和不可逆线粒体坏死仍会在短时间内夺走生命。",
        "direction_zh": "研发免复杂配型的超快精准溶栓与导管微取栓器械、阻断微血栓级联炎症风暴药物，以及在缺血缺氧期守住心肌与脑神经元存活的细胞保护剂。",
        "ai4ls_entry_zh": "急性多器官微循环级联动力学建模、急诊快速炎症/凝血表型分型、老药新用紧急器官保护筛选。",
    },
    {
        "id": "early_screen_oncology",
        "title_zh": "高致死实体瘤微环境破壁与普惠早筛",
        "subtitle_zh": "告别“一经发现即晚期”，打破难治肿瘤的免疫屏障",
        "color": "#10b981",
        "painpoint_zh": "胰腺癌、肝癌、晚期肺癌、卵巢癌等之所以致死率极高，核心在于早期几乎没有特异性感觉、发现太晚，且肿瘤被致密纤维基质与免疫抑制“盾牌”死死裹住。",
        "direction_zh": "推动一滴血极早期泛癌种微创早筛降本普及，开发打破癌巢基质屏障的新型核素偶联/双抗药物、通用型细胞免疫疗法与耐药逃逸实时监测。",
        "ai4ls_entry_zh": "低丰度循环肿瘤 DNA/表观甲基化早筛模型、肿瘤空间微环境耐药逆转靶点挖掘。",
    },
]


def _classify_tracks(
    l1: str,
    l2: str = "",
    lethality: str | None = None,
    course: str | None = None,
    is_rare: int = 0,
    has_genes: bool = False,
) -> list[str]:
    tracks: list[str] = []
    if lethality in ("hyperacute_minutes", "acute_hours_days") or l1 == "cardiovascular_thrombosis" or "血栓" in l2 or "卒中" in l2:
        tracks.append("early_monitoring")
    if course == "congenital_genetic" or is_rare or has_genes or l1 in ("rare_genetic_congenital", "hematology_coagulation"):
        if l1 in ("rare_genetic_congenital", "hematology_coagulation", "endocrine_metabolic") or course == "congenital_genetic" or has_genes:
            tracks.append("gene_root_repair")
    if l1 in ("neurological_cns", "psychiatric_behavioral", "respiratory_thoracic") or "神经" in l2 or "脑" in l2:
        tracks.append("targeted_delivery")
    if lethality in ("hyperacute_minutes", "acute_hours_days") or course in ("acute", "acute_on_chronic") or l1 in ("cardiovascular_thrombosis", "infectious_parasitic"):
        if lethality in ("hyperacute_minutes", "acute_hours_days") or "休克" in l2 or "衰竭" in l2 or "血管" in l2 or "急" in l2:
            tracks.append("emergency_organ_rescue")
    if l1 == "oncology_neoplasms":
        tracks.append("early_screen_oncology")
        if "targeted_delivery" not in tracks:
            tracks.append("targeted_delivery")
    if not tracks:
        tracks.append("targeted_delivery")
    return list(dict.fromkeys(tracks))


def load_json_cached(name: str) -> Any:
    if name not in _CACHE:
        p = DATA_DIR / name
        if not p.exists():
            raise HTTPException(status_code=500, detail=f"Missing precomputed file: {name}")
        _CACHE[name] = json.loads(p.read_text(encoding="utf-8"))
    return _CACHE[name]


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def get_atlas_node_lookup() -> dict[str, dict[str, Any]]:
    if "node_lookup" not in _CACHE:
        atlas = load_json_cached("atlas_nodes.json")
        fields = atlas["fields"]
        idx = {f: i for i, f in enumerate(fields)}
        lookup: dict[str, dict[str, Any]] = {}
        by_curated: dict[int, dict[str, Any]] = {}
        for r in atlas["rows"]:
            item = {
                "id": r[idx["id"]],
                "x": r[idx["x"]],
                "y": r[idx["y"]],
                "l1": r[idx["l1"]],
                "l2": r[idx["l2"]],
                "l3": r[idx["l3"]],
                "name_en": r[idx["name_en"]],
                "name_zh": r[idx["name_zh"]],
                "icd10": r[idx["icd10"]],
                "orpha_id": r[idx["orpha_id"]],
                "is_curated": r[idx["is_curated"]],
                "is_rare": r[idx["is_rare"]],
                "course": r[idx["course"]],
                "lethality": r[idx["lethality"]],
                "deaths_g": r[idx["deaths_g"]],
                "drugs": r[idx["drugs"]],
                "trials": r[idx["trials"]],
                "curated_id": r[idx["curated_id"]],
            }
            lookup[item["id"]] = item
            if item["curated_id"] is not None:
                by_curated[int(item["curated_id"])] = item
        _CACHE["node_lookup"] = lookup
        _CACHE["node_by_curated"] = by_curated
    return _CACHE["node_lookup"]


@app.get("/")
def serve_index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/bootstrap")
def api_bootstrap() -> JSONResponse:
    hierarchy = load_json_cached("atlas_hierarchy.json")
    summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8")) if SUMMARY_PATH.exists() else {}
    conn = get_db()
    try:
        showcase = [
            dict(r)
            for r in conn.execute("""
                SELECT id, disease_id, master_canonical_id, name_zh, name_en, course_type,
                       lethality_speed, treatability_status, annual_deaths_global_value,
                       highest_pipeline_stage
                FROM curated_dossiers
                WHERE name_zh IN (
                    '遗传性抗凝血酶缺乏症', '急性肺栓塞', '胰腺癌', '脊髓性肌萎缩症',
                    '脓毒症与脓毒性休克', '狂犬病', '慢性粒细胞白血病', '丙型肝炎'
                )
                ORDER BY id
            """)
        ]
    finally:
        conn.close()
    return JSONResponse({
        "hierarchy": hierarchy,
        "summary": summary,
        "showcase": showcase,
    })


@app.get("/api/map-points")
def api_map_points() -> JSONResponse:
    return JSONResponse(load_json_cached("atlas_nodes.json"))


@app.get("/api/flow")
def api_flow() -> JSONResponse:
    return JSONResponse(load_json_cached("flow_who.json"))


@app.get("/api/slice")
def api_slice(
    domain: str | None = None,
    subcluster: str | None = None,
    stage: str | None = None,
    chip: str | None = None,
    q: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> JSONResponse:
    """Return filtered & ranked diseases for any drill-down slice (Layer 1 -> 2 -> 3 -> 4)."""
    atlas = load_json_cached("atlas_nodes.json")
    fields = atlas["fields"]
    idx = {f: i for i, f in enumerate(fields)}
    q_norm = (q or "").strip().lower()

    matched = []
    stage_counts: dict[str, int] = {}
    course_counts: dict[str, int] = {}
    rare_count = 0
    curated_count = 0

    for r in atlas["rows"]:
        if domain and r[idx["l1"]] != domain:
            continue
        if subcluster and r[idx["l2"]] != subcluster:
            continue
        if stage and r[idx["l3"]] != stage:
            continue

        is_cur = r[idx["is_curated"]]
        is_rare = r[idx["is_rare"]]
        course = r[idx["course"]]
        lethality = r[idx["lethality"]]
        l3 = r[idx["l3"]]

        if chip == "curated" and not is_cur:
            continue
        elif chip == "hyperacute" and lethality != "hyperacute_minutes":
            continue
        elif chip == "acute" and not (course in ("acute", "acute_on_chronic") or lethality in ("hyperacute_minutes", "acute_hours_days")):
            continue
        elif chip == "chronic" and course != "chronic":
            continue
        elif chip == "rare" and not is_rare:
            continue
        elif chip == "treatable" and l3 not in ("curable", "controllable", "approved_therapy"):
            continue
        elif chip == "trials" and l3 not in ("phase3_trials", "phase2_trials", "phase1_trials"):
            continue
        elif chip == "unmet" and l3 not in ("symptomatic_only", "unmet_no_effective_therapy", "no_ot_drug_recorded"):
            continue

        if q_norm:
            hay = f"{r[idx['name_zh']] or ''} {r[idx['name_en']] or ''} {r[idx['id']] or ''} {r[idx['icd10']] or ''} {r[idx['orpha_id']] or ''}".lower()
            if q_norm not in hay:
                continue

        stage_counts[l3] = stage_counts.get(l3, 0) + 1
        if course:
            course_counts[course] = course_counts.get(course, 0) + 1
        rare_count += is_rare
        curated_count += is_cur

        matched.append(r)

    # Sort priority: curated first, then hyperacute/acute, then approved drugs, then trials, then alphabetical
    def sort_key(r: list[Any]) -> tuple[int, int, int, str]:
        is_cur = r[idx["is_curated"]]
        leth = r[idx["lethality"]]
        leth_score = 3 if leth == "hyperacute_minutes" else (2 if leth == "acute_hours_days" else 0)
        drugs = int(r[idx["drugs"]] or 0)
        trials = int(r[idx["trials"]] or 0)
        return (-is_cur, -leth_score, -(drugs * 10 + trials), (r[idx["name_en"]] or ""))

    matched.sort(key=sort_key)
    total = len(matched)
    page_rows = matched[offset : offset + limit]
    items = [
        {
            "id": r[idx["id"]],
            "x": r[idx["x"]],
            "y": r[idx["y"]],
            "l1": r[idx["l1"]],
            "l2": r[idx["l2"]],
            "l3": r[idx["l3"]],
            "name_en": r[idx["name_en"]],
            "name_zh": r[idx["name_zh"]],
            "icd10": r[idx["icd10"]],
            "orpha_id": r[idx["orpha_id"]],
            "is_curated": r[idx["is_curated"]],
            "is_rare": r[idx["is_rare"]],
            "course": r[idx["course"]],
            "lethality": r[idx["lethality"]],
            "deaths_g": r[idx["deaths_g"]],
            "drugs": r[idx["drugs"]],
            "trials": r[idx["trials"]],
            "curated_id": r[idx["curated_id"]],
        }
        for r in page_rows
    ]
    return JSONResponse({
        "total": total,
        "offset": offset,
        "limit": limit,
        "stats": {
            "curated_count": curated_count,
            "rare_count": rare_count,
            "stages": stage_counts,
            "courses": course_counts,
        },
        "items": items,
    })


@app.get("/api/search")
def api_search(q: str = Query(..., min_length=1), limit: int = Query(30, ge=1, le=100)) -> JSONResponse:
    """Fast bilingual + ICD + gene + FTS5 search returning exact coordinates for Map fly-to."""
    query = q.strip()
    lookup = get_atlas_node_lookup()
    by_curated = _CACHE["node_by_curated"]
    conn = get_db()
    results: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    def attach_node(cid: str | None, cur_id: int | None) -> dict[str, Any] | None:
        if cid and cid in lookup:
            return lookup[cid]
        if cur_id is not None and int(cur_id) in by_curated:
            return by_curated[int(cur_id)]
        return None

    try:
        # 1. Exact/Prefix match on curated_dossiers (highest clinical priority)
        like_pat = f"%{query}%"
        for r in conn.execute(
            """
            SELECT id, disease_id, master_canonical_id, name_zh, name_en, category,
                   course_type, lethality_speed, treatability_status, annual_deaths_global_value
            FROM curated_dossiers
            WHERE name_zh LIKE ? OR name_en LIKE ? OR aliases_zh_json LIKE ?
               OR icd10_json LIKE ? OR disease_id LIKE ? OR raw_record_json LIKE ?
            LIMIT ?
            """,
            (like_pat, like_pat, like_pat, like_pat, like_pat, like_pat, limit),
        ):
            node = attach_node(r["master_canonical_id"], r["id"])
            if not node:
                node = attach_node(r["disease_id"], r["id"])
            if node and node["id"] not in seen_ids:
                seen_ids.add(node["id"])
                results.append({
                    **node,
                    "match_source": "curated_dossier",
                    "treatability_status": r["treatability_status"],
                })

        # 2. Direct ID / ICD-10 / Gene match on diseases_master
        if len(results) < limit:
            for r in conn.execute(
                """
                SELECT canonical_id FROM diseases_master
                WHERE canonical_id = ? OR mondo_id = ? OR orpha_id = ? OR omim_id = ?
                   OR icd10_primary LIKE ? OR icd11_primary LIKE ? OR genes LIKE ?
                   OR name_zh LIKE ? OR name_en LIKE ?
                ORDER BY is_curated DESC, approved_drug_count DESC, clinical_trial_count DESC
                LIMIT ?
                """,
                (query.upper(), query.upper(), query.upper(), query.upper(),
                 like_pat, like_pat, like_pat, like_pat, like_pat, limit),
            ):
                cid = r["canonical_id"]
                if cid in lookup and cid not in seen_ids:
                    seen_ids.add(cid)
                    results.append({**lookup[cid], "match_source": "master_direct"})
                    if len(results) >= limit:
                        break

        # 3. FTS5 full-text fallback
        if len(results) < limit:
            fts_tokens = [t.strip('"') for t in query.replace(":", " ").split() if t.strip()]
            if fts_tokens:
                fts_expr = " AND ".join(f'"{t}"' for t in fts_tokens)
                try:
                    for r in conn.execute(
                        """
                        SELECT record_type, record_id, canonical_id
                        FROM diseases_fts
                        WHERE diseases_fts MATCH ?
                        ORDER BY bm25(diseases_fts)
                        LIMIT ?
                        """,
                        (fts_expr, limit * 2),
                    ):
                        node = None
                        if r["canonical_id"]:
                            node = lookup.get(r["canonical_id"])
                        elif r["record_type"] == "curated" and r["record_id"]:
                            node = by_curated.get(int(r["record_id"]))
                        if node and node["id"] not in seen_ids:
                            seen_ids.add(node["id"])
                            results.append({**node, "match_source": f"fts5:{r['record_type']}"})
                            if len(results) >= limit:
                                break
                except sqlite3.OperationalError:
                    pass
    finally:
        conn.close()

    return JSONResponse({"query": query, "count": len(results), "results": results})


@app.get("/api/disease/{disease_id:path}")
def api_disease_detail(disease_id: str) -> JSONResponse:
    """Return full integrated dossier for any master canonical_id or curated_id."""
    lookup = get_atlas_node_lookup()
    by_curated = _CACHE["node_by_curated"]
    conn = get_db()
    try:
        master: dict[str, Any] | None = None
        curated: dict[str, Any] | None = None
        drugs: list[dict[str, Any]] = []

        if disease_id.startswith("CURATED:"):
            cid_num = int(disease_id.split(":", 1)[1])
            row = conn.execute("SELECT * FROM curated_dossiers WHERE id = ?", (cid_num,)).fetchone()
            if row:
                curated = dict(row)
        else:
            m_row = conn.execute("SELECT * FROM diseases_master WHERE canonical_id = ?", (disease_id,)).fetchone()
            if m_row:
                master = dict(m_row)
                if master.get("curated_dossier_id"):
                    c_row = conn.execute("SELECT * FROM curated_dossiers WHERE id = ?", (master["curated_dossier_id"],)).fetchone()
                    if c_row:
                        curated = dict(c_row)
            else:
                c_row = conn.execute("SELECT * FROM curated_dossiers WHERE disease_id = ?", (disease_id,)).fetchone()
                if c_row:
                    curated = dict(c_row)

        if not master and not curated:
            raise HTTPException(status_code=404, detail=f"Disease not found: {disease_id}")

        if curated:
            raw_drugs = [
                dict(d)
                for d in conn.execute("SELECT * FROM curated_drugs WHERE dossier_id = ? ORDER BY china_nrdl_covered DESC, id", (curated["id"],))
            ]
            for d in raw_drugs:
                d["cost_estimate_cny"] = _scrub_policy_text(d.get("cost_estimate_cny"))
                d["cost_source"] = _scrub_policy_text(d.get("cost_source"))
                d["usage_role"] = _scrub_policy_text(d.get("usage_role"))
            drugs = raw_drugs

        # Parse JSON helper
        def parse_j(val: str | None, default: Any = None) -> Any:
            if not val:
                return default
            try:
                return json.loads(val)
            except Exception:
                return default

        node = lookup.get(master["canonical_id"] if master else disease_id)
        if not node and curated:
            node = by_curated.get(curated["id"])

        # Find nearest neighbors in the same Level-2 subcluster (just like Scime nearest papers!)
        neighbors: list[dict[str, Any]] = []
        if node:
            atlas = load_json_cached("atlas_nodes.json")
            fields = atlas["fields"]
            idx = {f: i for i, f in enumerate(fields)}
            nx, ny = node["x"], node["y"]
            cands = []
            for r in atlas["rows"]:
                if r[idx["id"]] == node["id"]:
                    continue
                if r[idx["l1"]] == node["l1"] and r[idx["l2"]] == node["l2"]:
                    dist2 = (r[idx["x"]] - nx) ** 2 + (r[idx["y"]] - ny) ** 2
                    cands.append((dist2 - r[idx["is_curated"]] * 800.0, r))
            cands.sort(key=lambda t: t[0])
            for _, r in cands[:12]:
                neighbors.append({
                    "id": r[idx["id"]],
                    "name_en": r[idx["name_en"]],
                    "name_zh": r[idx["name_zh"]],
                    "icd10": r[idx["icd10"]],
                    "l3": r[idx["l3"]],
                    "is_curated": r[idx["is_curated"]],
                    "lethality": r[idx["lethality"]],
                    "x": r[idx["x"]],
                    "y": r[idx["y"]],
                })

        genes_list = parse_j(master["genes_json"], []) if master else []
        track_ids = _classify_tracks(
            l1=node["l1"] if node else (curated["domain"] if curated else ""),
            l2=node["l2"] if node else (curated["category"] if curated else ""),
            lethality=(curated["lethality_speed"] if curated else (node["lethality"] if node else None)),
            course=(curated["course_type"] if curated else (node["course"] if node else None)),
            is_rare=int(master["is_rare"] or 0) if master else 0,
            has_genes=bool(genes_list),
        )
        track_map = {t["id"]: t for t in BREAKTHROUGH_TRACKS}
        breakthrough_tracks = [track_map[tid] for tid in track_ids if tid in track_map]

        cm_obj = parse_j(curated["clinical_management_json"], {}) if curated else {}
        if isinstance(cm_obj, dict):
            cm_obj = _scrub_nested(cm_obj)

        rp_obj = _scrub_nested(parse_j(curated["research_progress_json"], {}) if curated else {})
        epi_obj = _scrub_nested(parse_j(curated["epidemiology_json"], {}) if curated else {})
        sources_clean = _clean_sources(parse_j(curated["sources_json"], []) if curated else [])

        payload: dict[str, Any] = {
            "node": node,
            "breakthrough_tracks": breakthrough_tracks,
            "master": {
                "canonical_id": master["canonical_id"],
                "mondo_id": master["mondo_id"],
                "name_en": master["name_en"],
                "name_zh": master["name_zh"],
                "definition_en": master["definition_en"],
                "synonyms_en": parse_j(master["synonyms_en_json"], []),
                "icd10": parse_j(master["icd10_json"], []),
                "icd11": parse_j(master["icd11_json"], []),
                "orpha_id": master["orpha_id"],
                "omim_id": master["omim_id"],
                "efo_id": master["efo_id"],
                "disease_type": master["disease_type"],
                "mondo_categories": parse_j(master["mondo_categories_json"], []),
                "highest_clinical_stage": master["highest_clinical_stage"],
                "approved_drug_count": master["approved_drug_count"],
                "clinical_trial_count": master["clinical_trial_count"],
                "top_drugs": parse_j(master["top_drugs_json"], []),
                "orphanet_prevalence": master["orphanet_prevalence"],
                "orphanet_prevalence_list": parse_j(master["orphanet_prevalence_json"], []),
                "inheritance": master["inheritance"],
                "age_of_onset": master["age_of_onset"],
                "genes": genes_list,
                "is_rare": bool(master["is_rare"]),
                "source_flags": master["source_flags"],
            } if master else None,
            "curated": {
                "id": curated["id"],
                "domain": curated["domain"],
                "disease_id": curated["disease_id"],
                "name_zh": curated["name_zh"],
                "name_en": curated["name_en"],
                "aliases_zh": parse_j(curated["aliases_zh_json"], []),
                "icd10": parse_j(curated["icd10_json"], []),
                "icd11": parse_j(curated["icd11_json"], []),
                "category": curated["category"],
                "course_type": curated["course_type"],
                "lethality_speed": curated["lethality_speed"],
                "lethality_detail_zh": curated["lethality_detail_zh"],
                "treatability_status": curated["treatability_status"],
                "treatability_summary_zh": _scrub_policy_text(curated["treatability_summary_zh"]),
                "epidemiology": epi_obj,
                "clinical_management": cm_obj,
                "research_progress": rp_obj,
                "drugs": drugs,
                "sources": sources_clean,
            } if curated else None,
            "neighbors": neighbors,
        }
        return JSONResponse(payload)
    finally:
        conn.close()


@app.get("/api/roadmap")
def api_roadmap() -> JSONResponse:
    """Return cross-disease breakthrough tracks and live knowledge-base update feed."""
    if "roadmap_payload" in _CACHE:
        return JSONResponse(_CACHE["roadmap_payload"])

    atlas = load_json_cached("atlas_nodes.json")
    fields = atlas["fields"]
    idx = {f: i for i, f in enumerate(fields)}
    track_counts: dict[str, int] = {t["id"]: 0 for t in BREAKTHROUGH_TRACKS}
    curated_nodes_by_track: dict[str, list[dict[str, Any]]] = {t["id"]: [] for t in BREAKTHROUGH_TRACKS}

    stage_totals: dict[str, int] = {}
    for r in atlas["rows"]:
        l1 = r[idx["l1"]]
        l2 = r[idx["l2"]] or ""
        l3 = r[idx["l3"]]
        stage_totals[l3] = stage_totals.get(l3, 0) + 1
        leth = r[idx["lethality"]]
        course = r[idx["course"]]
        is_rare = int(r[idx["is_rare"]] or 0)
        is_cur = int(r[idx["is_curated"]] or 0)
        tids = _classify_tracks(l1, l2, leth, course, is_rare, has_genes=is_rare == 1)
        for tid in tids:
            track_counts[tid] += 1
            if is_cur:
                curated_nodes_by_track[tid].append({
                    "id": r[idx["id"]],
                    "curated_id": r[idx["curated_id"]],
                    "name_zh": r[idx["name_zh"]],
                    "name_en": r[idx["name_en"]],
                    "l1": l1,
                    "l2": l2,
                    "l3": l3,
                    "lethality": leth,
                    "deaths_g": r[idx["deaths_g"]],
                })

    conn = get_db()
    try:
        cur_rows = [
            dict(r)
            for r in conn.execute("""
                SELECT id, disease_id, master_canonical_id, domain, generated_at, name_zh, name_en,
                       lethality_speed, treatability_status, highest_pipeline_stage,
                       annual_deaths_global_value, research_progress_json, sources_json
                FROM curated_dossiers
                ORDER BY id
            """)
        ]
    finally:
        conn.close()

    cur_by_id = {int(c["id"]): c for c in cur_rows}
    tracks_out: list[dict[str, Any]] = []
    for t in BREAKTHROUGH_TRACKS:
        tid = t["id"]
        nodes_for_track = curated_nodes_by_track[tid]
        nodes_for_track.sort(
            key=lambda x: (
                0 if x["lethality"] == "hyperacute_minutes" else (1 if x["lethality"] == "acute_hours_days" else 2),
                -(x["deaths_g"] or 0),
                x["name_zh"] or x["name_en"],
            )
        )
        lit_signals = []
        for n in nodes_for_track[:8]:
            c = cur_by_id.get(int(n["curated_id"])) if n["curated_id"] else None
            if not c:
                continue
            rp = json.loads(c["research_progress_json"] or "{}")
            srcs = _clean_sources(json.loads(c["sources_json"] or "[]"))
            lit_signals.append({
                "id": n["id"],
                "name_zh": c["name_zh"],
                "name_en": c["name_en"],
                "pipeline_stage": c["highest_pipeline_stage"],
                "breakthrough_zh": _scrub_policy_text(rp.get("breakthroughs_and_trials_zh", "")),
                "unmet_zh": _scrub_policy_text(rp.get("key_unmet_needs_zh", "")),
                "sources": srcs[:2],
            })
        tracks_out.append({
            **t,
            "matched_total_count": track_counts[tid],
            "curated_count": len(nodes_for_track),
            "featured_diseases": nodes_for_track[:18],
            "literature_signals": lit_signals,
        })

    # Build real dynamic update feed from curated research frontier notes
    live_updates = []
    for c in cur_rows:
        rp = json.loads(c["research_progress_json"] or "{}")
        bt = rp.get("breakthroughs_and_trials_zh") or ""
        if not bt:
            continue
        cid = c["master_canonical_id"] or c["disease_id"] or f"CURATED:{c['id']}"
        srcs = _clean_sources(json.loads(c["sources_json"] or "[]"))
        live_updates.append({
            "id": cid,
            "name_zh": c["name_zh"],
            "name_en": c["name_en"],
            "generated_at": c["generated_at"],
            "lethality_speed": c["lethality_speed"],
            "treatability_status": c["treatability_status"],
            "pipeline_stage": c["highest_pipeline_stage"],
            "summary_zh": _scrub_policy_text(bt),
            "unmet_zh": _scrub_policy_text(rp.get("key_unmet_needs_zh", "")),
            "sources": srcs[:2],
        })

    # Prioritize hyperacute & advancing clinical milestones first
    live_updates.sort(
        key=lambda u: (
            0 if u["name_zh"] in ("遗传性抗凝血酶缺乏症", "急性肺栓塞", "脊髓性肌萎缩症", "胰腺癌", "脓毒症与脓毒性休克", "阿尔茨海默病") else 1,
            0 if u["lethality_speed"] == "hyperacute_minutes" else 1,
            u["name_zh"] or "",
        )
    )

    payload = {
        "total_diseases": len(atlas["rows"]),
        "curated_count": len(cur_rows),
        "stage_totals": stage_totals,
        "tracks": tracks_out,
        "live_updates": live_updates[:36],
        "data_snapshots": [
            {"name": "人类疾病全景主干 (MONDO + Orphanet)", "version": "2026-11 / Open Targets 26.09", "count": "25,697 种疾病"},
            {"name": "全球与中国死因时间序列 (WHO GHE)", "version": "2000–2023 全序列", "count": "226 个死因层级"},
            {"name": "重点急重症与遗传病中文深度解读", "version": "持续迭代更新", "count": f"{len(cur_rows)} 份档案"},
        ],
    }
    _CACHE["roadmap_payload"] = payload
    return JSONResponse(payload)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def main() -> None:
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Run Salus Disease Atlas Web Server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
