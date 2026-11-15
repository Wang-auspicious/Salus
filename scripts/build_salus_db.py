#!/usr/bin/env python3
"""
Salus Global Disease & Treatment Knowledge Base — Unified SQLite Builder.

Integrates:
1. Global disease backbone: MONDO + Orphanet + Open Targets -> `diseases_master`
2. Deeply curated bilingual dossiers: `data/curated/*.json` -> `curated_dossiers`, `curated_drugs`
3. Global & China cause-of-death mortality: WHO GHE 2000-2023 + GHO -> `who_mortality`
4. Bilingual Chinese + English + ICD-10/11 FTS5 full-text search -> `diseases_fts`
5. Build & coverage metrics -> `data/salus_summary.json`
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CURATED_DIR = DATA_DIR / "curated"
DEFAULT_DB_PATH = DATA_DIR / "salus.db"
DEFAULT_SUMMARY_PATH = DATA_DIR / "salus_summary.json"

CJK_SEQ_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]+")
ALNUM_CODE_RE = re.compile(r"[A-Za-z0-9]+(?:[._:-][A-Za-z0-9]+)*")

STAGE_RANK = {
    "APPROVAL": 10,
    "PREAPPROVAL": 9,
    "PHASE_3": 8,
    "PHASE_2_3": 7,
    "PHASE_2": 6,
    "PHASE_1_2": 5,
    "PHASE_1": 4,
    "EARLY_PHASE_1": 3,
    "IND": 2,
    "PRECLINICAL": 1,
    "UNKNOWN": 0,
}

NON_DISEASE_OT_AREAS = {
    "EFO_0001444",  # measurement
    "EFO_0000651",  # phenotype
    "GO_0008150",   # biological_process
    "EFO_0002571",  # medical procedure
    "OTAR_0000018", # genetic, familial or congenital? wait check: keep genetic!
    "MONDO_0005583",# non-human animal disease
}
# Note: OTAR_0000018 is "genetic, familial or congenital disease" - genuine disease area!
PURE_NON_DISEASE_OT_AREAS = {
    "EFO_0001444",
    "EFO_0000651",
    "GO_0008150",
    "EFO_0002571",
    "MONDO_0005583",
}

MONDO_CATEGORY_MAP = [
    ("cancer or benign tumor", "oncology"),
    ("infectious disease", "infectious"),
    ("cardiovascular disorder", "cardiovascular"),
    ("nervous system disorder", "neurological"),
    ("psychiatric disorder", "psychiatric"),
    ("respiratory system disorder", "respiratory"),
    ("endocrine system disorder", "endocrine_metabolic"),
    ("metabolic disease", "endocrine_metabolic"),
    ("immune system disorder", "immune_autoimmune"),
    ("hematologic disorder", "hematologic"),
    ("digestive system disorder", "digestive"),
    ("urinary system disorder", "renal_urinary"),
    ("musculoskeletal system disorder", "musculoskeletal"),
    ("integumentary system disorder", "dermatologic"),
    ("disorder of visual system", "ophthalmologic"),
    ("reproductive system disorder", "reproductive"),
    ("disease of genetic or genomic mechanism", "rare_genetic"),
    ("syndromic disease", "rare_genetic"),
]

# Bilingual clinical index hints mapping common Chinese disease terms -> (curated_zh_hints, english_hints, icd10_hints)
BILINGUAL_CLINICAL_HINTS: dict[str, tuple[list[str], list[str], list[str]]] = {
    "再生障碍性贫血": (["再生障碍性贫血"], ["aplastic anemia"], ["D61"]),
    "血友病": (["血友病A", "血友病B", "血友病"], ["hemophilia a", "hemophilia b", "hemophilia"], ["D66", "D67"]),
    "重症肌无力": (["重症肌无力"], ["myasthenia gravis"], ["G70.0"]),
    "系统性红斑狼疮": (["系统性红斑狼疮"], ["systemic lupus erythematosus"], ["M32"]),
    "癫痫": (["癫痫及癫痫持续状态", "癫痫"], ["epilepsy"], ["G40"]),
    "冠心病": (["冠状动脉粥样硬化性心脏病", "冠心病"], ["coronary artery disease", "coronary heart disease"], ["I25"]),
    "糖尿病": (["2型糖尿病", "1型糖尿病"], ["type 2 diabetes mellitus", "diabetes mellitus"], ["E11", "E10"]),
    "肝豆状核变性": (["肝豆状核变性", "Wilson病"], ["wilson disease", "hepatolenticular degeneration"], ["E83.0"]),
    "股骨头坏死": ([], ["osteonecrosis", "avascular necrosis of femoral head", "femoral head necrosis"], ["M87"]),
    "类风湿性关节炎": (["类风湿关节炎"], ["rheumatoid arthritis"], ["M05", "M06"]),
    "慢性心力衰竭": (["慢性心力衰竭", "心力衰竭"], ["heart failure", "chronic heart failure"], ["I50"]),
    "慢性阻塞性肺疾病": (["慢性阻塞性肺疾病"], ["chronic obstructive pulmonary disease"], ["J44"]),
    "肺源性心脏病": ([], ["pulmonary heart disease", "cor pulmonale"], ["I27.9"]),
    "慢性肾脏病": (["慢性肾脏病与终末期肾病", "慢性肾脏病"], ["chronic kidney disease"], ["N18"]),
    "原发性血小板增多症": ([], ["essential thrombocythemia", "essential thrombocytosis"], ["D47.3"]),
    "肝硬化": (["失代偿期肝硬化", "肝硬化"], ["liver cirrhosis", "cirrhosis of liver"], ["K74"]),
    "白血病": (["急性髓系白血病", "急性淋巴细胞白血病", "慢性粒细胞白血病"], ["leukemia", "acute myeloid leukemia"], ["C92", "C91"]),
    "运动神经元病": (["肌萎缩侧索硬化"], ["motor neuron disease", "amyotrophic lateral sclerosis"], ["G12.2"]),
    "肾病综合征": ([], ["nephrotic syndrome"], ["N04"]),
    "肺间质纤维化": (["特发性肺纤维化"], ["idiopathic pulmonary fibrosis", "pulmonary fibrosis"], ["J84.1"]),
    "原发性骨髓纤维化": ([], ["primary myelofibrosis", "myelofibrosis"], ["D47.4"]),
    "骨髓增生异常综合征": (["骨髓增生异常综合征"], ["myelodysplastic syndrome", "myelodysplastic syndromes"], ["D46"]),
    "生长激素缺乏症": ([], ["growth hormone deficiency", "isolated growth hormone deficiency"], ["E23.0"]),
    "系统性硬化症": (["系统性硬化症"], ["systemic sclerosis", "scleroderma"], ["M34"]),
    "进行性肌营养不良": (["杜氏肌营养不良"], ["duchenne muscular dystrophy", "muscular dystrophy"], ["G71.0"]),
    "多发性肌炎": ([], ["polymyositis", "dermatomyositis"], ["M33"]),
    "系统性血管炎": ([], ["systemic vasculitis", "vasculitis"], ["M31"]),
    "脑出血": (["脑出血（出血性脑卒中）", "出血性脑卒中"], ["intracerebral hemorrhage", "hemorrhagic stroke"], ["I61"]),
    "脑梗死": (["急性缺血性脑卒中", "缺血性脑卒中"], ["ischemic stroke", "cerebral infarction"], ["I63"]),
    "真性红细胞增多症": ([], ["polycythemia vera"], ["D45"]),
    "高血压病伴并发症": (["原发性高血压"], ["essential hypertension", "hypertensive heart disease", "hypertension"], ["I10", "I11"]),
    "高血压": (["原发性高血压"], ["essential hypertension", "hypertension"], ["I10"]),
    "阿尔茨海默": (["阿尔茨海默病"], ["alzheimer disease", "alzheimer's disease"], ["G30"]),
    "干燥综合征": ([], ["sjogren syndrome", "sjögren syndrome"], ["M35.0"]),
    "慢性丙型病毒性肝炎": (["慢性丙型病毒性肝炎", "丙型肝炎"], ["chronic hepatitis c", "hepatitis c"], ["B18.2"]),
    "慢性乙型病毒性肝炎": (["慢性乙型病毒性肝炎", "慢性乙型肝炎"], ["chronic hepatitis b", "hepatitis b"], ["B18.1"]),
    "强直性脊柱炎": (["强直性脊柱炎"], ["ankylosing spondylitis"], ["M45"]),
    "脊柱关节炎": (["强直性脊柱炎"], ["ankylosing spondylitis", "spondyloarthritis"], ["M45"]),
    "耐多药结核": (["肺结核"], ["multidrug-resistant tuberculosis", "pulmonary tuberculosis"], ["A15"]),
    "广泛耐药结核": (["肺结核"], ["pulmonary tuberculosis", "tuberculosis"], ["A15"]),
    "肺外其他部位结核": (["肺结核"], ["tuberculosis", "extrapulmonary tuberculosis"], ["A18"]),
    "肺结核": (["肺结核"], ["pulmonary tuberculosis", "tuberculosis"], ["A15"]),
    "人类免疫缺陷": (["艾滋病"], ["hiv infectious disease", "hiv-1 infection", "acquired immunodeficiency syndrome"], ["B20"]),
    "恶性肿瘤": (["肺癌", "乳腺癌", "胃癌"], ["cancer", "malignant neoplasm"], ["C80"]),
    "神经系统良性肿瘤": ([], ["benign neoplasm of nervous system", "meningioma", "schwannoma"], ["D33"]),
    "地中海贫血": (["重型β-地中海贫血"], ["beta-thalassemia", "thalassemia"], ["D56"]),
    "帕金森病": (["帕金森病"], ["parkinson disease", "parkinson's disease"], ["G20"]),
    "视神经脊髓炎": (["视神经脊髓炎谱系疾病"], ["neuromyelitis optica", "neuromyelitis optica spectrum disorder"], ["G36.0"]),
    "原发性酪氨酸血症": ([], ["tyrosinemia", "hereditary tyrosinemia"], ["E70.2"]),
    "发作性睡病": ([], ["narcolepsy"], ["G47.4"]),
    "迟发性运动障碍": ([], ["tardive dyskinesia", "dyskinesia"], ["G24.0"]),
    "葡萄膜炎": ([], ["uveitis", "non-infectious uveitis"], ["H20"]),
    "肠易激综合征": ([], ["irritable bowel syndrome"], ["K58"]),
    "结节性硬化症": (["结节性硬化症"], ["tuberous sclerosis complex", "tuberous sclerosis"], ["Q85.1"]),
    "血小板无力症": ([], ["glanzmann thrombasthenia"], ["D69.1"]),
    "非典型溶血性尿毒症综合征": ([], ["atypical hemolytic uremic syndrome", "hemolytic uremic syndrome"], ["D59.3"]),
    "免疫性血小板减少": ([], ["immune thrombocytopenia", "idiopathic thrombocytopenic purpura"], ["D69.3"]),
    "银屑病": ([], ["psoriasis"], ["L40"]),
    "克罗恩病": (["克罗恩病"], ["crohn disease", "crohn's disease"], ["K50"]),
    "阵发性睡眠性血红蛋白尿": ([], ["paroxysmal nocturnal hemoglobinuria"], ["D59.5"]),
    "castleman病": (["Castleman病"], ["castleman disease"], ["D47.7"]),
    "west综合征": ([], ["west syndrome", "infantile spasms"], ["G40.8"]),
    "多发性硬化": (["多发性硬化"], ["multiple sclerosis"], ["G35"]),
    "肺动脉高压": (["肺动脉高压"], ["pulmonary arterial hypertension", "pulmonary hypertension"], ["I27.0"]),
    "肢端肥大症": ([], ["acromegaly"], ["E22.0"]),
    "c型尼曼匹克病": (["C型尼曼匹克病"], ["niemann-pick disease type c"], ["E75.2"]),
    "子宫内膜异位症": ([], ["endometriosis"], ["N80"]),
    "特应性皮炎": ([], ["atopic dermatitis"], ["L20"]),
    "溃疡性结": (["溃疡性结肠炎"], ["ulcerative colitis"], ["K51"]),
    "戈谢病": (["戈谢病"], ["gaucher disease"], ["E75.2"]),
    "法布雷病": (["法布雷病"], ["fabry disease"], ["E75.2"]),
    "亨廷顿": (["亨廷顿病"], ["huntington disease", "huntington's disease"], ["G10"]),
    "转甲状腺素蛋白淀粉样变性心肌病": ([], ["transthyretin amyloidosis", "hereditary transthyretin-related amyloidosis"], ["E85.1"]),
    "脊髓性肌萎缩症": (["脊髓性肌萎缩症"], ["spinal muscular atrophy"], ["G12.0"]),
    "遗传性血管性水肿": (["遗传性血管性水肿"], ["hereditary angioedema"], ["D84.1"]),
    "先天性凝血因子": ([], ["factor vii deficiency", "congenital factor vii deficiency"], ["D68.2"]),
    "重度哮喘": (["支气管哮喘", "重症哮喘持续状态"], ["severe asthma", "asthma"], ["J45"]),
    "支气管哮喘": (["支气管哮喘"], ["asthma", "bronchial asthma"], ["J45"]),
    "苯丙酮尿症": (["苯丙酮尿症"], ["phenylketonuria"], ["E70.0"]),
    "庞贝": (["庞贝病"], ["pompe disease", "glycogen storage disease type ii"], ["E74.0"]),
    "黄斑病变": ([], ["macular degeneration", "age-related macular degeneration"], ["H35.3"]),
    "风湿性心脏病": ([], ["rheumatic heart disease"], ["I09"]),
    "甲状腺功能减退症": ([], ["hypothyroidism"], ["E03"]),
    "甲状腺功能亢进症": (["甲状腺功能亢进"], ["hyperthyroidism", "graves disease"], ["E05"]),
    "脑瘫": ([], ["cerebral palsy"], ["G80"]),
    "精神分裂症": (["精神分裂症"], ["schizophrenia"], ["F20"]),
    "严重精神障碍": (["精神分裂症"], ["schizophrenia", "psychotic disorder"], ["F20"]),
    "双相情感障碍": (["双相情感障碍"], ["bipolar disorder"], ["F31"]),
    "抑郁症": (["重度抑郁障碍"], ["major depressive disorder", "depressive disorder"], ["F32"]),
    "白塞病": (["贝赫切特病"], ["behcet disease", "behcet syndrome"], ["M35.2"]),
    "心房颤动": (["心房颤动"], ["atrial fibrillation"], ["I48"]),
    "支气管扩张": (["支气管扩张症"], ["bronchiectasis"], ["J47"]),
    "尘肺": (["尘肺病"], ["pneumoconiosis"], ["J64"]),
    "痛风": (["高尿酸血症与痛风"], ["gout", "hyperuricemia"], ["M10"]),
    "溶血性贫血": ([], ["hemolytic anemia"], ["D59"]),
    "原发性醛固酮增多症": ([], ["primary hyperaldosteronism", "hyperaldosteronism"], ["E26.0"]),
    "慢性肾炎": ([], ["chronic glomerulonephritis", "glomerulonephritis"], ["N03"]),
    "过敏性紫癜": ([], ["henoch-schoenlein purpura", "iga vasculitis"], ["D69.0"]),
    "尿崩症": ([], ["diabetes insipidus"], ["E23.2"]),
    "自身免疫性肝病": ([], ["autoimmune hepatitis"], ["K75.4"]),
    "脊髓空洞症": ([], ["syringomyelia"], ["G95.0"]),
    "孤独症": ([], ["autism spectrum disorder", "autism"], ["F84.0"]),
    "骨关节炎": ([], ["osteoarthritis"], ["M19"]),
    "皮质醇增多症": ([], ["cushing syndrome"], ["E24"]),
    "慢性病毒性肝炎": (["慢性乙型病毒性肝炎", "慢性丙型病毒性肝炎"], ["chronic viral hepatitis", "viral hepatitis"], ["B18"]),
    "风湿性关节炎": (["类风湿关节炎"], ["rheumatic fever", "rheumatoid arthritis"], ["I00"]),
}

WHO_CAUSE_ZH_MAP: dict[str, str] = {
    "All Causes": "全因死亡",
    "Communicable, maternal, perinatal and nutritional conditions": "传染病、孕产妇、围产期及营养性疾病",
    "Noncommunicable diseases": "非传染性疾病",
    "Injuries": "伤害",
    "Ischaemic heart disease": "缺血性心脏病",
    "Stroke": "脑卒中",
    "Chronic obstructive pulmonary disease": "慢性阻塞性肺疾病",
    "Lower respiratory infections": "下呼吸道感染",
    "Trachea, bronchus, lung cancers": "气管、支气管和肺癌",
    "Alzheimer disease and other dementias": "阿尔茨海默病及其他痴呆症",
    "Diabetes mellitus": "糖尿病",
    "Kidney diseases": "肾脏疾病",
    "Diarrhoeal diseases": "腹泻病",
    "Tuberculosis": "结核病",
    "HIV/AIDS": "艾滋病",
    "Malaria": "疟疾",
    "Hypertensive heart disease": "高血压性心脏病",
    "Cirrhosis of the liver": "肝硬化",
    "Stomach cancer": "胃癌",
    "Liver cancer": "肝癌",
    "Colon and rectum cancers": "结直肠癌",
    "Breast cancer": "乳腺癌",
    "Oesophagus cancer": "食管癌",
    "Pancreas cancer": "胰腺癌",
    "Leukaemia": "白血病",
    "Lymphomas, multiple myeloma": "淋巴瘤与多发性骨髓瘤",
    "Asthma": "支气管哮喘",
    "Cardiomyopathy, myocarditis, endocarditis": "心肌病、心肌炎与心内膜炎",
    "Rheumatic heart disease": "风湿性心脏病",
    "Hepatitis B": "乙型肝炎",
    "Hepatitis C": "丙型肝炎",
    "Parkinson disease": "帕金森病",
    "Epilepsy": "癫痫",
    "Road injury": "道路交通伤害",
    "Self-harm": "自伤",
    "Falls": "跌倒",
    "COVID-19": "新型冠状病毒感染",
    "Neonatal conditions": "新生儿疾病",
    "Maternal conditions": "孕产妇疾病",
}


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def clean_str(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def clean_icd10(raw_code: str) -> str:
    s = str(raw_code).strip()
    for prefix in ("ICD10CM:", "ICD10WHO:", "ICD10:", "ICD-10:"):
        if s.upper().startswith(prefix):
            s = s[len(prefix) :]
            break
    return s.strip().upper()


def clean_icd11(raw_code: str) -> str:
    s = str(raw_code).strip()
    for prefix in ("ICD11:", "ICD-11:", "icd11.foundation:"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix) :]
            break
    return s.strip()


def clean_omim(raw_code: str) -> str:
    s = str(raw_code).strip().upper()
    for prefix in ("OMIM:", "OMIMPS:"):
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return f"OMIM:{s}" if s else ""


def clean_orpha(raw_code: str) -> str:
    s = str(raw_code).strip().upper()
    if s.startswith("ORPHA:"):
        return s
    if s.startswith("ORPHANET:"):
        return "ORPHA:" + s.split(":", 1)[1]
    return f"ORPHA:{s}" if s else ""


def dedupe_list(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        s = str(item).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def normalize_en(text: str | None) -> str:
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_zh(text: str | None) -> str:
    if not text:
        return ""
    s = text.strip()
    s = s.replace("综合症", "综合征").replace("阿尔茨海默症", "阿尔茨海默病")
    s = re.sub(r"[（(][^）)]*[）)]", "", s)
    s = re.sub(r"[\s·•、，,。:：;；\-_/]+", "", s)
    return s.lower()


def build_cjk_search_tokens(*texts: str | None) -> str:
    """Emit FTS5 unicode61 tokens with CJK 1..4-grams + dot-stripped ICD codes."""
    tokens: list[str] = []
    seen: set[str] = set()

    def add_tok(tok: str) -> None:
        if tok and tok not in seen:
            seen.add(tok)
            tokens.append(tok)

    for raw in texts:
        if not raw:
            continue
        text = str(raw).strip()
        if not text:
            continue
        for seq in CJK_SEQ_RE.findall(text):
            add_tok(seq)
            n = len(seq)
            for size in (1, 2, 3, 4):
                if size <= n:
                    for i in range(n - size + 1):
                        add_tok(seq[i : i + size])
        for code in ALNUM_CODE_RE.findall(text):
            add_tok(code)
            if "." in code:
                add_tok(code.replace(".", ""))
                add_tok(code.split(".", 1)[0])
            if ":" in code or "-" in code or "_" in code:
                for part in re.split(r"[:_-]+", code):
                    if part:
                        add_tok(part)
    return " ".join(tokens)


def infer_disease_type(
    top_level_names: list[str],
    is_rare: bool,
    name_en: str,
) -> str:
    tl_lower = [t.lower() for t in top_level_names]
    for label, mapped in MONDO_CATEGORY_MAP:
        if label in tl_lower:
            return mapped
    nl = name_en.lower()
    if any(k in nl for k in ("carcinoma", "sarcoma", "leukemia", "lymphoma", "melanoma", "glioma", "myeloma", "cancer")):
        return "oncology"
    if any(k in nl for k in ("infection", "tuberculosis", "hepatitis", "sepsis", "meningitis", "influenza", "rabies")):
        return "infectious"
    if is_rare:
        return "rare_genetic"
    return "general"


def create_schema(conn: sqlite3.Connection) -> None:
    """Create normalized tables, indexes, and FTS5 index."""
    conn.executescript(
        """
        PRAGMA journal_mode = DELETE;
        PRAGMA synchronous = NORMAL;
        PRAGMA foreign_keys = ON;

        DROP TABLE IF EXISTS diseases_fts;
        DROP TABLE IF EXISTS curated_drugs;
        DROP TABLE IF EXISTS curated_dossiers;
        DROP TABLE IF EXISTS who_mortality;
        DROP TABLE IF EXISTS diseases_master;
        DROP TABLE IF EXISTS build_metadata;

        CREATE TABLE build_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE diseases_master (
            canonical_id TEXT PRIMARY KEY,
            mondo_id TEXT,
            name_en TEXT NOT NULL,
            name_zh TEXT,
            definition_en TEXT,
            synonyms_en_json TEXT,
            synonyms_zh_json TEXT,
            icd10_json TEXT,
            icd11_json TEXT,
            icd10_primary TEXT,
            icd11_primary TEXT,
            orpha_id TEXT,
            orpha_ids_json TEXT,
            omim_id TEXT,
            omim_ids_json TEXT,
            efo_id TEXT,
            disease_type TEXT NOT NULL,
            mondo_categories_json TEXT,
            highest_clinical_stage TEXT,
            highest_clinical_stage_rank INTEGER DEFAULT 0,
            approved_drug_count INTEGER DEFAULT 0,
            clinical_trial_count INTEGER DEFAULT 0,
            top_drugs_json TEXT,
            orphanet_prevalence TEXT,
            orphanet_prevalence_json TEXT,
            inheritance TEXT,
            age_of_onset TEXT,
            genes TEXT,
            genes_json TEXT,
            is_rare INTEGER DEFAULT 0,
            is_curated INTEGER DEFAULT 0,
            curated_dossier_id INTEGER,
            source_flags TEXT
        );

        CREATE INDEX idx_master_mondo ON diseases_master(mondo_id);
        CREATE INDEX idx_master_orpha ON diseases_master(orpha_id);
        CREATE INDEX idx_master_omim ON diseases_master(omim_id);
        CREATE INDEX idx_master_efo ON diseases_master(efo_id);
        CREATE INDEX idx_master_icd10 ON diseases_master(icd10_primary);
        CREATE INDEX idx_master_name_en ON diseases_master(name_en);
        CREATE INDEX idx_master_name_zh ON diseases_master(name_zh);
        CREATE INDEX idx_master_type ON diseases_master(disease_type);
        CREATE INDEX idx_master_stage ON diseases_master(highest_clinical_stage);
        CREATE INDEX idx_master_curated ON diseases_master(is_curated);

        CREATE TABLE curated_dossiers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL,
            source_file TEXT NOT NULL,
            curator TEXT,
            generated_at TEXT,
            disease_id TEXT,
            master_canonical_id TEXT REFERENCES diseases_master(canonical_id),
            match_method TEXT,
            name_zh TEXT NOT NULL,
            name_en TEXT NOT NULL,
            aliases_zh_json TEXT,
            icd10_json TEXT,
            icd11_json TEXT,
            category TEXT,
            course_type TEXT NOT NULL,
            lethality_speed TEXT NOT NULL,
            lethality_detail_zh TEXT NOT NULL,
            treatability_status TEXT NOT NULL,
            treatability_summary_zh TEXT NOT NULL,
            annual_deaths_global_value REAL,
            annual_deaths_global_note TEXT,
            annual_deaths_global_year TEXT,
            annual_deaths_global_source TEXT,
            annual_deaths_china_value REAL,
            annual_deaths_china_note TEXT,
            annual_deaths_china_year TEXT,
            annual_deaths_china_source TEXT,
            prevalence_global TEXT,
            prevalence_china TEXT,
            acute_emergency_protocol_zh TEXT,
            non_drug_therapies_json TEXT,
            annual_total_cost_china_cny TEXT,
            highest_pipeline_stage TEXT,
            breakthroughs_and_trials_zh TEXT,
            key_unmet_needs_zh TEXT,
            drug_count INTEGER DEFAULT 0,
            nrdl_covered_drug_count INTEGER DEFAULT 0,
            sources_json TEXT NOT NULL,
            epidemiology_json TEXT NOT NULL,
            clinical_management_json TEXT NOT NULL,
            research_progress_json TEXT NOT NULL,
            raw_record_json TEXT NOT NULL
        );

        CREATE INDEX idx_curated_domain ON curated_dossiers(domain);
        CREATE INDEX idx_curated_master ON curated_dossiers(master_canonical_id);
        CREATE INDEX idx_curated_name_zh ON curated_dossiers(name_zh);
        CREATE INDEX idx_curated_name_en ON curated_dossiers(name_en);
        CREATE INDEX idx_curated_course ON curated_dossiers(course_type);
        CREATE INDEX idx_curated_lethality ON curated_dossiers(lethality_speed);
        CREATE INDEX idx_curated_treatability ON curated_dossiers(treatability_status);

        CREATE TABLE curated_drugs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dossier_id INTEGER NOT NULL REFERENCES curated_dossiers(id) ON DELETE CASCADE,
            name_zh TEXT NOT NULL,
            name_en TEXT NOT NULL,
            drug_class TEXT,
            usage_role TEXT,
            china_nrdl_covered INTEGER NOT NULL,
            cost_estimate_cny TEXT,
            cost_source TEXT
        );

        CREATE INDEX idx_drugs_dossier ON curated_drugs(dossier_id);
        CREATE INDEX idx_drugs_nrdl ON curated_drugs(china_nrdl_covered);

        CREATE TABLE who_mortality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            indicator_code TEXT NOT NULL,
            location_code TEXT NOT NULL,
            year INTEGER NOT NULL,
            sex TEXT,
            cause_code TEXT NOT NULL,
            cause_name_en TEXT NOT NULL,
            cause_name_zh TEXT,
            cause_level INTEGER,
            cause_group_id INTEGER,
            cause_group_name TEXT,
            is_single_cause INTEGER,
            is_rankable INTEGER,
            population REAL,
            deaths REAL,
            deaths_global REAL,
            deaths_low REAL,
            deaths_high REAL,
            death_rate_per_100k REAL,
            pct_total_deaths REAL,
            dalys REAL,
            dalys_rate_per_100k REAL,
            ylls REAL,
            ylds REAL,
            display_value TEXT
        );

        CREATE INDEX idx_who_loc_year ON who_mortality(location_code, year);
        CREATE INDEX idx_who_cause ON who_mortality(cause_code);
        CREATE INDEX idx_who_name ON who_mortality(cause_name_en);
        CREATE INDEX idx_who_source ON who_mortality(source);

        CREATE VIRTUAL TABLE diseases_fts USING fts5(
            record_type,
            record_id,
            canonical_id,
            name_zh,
            name_en,
            aliases,
            icd_codes,
            category,
            summary_text,
            search_tokens,
            tokenize="unicode61"
        );
        """
    )
    conn.commit()


def build_master_catalog(
    mondo_data: dict[str, Any],
    orphanet_data: dict[str, Any],
    ot_data: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Merge MONDO human diseases, Orphanet rare diseases, and Open Targets drugs/stages."""
    master: dict[str, dict[str, Any]] = {}
    orpha_to_canon: dict[str, str] = {}
    efo_to_canon: dict[str, str] = {}
    omim_to_canons: dict[str, list[str]] = {}
    name_to_canon: dict[str, str] = {}
    exact_syn_to_canon: dict[str, str] = {}

    # 1. Base: MONDO human diseases (is_human_disease == True)
    for d in mondo_data["diseases"]:
        if not d.get("is_human_disease", True):
            continue
        mid = d["mondo_id"]
        icd10_list = dedupe_list([clean_icd10(x) for x in d.get("icd10", [])])
        icd11_list = dedupe_list([clean_icd11(x) for x in d.get("icd11", [])])
        omim_list = dedupe_list([clean_omim(x) for x in d.get("omim", [])])
        orpha_list = dedupe_list([clean_orpha(x) for x in d.get("orphanet", [])])
        efo_raw = d.get("xrefs", {}).get("efo", [])
        efo_list = dedupe_list([f"EFO:{e}" for e in efo_raw])
        syns = [s["text"] for s in d.get("synonyms", []) if s.get("text")]
        tl_names = [t["name"] for t in d.get("top_level_categories", []) if t.get("name")]

        row = {
            "canonical_id": mid,
            "mondo_id": mid,
            "name_en": d["name"].strip(),
            "name_zh": None,
            "definition_en": clean_str(d.get("definition")),
            "synonyms_en": dedupe_list(syns),
            "synonyms_zh": [],
            "icd10": icd10_list,
            "icd11": icd11_list,
            "orpha_ids": orpha_list,
            "omim_ids": omim_list,
            "efo_id": efo_list[0] if efo_list else None,
            "mondo_categories": dedupe_list(tl_names + d.get("parent_names", [])),
            "top_level_names": tl_names,
            "disease_type": infer_disease_type(tl_names, bool(d.get("is_rare")), d["name"]),
            "highest_clinical_stage": None,
            "highest_clinical_stage_rank": 0,
            "approved_drugs": {},
            "pipeline_drugs": {},
            "orphanet_prevalence": None,
            "orphanet_prevalence_list": [],
            "inheritance": [],
            "age_of_onset": [],
            "genes": [],
            "is_rare": 1 if d.get("is_rare") else 0,
            "source_flags": {"MONDO"},
        }
        master[mid] = row

        n_key = normalize_en(row["name_en"])
        if n_key:
            name_to_canon.setdefault(n_key, mid)
        for syn in d.get("synonyms", []):
            if syn.get("scope") == "EXACT" and syn.get("text"):
                sk = normalize_en(syn["text"])
                if sk:
                    exact_syn_to_canon.setdefault(sk, mid)
        for o in orpha_list:
            orpha_to_canon.setdefault(o, mid)
        for e in efo_raw:
            efo_to_canon.setdefault(f"EFO_{e}", mid)
        for om in omim_list:
            omim_to_canons.setdefault(om, []).append(mid)

    # 2. Enrich & extend with Orphanet
    orph_matched = 0
    orph_added = 0
    for o in orphanet_data["diseases"]:
        if o["name"].startswith("OBSOLETE:"):
            continue
        oid = o["orpha_id"]
        target_id: str | None = None

        for mref in o.get("other_xrefs", {}).get("mondo", []):
            cand = f"MONDO:{mref}"
            if cand in master:
                target_id = cand
                break
        if not target_id and oid in orpha_to_canon:
            target_id = orpha_to_canon[oid]
        if not target_id:
            nk = normalize_en(o["name"])
            target_id = name_to_canon.get(nk) or exact_syn_to_canon.get(nk)
        if not target_id:
            for om in o.get("omim", []):
                cands = omim_to_canons.get(clean_omim(om), [])
                if len(cands) == 1:
                    target_id = cands[0]
                    break

        o_icd10 = [clean_icd10(x) for x in o.get("icd10", [])]
        o_icd11 = [clean_icd11(x) for x in o.get("icd11", [])]
        o_omim = [clean_omim(x) for x in o.get("omim", [])]
        o_syns = [s for s in o.get("synonyms", []) if s]
        o_genes = o.get("genes", [])
        o_inh = [x for x in o.get("inheritance", []) if x]
        o_age = [x for x in o.get("age_of_onset", []) if x]
        o_prev_sum = clean_str(o.get("prevalence_summary"))
        o_prev_list = o.get("prevalences") or []

        if target_id:
            orph_matched += 1
            row = master[target_id]
            row["source_flags"].add("Orphanet")
            row["is_rare"] = 1
            row["orpha_ids"] = dedupe_list(row["orpha_ids"] + [oid])
            # Prefer clean ICD-11 MMS codes from Orphanet ahead of foundation URIs
            row["icd10"] = dedupe_list(o_icd10 + row["icd10"])
            row["icd11"] = dedupe_list(o_icd11 + row["icd11"])
            row["omim_ids"] = dedupe_list(row["omim_ids"] + o_omim)
            row["synonyms_en"] = dedupe_list(row["synonyms_en"] + o_syns)
            if not row["definition_en"] and o.get("summary"):
                row["definition_en"] = clean_str(o["summary"])
            if o_prev_sum and not row["orphanet_prevalence"]:
                row["orphanet_prevalence"] = o_prev_sum
            if o_prev_list:
                row["orphanet_prevalence_list"].extend(o_prev_list)
            row["inheritance"] = dedupe_list(row["inheritance"] + o_inh)
            row["age_of_onset"] = dedupe_list(row["age_of_onset"] + o_age)
            if o_genes:
                existing_syms = {g["symbol"] for g in row["genes"] if isinstance(g, dict)}
                for g in o_genes:
                    if g.get("symbol") and g["symbol"] not in existing_syms:
                        row["genes"].append(g)
                        existing_syms.add(g["symbol"])
            orpha_to_canon.setdefault(oid, target_id)
        else:
            orph_added += 1
            clean_name = o["name"].replace("NON RARE IN EUROPE: ", "").strip()
            row = {
                "canonical_id": oid,
                "mondo_id": None,
                "name_en": clean_name,
                "name_zh": None,
                "definition_en": clean_str(o.get("summary")),
                "synonyms_en": dedupe_list(o_syns),
                "synonyms_zh": [],
                "icd10": dedupe_list(o_icd10),
                "icd11": dedupe_list(o_icd11),
                "orpha_ids": [oid],
                "omim_ids": dedupe_list(o_omim),
                "efo_id": None,
                "mondo_categories": dedupe_list([o.get("disorder_type") or "", o.get("disorder_group") or ""]),
                "top_level_names": [],
                "disease_type": infer_disease_type([], True, clean_name),
                "highest_clinical_stage": None,
                "highest_clinical_stage_rank": 0,
                "approved_drugs": {},
                "pipeline_drugs": {},
                "orphanet_prevalence": o_prev_sum,
                "orphanet_prevalence_list": list(o_prev_list),
                "inheritance": dedupe_list(o_inh),
                "age_of_onset": dedupe_list(o_age),
                "genes": list(o_genes),
                "is_rare": 1,
                "source_flags": {"Orphanet"},
            }
            master[oid] = row
            orpha_to_canon.setdefault(oid, oid)
            nk = normalize_en(clean_name)
            if nk:
                name_to_canon.setdefault(nk, oid)

    # 3. Enrich & extend with Open Targets
    molecules = ot_data.get("molecules", {})
    ot_matched = 0
    ot_added = 0

    def enrich_drug_list(drugs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        enriched: list[dict[str, Any]] = []
        for dr in drugs:
            did = dr.get("drug_id")
            mol = molecules.get(did, {}) if did else {}
            mechs = [
                m.get("mechanism_of_action")
                for m in mol.get("mechanisms", [])
                if m.get("mechanism_of_action")
            ]
            enriched.append(
                {
                    "drug_id": did,
                    "drug_name": dr.get("drug_name") or mol.get("name"),
                    "drug_type": dr.get("drug_type") or mol.get("drug_type"),
                    "max_clinical_stage": dr.get("max_clinical_stage"),
                    "mechanisms": dedupe_list(mechs),
                }
            )
        return enriched

    for ot_d in ot_data["diseases"]:
        oid = ot_d["id"]
        target_id = None
        if oid.startswith("MONDO_"):
            cand = oid.replace("_", ":")
            if cand in master:
                target_id = cand
        elif oid.startswith("Orphanet_"):
            cand = "ORPHA:" + oid.split("_", 1)[1]
            target_id = orpha_to_canon.get(cand) or (cand if cand in master else None)
        elif oid.startswith("EFO_"):
            target_id = efo_to_canon.get(oid)
            if not target_id:
                nk = normalize_en(ot_d["name"])
                target_id = name_to_canon.get(nk) or exact_syn_to_canon.get(nk)
        else:
            # For HP_/GO_ only enrich if exact name matches an existing human disease
            nk = normalize_en(ot_d["name"])
            target_id = name_to_canon.get(nk)

        if target_id:
            ot_matched += 1
            row = master[target_id]
            row["source_flags"].add("OpenTargets")
            if oid.startswith("EFO_") and not row["efo_id"]:
                row["efo_id"] = oid.replace("_", ":")
            if not row["definition_en"] and ot_d.get("description"):
                row["definition_en"] = clean_str(ot_d["description"])
            rank = ot_d.get("highest_clinical_stage_rank") or 0
            if rank > row["highest_clinical_stage_rank"]:
                row["highest_clinical_stage_rank"] = rank
                row["highest_clinical_stage"] = ot_d.get("highest_clinical_stage")
            for dr in enrich_drug_list(ot_d.get("approved_drugs", [])):
                if dr.get("drug_id"):
                    row["approved_drugs"][dr["drug_id"]] = dr
            for dr in enrich_drug_list(ot_d.get("pipeline_drugs", [])):
                if dr.get("drug_id"):
                    row["pipeline_drugs"][dr["drug_id"]] = dr
        else:
            # Add genuine disease EFO nodes with clinical indications
            if (
                oid.startswith("EFO_")
                and not ot_d.get("is_therapeutic_area")
                and ot_d.get("total_clinical_drug_count", 0) > 0
            ):
                ta_ids = {t["id"] for t in ot_d.get("therapeutic_areas", [])}
                ta_names = [t["name"] for t in ot_d.get("therapeutic_areas", [])]
                if ta_ids and not ta_ids.issubset(PURE_NON_DISEASE_OT_AREAS):
                    ot_added += 1
                    canon = oid.replace("_", ":")
                    app_map = {
                        dr["drug_id"]: dr
                        for dr in enrich_drug_list(ot_d.get("approved_drugs", []))
                        if dr.get("drug_id")
                    }
                    pipe_map = {
                        dr["drug_id"]: dr
                        for dr in enrich_drug_list(ot_d.get("pipeline_drugs", []))
                        if dr.get("drug_id")
                    }
                    row = {
                        "canonical_id": canon,
                        "mondo_id": None,
                        "name_en": ot_d["name"].strip(),
                        "name_zh": None,
                        "definition_en": clean_str(ot_d.get("description")),
                        "synonyms_en": [],
                        "synonyms_zh": [],
                        "icd10": [],
                        "icd11": [],
                        "orpha_ids": [],
                        "omim_ids": [],
                        "efo_id": canon,
                        "mondo_categories": dedupe_list(ta_names),
                        "top_level_names": ta_names,
                        "disease_type": infer_disease_type(ta_names, False, ot_d["name"]),
                        "highest_clinical_stage": ot_d.get("highest_clinical_stage"),
                        "highest_clinical_stage_rank": ot_d.get("highest_clinical_stage_rank") or 0,
                        "approved_drugs": app_map,
                        "pipeline_drugs": pipe_map,
                        "orphanet_prevalence": None,
                        "orphanet_prevalence_list": [],
                        "inheritance": [],
                        "age_of_onset": [],
                        "genes": [],
                        "is_rare": 0,
                        "source_flags": {"OpenTargets"},
                    }
                    master[canon] = row
                    nk = normalize_en(row["name_en"])
                    if nk:
                        name_to_canon.setdefault(nk, canon)

    stats = {
        "mondo_human_base": sum(1 for r in master.values() if "MONDO" in r["source_flags"]),
        "orphanet_matched_to_mondo": orph_matched,
        "orphanet_added_standalone": orph_added,
        "opentargets_matched": ot_matched,
        "opentargets_efo_added": ot_added,
        "total_master": len(master),
    }
    return master, stats


def build_master_indexes(master: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Build fast lookup indexes for curated and local policy cross-linking."""
    by_orpha: dict[str, list[str]] = {}
    by_omim: dict[str, list[str]] = {}
    by_icd10: dict[str, list[str]] = {}
    by_name_en: dict[str, list[str]] = {}

    for cid, row in master.items():
        nk = normalize_en(row["name_en"])
        if nk:
            by_name_en.setdefault(nk, []).append(cid)
        for syn in row["synonyms_en"]:
            sk = normalize_en(syn)
            if sk:
                by_name_en.setdefault(sk, []).append(cid)
        for o in row["orpha_ids"]:
            by_orpha.setdefault(o, []).append(cid)
        for om in row["omim_ids"]:
            by_omim.setdefault(om, []).append(cid)
        for code in row["icd10"]:
            c = code.upper()
            by_icd10.setdefault(c, []).append(cid)
            if "." in c:
                by_icd10.setdefault(c.split(".", 1)[0], []).append(cid)

    return {
        "by_orpha": by_orpha,
        "by_omim": by_omim,
        "by_icd10": by_icd10,
        "by_name_en": by_name_en,
    }


def match_curated_to_master(
    rec: dict[str, Any],
    master: dict[str, dict[str, Any]],
    idx: dict[str, Any],
) -> tuple[str | None, str]:
    """Link a curated dossier record to diseases_master where possible."""
    did = clean_str(rec.get("disease_id"))
    if did:
        if did in master:
            return did, "exact_disease_id"
        if did.startswith("ORPHA:"):
            hits = idx["by_orpha"].get(did, [])
            if hits:
                return hits[0], "orpha_id"
        if did.startswith("OMIM:"):
            hits = idx["by_omim"].get(did, [])
            if hits:
                return hits[0], "omim_id"

    # Normalized English name match
    ne = normalize_en(rec.get("name_en"))
    if ne and ne in idx["by_name_en"]:
        cands = idx["by_name_en"][ne]
        # Prefer MONDO IDs and rows with richer annotations
        cands_sorted = sorted(cands, key=lambda c: (0 if c.startswith("MONDO:") else 1, -len(master[c]["icd10"])))
        return cands_sorted[0], "exact_name_en"

    # Try sub-parts separated by '/' or 'and'
    raw_en = rec.get("name_en") or ""
    for part in re.split(r"[/|;]| including ", raw_en):
        pk = normalize_en(part)
        if pk and pk in idx["by_name_en"]:
            return idx["by_name_en"][pk][0], "partial_name_en"

    # ICD-10 fallback with lexical similarity tie-breaker
    icd_cands: list[str] = []
    for code in rec.get("icd10", []):
        cu = clean_icd10(code)
        icd_cands.extend(idx["by_icd10"].get(cu, []))
    if icd_cands:
        rec_words = set(ne.split())
        best_cid = max(
            dedupe_list(icd_cands),
            key=lambda c: len(rec_words & set(normalize_en(master[c]["name_en"]).split())),
        )
        overlap = len(rec_words & set(normalize_en(master[best_cid]["name_en"]).split()))
        if overlap > 0 or len(set(icd_cands)) == 1:
            return best_cid, "icd10_match"

    return None, "unmatched"


def backfill_bilingual_hints(
    master: dict[str, dict[str, Any]],
    idx: dict[str, Any],
    cur: sqlite3.Cursor,
) -> int:
    """Enrich matching diseases_master rows with bilingual Chinese disease labels for search."""
    updated = 0
    for zh_term, (_, en_hints, icd_hints) in sorted(
        BILINGUAL_CLINICAL_HINTS.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        if zh_term.endswith("透析") or zh_term.endswith("透析治疗"):
            continue
        matched_cid: str | None = None
        for eh in en_hints:
            ek = normalize_en(eh)
            if ek in idx["by_name_en"]:
                cands = sorted(
                    idx["by_name_en"][ek],
                    key=lambda c: (0 if c.startswith("MONDO:") else 1, -len(master[c]["icd10"])),
                )
                matched_cid = cands[0]
                break
        if not matched_cid:
            for ic in icd_hints:
                if ic in idx["by_icd10"]:
                    matched_cid = idx["by_icd10"][ic][0]
                    break
        if matched_cid:
            m_row = master[matched_cid]
            if not m_row["name_zh"]:
                m_row["name_zh"] = zh_term
            m_row["synonyms_zh"] = dedupe_list(m_row["synonyms_zh"] + [zh_term])
            cur.execute(
                """
                UPDATE diseases_master
                SET name_zh = COALESCE(NULLIF(TRIM(name_zh), ''), ?),
                    synonyms_zh_json = ?
                WHERE canonical_id = ?
                """,
                (m_row["name_zh"], json_dumps(m_row["synonyms_zh"]), matched_cid),
            )
            updated += 1
    return updated


def build_database(db_path: Path, summary_path: Path) -> dict[str, Any]:
    t0 = time.perf_counter()

    mondo_path = RAW_DIR / "mondo_diseases.json"
    orph_path = RAW_DIR / "orphanet_diseases.json"
    ot_path = RAW_DIR / "opentargets_diseases_drugs.json"
    who_path = RAW_DIR / "who_mortality.json"
    curated_files = sorted(CURATED_DIR.glob("*.json")) if CURATED_DIR.exists() else []

    for req in (mondo_path, orph_path, ot_path, who_path):
        if not req.exists():
            raise FileNotFoundError(f"Missing required backbone input: {req}")
    if not curated_files:
        raise FileNotFoundError(f"No curated JSON files found under {CURATED_DIR}")

    print("Loading raw backbone datasets...")
    mondo_data = json.loads(mondo_path.read_text(encoding="utf-8"))
    orph_data = json.loads(orph_path.read_text(encoding="utf-8"))
    ot_data = json.loads(ot_path.read_text(encoding="utf-8"))
    who_data = json.loads(who_path.read_text(encoding="utf-8"))

    print("Merging MONDO + Orphanet + Open Targets into diseases_master...")
    master, merge_stats = build_master_catalog(mondo_data, orph_data, ot_data)
    master_idx = build_master_indexes(master)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_db = db_path.with_suffix(".db.tmp")
    if tmp_db.exists():
        tmp_db.unlink()

    conn = sqlite3.connect(tmp_db)
    try:
        create_schema(conn)
        cur = conn.cursor()

        # 1. Insert initial diseases_master rows (we will update zh/curated flags next)
        master_rows_to_insert = []
        for cid, r in master.items():
            app_drugs = list(r["approved_drugs"].values())
            pipe_drugs = list(r["pipeline_drugs"].values())
            # Exclude approved drugs from clinical_trial_count if overlapped, keep total unique clinical drugs
            all_drug_ids = set(r["approved_drugs"].keys()) | set(r["pipeline_drugs"].keys())
            top_drugs = (app_drugs[:15] + pipe_drugs[:10])[:25]
            gene_syms = [g["symbol"] for g in r["genes"] if isinstance(g, dict) and g.get("symbol")]
            master_rows_to_insert.append(
                (
                    cid,
                    r["mondo_id"],
                    r["name_en"],
                    r["name_zh"],
                    r["definition_en"],
                    json_dumps(r["synonyms_en"]),
                    json_dumps(r["synonyms_zh"]),
                    json_dumps(r["icd10"]),
                    json_dumps(r["icd11"]),
                    r["icd10"][0] if r["icd10"] else None,
                    r["icd11"][0] if r["icd11"] else None,
                    r["orpha_ids"][0] if r["orpha_ids"] else None,
                    json_dumps(r["orpha_ids"]),
                    r["omim_ids"][0] if r["omim_ids"] else None,
                    json_dumps(r["omim_ids"]),
                    r["efo_id"],
                    r["disease_type"],
                    json_dumps(r["mondo_categories"]),
                    r["highest_clinical_stage"],
                    r["highest_clinical_stage_rank"],
                    len(app_drugs),
                    len(all_drug_ids),
                    json_dumps(top_drugs) if top_drugs else None,
                    r["orphanet_prevalence"],
                    json_dumps(r["orphanet_prevalence_list"]) if r["orphanet_prevalence_list"] else None,
                    ", ".join(r["inheritance"]) if r["inheritance"] else None,
                    ", ".join(r["age_of_onset"]) if r["age_of_onset"] else None,
                    ", ".join(gene_syms) if gene_syms else None,
                    json_dumps(r["genes"]) if r["genes"] else None,
                    r["is_rare"],
                    0,  # is_curated
                    None,  # curated_dossier_id
                    "|".join(sorted(r["source_flags"])),
                )
            )

        cur.executemany(
            """
            INSERT INTO diseases_master (
                canonical_id, mondo_id, name_en, name_zh, definition_en,
                synonyms_en_json, synonyms_zh_json, icd10_json, icd11_json,
                icd10_primary, icd11_primary, orpha_id, orpha_ids_json,
                omim_id, omim_ids_json, efo_id, disease_type, mondo_categories_json,
                highest_clinical_stage, highest_clinical_stage_rank,
                approved_drug_count, clinical_trial_count, top_drugs_json,
                orphanet_prevalence, orphanet_prevalence_json, inheritance,
                age_of_onset, genes, genes_json, is_rare, is_curated,
                curated_dossier_id, source_flags
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            master_rows_to_insert,
        )

        # 2. Load & link curated dossiers + curated drugs
        print(f"Loading {len(curated_files)} curated domain files...")
        curated_index_rows: list[dict[str, Any]] = []
        curated_linked_count = 0
        total_drugs = 0
        total_nrdl_drugs = 0
        domain_counts: Counter[str] = Counter()
        treatability_counts: Counter[str] = Counter()
        lethality_counts: Counter[str] = Counter()
        course_counts: Counter[str] = Counter()

        for cfile in curated_files:
            payload = json.loads(cfile.read_text(encoding="utf-8"))
            domain = payload.get("domain", cfile.stem)
            curator = payload.get("curator")
            gen_at = payload.get("generated_at")
            for rec in payload.get("records", []):
                master_cid, match_method = match_curated_to_master(rec, master, master_idx)
                if master_cid:
                    curated_linked_count += 1

                epi = rec["epidemiology"]
                g_d = epi["annual_deaths_global"]
                c_d = epi["annual_deaths_china"]
                cm = rec["clinical_management"]
                rp = rec["research_progress"]
                drugs = cm.get("standard_drugs", [])
                nrdl_cnt = sum(1 for dr in drugs if dr.get("china_nrdl_covered"))
                total_drugs += len(drugs)
                total_nrdl_drugs += nrdl_cnt

                cur.execute(
                    """
                    INSERT INTO curated_dossiers (
                        domain, source_file, curator, generated_at, disease_id,
                        master_canonical_id, match_method, name_zh, name_en,
                        aliases_zh_json, icd10_json, icd11_json, category,
                        course_type, lethality_speed, lethality_detail_zh,
                        treatability_status, treatability_summary_zh,
                        annual_deaths_global_value, annual_deaths_global_note,
                        annual_deaths_global_year, annual_deaths_global_source,
                        annual_deaths_china_value, annual_deaths_china_note,
                        annual_deaths_china_year, annual_deaths_china_source,
                        prevalence_global, prevalence_china,
                        acute_emergency_protocol_zh, non_drug_therapies_json,
                        annual_total_cost_china_cny,
                        highest_pipeline_stage, breakthroughs_and_trials_zh,
                        key_unmet_needs_zh, drug_count, nrdl_covered_drug_count,
                        sources_json, epidemiology_json, clinical_management_json,
                        research_progress_json, raw_record_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        domain,
                        cfile.name,
                        curator,
                        gen_at,
                        rec.get("disease_id"),
                        master_cid,
                        match_method,
                        rec["name_zh"].strip(),
                        rec["name_en"].strip(),
                        json_dumps(rec.get("aliases_zh", [])),
                        json_dumps(rec.get("icd10", [])),
                        json_dumps(rec.get("icd11", [])),
                        rec.get("category"),
                        rec["course_type"],
                        rec["lethality_speed"],
                        rec["lethality_detail_zh"],
                        rec["treatability_status"],
                        rec["treatability_summary_zh"],
                        g_d.get("value"),
                        g_d.get("range_or_note"),
                        g_d.get("year"),
                        g_d.get("source"),
                        c_d.get("value"),
                        c_d.get("range_or_note"),
                        c_d.get("year"),
                        c_d.get("source"),
                        epi.get("prevalence_global"),
                        epi.get("prevalence_china"),
                        cm.get("acute_emergency_protocol_zh"),
                        json_dumps(cm.get("non_drug_therapies_zh", [])),
                        cm.get("annual_total_cost_china_cny"),
                        rp.get("highest_pipeline_stage"),
                        rp.get("breakthroughs_and_trials_zh"),
                        rp.get("key_unmet_needs_zh"),
                        len(drugs),
                        nrdl_cnt,
                        json_dumps(rec.get("sources", [])),
                        json_dumps(epi),
                        json_dumps(cm),
                        json_dumps(rp),
                        json_dumps(rec),
                    ),
                )
                dossier_id = cur.lastrowid

                for dr in drugs:
                    cur.execute(
                        """
                        INSERT INTO curated_drugs (
                            dossier_id, name_zh, name_en, drug_class, usage_role,
                            china_nrdl_covered, cost_estimate_cny, cost_source
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            dossier_id,
                            dr["name_zh"],
                            dr["name_en"],
                            dr.get("drug_class"),
                            dr.get("usage_role"),
                            1 if dr.get("china_nrdl_covered") else 0,
                            dr.get("cost_estimate_cny"),
                            dr.get("cost_source"),
                        ),
                    )

                domain_counts[domain] += 1
                treatability_counts[rec["treatability_status"]] += 1
                lethality_counts[rec["lethality_speed"]] += 1
                course_counts[rec["course_type"]] += 1

                curated_index_rows.append(
                    {
                        "id": dossier_id,
                        "master_canonical_id": master_cid,
                        "name_zh": rec["name_zh"].strip(),
                        "norm_zh": normalize_zh(rec["name_zh"]),
                        "norm_aliases_zh": {normalize_zh(a) for a in rec.get("aliases_zh", []) if normalize_zh(a)},
                        "aliases_zh": rec.get("aliases_zh", []),
                        "name_en": rec["name_en"].strip(),
                        "icd10": rec.get("icd10", []),
                        "icd11": rec.get("icd11", []),
                        "category": rec.get("category") or domain,
                        "summary_text": f"{rec.get('treatability_summary_zh','')} {rec.get('lethality_detail_zh','')} {' '.join(dr['name_zh'] + ' ' + dr['name_en'] for dr in drugs)}",
                    }
                )

                # Backfill diseases_master with curated Chinese name and link
                if master_cid:
                    m_row = master[master_cid]
                    if not m_row["name_zh"]:
                        m_row["name_zh"] = rec["name_zh"].strip()
                    m_row["synonyms_zh"] = dedupe_list(
                        m_row["synonyms_zh"] + [rec["name_zh"].strip()] + list(rec.get("aliases_zh", []))
                    )
                    m_row["source_flags"].add("Curated")
                    cur.execute(
                        """
                        UPDATE diseases_master
                        SET name_zh = COALESCE(name_zh, ?),
                            synonyms_zh_json = ?,
                            is_curated = 1,
                            curated_dossier_id = COALESCE(curated_dossier_id, ?),
                            source_flags = ?
                        WHERE canonical_id = ?
                        """,
                        (
                            m_row["name_zh"],
                            json_dumps(m_row["synonyms_zh"]),
                            dossier_id,
                            "|".join(sorted(m_row["source_flags"])),
                            master_cid,
                        ),
                    )

        # 3. Insert WHO mortality
        print("Inserting WHO GHE & GHO mortality rows...")
        cause_meta = {c["ghe_cause_code"]: c for c in who_data.get("cause_catalog", [])}
        who_rows = []

        def add_ghe_row(r: dict[str, Any]) -> None:
            cm_item = cause_meta.get(r["ghe_cause_code"], {})
            title = r["cause_title"]
            who_rows.append(
                (
                    "WHO_GHE",
                    f"GHE_{r['ghe_cause_code']}",
                    r["location_code"],
                    int(r["year"]),
                    r.get("sex"),
                    str(r["ghe_cause_code"]),
                    title,
                    WHO_CAUSE_ZH_MAP.get(title),
                    r.get("cause_level"),
                    r.get("cause_group_id"),
                    cm_item.get("cause_group_name"),
                    1 if r.get("is_single_cause") else 0,
                    1 if r.get("is_rankable") else 0,
                    r.get("population"),
                    r.get("deaths"),
                    r.get("deaths") if r["location_code"] == "WRLD" and r.get("sex") == "TOTAL" else None,
                    r.get("deaths_low"),
                    r.get("deaths_high"),
                    r.get("deaths_rate_per_100k"),
                    r.get("deaths_percent_of_total"),
                    r.get("dalys"),
                    r.get("dalys_rate_per_100k"),
                    r.get("ylls"),
                    r.get("ylds"),
                    None,
                )
            )

        for r in who_data.get("global_time_series_2000_2023", []):
            add_ghe_row(r)
        for r in who_data.get("china_time_series_2000_2023", []):
            add_ghe_row(r)
        for r in who_data.get("global_2023", []):
            if r.get("sex") != "TOTAL":
                add_ghe_row(r)
        for r in who_data.get("china_2023", []):
            if r.get("sex") != "TOTAL":
                add_ghe_row(r)
        for r in who_data.get("regional_2023", []):
            add_ghe_row(r)

        for ind, items in who_data.get("gho_supplemental", {}).items():
            for item in items:
                loc = item.get("location_code") or "GLOBAL"
                val = item.get("numeric_value")
                who_rows.append(
                    (
                        "WHO_GHO",
                        ind,
                        loc,
                        int(item["year"]),
                        item.get("sex") or "TOTAL",
                        ind,
                        ind,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        val,
                        val if loc == "GLOBAL" else None,
                        item.get("low"),
                        item.get("high"),
                        None,
                        None,
                        None,
                        None,
                        None,
                        None,
                        item.get("display_value"),
                    )
                )

        cur.executemany(
            """
            INSERT INTO who_mortality (
                source, indicator_code, location_code, year, sex, cause_code,
                cause_name_en, cause_name_zh, cause_level, cause_group_id,
                cause_group_name, is_single_cause, is_rankable, population,
                deaths, deaths_global, deaths_low, deaths_high,
                death_rate_per_100k, pct_total_deaths, dalys, dalys_rate_per_100k,
                ylls, ylds, display_value
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            who_rows,
        )

        # 4. Enrich diseases_master with bilingual Chinese clinical labels
        print("Backfilling bilingual Chinese disease hints on diseases_master...")
        bilingual_backfilled = backfill_bilingual_hints(master, master_idx, cur)

        # 5. Populate FTS5 bilingual search table (`diseases_fts`)
        print("Building bilingual Chinese+English+ICD FTS5 index...")
        fts_rows = []
        for cid, r in master.items():
            aliases = " | ".join(r["synonyms_zh"] + r["synonyms_en"][:12])
            icds = " ".join(r["icd10"] + r["icd11"] + [cid] + r["orpha_ids"][:4] + r["omim_ids"][:4])
            drug_names = " ".join(
                d["drug_name"] for d in list(r["approved_drugs"].values())[:8] if d.get("drug_name")
            )
            gene_str = " ".join(g["symbol"] for g in r["genes"][:8] if isinstance(g, dict) and g.get("symbol"))
            summary = " ".join(x for x in (r["definition_en"], drug_names, gene_str) if x)
            tokens = build_cjk_search_tokens(r["name_zh"], r["name_en"], aliases, icds, r["disease_type"], summary)
            fts_rows.append(
                (
                    "master",
                    cid,
                    cid,
                    r["name_zh"] or "",
                    r["name_en"],
                    aliases,
                    icds,
                    r["disease_type"],
                    summary[:1000],
                    tokens,
                )
            )

        for cd in curated_index_rows:
            aliases = " | ".join(cd["aliases_zh"])
            icds = " ".join(cd["icd10"] + cd["icd11"] + ([cd["master_canonical_id"]] if cd["master_canonical_id"] else []))
            tokens = build_cjk_search_tokens(
                cd["name_zh"], cd["name_en"], aliases, icds, cd["category"], cd["summary_text"]
            )
            fts_rows.append(
                (
                    "curated",
                    str(cd["id"]),
                    cd["master_canonical_id"] or "",
                    cd["name_zh"],
                    cd["name_en"],
                    aliases,
                    icds,
                    cd["category"],
                    cd["summary_text"][:1000],
                    tokens,
                )
            )

        cur.executemany(
            """
            INSERT INTO diseases_fts (
                record_type, record_id, canonical_id, name_zh, name_en,
                aliases, icd_codes, category, summary_text, search_tokens
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            fts_rows,
        )

        # Build metadata + optimize FTS5
        build_ts = datetime.now(timezone.utc).isoformat()
        cur.execute("INSERT INTO diseases_fts(diseases_fts) VALUES('optimize')")

        # Compute exact summary metrics directly from SQLite tables
        table_names = [
            "diseases_master",
            "curated_dossiers",
            "curated_drugs",
            "who_mortality",
            "diseases_fts",
        ]
        table_counts = {
            t: cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in table_names
        }

        master_coverage = {
            "with_mondo_id": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE mondo_id IS NOT NULL").fetchone()[0],
            "with_name_zh": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE name_zh IS NOT NULL AND TRIM(name_zh) != ''").fetchone()[0],
            "with_icd10": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE icd10_primary IS NOT NULL").fetchone()[0],
            "with_icd11": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE icd11_primary IS NOT NULL").fetchone()[0],
            "with_orpha_id": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE orpha_id IS NOT NULL").fetchone()[0],
            "with_omim_id": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE omim_id IS NOT NULL").fetchone()[0],
            "with_highest_clinical_stage": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE highest_clinical_stage IS NOT NULL").fetchone()[0],
            "with_approved_drugs": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE approved_drug_count > 0").fetchone()[0],
            "with_clinical_trials": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE clinical_trial_count > 0").fetchone()[0],
            "with_orphanet_prevalence": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE orphanet_prevalence IS NOT NULL").fetchone()[0],
            "with_inheritance": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE inheritance IS NOT NULL").fetchone()[0],
            "with_genes": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE genes IS NOT NULL").fetchone()[0],
            "is_rare": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE is_rare = 1").fetchone()[0],
            "is_curated": cur.execute("SELECT COUNT(*) FROM diseases_master WHERE is_curated = 1").fetchone()[0],
            "bilingual_hints_backfilled": bilingual_backfilled,
        }

        fts_benchmarks = {}
        for label, q in [
            ("diabetes", '"diabetes"'),
            ("embolism", '"embolism"'),
            ("糖尿病", '"糖尿病"'),
            ("肺栓塞", '"肺栓塞"'),
            ("肺癌", '"肺癌"'),
            ("I26", '"I26"'),
        ]:
            qt0 = time.perf_counter()
            hits = cur.execute("SELECT COUNT(*) FROM diseases_fts WHERE diseases_fts MATCH ?", (q,)).fetchone()[0]
            fts_benchmarks[label] = {"hits": hits, "ms": round((time.perf_counter() - qt0) * 1000, 2)}

        summary = {
            "generated_at_utc": build_ts,
            "database_path": str(db_path),
            "build_seconds": round(time.perf_counter() - t0, 2),
            "table_counts": table_counts,
            "backbone_merge_stats": merge_stats,
            "diseases_master_coverage": master_coverage,
            "curated_coverage": {
                "files_count": len(curated_files),
                "total_dossiers": table_counts["curated_dossiers"],
                "linked_to_master": curated_linked_count,
                "total_drugs": total_drugs,
                "nrdl_covered_drugs": total_nrdl_drugs,
                "by_domain": dict(domain_counts),
                "by_course_type": dict(course_counts),
                "by_lethality_speed": dict(lethality_counts),
                "by_treatability_status": dict(treatability_counts),
            },
            "who_mortality_coverage": {
                "total_rows": table_counts["who_mortality"],
                "unique_causes": cur.execute("SELECT COUNT(DISTINCT cause_code) FROM who_mortality WHERE source='WHO_GHE'").fetchone()[0],
                "years_range": cur.execute("SELECT MIN(year), MAX(year) FROM who_mortality WHERE source='WHO_GHE'").fetchone(),
                "global_2023_all_causes_deaths": cur.execute(
                    "SELECT deaths FROM who_mortality WHERE source='WHO_GHE' AND location_code='WRLD' AND year=2023 AND sex='TOTAL' AND cause_code='0'"
                ).fetchone()[0],
                "china_2023_all_causes_deaths": cur.execute(
                    "SELECT deaths FROM who_mortality WHERE source='WHO_GHE' AND location_code='CHN' AND year=2023 AND sex='TOTAL' AND cause_code='0'"
                ).fetchone()[0],
            },
            "fts5_benchmarks": fts_benchmarks,
        }

        cur.executemany(
            "INSERT INTO build_metadata (key, value) VALUES (?, ?)",
            [
                ("generated_at_utc", build_ts),
                ("summary_json", json_dumps(summary)),
            ],
        )
        conn.commit()
        cur.close()
    finally:
        conn.close()
        import gc
        gc.collect()

    for _attempt in range(10):
        try:
            if db_path.exists():
                db_path.unlink()
            tmp_db.replace(db_path)
            break
        except PermissionError:
            if _attempt == 9:
                raise
            time.sleep(0.4)

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Salus unified SQLite database.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--summary", type=Path, default=DEFAULT_SUMMARY_PATH)
    args = parser.parse_args()
    summary = build_database(args.db, args.summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
