#!/usr/bin/env python3
"""
Salus Web Atlas Precomputation Script.
Reads D:\\Desktop\\Publication\\Salus\\data\\salus.db and generates lightweight,
deterministic multi-layer datasets under D:\\Desktop\\Publication\\Salus\\web\\data\\:
  - atlas_nodes.json       (25,697 diseases with 4-tier hierarchy + 2D galaxy coordinates)
  - atlas_hierarchy.json   (Level 1 domains -> Level 2 subclusters -> Level 3 treatability/pipeline stats)
  - flow_who.json          (WHO GHE 2000-2023 WRLD & CHN multi-level death series for Flow View)
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "salus.db"
OUT_DIR = PROJECT_ROOT / "web" / "data"

MACRO_DOMAINS: list[dict[str, str]] = [
    {"id": "cardiovascular_thrombosis", "name_zh": "心血管与血栓血管", "name_en": "Cardiovascular & Thrombosis", "color": "#ef4444"},
    {"id": "oncology_neoplasms", "name_zh": "恶性肿瘤与新生物", "name_en": "Oncology & Neoplasms", "color": "#f97316"},
    {"id": "hematology_coagulation", "name_zh": "血液与凝血障碍", "name_en": "Hematology & Coagulation", "color": "#e64980"},
    {"id": "neurological_cns", "name_zh": "神经与中枢系统", "name_en": "Neurological & CNS", "color": "#3b82f6"},
    {"id": "psychiatric_behavioral", "name_zh": "精神与心理行为", "name_en": "Psychiatric & Behavioral", "color": "#8b5cf6"},
    {"id": "infectious_parasitic", "name_zh": "感染与寄生虫病", "name_en": "Infectious & Parasitic", "color": "#22c55e"},
    {"id": "respiratory_thoracic", "name_zh": "呼吸与胸腔系统", "name_en": "Respiratory & Thoracic", "color": "#06b6d4"},
    {"id": "endocrine_metabolic", "name_zh": "内分泌与代谢病", "name_en": "Endocrine & Metabolic", "color": "#eab308"},
    {"id": "digestive_hepatobiliary", "name_zh": "消化与肝胆系统", "name_en": "Digestive & Hepatobiliary", "color": "#f59e0b"},
    {"id": "renal_urinary", "name_zh": "泌尿与肾脏系统", "name_en": "Renal & Urinary", "color": "#14b8a6"},
    {"id": "immune_rheumatic", "name_zh": "免疫与风湿结缔组织", "name_en": "Immune & Rheumatic", "color": "#ec4899"},
    {"id": "musculoskeletal_sensory_skin", "name_zh": "骨肌、五官与皮肤", "name_en": "Musculoskeletal, Sensory & Skin", "color": "#38bdf8"},
    {"id": "reproductive_perinatal", "name_zh": "生殖、孕产与围产期", "name_en": "Reproductive & Perinatal", "color": "#fb7185"},
    {"id": "rare_genetic_congenital", "name_zh": "遗传综合征与先天发育", "name_en": "Genetic Syndromes & Congenital", "color": "#a855f7"},
]

STAGE_LABELS = {
    "curable": {"zh": "临床可治愈", "en": "Curable", "color": "#10b981", "order": 1},
    "controllable": {"zh": "可长期控制", "en": "Controllable (SOC)", "color": "#3b82f6", "order": 2},
    "approved_therapy": {"zh": "已有获批药物", "en": "Approved Drugs", "color": "#38bdf8", "order": 3},
    "partially_manageable": {"zh": "部分缓解/仍高危", "en": "Partially Manageable", "color": "#f59e0b", "order": 4},
    "phase3_trials": {"zh": "Ⅲ期临床推进中", "en": "Phase III Trials", "color": "#a855f7", "order": 5},
    "phase2_trials": {"zh": "Ⅱ期临床探索中", "en": "Phase II Trials", "color": "#8b5cf6", "order": 6},
    "phase1_trials": {"zh": "Ⅰ期/早期临床", "en": "Phase I / Early", "color": "#6366f1", "order": 7},
    "symptomatic_only": {"zh": "仅对症支持", "en": "Symptomatic Only", "color": "#fb7185", "order": 8},
    "unmet_no_effective_therapy": {"zh": "尚无有效疗法", "en": "Unmet / No Therapy", "color": "#ef4444", "order": 9},
    "no_ot_drug_recorded": {"zh": "暂无药物管线记录", "en": "No Recorded Pipeline", "color": "#6b7280", "order": 10},
}

WHO_ZH_EXTENDED: dict[str, str] = {
    "All Causes": "全因死亡",
    "Communicable, maternal, perinatal and nutritional conditions": "传染病、孕产妇、围产期及营养性疾病",
    "Noncommunicable diseases": "非传染性疾病",
    "Injuries": "伤害与外因",
    "Cardiovascular diseases": "心血管疾病",
    "Malignant neoplasms": "恶性肿瘤",
    "Other neoplasms": "其他肿瘤",
    "Infectious and parasitic diseases": "传染病与寄生虫病",
    "Respiratory infections": "呼吸道感染",
    "Respiratory diseases": "呼吸系统疾病",
    "Neurological conditions": "神经系统疾病",
    "Digestive diseases": "消化系统疾病",
    "Genitourinary diseases": "泌尿生殖系统疾病",
    "Diabetes mellitus": "糖尿病",
    "Neonatal conditions": "新生儿疾病",
    "Maternal conditions": "孕产妇疾病",
    "Nutritional deficiencies": "营养缺乏症",
    "Endocrine, blood, immune disorders": "内分泌、血液与免疫疾病",
    "Mental and substance use disorders": "精神与物质使用障碍",
    "Musculoskeletal diseases": "肌肉骨骼疾病",
    "Congenital anomalies": "先天畸形",
    "Skin diseases": "皮肤疾病",
    "Sense organ diseases": "感觉器官疾病",
    "Oral conditions": "口腔疾病",
    "Sudden infant death syndrome": "婴儿猝死综合征",
    "Unintentional injuries": "意外伤害",
    "Intentional injuries": "蓄意伤害",
    "Ischaemic heart disease": "缺血性心脏病",
    "Stroke": "脑卒中",
    "Hypertensive heart disease": "高血压性心脏病",
    "Rheumatic heart disease": "风湿性心脏病",
    "Cardiomyopathy, myocarditis, endocarditis": "心肌病、心肌炎与心内膜炎",
    "Other circulatory diseases": "其他循环系统疾病（含肺栓塞/主动脉夹层等）",
    "Chronic obstructive pulmonary disease": "慢性阻塞性肺疾病（COPD）",
    "Asthma": "支气管哮喘",
    "Other respiratory diseases": "其他呼吸系统疾病",
    "Lower respiratory infections": "下呼吸道感染",
    "Upper respiratory infections": "上呼吸道感染",
    "COVID-19": "新型冠状病毒感染",
    "Tuberculosis": "结核病",
    "HIV/AIDS": "艾滋病",
    "Diarrhoeal diseases": "腹泻病",
    "Malaria": "疟疾",
    "Meningitis": "脑膜炎",
    "Encephalitis": "脑炎",
    "Hepatitis": "病毒性肝炎",
    "Trachea, bronchus, lung cancers": "气管、支气管和肺癌",
    "Colon and rectum cancers": "结直肠癌",
    "Liver cancer": "肝癌",
    "Stomach cancer": "胃癌",
    "Breast cancer": "乳腺癌",
    "Pancreas cancer": "胰腺癌",
    "Oesophagus cancer": "食管癌",
    "Prostate cancer": "前列腺癌",
    "Cervix uteri cancer": "宫颈癌",
    "Brain and nervous system cancers": "脑与神经系统肿瘤",
    "Leukaemia": "白血病",
    "Lymphomas, multiple myeloma": "淋巴瘤与多发性骨髓瘤",
    "Kidney diseases": "肾脏疾病",
    "Cirrhosis of the liver": "肝硬化",
    "Peptic ulcer disease": "消化性溃疡",
    "Pancreatitis": "胰腺炎",
    "Alzheimer disease and other dementias": "阿尔茨海默病及其他痴呆症",
    "Parkinson disease": "帕金森病",
    "Epilepsy": "癫痫",
    "Multiple sclerosis": "多发性硬化",
    "Road injury": "道路交通伤害",
    "Falls": "跌落伤",
    "Drowning": "溺水",
    "Self-harm": "自伤",
    "Interpersonal violence": "人际暴力",
}


def stable_hash_unit(key: str, salt: str) -> float:
    h = hashlib.sha256(f"{salt}:{key}".encode("utf-8")).digest()
    val = int.from_bytes(h[:8], "big")
    return val / float(2**64 - 1)


def stable_normal(key: str, salt: str) -> float:
    u1 = max(1e-9, stable_hash_unit(key, salt + "_u1"))
    u2 = stable_hash_unit(key, salt + "_u2")
    return math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def classify_domain(row: dict[str, Any], curated: dict[str, Any] | None) -> tuple[str, str]:
    """Return unified (level1_domain, level2_subcluster) so curated & master records share subclusters."""
    name_en = (row["name_en"] or "").lower()
    name_zh = (row["name_zh"] or "")
    icd10 = (row["icd10_primary"] or "").upper()
    cats_raw = row["mondo_categories_json"] or "[]"
    try:
        cats = [c.lower() for c in json.loads(cats_raw)]
    except Exception:
        cats = []
    cat_str = " | ".join(cats)

    if curated:
        cdom = curated["domain"]
        ccat = curated["category"] or ""
        if cdom == "acute_vascular_thrombophilia":
            if "易栓症" in ccat or "凝血" in ccat or any(k in name_en for k in ("antithrombin", "protein c", "protein s", "leiden", "prothrombin", "antiphospholipid", "thrombophilia")):
                return "cardiovascular_thrombosis", "遗传性/获得性易栓症与凝血"
            if "过敏" in name_zh or "anaphylaxis" in name_en:
                return "immune_rheumatic", "原发免疫缺陷与过敏危象"
            if curated["course_type"] in ("acute", "acute_on_chronic") and curated["lethality_speed"] in ("hyperacute_minutes", "acute_hours_days"):
                return "cardiovascular_thrombosis", "急性致死性血管与心脏急症"
            if any(k in name_en for k in ("cardiomyopathy", "arrhythmia", "fibrillation")):
                return "cardiovascular_thrombosis", "心律失常、心肌病与先心病"
            return "cardiovascular_thrombosis", "慢性心血管、心肌与血管病"
        if cdom == "oncology_hematology":
            if any(k in name_zh for k in ("白血病", "淋巴瘤", "骨髓瘤", "骨髓增生")):
                return "hematology_coagulation", "血液系统恶性肿瘤与MDS"
            if any(k in name_zh for k in ("贫血", "血友病", "地中海")):
                return "hematology_coagulation", "遗传性/获得性出血与造血障碍"
            return "oncology_neoplasms", "实体恶性肿瘤与肉瘤"
        if cdom == "infectious_respiratory":
            if any(k in name_zh for k in ("哮喘", "阻塞性肺", "肺纤维化", "支气管", "尘肺", "气胸", "呼吸窘迫")):
                return "respiratory_thoracic", "急慢性呼吸与肺血管病变"
            if curated["lethality_speed"] in ("hyperacute_minutes", "acute_hours_days"):
                return "infectious_parasitic", "急性致命感染与脓毒症"
            return "infectious_parasitic", "慢性、寄生虫与系统感染"
        if cdom == "neuro_mental_rare":
            if any(k in name_zh for k in ("抑郁", "双相", "精神分裂")):
                return "psychiatric_behavioral", "精神、情绪与神经发育障碍"
            if any(k in name_zh for k in ("血管性水肿",)):
                return "immune_rheumatic", "原发免疫缺陷与过敏危象"
            if any(k in name_zh for k in ("癫痫", "脑炎")):
                return "neurological_cns", "癫痫与急性/发育性脑病"
            if any(k in name_zh for k in ("肌萎缩侧索硬化", "脊髓性肌萎缩", "肌营养不良", "重症肌无力")):
                return "neurological_cns", "神经肌肉与运动神经元病变"
            if curated["course_type"] == "congenital_genetic" and any(k in name_zh for k in ("尼曼", "戈谢", "法布雷", "庞贝", "苯丙酮")):
                return "endocrine_metabolic", "先天遗传性代谢与线粒体病"
            if curated["course_type"] == "congenital_genetic":
                return "rare_genetic_congenital", "多发畸形与发育遗传综合征"
            return "neurological_cns", "中枢神经变性与遗传性脑病"
        if cdom == "metabolic_organ_immune":
            if any(k in name_zh for k in ("狼疮", "关节炎", "脊柱炎", "贝赫切特", "硬化症", "川崎")):
                return "immune_rheumatic", "炎症性与系统性自身免疫病"
            if any(k in name_zh for k in ("肾", "尿毒症")):
                return "renal_urinary", "肾小球、肾小管与泌尿系统疾病"
            if any(k in name_zh for k in ("胰腺", "肝", "消化", "克罗恩", "结肠炎", "肠梗阻", "胆管")):
                return "digestive_hepatobiliary", "胃肠、肝胆与胰腺疾病"
            if any(k in name_zh for k in ("热射病", "中毒")):
                return "respiratory_thoracic", "环境急症与呼吸危重症"
            return "endocrine_metabolic", "糖尿病、甲状腺与内分泌病"

    # Non-curated master clinical routing
    if any(k in name_en for k in ("thrombophilia", "antithrombin", "protein c deficien", "protein s deficien", "factor v leiden", "prothrombin", "hemophilia", "thalassemia", "sickle cell", "aplastic anemia", "myelodysplastic", "thrombocytopenia", "coagulation factor", "von willebrand", "antiphospholipid")):
        if any(k in name_en for k in ("thrombophilia", "antithrombin", "protein c", "protein s", "leiden", "prothrombin", "antiphospholipid")):
            return "cardiovascular_thrombosis", "遗传性/获得性易栓症与凝血"
        return "hematology_coagulation", "遗传性/获得性出血与造血障碍"

    if "cancer or benign tumor" in cat_str or icd10.startswith(("C", "D0", "D1", "D2", "D3", "D4")) or any(k in name_en for k in ("carcinoma", "sarcoma", "melanoma", "glioma", "glioblastoma", "neuroblastoma", "adenocarcinoma", "neoplasm")):
        if any(k in name_en for k in ("leukemia", "lymphoma", "myeloma", "myeloproliferative", "myelodysplastic")):
            return "hematology_coagulation", "血液系统恶性肿瘤与MDS"
        if "benign" in name_en or icd10.startswith(("D1", "D2", "D3")):
            return "oncology_neoplasms", "良性肿瘤与交界性增生"
        return "oncology_neoplasms", "实体恶性肿瘤与肉瘤"

    if "infectious disease" in cat_str or icd10.startswith(("A", "B")) or any(k in name_en for k in ("sepsis", "tuberculosis", "hepatitis", "malaria", "dengue", "rabies", "tetanus", "cholera", "ebola", "influenza", "hiv ", "infection", "bacterial ", "viral ", "fungal ", "parasit")):
        if any(k in name_en for k in ("sepsis", "meningit", "encephalit", "rabies", "ebola", "cholera", "tetanus", "pneumonia", "hemorrhagic fever")):
            return "infectious_parasitic", "急性致命感染与脓毒症"
        return "infectious_parasitic", "慢性、寄生虫与系统感染"

    if "cardiovascular disorder" in cat_str or icd10.startswith("I") or any(k in name_en for k in ("myocardial", "coronary", "heart failure", "cardiomyopathy", "arrhythmia", "atrial fibrillation", "aortic", "aneurysm", "stroke", "cerebrovascular", "embolism", "thrombosis", "hypertension", "valvular")):
        if any(k in name_en for k in ("embolism", "thrombosis", "infarction", "dissection", "stroke", "hemorrhage", "arrest", "rupture")):
            return "cardiovascular_thrombosis", "急性致死性血管与心脏急症"
        if any(k in name_en for k in ("cardiomyopathy", "arrhythmia", "brugada", "long qt", "congenital heart", "tetralogy", "septal defect", "fibrillation")):
            return "cardiovascular_thrombosis", "心律失常、心肌病与先心病"
        return "cardiovascular_thrombosis", "慢性心血管、心肌与血管病"

    if "hematologic disorder" in cat_str or icd10.startswith(("D5", "D6", "D7")):
        return "hematology_coagulation", "贫血、血细胞与凝血系统疾病"

    if "psychiatric disorder" in cat_str or icd10.startswith("F") or any(k in name_en for k in ("schizophrenia", "bipolar", "depressive", "autism", "anxiety disorder", "obsessive-compulsive", "eating disorder")):
        return "psychiatric_behavioral", "精神、情绪与神经发育障碍"

    if "respiratory system disorder" in cat_str or icd10.startswith("J") or any(k in name_en for k in ("asthma", "bronch", "pulmonary", "pneumon", "respiratory", "copd", "emphysema", "pleur", "interstitial lung")):
        return "respiratory_thoracic", "急慢性呼吸与肺血管病变"

    if "nervous system disorder" in cat_str or icd10.startswith("G") or any(k in name_en for k in ("epilepsy", "ataxia", "neuropath", "myopath", "muscular dystrophy", "alzheimer", "parkinson", "sclerosis", "encephalopathy", "dementia", "spastic paraplegia", "charcot-marie")):
        if any(k in name_en for k in ("epilep", "seizure", "encephalopathy")):
            return "neurological_cns", "癫痫与急性/发育性脑病"
        if any(k in name_en for k in ("neuropath", "myopath", "muscular dystrophy", "amyotrophic", "spinal muscular", "myasthenia")):
            return "neurological_cns", "神经肌肉与运动神经元病变"
        return "neurological_cns", "中枢神经变性与遗传性脑病"

    if "endocrine system disorder" in cat_str or "metabolic disease" in cat_str or "mitochondrial disease" in cat_str or icd10.startswith("E"):
        if "mitochondrial" in cat_str or any(k in name_en for k in ("mitochondrial", "glycogen", "lysosomal", "mucopolysaccharidosis", "inborn error", "phenylketonuria", "gaucher", "fabry", "pompe", "niemann")):
            return "endocrine_metabolic", "先天遗传性代谢与线粒体病"
        return "endocrine_metabolic", "糖尿病、甲状腺与内分泌病"

    if "digestive system disorder" in cat_str or icd10.startswith("K"):
        return "digestive_hepatobiliary", "胃肠、肝胆与胰腺疾病"

    if "urinary system disorder" in cat_str or icd10.startswith(("N0", "N1", "N2", "N3")):
        return "renal_urinary", "肾小球、肾小管与泌尿系统疾病"

    if "immune system disorder" in cat_str or "connective tissue disorder" in cat_str or icd10.startswith(("D8", "M3")):
        if "angioedema" in name_en or "anaphylaxis" in name_en or "immunodeficiency" in name_en:
            return "immune_rheumatic", "原发免疫缺陷与过敏危象"
        return "immune_rheumatic", "炎症性与系统性自身免疫病"

    if "reproductive system disorder" in cat_str or icd10.startswith(("N4", "N5", "N6", "N7", "N8", "N9", "O", "P")):
        return "reproductive_perinatal", "生殖系统、孕产与围产期病变"

    if any(k in cat_str for k in ("musculoskeletal", "integumentary", "visual system", "auditory system", "otorhinolaryngologic", "mouth disorder", "orbital region")) or icd10.startswith(("H", "L", "M")):
        if "visual" in cat_str or "orbital" in cat_str or icd10.startswith(("H0", "H1", "H2", "H3", "H4", "H5")):
            return "musculoskeletal_sensory_skin", "眼科与视觉系统疾病"
        if "auditory" in cat_str or "otorhinolaryngologic" in cat_str or icd10.startswith(("H6", "H7", "H8", "H9")):
            return "musculoskeletal_sensory_skin", "耳鼻咽喉与听觉系统疾病"
        if "integumentary" in cat_str or icd10.startswith("L"):
            return "musculoskeletal_sensory_skin", "皮肤与附属器疾病"
        return "musculoskeletal_sensory_skin", "骨关节与肌肉骨骼疾病"

    if "inflammatory disease" in cat_str:
        return "immune_rheumatic", "炎症性与系统性自身免疫病"

    if "multiple congenital anomalies" in cat_str or "disorder of development" in cat_str or "syndromic disease" in cat_str or icd10.startswith("Q"):
        return "rare_genetic_congenital", "多发畸形与发育遗传综合征"

    return "rare_genetic_congenital", "单基因/疑难罕见及未归类疾病"


def resolve_stage(row: dict[str, Any], curated: dict[str, Any] | None) -> str:
    if curated:
        ts = curated["treatability_status"]
        if ts in STAGE_LABELS:
            return ts
    ot = (row["highest_clinical_stage"] or "").upper()
    if ot in ("APPROVAL", "PREAPPROVAL"):
        return "approved_therapy"
    if ot in ("PHASE_3", "PHASE_2_3"):
        return "phase3_trials"
    if ot in ("PHASE_2", "PHASE_1_2"):
        return "phase2_trials"
    if ot in ("PHASE_1", "EARLY_PHASE_1", "IND"):
        return "phase1_trials"
    return "no_ot_drug_recorded"


def compute_domain_centers(domain_counts: dict[str, int]) -> dict[str, tuple[float, float, float]]:
    ordered = sorted(MACRO_DOMAINS, key=lambda d: domain_counts.get(d["id"], 0), reverse=True)
    centers: dict[str, tuple[float, float, float]] = {}
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    total = len(ordered)
    for idx, d in enumerate(ordered):
        did = d["id"]
        cnt = max(80, domain_counts.get(did, 80))
        spread = 95.0 + 180.0 * math.sqrt(cnt / 4800.0)
        if idx == 0:
            cx, cy = 0.0, 0.0
        else:
            r = 265.0 + 530.0 * math.sqrt(idx / float(total))
            theta = idx * golden_angle + 0.45
            cx = r * math.cos(theta)
            cy = r * math.sin(theta) * 0.88
        centers[did] = (cx, cy, spread)
    return centers


def build_atlas() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    curated_by_id: dict[int, dict[str, Any]] = {}
    curated_by_master: dict[str, dict[str, Any]] = {}
    for r in conn.execute("SELECT * FROM curated_dossiers"):
        d = dict(r)
        curated_by_id[d["id"]] = d
        if d["master_canonical_id"]:
            curated_by_master[d["master_canonical_id"]] = d

    master_rows = [dict(r) for r in conn.execute("""
        SELECT canonical_id, mondo_id, name_en, name_zh, icd10_primary, icd11_primary,
               orpha_id, omim_id, disease_type, mondo_categories_json,
               highest_clinical_stage, approved_drug_count, clinical_trial_count,
               orphanet_prevalence, is_rare, is_curated, curated_dossier_id
        FROM diseases_master
    """)]

    classified: list[dict[str, Any]] = []
    domain_counts: Counter[str] = Counter()
    subcluster_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for row in master_rows:
        cur = curated_by_id.get(row["curated_dossier_id"]) if row["curated_dossier_id"] else curated_by_master.get(row["canonical_id"])
        l1, l2 = classify_domain(row, cur)
        l3 = resolve_stage(row, cur)
        domain_counts[l1] += 1
        subcluster_counts[l1][l2] += 1
        classified.append({
            "id": row["canonical_id"],
            "curated_id": cur["id"] if cur else None,
            "name_en": cur["name_en"] if cur else row["name_en"],
            "name_zh": cur["name_zh"] if cur else row["name_zh"],
            "icd10": row["icd10_primary"],
            "orpha_id": row["orpha_id"],
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "is_curated": 1 if cur else 0,
            "is_rare": int(row["is_rare"] or 0),
            "course": cur["course_type"] if cur else None,
            "lethality": cur["lethality_speed"] if cur else None,
            "deaths_g": cur["annual_deaths_global_value"] if cur else None,
            "stage_raw": row["highest_clinical_stage"],
            "drugs": int(row["approved_drug_count"] or 0),
            "trials": int(row["clinical_trial_count"] or 0),
        })

    linked_cur_ids = {c["curated_id"] for c in classified if c["curated_id"]}
    for cid, cur in curated_by_id.items():
        if cid not in linked_cur_ids:
            icd10_list = json.loads(cur["icd10_json"] or "[]")
            pseudo = {
                "name_en": cur["name_en"],
                "name_zh": cur["name_zh"],
                "icd10_primary": icd10_list[0] if icd10_list else None,
                "mondo_categories_json": "[]",
            }
            l1, l2 = classify_domain(pseudo, cur)
            l3 = resolve_stage({"highest_clinical_stage": None}, cur)
            domain_counts[l1] += 1
            subcluster_counts[l1][l2] += 1
            classified.append({
                "id": cur["disease_id"] or f"CURATED:{cid}",
                "curated_id": cid,
                "name_en": cur["name_en"],
                "name_zh": cur["name_zh"],
                "icd10": icd10_list[0] if icd10_list else None,
                "orpha_id": None,
                "l1": l1,
                "l2": l2,
                "l3": l3,
                "is_curated": 1,
                "is_rare": 0,
                "course": cur["course_type"],
                "lethality": cur["lethality_speed"],
                "deaths_g": cur["annual_deaths_global_value"],
                "stage_raw": cur["highest_pipeline_stage"],
                "drugs": int(cur["drug_count"] or 0),
                "trials": 0,
            })

    centers = compute_domain_centers(domain_counts)
    sub_centers: dict[tuple[str, str], tuple[float, float, float]] = {}
    for l1, sc_counter in subcluster_counts.items():
        cx, cy, spread = centers[l1]
        subs = [k for k, _ in sc_counter.most_common()]
        n_sub = len(subs)
        for s_idx, s_name in enumerate(subs):
            if n_sub == 1:
                sx, sy = cx, cy
            else:
                ang = (2.0 * math.pi * s_idx / n_sub) + 0.35
                ring = spread * (0.45 if s_idx % 2 == 0 else 0.78)
                sx = cx + ring * math.cos(ang)
                sy = cy + ring * math.sin(ang)
            sub_spread = max(28.0, spread * 0.38 * math.sqrt(sc_counter[s_name] / max(1, domain_counts[l1])))
            sub_centers[(l1, s_name)] = (sx, sy, sub_spread)

    nodes_out: list[dict[str, Any]] = []
    for item in classified:
        sx, sy, sub_spread = sub_centers[(item["l1"], item["l2"])]
        u_ang = stable_hash_unit(item["id"], "ang") * 2.0 * math.pi
        u_rad = abs(stable_normal(item["id"], "rad")) * 0.58
        if item["is_curated"]:
            u_rad *= 0.65
        dx = sub_spread * u_rad * math.cos(u_ang) + stable_normal(item["id"], "jx") * 8.5
        dy = sub_spread * u_rad * math.sin(u_ang) + stable_normal(item["id"], "jy") * 8.5
        item["x"] = round(sx + dx, 2)
        item["y"] = round(sy + dy, 2)
        nodes_out.append(item)

    hierarchy_domains: list[dict[str, Any]] = []
    total_count = len(nodes_out)
    for d in sorted(MACRO_DOMAINS, key=lambda m: domain_counts.get(m["id"], 0), reverse=True):
        did = d["id"]
        d_nodes = [n for n in nodes_out if n["l1"] == did]
        if not d_nodes:
            continue
        cx = round(sum(n["x"] for n in d_nodes) / len(d_nodes), 2)
        cy = round(sum(n["y"] for n in d_nodes) / len(d_nodes), 2)
        stage_dist = Counter(n["l3"] for n in d_nodes)
        sub_list = []
        for s_name, s_cnt in subcluster_counts[did].most_common():
            s_nodes = [n for n in d_nodes if n["l2"] == s_name]
            scx = round(sum(n["x"] for n in s_nodes) / len(s_nodes), 2)
            scy = round(sum(n["y"] for n in s_nodes) / len(s_nodes), 2)
            s_stages = Counter(n["l3"] for n in s_nodes)
            sub_list.append({
                "name": s_name,
                "count": s_cnt,
                "share_in_domain": round(s_cnt * 100.0 / len(d_nodes), 2),
                "curated_count": sum(n["is_curated"] for n in s_nodes),
                "rare_count": sum(n["is_rare"] for n in s_nodes),
                "cx": scx,
                "cy": scy,
                "stages": {k: s_stages[k] for k in STAGE_LABELS if s_stages[k] > 0},
            })
        hierarchy_domains.append({
            "id": did,
            "name_zh": d["name_zh"],
            "name_en": d["name_en"],
            "color": d["color"],
            "count": len(d_nodes),
            "share_pct": round(len(d_nodes) * 100.0 / total_count, 2),
            "curated_count": sum(n["is_curated"] for n in d_nodes),
            "rare_count": sum(n["is_rare"] for n in d_nodes),
            "approved_or_curable_count": sum(1 for n in d_nodes if n["l3"] in ("curable", "controllable", "approved_therapy")),
            "in_trials_count": sum(1 for n in d_nodes if n["l3"] in ("phase3_trials", "phase2_trials", "phase1_trials")),
            "cx": cx,
            "cy": cy,
            "stages": {k: stage_dist[k] for k in STAGE_LABELS if stage_dist[k] > 0},
            "subclusters": sub_list,
        })

    compact_nodes = {
        "fields": [
            "id", "x", "y", "l1", "l2", "l3",
            "name_en", "name_zh", "icd10", "orpha_id",
            "is_curated", "is_rare", "course", "lethality",
            "deaths_g", "drugs", "trials", "curated_id"
        ],
        "rows": [
            [
                n["id"], n["x"], n["y"], n["l1"], n["l2"], n["l3"],
                n["name_en"], n["name_zh"], n["icd10"], n["orpha_id"],
                n["is_curated"], n["is_rare"], n["course"], n["lethality"],
                n["deaths_g"], n["drugs"], n["trials"], n["curated_id"]
            ]
            for n in nodes_out
        ]
    }
    (OUT_DIR / "atlas_nodes.json").write_text(json.dumps(compact_nodes, ensure_ascii=False), encoding="utf-8")
    (OUT_DIR / "atlas_hierarchy.json").write_text(
        json.dumps({"total_diseases": total_count, "domains": hierarchy_domains, "stage_meta": STAGE_LABELS}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Build WHO Mortality Flow Dataset with exact hierarchical parent_code (L0 -> L1 -> L2 -> L3)
    flow_out: dict[str, Any] = {"years": list(range(2000, 2024)), "locations": {}}
    for loc in ("WRLD", "CHN"):
        rows = [dict(r) for r in conn.execute("""
            SELECT year, CAST(cause_code AS INTEGER) AS cc_int, cause_code, cause_name_en, cause_name_zh,
                   cause_level, cause_group_id, cause_group_name,
                   is_single_cause, is_rankable, deaths, pct_total_deaths
            FROM who_mortality
            WHERE source='WHO_GHE' AND location_code=? AND sex='TOTAL' AND year BETWEEN 2000 AND 2023
            ORDER BY year, cc_int
        """, (loc,))]

        # Derive exact L0/L1/L2/L3 parent links from 2023 ordered cause_code sequence
        ordered_meta = []
        seen_codes = set()
        for r in rows:
            cc = str(r["cause_code"])
            if r["year"] == 2023 and cc not in seen_codes:
                seen_codes.add(cc)
                ordered_meta.append(r)

        cur_l1: str | None = None
        cur_l2: str | None = None
        parent_map: dict[str, str | None] = {}
        for r in ordered_meta:
            cc = str(r["cause_code"])
            lvl = int(r["cause_level"])
            if lvl == 0:
                parent_map[cc] = None
            elif lvl == 1:
                parent_map[cc] = "0"
                cur_l1 = cc
                cur_l2 = None
            elif lvl == 2:
                parent_map[cc] = cur_l1
                cur_l2 = cc
            elif lvl == 3:
                parent_map[cc] = cur_l2 if cur_l2 else cur_l1
            else:
                parent_map[cc] = cur_l2 if cur_l2 else cur_l1

        causes_meta: dict[str, dict[str, Any]] = {}
        series_deaths: dict[str, dict[int, float]] = defaultdict(dict)
        series_pct: dict[str, dict[int, float]] = defaultdict(dict)
        for r in rows:
            cc = str(r["cause_code"])
            if cc not in causes_meta:
                en = r["cause_name_en"]
                zh = r["cause_name_zh"] or WHO_ZH_EXTENDED.get(en)
                causes_meta[cc] = {
                    "code": cc,
                    "cc_int": r["cc_int"],
                    "name_en": en,
                    "name_zh": zh,
                    "level": int(r["cause_level"]),
                    "parent_code": parent_map.get(cc),
                    "group_name": r["cause_group_name"],
                    "is_rankable": int(r["is_rankable"] or 0),
                }
            series_deaths[cc][r["year"]] = round(float(r["deaths"] or 0.0), 1)
            series_pct[cc][r["year"]] = round(float(r["pct_total_deaths"] or 0.0), 2)

        flow_out["locations"][loc] = {
            "causes": list(causes_meta.values()),
            "deaths": {cc: [series_deaths[cc].get(y, 0.0) for y in range(2000, 2024)] for cc in causes_meta},
            "pct": {cc: [series_pct[cc].get(y, 0.0) for y in range(2000, 2024)] for cc in causes_meta},
        }
    (OUT_DIR / "flow_who.json").write_text(json.dumps(flow_out, ensure_ascii=False), encoding="utf-8")

    pol_path = OUT_DIR / "policy_overview.json"
    if pol_path.exists():
        pol_path.unlink()

    conn.close()
    print(f"Prepared atlas: {total_count} nodes across {len(hierarchy_domains)} domains -> {OUT_DIR}")


if __name__ == "__main__":
    build_atlas()
