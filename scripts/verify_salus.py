#!/usr/bin/env python3
"""Strict verification suite for the Salus Global Disease & Treatment Knowledge Base.

Validates:
1. Curated disease dossiers under data/curated/*.json against SCHEMA.md
   (required keys, strict enums, citations with valid URLs, zero placeholders,
   de-identification check, and numeric/semantic sanity checks).
2. Open biomedical backbone datasets under data/raw/ (when present) and .licenses/.
3. Unified SQLite knowledge base (data/salus.db) and data/salus_summary.json:
   PRAGMA integrity_check, table schemas, exact row count reconciliation,
   and bilingual/ICD FTS5 search queries.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EXPECTED_CURATED_FILES = [
    "01_acute_vascular_thrombophilia.json",
    "02_oncology_hematology.json",
    "03_infectious_respiratory.json",
    "04_neuro_mental_rare.json",
    "05_metabolic_organ_immune.json",
]

FORBIDDEN_POLICY_RE = re.compile(
    r"shandong_policy_match|\u5c71\u4e1c|\u6162\u7279\u75c5|\u533b\u4fdd|\u9c81\u533b\u4fdd\u53d1|\u653f\u7b56\u5efa\u6a21|01_\u6b63\u5f0f"
)

COURSE_TYPES = {"acute", "chronic", "acute_on_chronic", "congenital_genetic"}
LETHALITY_SPEEDS = {
    "hyperacute_minutes",
    "acute_hours_days",
    "subacute_weeks",
    "chronic_years",
    "low_lethality",
}
TREATABILITY_STATUSES = {
    "curable",
    "controllable",
    "partially_manageable",
    "symptomatic_only",
    "in_clinical_trials",
    "unmet_no_effective_therapy",
}
PIPELINE_STAGES = {
    "Approved_Curative",
    "Approved_Maintenance",
    "Phase_3",
    "Phase_2",
    "Phase_1",
    "Preclinical",
}

PLACEHOLDER_WORD_RE = re.compile(
    r"\b(TODO|TBD|FIXME|PLACEHOLDER|COMING\s+SOON)\b", re.IGNORECASE
)
PLACEHOLDER_LITERAL_RE = re.compile(
    r"(?:^\s*(?:待补充|待完善|待填写|待确认|待定|暂无)\s*$|[【\[（(]\s*(?:待补充|待完善|待填写|待确认|待定)\s*[】\]）)]|待补充|待填写|example\.com|example\.org|https?://\.\.\.)",
    re.IGNORECASE,
)
SCHEMA_EXAMPLE_STRINGS = {
    "中文标准病名",
    "Standard English Disease Name",
    "常见别名/俗称",
    "来源标题",
    "药物通用名",
    "Generic Name",
    "类别/靶点机制",
    "一线/急救/维持/靶向/基因治疗",
    "2021/2023/2024",
    "ISO-8601 date",
    "agent name",
}

DISEASE_ID_RE = re.compile(
    r"^(MONDO|ORPHA|ICD10|ICD11|OMIM|DOID):[A-Za-z0-9._-]+$"
)
ICD10_RE = re.compile(
    r"^[A-Z][0-9]{2}(\.[0-9A-Za-z]{1,4})?(-[A-Z][0-9]{2}(\.[0-9A-Za-z]{1,4})?)?$"
)
ICD11_RE = re.compile(
    r"^[0-9A-Z]{2,6}(\.[0-9A-Z]{1,4})?([-/&][0-9A-Z]{2,6}(\.[0-9A-Z]{1,4})?)?$"
)
YEAR_4DIGIT_RE = re.compile(r"(19[5-9]\d|20[0-2]\d)")

MAX_GLOBAL_DEATHS_PER_YEAR = 65_000_000
MAX_CHINA_DEATHS_PER_YEAR = 12_000_000


def is_non_empty_str(val: Any) -> bool:
    return isinstance(val, str) and len(val.strip()) > 0


def is_valid_number(val: Any) -> bool:
    return (
        isinstance(val, (int, float))
        and not isinstance(val, bool)
        and math.isfinite(val)
    )


def is_valid_url(val: Any) -> bool:
    if not is_non_empty_str(val):
        return False
    s = val.strip()
    if any(ch.isspace() for ch in s):
        return False
    parsed = urlparse(s)
    if parsed.scheme not in {"http", "https"}:
        return False
    host = parsed.netloc.split(":")[0].lower()
    if not host or "." not in host or host in {"localhost", "example.com", "example.org"}:
        return False
    if "..." in s:
        return False
    return True


def scan_placeholders(obj: Any, path: str, errors: list[str]) -> None:
    """Recursively scan JSON objects for placeholder or template strings."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            scan_placeholders(v, f"{path}.{k}", errors)
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            scan_placeholders(item, f"{path}[{idx}]", errors)
    elif isinstance(obj, str):
        s = obj.strip()
        if s in SCHEMA_EXAMPLE_STRINGS:
            errors.append(f"{path}: contains SCHEMA.md template string {s!r}")
        elif PLACEHOLDER_WORD_RE.search(s) or PLACEHOLDER_LITERAL_RE.search(s):
            errors.append(f"{path}: contains placeholder token in {s[:100]!r}")


def validate_mortality_block(
    block: Any,
    scope: str,
    prefix: str,
    max_cap: float,
    errors: list[str],
    warnings: list[str],
) -> float | None:
    """Validate annual_deaths_global or annual_deaths_china block."""
    if not isinstance(block, dict):
        errors.append(f"{prefix}: must be a JSON object")
        return None

    required = {"value", "range_or_note", "year", "source"}
    missing = required - set(block.keys())
    if missing:
        errors.append(f"{prefix}: missing keys {sorted(missing)}")
        return None

    val = block["value"]
    if val is not None:
        if not is_valid_number(val):
            errors.append(f"{prefix}.value: must be number or null, got {type(val).__name__}")
            val_num = None
        else:
            val_num = float(val)
            if val_num < 0:
                errors.append(f"{prefix}.value: negative mortality {val_num}")
            elif val_num > max_cap:
                errors.append(
                    f"{prefix}.value: implausibly high ({val_num:,.0f} > {max_cap:,.0f})"
                )
            if not is_non_empty_str(block["year"]):
                errors.append(f"{prefix}.year: required non-empty string when value is numeric")
            if not is_non_empty_str(block["source"]):
                errors.append(f"{prefix}.source: required non-empty string when value is numeric")
    else:
        val_num = None
        if block["year"] is not None and not isinstance(block["year"], str):
            errors.append(f"{prefix}.year: must be string or null")
        if block["source"] is not None and not isinstance(block["source"], str):
            errors.append(f"{prefix}.source: must be string or null")
        if block["year"] == "" or block["source"] == "":
            warnings.append(f"{prefix}: prefer null over empty string when value is null")

    if not is_non_empty_str(block["range_or_note"]):
        errors.append(f"{prefix}.range_or_note: must be a non-empty explanation string")

    if scope == "global" and val_num is not None and not YEAR_4DIGIT_RE.search(str(block["year"])):
        warnings.append(f"{prefix}.year: expected 4-digit year in {block['year']!r}")

    return val_num


def validate_curated_record(
    rec: Any, idx: int, errors: list[str], warnings: list[str]
) -> dict[str, Any]:
    """Validate one disease record against SCHEMA.md and return stats."""
    prefix = f"records[{idx}]"
    stats: dict[str, Any] = {
        "name_en": None,
        "name_zh": None,
        "disease_id": None,
        "global_deaths_null": False,
        "china_deaths_null": False,
        "drug_count": 0,
        "nrdl_covered_count": 0,
        "source_urls": [],
    }
    if not isinstance(rec, dict):
        errors.append(f"{prefix}: record must be a JSON object")
        return stats

    required_keys = {
        "disease_id",
        "name_zh",
        "name_en",
        "aliases_zh",
        "icd10",
        "icd11",
        "category",
        "course_type",
        "lethality_speed",
        "lethality_detail_zh",
        "treatability_status",
        "treatability_summary_zh",
        "epidemiology",
        "clinical_management",
        "research_progress",
        "sources",
    }
    missing = required_keys - set(rec.keys())
    if missing:
        errors.append(f"{prefix}: missing required keys {sorted(missing)}")
        return stats

    scan_placeholders(rec, prefix, errors)

    label = rec.get("name_en") or rec.get("name_zh") or prefix
    p = f"{prefix}({label})"

    # Identifiers & names
    did = rec["disease_id"]
    if did is not None:
        if not is_non_empty_str(did) or not DISEASE_ID_RE.match(did.strip()):
            errors.append(
                f"{p}.disease_id: must be null or canonical ID (MONDO:/ORPHA:/ICD10:/ICD11:/OMIM:), got {did!r}"
            )
        else:
            stats["disease_id"] = did.strip()

    for key in ("name_zh", "name_en", "category", "lethality_detail_zh", "treatability_summary_zh"):
        if not is_non_empty_str(rec[key]):
            errors.append(f"{p}.{key}: must be a non-empty string")
    stats["name_en"] = rec["name_en"].strip() if isinstance(rec["name_en"], str) else None
    stats["name_zh"] = rec["name_zh"].strip() if isinstance(rec["name_zh"], str) else None

    if not isinstance(rec["aliases_zh"], list) or any(
        not is_non_empty_str(x) for x in rec["aliases_zh"]
    ):
        errors.append(f"{p}.aliases_zh: must be a list of non-empty strings")

    # ICD codes
    if not isinstance(rec["icd10"], list) or any(not is_non_empty_str(x) for x in rec["icd10"]):
        errors.append(f"{p}.icd10: must be a list of non-empty strings")
    else:
        if len(rec["icd10"]) == 0:
            warnings.append(f"{p}.icd10: empty ICD-10 list")
        for code in rec["icd10"]:
            if not ICD10_RE.match(code.strip()):
                warnings.append(f"{p}.icd10: unusual ICD-10 code format {code!r}")

    if not isinstance(rec["icd11"], list) or any(not is_non_empty_str(x) for x in rec["icd11"]):
        errors.append(f"{p}.icd11: must be a list of non-empty strings")
    else:
        for code in rec["icd11"]:
            if not ICD11_RE.match(code.strip()):
                warnings.append(f"{p}.icd11: unusual ICD-11 code format {code!r}")

    # Enums
    if rec["course_type"] not in COURSE_TYPES:
        errors.append(f"{p}.course_type: invalid enum {rec['course_type']!r}")
    if rec["lethality_speed"] not in LETHALITY_SPEEDS:
        errors.append(f"{p}.lethality_speed: invalid enum {rec['lethality_speed']!r}")
    if rec["treatability_status"] not in TREATABILITY_STATUSES:
        errors.append(f"{p}.treatability_status: invalid enum {rec['treatability_status']!r}")

    # Epidemiology
    epi = rec["epidemiology"]
    if not isinstance(epi, dict):
        errors.append(f"{p}.epidemiology: must be a JSON object")
    else:
        epi_req = {
            "annual_deaths_global",
            "annual_deaths_china",
            "prevalence_global",
            "prevalence_china",
        }
        epi_missing = epi_req - set(epi.keys())
        if epi_missing:
            errors.append(f"{p}.epidemiology: missing keys {sorted(epi_missing)}")
        else:
            g_val = validate_mortality_block(
                epi["annual_deaths_global"],
                "global",
                f"{p}.epidemiology.annual_deaths_global",
                MAX_GLOBAL_DEATHS_PER_YEAR,
                errors,
                warnings,
            )
            c_val = validate_mortality_block(
                epi["annual_deaths_china"],
                "china",
                f"{p}.epidemiology.annual_deaths_china",
                MAX_CHINA_DEATHS_PER_YEAR,
                errors,
                warnings,
            )
            stats["global_deaths_null"] = g_val is None
            stats["china_deaths_null"] = c_val is None
            if g_val is not None and c_val is not None and c_val > g_val:
                errors.append(
                    f"{p}.epidemiology: annual_deaths_china ({c_val:,.0f}) exceeds annual_deaths_global ({g_val:,.0f})"
                )
            if not is_non_empty_str(epi["prevalence_global"]):
                errors.append(f"{p}.epidemiology.prevalence_global: must be non-empty string")
            if not is_non_empty_str(epi["prevalence_china"]):
                errors.append(f"{p}.epidemiology.prevalence_china: must be non-empty string")

    # Clinical management
    cm = rec["clinical_management"]
    if not isinstance(cm, dict):
        errors.append(f"{p}.clinical_management: must be a JSON object")
    else:
        cm_req = {
            "acute_emergency_protocol_zh",
            "standard_drugs",
            "non_drug_therapies_zh",
            "annual_total_cost_china_cny",
        }
        cm_missing = cm_req - set(cm.keys())
        if cm_missing:
            errors.append(f"{p}.clinical_management: missing keys {sorted(cm_missing)}")
        else:
            acute_proto = cm["acute_emergency_protocol_zh"]
            is_acute_or_fast = rec.get("course_type") in {
                "acute",
                "acute_on_chronic",
            } or rec.get("lethality_speed") in {"hyperacute_minutes", "acute_hours_days"}
            if acute_proto is None:
                if is_acute_or_fast:
                    errors.append(
                        f"{p}.clinical_management.acute_emergency_protocol_zh: required for acute/hyperacute disease"
                    )
            elif not is_non_empty_str(acute_proto):
                errors.append(
                    f"{p}.clinical_management.acute_emergency_protocol_zh: must be null or non-empty string"
                )

            drugs = cm["standard_drugs"]
            if not isinstance(drugs, list):
                errors.append(f"{p}.clinical_management.standard_drugs: must be a list")
            else:
                stats["drug_count"] = len(drugs)
                if len(drugs) == 0 and rec.get("treatability_status") in {
                    "curable",
                    "controllable",
                }:
                    warnings.append(
                        f"{p}.clinical_management.standard_drugs: empty drug list for {rec.get('treatability_status')} disease"
                    )
                for d_idx, drug in enumerate(drugs):
                    dp = f"{p}.standard_drugs[{d_idx}]"
                    if not isinstance(drug, dict):
                        errors.append(f"{dp}: must be a JSON object")
                        continue
                    d_req = {
                        "name_zh",
                        "name_en",
                        "drug_class",
                        "usage_role",
                        "china_nrdl_covered",
                        "cost_estimate_cny",
                        "cost_source",
                    }
                    d_missing = d_req - set(drug.keys())
                    if d_missing:
                        errors.append(f"{dp}: missing keys {sorted(d_missing)}")
                        continue
                    for dk in ("name_zh", "name_en", "drug_class", "usage_role"):
                        if not is_non_empty_str(drug[dk]):
                            errors.append(f"{dp}.{dk}: must be non-empty string")
                    if not isinstance(drug["china_nrdl_covered"], bool):
                        errors.append(f"{dp}.china_nrdl_covered: must be boolean")
                    elif drug["china_nrdl_covered"]:
                        stats["nrdl_covered_count"] += 1

                    cost = drug["cost_estimate_cny"]
                    csrc = drug["cost_source"]
                    if cost is not None:
                        if not is_non_empty_str(cost):
                            errors.append(f"{dp}.cost_estimate_cny: must be null or non-empty string")
                        if not is_non_empty_str(csrc):
                            errors.append(
                                f"{dp}.cost_source: required non-empty string when cost_estimate_cny is provided"
                            )
                    elif csrc is not None and not isinstance(csrc, str):
                        errors.append(f"{dp}.cost_source: must be string or null")

            if not isinstance(cm["non_drug_therapies_zh"], list) or any(
                not is_non_empty_str(x) for x in cm["non_drug_therapies_zh"]
            ):
                errors.append(
                    f"{p}.clinical_management.non_drug_therapies_zh: must be list of non-empty strings"
                )

            if cm["annual_total_cost_china_cny"] is not None and not is_non_empty_str(
                cm["annual_total_cost_china_cny"]
            ):
                errors.append(
                    f"{p}.clinical_management.annual_total_cost_china_cny: must be null or non-empty string"
                )

            if "shandong_policy_match" in cm:
                errors.append(
                    f"{p}.clinical_management.shandong_policy_match: forbidden deprecated policy field must be omitted"
                )

    raw_rec_str = json.dumps(rec, ensure_ascii=False)
    pm = FORBIDDEN_POLICY_RE.search(raw_rec_str)
    if pm:
        errors.append(f"{p}: forbidden confidential/insurance term {pm.group(0)!r} detected")

    # Research progress
    rp = rec["research_progress"]
    if not isinstance(rp, dict):
        errors.append(f"{p}.research_progress: must be a JSON object")
    else:
        rp_req = {"highest_pipeline_stage", "breakthroughs_and_trials_zh", "key_unmet_needs_zh"}
        rp_missing = rp_req - set(rp.keys())
        if rp_missing:
            errors.append(f"{p}.research_progress: missing keys {sorted(rp_missing)}")
        else:
            if rp["highest_pipeline_stage"] not in PIPELINE_STAGES:
                errors.append(
                    f"{p}.research_progress.highest_pipeline_stage: invalid enum {rp['highest_pipeline_stage']!r}"
                )
            if not is_non_empty_str(rp["breakthroughs_and_trials_zh"]):
                errors.append(
                    f"{p}.research_progress.breakthroughs_and_trials_zh: must be non-empty string"
                )
            if not is_non_empty_str(rp["key_unmet_needs_zh"]):
                errors.append(f"{p}.research_progress.key_unmet_needs_zh: must be non-empty string")

    # Sources
    sources = rec["sources"]
    if not isinstance(sources, list) or len(sources) == 0:
        errors.append(f"{p}.sources: must be a non-empty list of citations")
    else:
        for s_idx, src in enumerate(sources):
            sp = f"{p}.sources[{s_idx}]"
            if not isinstance(src, dict):
                errors.append(f"{sp}: must be a JSON object")
                continue
            s_req = {"title", "url", "publisher", "year"}
            s_missing = s_req - set(src.keys())
            if s_missing:
                errors.append(f"{sp}: missing keys {sorted(s_missing)}")
                continue
            if not is_non_empty_str(src["title"]):
                errors.append(f"{sp}.title: must be non-empty string")
            if not is_non_empty_str(src["publisher"]):
                errors.append(f"{sp}.publisher: must be non-empty string")
            if not is_valid_url(src["url"]):
                errors.append(f"{sp}.url: invalid or placeholder URL {src['url']!r}")
            else:
                stats["source_urls"].append(src["url"].strip())
            if not isinstance(src["year"], str) or not YEAR_4DIGIT_RE.search(src["year"]):
                errors.append(
                    f"{sp}.year: must be string containing a valid 4-digit year, got {src['year']!r}"
                )

    return stats


def validate_curated_file(path: Path) -> dict[str, Any]:
    """Validate one curated domain JSON file and return structured report."""
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "file": str(path),
        "filename": path.name,
        "valid": False,
        "domain": None,
        "curator": None,
        "generated_at": None,
        "record_count": 0,
        "disease_id_populated": 0,
        "global_deaths_numeric": 0,
        "china_deaths_numeric": 0,
        "drug_count": 0,
        "nrdl_covered_count": 0,
        "unique_urls": 0,
        "course_type_counts": {},
        "lethality_speed_counts": {},
        "treatability_counts": {},
        "pipeline_stage_counts": {},
        "errors": errors,
        "warnings": warnings,
    }

    if not path.exists():
        errors.append(f"File does not exist: {path}")
        return result

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        errors.append(f"Invalid JSON syntax: {exc}")
        return result

    if not isinstance(data, dict):
        errors.append("Top-level JSON must be an object")
        return result

    top_req = {"domain", "generated_at", "curator", "records"}
    missing = top_req - set(data.keys())
    if missing:
        errors.append(f"Missing top-level keys: {sorted(missing)}")
        return result

    for k in ("domain", "curator"):
        if not is_non_empty_str(data[k]):
            errors.append(f"Top-level {k} must be a non-empty string")
    result["domain"] = data.get("domain")
    result["curator"] = data.get("curator")

    gen_at = data.get("generated_at")
    if not is_non_empty_str(gen_at):
        errors.append("Top-level generated_at must be an ISO-8601 date string")
    else:
        try:
            datetime.datetime.fromisoformat(gen_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append(f"Top-level generated_at is not valid ISO-8601: {gen_at!r}")
    result["generated_at"] = gen_at

    records = data.get("records")
    if not isinstance(records, list) or len(records) == 0:
        errors.append("Top-level records must be a non-empty list")
        return result

    result["record_count"] = len(records)
    if len(records) < 20:
        warnings.append(f"Record count ({len(records)}) is below expected target (>=22)")

    seen_en: set[str] = set()
    seen_zh: set[str] = set()
    urls: set[str] = set()
    course_ctr: Counter[str] = Counter()
    speed_ctr: Counter[str] = Counter()
    treat_ctr: Counter[str] = Counter()
    stage_ctr: Counter[str] = Counter()

    for idx, rec in enumerate(records):
        st = validate_curated_record(rec, idx, errors, warnings)
        if st["name_en"]:
            key_en = st["name_en"].lower()
            if key_en in seen_en:
                errors.append(f"records[{idx}]: duplicate name_en {st['name_en']!r}")
            seen_en.add(key_en)
        if st["name_zh"]:
            if st["name_zh"] in seen_zh:
                errors.append(f"records[{idx}]: duplicate name_zh {st['name_zh']!r}")
            seen_zh.add(st["name_zh"])
        if st["disease_id"]:
            result["disease_id_populated"] += 1
        if not st["global_deaths_null"]:
            result["global_deaths_numeric"] += 1
        if not st["china_deaths_null"]:
            result["china_deaths_numeric"] += 1
        result["drug_count"] += st["drug_count"]
        result["nrdl_covered_count"] += st["nrdl_covered_count"]
        urls.update(st["source_urls"])
        if isinstance(rec, dict):
            course_ctr[str(rec.get("course_type"))] += 1
            speed_ctr[str(rec.get("lethality_speed"))] += 1
            treat_ctr[str(rec.get("treatability_status"))] += 1
            rp = rec.get("research_progress")
            if isinstance(rp, dict):
                stage_ctr[str(rp.get("highest_pipeline_stage"))] += 1

    result["unique_urls"] = len(urls)
    result["course_type_counts"] = dict(course_ctr)
    result["lethality_speed_counts"] = dict(speed_ctr)
    result["treatability_counts"] = dict(treat_ctr)
    result["pipeline_stage_counts"] = dict(stage_ctr)
    result["valid"] = len(errors) == 0
    return result


def validate_open_backbone(raw_dir: Path, licenses_dir: Path) -> dict[str, Any]:
    """Verify open biomedical backbone files and attribution license completeness."""
    errors: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "valid": False,
        "raw_files": [],
        "errors": errors,
        "warnings": warnings,
    }

    if raw_dir.exists():
        raw_files = sorted(p for p in raw_dir.rglob("*") if p.is_file())
        report["raw_files"] = [{"name": p.name, "bytes": p.stat().st_size} for p in raw_files]
        if not raw_files:
            warnings.append(f"No files found in {raw_dir}")
    else:
        warnings.append(f"Raw directory missing: {raw_dir}")

    ot_license = licenses_dir / "opentargets_database_LICENSE.txt"
    if not ot_license.exists() or ot_license.stat().st_size == 0:
        errors.append(f"Missing or empty license file: {ot_license}")

    report["valid"] = len(errors) == 0
    return report


def validate_database(
    db_path: Path,
    summary_path: Path,
    curated_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify data/salus.db schema, integrity, row counts, FTS5 search, and summary JSON."""
    errors: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {
        "db_path": str(db_path),
        "valid": False,
        "integrity_check": None,
        "tables": {},
        "fts5_tables": [],
        "fts5_tests": [],
        "summary_matches": None,
        "errors": errors,
        "warnings": warnings,
    }

    if not db_path.exists():
        errors.append(f"SQLite database missing: {db_path}")
        return report

    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    try:
        cur = conn.cursor()

        # Integrity check
        integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
        report["integrity_check"] = integrity
        if integrity != "ok":
            errors.append(f"PRAGMA integrity_check failed: {integrity}")

        # Inspect tables
        rows = cur.execute(
            "SELECT name, type, sql FROM sqlite_master WHERE type IN ('table', 'view') ORDER BY name"
        ).fetchall()
        table_meta = {r[0]: {"type": r[1], "sql": r[2] or ""} for r in rows}

        required_tables = [
            "diseases_master",
            "curated_dossiers",
            "curated_drugs",
            "who_mortality",
        ]
        for t in required_tables:
            if t not in table_meta:
                errors.append(f"Missing required table: {t}")
                continue
            cnt = cur.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            cols = [c[1] for c in cur.execute(f'PRAGMA table_info("{t}")').fetchall()]
            report["tables"][t] = {"row_count": cnt, "columns": cols}

        # Check diseases_master volume and quality
        if "diseases_master" in report["tables"]:
            dm = report["tables"]["diseases_master"]
            if dm["row_count"] < 20_000:
                errors.append(
                    f"diseases_master row count too low: {dm['row_count']} (expected >= 20,000)"
                )
            cols_lower = {c.lower() for c in dm["columns"]}
            if not any("mondo" in c or c == "disease_id" or c == "id" for c in cols_lower):
                errors.append("diseases_master missing MONDO/disease ID column")
            if "name_en" not in cols_lower:
                errors.append("diseases_master missing name_en column")
            else:
                non_null_en = cur.execute(
                    "SELECT COUNT(*) FROM diseases_master WHERE name_en IS NOT NULL AND TRIM(name_en) != ''"
                ).fetchone()[0]
                if non_null_en < dm["row_count"] * 0.98:
                    errors.append(
                        f"diseases_master has too many empty name_en rows: {non_null_en}/{dm['row_count']}"
                    )

        # Check curated_dossiers exact reconciliation with curated JSON files
        if "curated_dossiers" in report["tables"]:
            cd = report["tables"]["curated_dossiers"]
            expected_curated_total = sum(r["record_count"] for r in curated_results)
            if expected_curated_total > 0 and cd["row_count"] != expected_curated_total:
                errors.append(
                    f"curated_dossiers row count ({cd['row_count']}) != sum of curated JSON records ({expected_curated_total})"
                )
            elif cd["row_count"] == 0:
                errors.append("curated_dossiers table is empty")

        # Check who_mortality
        if "who_mortality" in report["tables"]:
            wm = report["tables"]["who_mortality"]
            if wm["row_count"] == 0:
                errors.append("who_mortality table is empty")

        # FTS5 discovery and live search verification
        fts_tables = [
            name
            for name, meta in table_meta.items()
            if "using fts5" in meta["sql"].lower()
            and not name.endswith(("_data", "_idx", "_docsize", "_config", "_content"))
        ]
        report["fts5_tables"] = fts_tables
        if not fts_tables:
            errors.append("No FTS5 virtual table found in data/salus.db")
        else:
            test_queries = [
                ("en_common", "diabetes"),
                ("en_acute", "embolism"),
                ("zh_common", "糖尿病"),
                ("zh_acute", "肺栓塞"),
                ("icd10", "I26"),
            ]
            # Test primary FTS table (prefer one covering diseases/search)
            primary_fts = fts_tables[0]
            for name in fts_tables:
                if any(k in name.lower() for k in ("disease", "search", "salus", "master")):
                    primary_fts = name
                    break

            for label, q in test_queries:
                t0 = time.perf_counter()
                try:
                    # Quote phrase for safe FTS5 MATCH across tokens/codes
                    fts_query = f'"{q}"'
                    cnt = cur.execute(
                        f'SELECT COUNT(*) FROM "{primary_fts}" WHERE "{primary_fts}" MATCH ?',
                        (fts_query,),
                    ).fetchone()[0]
                    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)
                    report["fts5_tests"].append(
                        {"table": primary_fts, "label": label, "query": q, "hits": cnt, "ms": elapsed_ms}
                    )
                    if cnt == 0:
                        # Distinguish CJK unicode61 tokenization flaw vs missing data
                        if label.startswith("zh_"):
                            errors.append(
                                f"FTS5 query {q!r} on {primary_fts} returned 0 hits (check CJK tokenization / trigram / indexed Chinese text)"
                            )
                        else:
                            errors.append(
                                f"FTS5 query {q!r} on {primary_fts} returned 0 hits"
                            )
                except sqlite3.Error as exc:
                    errors.append(f"FTS5 query {q!r} on {primary_fts} failed: {exc}")

        # Verify salus_summary.json if present
        if not summary_path.exists():
            errors.append(f"Missing summary file: {summary_path}")
        else:
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary_str = json.dumps(summary)
                mismatches = []
                for tname, tinfo in report["tables"].items():
                    expected_n = str(tinfo["row_count"])
                    if expected_n not in summary_str:
                        mismatches.append(f"{tname}={expected_n}")
                if mismatches:
                    warnings.append(
                        f"salus_summary.json may not reflect exact DB counts for: {mismatches}"
                    )
                report["summary_matches"] = len(mismatches) == 0
            except Exception as exc:
                errors.append(f"Invalid JSON in {summary_path}: {exc}")
    finally:
        conn.close()

    report["valid"] = len(errors) == 0
    return report


def check_cross_file_duplicates(curated_results: list[dict[str, Any]], curated_dir: Path) -> list[str]:
    """Detect duplicate disease names across separate curated JSON files."""
    seen_en: dict[str, list[str]] = {}
    for res in curated_results:
        fpath = Path(res["file"])
        if not fpath.exists():
            continue
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
            for rec in data.get("records", []):
                ne = (rec.get("name_en") or "").strip().lower()
                if ne:
                    seen_en.setdefault(ne, []).append(fpath.name)
        except Exception:
            continue
    overlaps = []
    for ne, files in sorted(seen_en.items()):
        if len(files) > 1:
            overlaps.append(f"{ne!r} appears in {files}")
    return overlaps


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify Salus knowledge base files and SQLite DB.")
    parser.add_argument("--curated-dir", type=Path, default=Path("data/curated"))
    parser.add_argument("--file", type=Path, help="Validate a single curated JSON file")
    parser.add_argument("--raw-dir", type=Path, default=Path("data/raw"))
    parser.add_argument("--licenses-dir", type=Path, default=Path(".licenses"))
    parser.add_argument("--db", type=Path, default=Path("data/salus.db"))
    parser.add_argument("--summary", type=Path, default=Path("data/salus_summary.json"))
    parser.add_argument("--curated-only", action="store_true")
    parser.add_argument("--db-only", action="store_true")
    parser.add_argument("--strict-all-files", action="store_true", help="Require all 5 core domain files + DB")
    parser.add_argument("--json-report", type=Path, help="Optional path to write full JSON verification report")
    args = parser.parse_args()

    overall_ok = True
    full_report: dict[str, Any] = {
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "curated": [],
        "cross_file_overlaps": [],
        "backbone": None,
        "database": None,
    }

    if args.file:
        res = validate_curated_file(args.file)
        full_report["curated"].append(res)
        print(f"[{ 'PASS' if res['valid'] else 'FAIL' }] {res['filename']}: {res['record_count']} records, "
              f"{res['drug_count']} drugs ({res['nrdl_covered_count']} NRDL), {res['unique_urls']} URLs")
        for w in res["warnings"]:
            print(f"  WARN: {w}")
        for e in res["errors"]:
            print(f"  ERROR: {e}")
        overall_ok = res["valid"]
    else:
        if not args.db_only:
            curated_files = sorted(args.curated_dir.glob("*.json")) if args.curated_dir.exists() else []
            if args.strict_all_files:
                existing_names = {p.name for p in curated_files}
                for exp in EXPECTED_CURATED_FILES:
                    if exp not in existing_names:
                        print(f"[FAIL] Missing expected curated file: {args.curated_dir / exp}")
                        overall_ok = False
            for fpath in curated_files:
                res = validate_curated_file(fpath)
                full_report["curated"].append(res)
                status = "PASS" if res["valid"] else "FAIL"
                print(
                    f"[{status}] {res['filename']}: {res['record_count']} records | "
                    f"disease_id={res['disease_id_populated']}/{res['record_count']} | "
                    f"global_deaths={res['global_deaths_numeric']}/{res['record_count']} | "
                    f"drugs={res['drug_count']} (NRDL={res['nrdl_covered_count']}) | "
                    f"urls={res['unique_urls']}"
                )
                for w in res["warnings"]:
                    print(f"  WARN: {w}")
                for e in res["errors"]:
                    print(f"  ERROR: {e}")
                if not res["valid"]:
                    overall_ok = False

            overlaps = check_cross_file_duplicates(full_report["curated"], args.curated_dir)
            full_report["cross_file_overlaps"] = overlaps
            if overlaps:
                print(f"[INFO] Cross-file disease overlaps ({len(overlaps)}):")
                for ov in overlaps:
                    print(f"  OVERLAP: {ov}")

        if not args.curated_only:
            bb_res = validate_open_backbone(args.raw_dir, args.licenses_dir)
            full_report["backbone"] = bb_res
            bb_status = "PASS" if bb_res["valid"] else "FAIL"
            print(f"[{bb_status}] backbone & licenses: {len(bb_res['raw_files'])} raw files")
            for w in bb_res["warnings"]:
                print(f"  WARN: {w}")
            for e in bb_res["errors"]:
                print(f"  ERROR: {e}")
            if not bb_res["valid"]:
                overall_ok = False

            db_res = validate_database(args.db, args.summary, full_report["curated"])
            full_report["database"] = db_res
            db_status = "PASS" if db_res["valid"] else "FAIL"
            print(f"[{db_status}] database ({args.db}): integrity={db_res['integrity_check']}")
            for tname, tinfo in db_res["tables"].items():
                print(f"  TABLE {tname}: {tinfo['row_count']:,} rows")
            for ft in db_res["fts5_tests"]:
                print(f"  FTS5 [{ft['table']}] {ft['label']} ({ft['query']}): {ft['hits']} hits in {ft['ms']} ms")
            for w in db_res["warnings"]:
                print(f"  WARN: {w}")
            for e in db_res["errors"]:
                print(f"  ERROR: {e}")
            if not db_res["valid"]:
                overall_ok = False

    if args.json_report:
        args.json_report.parent.mkdir(parents=True, exist_ok=True)
        args.json_report.write_text(json.dumps(full_report, ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
