#!/usr/bin/env python3
"""Salus Backbone Data Ingestion & Normalization Pipeline.

Extracts, parses, and normalizes global authoritative biomedical backbones into data/raw/:
- MONDO Disease Ontology (mondo.obo -> mondo_diseases.json)
- Orphadata rare diseases (en_product1/6/9_prev/9_ages.xml -> orphanet_diseases.json)
- Open Targets Platform 26.09 (parquet -> opentargets_diseases_drugs.json)
- WHO Global Health Estimates 2000-2023 & WHO GHO indicators (who_mortality.json)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq


SALUS_ROOT = Path(r"D:\Desktop\Publication\Salus")
RAW_DIR = SALUS_ROOT / "data" / "raw"
RAW_SOURCES_DIR = RAW_DIR / "sources"

CLINICAL_STAGE_RANK = {
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


def write_json(path: Path, payload: Any, indent: int | None = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=indent)



def _parse_obo_quoted_field(raw: str) -> tuple[str, list[str]]:
    """Parse OBO quoted string `"text" [src1, src2]` cleanly."""
    if not raw.startswith('"'):
        return raw, []
    buf: list[str] = []
    i = 1
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            buf.append(raw[i + 1])
            i += 2
        elif ch == '"':
            i += 1
            break
        else:
            buf.append(ch)
            i += 1
    rest = raw[i:].strip()
    sources: list[str] = []
    m_br = re.search(r"\[(.*?)\]", rest)
    if m_br and m_br.group(1).strip():
        sources = [s.strip() for s in m_br.group(1).split(",") if s.strip()]
    return "".join(buf), sources


def _parse_synonym(raw: str) -> dict[str, Any]:
    text, sources = _parse_obo_quoted_field(raw)
    after_quote = raw
    if raw.startswith('"'):
        # Locate scope token after closing unescaped quote
        m = re.search(r'(?<!\\)"\s+(EXACT|NARROW|BROAD|RELATED)\b', raw)
        scope = m.group(1) if m else "RELATED"
    else:
        scope = "RELATED"
    return {"text": text, "scope": scope, "sources": sources}


def ingest_mondo() -> dict[str, Any]:
    """Parse MONDO Disease Ontology OBO file into data/raw/mondo_diseases.json."""
    print("\n=== [Stage 2] Parsing MONDO Disease Ontology ===")
    obo_path = RAW_SOURCES_DIR / "mondo.obo"
    header_version = None
    raw_terms: dict[str, dict[str, Any]] = {}
    cur: dict[str, Any] | None = None
    in_header = True

    def flush_term(t: dict[str, Any] | None) -> None:
        if t and t.get("id", "").startswith("MONDO:") and not t.get("is_obsolete"):
            raw_terms[t["id"]] = t

    with obo_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if in_header:
                if line.startswith("data-version:"):
                    header_version = line.split(":", 1)[1].strip()
                if line == "[Term]":
                    in_header = False
                    cur = {"synonyms": [], "xrefs": [], "is_a": [], "subsets": []}
                continue

            if line == "[Term]":
                flush_term(cur)
                cur = {"synonyms": [], "xrefs": [], "is_a": [], "subsets": []}
            elif line.startswith("[") and line.endswith("]"):
                flush_term(cur)
                cur = None
            elif cur is not None:
                if line.startswith("id: "):
                    cur["id"] = line[4:].strip()
                elif line.startswith("name: "):
                    cur["name"] = line[6:].strip()
                elif line.startswith("def: "):
                    text, srcs = _parse_obo_quoted_field(line[5:].strip())
                    cur["definition"] = text
                    if srcs:
                        cur["def_sources"] = srcs
                elif line.startswith("synonym: "):
                    cur["synonyms"].append(_parse_synonym(line[9:].strip()))
                elif line.startswith("xref: "):
                    cur["xrefs"].append(line[6:].strip().split()[0])
                elif line.startswith("is_a: "):
                    cur["is_a"].append(line[6:].strip().split()[0])
                elif line.startswith("subset: "):
                    cur["subsets"].append(line[8:].strip().split()[0])
                elif line == "is_obsolete: true":
                    cur["is_obsolete"] = True
    flush_term(cur)

    # Identify informative top-level medical categories under MONDO axis wrapper nodes
    axis_roots = {
        "MONDO:0000001",  # disease
        "MONDO:0700096",  # human disease
        "MONDO:7770006",  # disease by body system or component
        "MONDO:7770007",  # disease by developmental or physiological process
        "MONDO:7770008",  # disease by etiologic mechanism
    }
    top_cats = {
        tid
        for tid, t in raw_terms.items()
        if tid not in axis_roots and any(pid in axis_roots for pid in t["is_a"])
    }
    # Precompute ancestor top-level categories for every term via memoized DFS
    cat_memo: dict[str, tuple[str, ...]] = {}

    def get_top_cats(tid: str, visiting: set[str]) -> tuple[str, ...]:
        if tid in cat_memo:
            return cat_memo[tid]
        if tid in axis_roots:
            return ()
        if tid in top_cats:
            res = (tid,)
            cat_memo[tid] = res
            return res
        term = raw_terms.get(tid)
        if not term:
            return ()
        cats: list[str] = []
        for pid in term["is_a"]:
            if pid in axis_roots or pid in visiting or pid not in raw_terms:
                continue
            visiting.add(pid)
            for c in get_top_cats(pid, visiting):
                if c not in cats:
                    cats.append(c)
            visiting.remove(pid)
        res = tuple(cats)
        cat_memo[tid] = res
        return res

    diseases: list[dict[str, Any]] = []
    stats_icd10 = 0
    stats_icd11 = 0
    stats_omim = 0
    stats_orpha = 0

    for tid in sorted(raw_terms):
        t = raw_terms[tid]
        icd10: list[str] = []
        icd11: list[str] = []
        omim: list[str] = []
        orphanet: list[str] = []
        extra_xrefs: dict[str, list[str]] = defaultdict(list)

        for x in t["xrefs"]:
            if ":" not in x:
                continue
            prefix, code = x.split(":", 1)
            if prefix in ("ICD10CM", "ICD10WHO", "ICD10"):
                icd10.append(f"{prefix}:{code}")
            elif prefix in ("icd11.foundation", "ICD11"):
                icd11.append(f"{prefix}:{code}")
            elif prefix in ("OMIM", "OMIMPS"):
                omim.append(f"{prefix}:{code}")
            elif prefix == "Orphanet":
                orphanet.append(code)
            elif prefix in ("DOID", "EFO", "MESH", "NCIT", "SCTID", "UMLS", "MEDGEN", "GARD", "ICD9", "ICDO", "ONCOTREE"):
                extra_xrefs[prefix.lower()].append(code)

        if icd10:
            stats_icd10 += 1
        if icd11:
            stats_icd11 += 1
        if omim:
            stats_omim += 1
        if orphanet:
            stats_orpha += 1

        parent_ids = [pid for pid in t["is_a"] if pid in raw_terms]
        parent_names = [raw_terms[pid].get("name", pid) for pid in parent_ids]
        top_cat_ids = get_top_cats(tid, {tid})
        top_categories = [{"id": cid, "name": raw_terms[cid].get("name", cid)} for cid in top_cat_ids]

        subsets = sorted(set(t["subsets"]))
        is_rare = bool(
            orphanet
            or any(s in ("rare", "inferred_rare", "nord_rare", "gard_rare") for s in subsets)
        )
        is_human_disease = top_cat_ids != ("MONDO:0005583",)

        # Deduplicate synonyms cleanly preserving text+scope
        syn_texts: list[dict[str, str]] = []
        seen_syn: set[tuple[str, str]] = set()
        for s in t["synonyms"]:
            k = (s["text"], s["scope"])
            if k not in seen_syn:
                seen_syn.add(k)
                syn_texts.append({"text": s["text"], "scope": s["scope"]})

        diseases.append(
            {
                "mondo_id": tid,
                "ot_id": tid.replace(":", "_"),
                "name": t.get("name"),
                "definition": t.get("definition"),
                "synonyms": syn_texts,
                "icd10": sorted(set(icd10)),
                "icd11": sorted(set(icd11)),
                "omim": sorted(set(omim)),
                "orphanet": sorted(set(orphanet)),
                "xrefs": dict(extra_xrefs),
                "parent_ids": parent_ids,
                "parent_names": parent_names,
                "top_level_categories": top_categories,
                "subsets": subsets,
                "is_human_disease": is_human_disease,
                "is_rare": is_rare,
            }
        )

    human_count = sum(1 for d in diseases if d["is_human_disease"])
    payload = {
        "metadata": {
            "source": "Monarch Initiative MONDO Disease Ontology (mondo.obo)",
            "data_version": header_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "total_diseases": len(diseases),
            "human_diseases": human_count,
            "non_human_animal_diseases": len(diseases) - human_count,
            "with_icd10": stats_icd10,
            "with_icd11": stats_icd11,
            "with_omim": stats_omim,
            "with_orphanet": stats_orpha,
            "with_definition": sum(1 for d in diseases if d["definition"]),
        },
        "diseases": diseases,
    }
    write_json(RAW_DIR / "mondo_diseases.json", payload, indent=None)
    print("  MONDO Summary:", json.dumps(payload["metadata"], ensure_ascii=False))
    return payload["metadata"]


def _elem_text(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    node = elem.find(path)
    if node is None or node.text is None:
        return None
    val = node.text.strip()
    return val or None


def ingest_orphanet() -> dict[str, Any]:
    """Parse & merge Orphadata XMLs (en_product1/6/9_prev/9_ages) -> orphanet_diseases.json."""
    print("\n=== [Stage 3] Parsing Orphadata XML Datasets ===")
    orphan_dir = RAW_SOURCES_DIR / "orphadata"
    disorders: dict[str, dict[str, Any]] = {}

    # 1. Base catalog + xrefs + synonyms + clinical summaries from en_product1.xml
    for event, elem in ET.iterparse(orphan_dir / "en_product1.xml", events=("end",)):
        if elem.tag != "Disorder":
            continue
        code = _elem_text(elem, "OrphaCode")
        if not code:
            elem.clear()
            continue

        icd10: list[str] = []
        icd11: list[str] = []
        omim: list[str] = []
        other_xrefs: dict[str, list[str]] = defaultdict(list)
        mapping_details: list[dict[str, Any]] = []

        for ref in elem.findall("./ExternalReferenceList/ExternalReference"):
            src = _elem_text(ref, "Source")
            val = _elem_text(ref, "Reference")
            if not src or not val:
                continue
            rel = _elem_text(ref, "./DisorderMappingRelation/Name")
            val_status = _elem_text(ref, "./DisorderMappingValidationStatus/Name")
            mapping_details.append({"source": src, "reference": val, "relation": rel, "validation": val_status})
            su = src.upper()
            if su == "ICD-10":
                icd10.append(val)
            elif su == "ICD-11":
                icd11.append(val)
            elif su == "OMIM":
                omim.append(val)
            else:
                other_xrefs[src.lower()].append(val)

        summaries = [
            ts.text.strip()
            for ts in elem.findall("./SummaryInformationList/SummaryInformation/TextSectionList/TextSection/Contents")
            if ts.text and ts.text.strip()
        ]
        synonyms = [
            s.text.strip()
            for s in elem.findall("./SynonymList/Synonym")
            if s.text and s.text.strip()
        ]

        disorders[code] = {
            "orpha_code": code,
            "orpha_id": f"ORPHA:{code}",
            "name": _elem_text(elem, "Name"),
            "expert_link": _elem_text(elem, "ExpertLink"),
            "disorder_type": _elem_text(elem, "./DisorderType/Name"),
            "disorder_group": _elem_text(elem, "./DisorderGroup/Name"),
            "synonyms": synonyms,
            "summary": " ".join(summaries) if summaries else None,
            "icd10": sorted(set(icd10)),
            "icd11": sorted(set(icd11)),
            "omim": sorted(set(omim)),
            "other_xrefs": dict(other_xrefs),
            "external_mappings_detail": mapping_details,
            "prevalences": [],
            "prevalence_summary": None,
            "inheritance": [],
            "age_of_onset": [],
            "genes": [],
        }
        elem.clear()

    # 2. Prevalence / incidence from en_product9_prev.xml
    for event, elem in ET.iterparse(orphan_dir / "en_product9_prev.xml", events=("end",)):
        if elem.tag != "Disorder":
            continue
        code = _elem_text(elem, "OrphaCode")
        if code and code in disorders:
            prev_list: list[dict[str, Any]] = []
            for p in elem.findall("./PrevalenceList/Prevalence"):
                val_moy_raw = _elem_text(p, "ValMoy")
                val_moy = float(val_moy_raw) if val_moy_raw is not None else None
                prev_list.append(
                    {
                        "type": _elem_text(p, "./PrevalenceType/Name"),
                        "qualification": _elem_text(p, "./PrevalenceQualification/Name"),
                        "prevalence_class": _elem_text(p, "./PrevalenceClass/Name"),
                        "val_moy_per_100k": val_moy,
                        "geographic": _elem_text(p, "./PrevalenceGeographic/Name"),
                        "validation_status": _elem_text(p, "./PrevalenceValidationStatus/Name"),
                        "source": _elem_text(p, "Source"),
                    }
                )
            disorders[code]["prevalences"] = prev_list
            # Pick most informative summary: Worldwide/Point/Birth prevalence first
            ranked = sorted(
                prev_list,
                key=lambda x: (
                    0 if x["geographic"] == "Worldwide" else 1,
                    0 if x["type"] in ("Point prevalence", "Prevalence at birth", "Annual incidence") else 1,
                    0 if x["validation_status"] == "Validated" else 1,
                    0 if x["prevalence_class"] else 1,
                ),
            )
            if ranked:
                best = ranked[0]
                if best["prevalence_class"]:
                    disorders[code]["prevalence_summary"] = f"{best['type']}: {best['prevalence_class']} ({best['geographic']})"
                elif best["val_moy_per_100k"] is not None:
                    disorders[code]["prevalence_summary"] = f"{best['type']} ({best['qualification']}): {best['val_moy_per_100k']} ({best['geographic']})"
        elem.clear()

    # 3. Inheritance & age of onset from en_product9_ages.xml
    ages_path = orphan_dir / "en_product9_ages.xml"
    if ages_path.exists():
        for event, elem in ET.iterparse(ages_path, events=("end",)):
            if elem.tag != "Disorder":
                continue
            code = _elem_text(elem, "OrphaCode")
            if code and code in disorders:
                inh = [
                    n.text.strip()
                    for n in elem.findall("./TypeOfInheritanceList/TypeOfInheritance/Name")
                    if n.text and n.text.strip()
                ]
                ages = [
                    n.text.strip()
                    for n in elem.findall("./AverageAgeOfOnsetList/AverageAgeOfOnset/Name")
                    if n.text and n.text.strip()
                ]
                disorders[code]["inheritance"] = inh
                disorders[code]["age_of_onset"] = ages
            elem.clear()

    # 4. Causal / associated genes from en_product6.xml
    for event, elem in ET.iterparse(orphan_dir / "en_product6.xml", events=("end",)):
        if elem.tag != "Disorder":
            continue
        code = _elem_text(elem, "OrphaCode")
        if code and code in disorders:
            genes_out: list[dict[str, Any]] = []
            for assoc in elem.findall("./DisorderGeneAssociationList/DisorderGeneAssociation"):
                g = assoc.find("Gene")
                if g is None:
                    continue
                g_xrefs: dict[str, str] = {}
                for er in g.findall("./ExternalReferenceList/ExternalReference"):
                    s = _elem_text(er, "Source")
                    v = _elem_text(er, "Reference")
                    if s and v:
                        g_xrefs[s.lower()] = v
                genes_out.append(
                    {
                        "symbol": _elem_text(g, "Symbol"),
                        "name": _elem_text(g, "Name"),
                        "gene_type": _elem_text(g, "./GeneType/Name"),
                        "association_type": _elem_text(assoc, "./DisorderGeneAssociationType/Name"),
                        "association_status": _elem_text(assoc, "./DisorderGeneAssociationStatus/Name"),
                        "source_of_validation": _elem_text(assoc, "SourceOfValidation"),
                        "hgnc": g_xrefs.get("hgnc"),
                        "ensembl": g_xrefs.get("ensembl"),
                        "omim": g_xrefs.get("omim"),
                    }
                )
            disorders[code]["genes"] = genes_out
        elem.clear()

    disorder_list = [disorders[k] for k in sorted(disorders, key=lambda x: int(x))]
    meta = {
        "source": "Orphadata (Orphanet Rare Disease Knowledge Base, CC-BY-4.0)",
        "files": ["en_product1.xml", "en_product9_prev.xml", "en_product9_ages.xml", "en_product6.xml"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_disorders": len(disorder_list),
        "with_prevalence": sum(1 for d in disorder_list if d["prevalences"]),
        "with_inheritance": sum(1 for d in disorder_list if d["inheritance"]),
        "with_age_of_onset": sum(1 for d in disorder_list if d["age_of_onset"]),
        "with_genes": sum(1 for d in disorder_list if d["genes"]),
        "with_icd10": sum(1 for d in disorder_list if d["icd10"]),
        "with_icd11": sum(1 for d in disorder_list if d["icd11"]),
        "with_omim": sum(1 for d in disorder_list if d["omim"]),
    }
    write_json(RAW_DIR / "orphanet_diseases.json", {"metadata": meta, "diseases": disorder_list}, indent=None)
    print("  Orphadata Summary:", json.dumps(meta, ensure_ascii=False))
    return meta


def ingest_opentargets() -> dict[str, Any]:
    """Extract Open Targets diseases, drug molecules, MoA, & max trial stage per disease."""
    print("\n=== [Stage 4] Parsing Open Targets 26.09 Parquet Datasets ===")
    ot_dir = RAW_SOURCES_DIR / "opentargets"

    # 1. Drug mechanism of action indexed by chemblId
    moa_tbl = pq.read_table(ot_dir / "drug_mechanism_of_action" / "00000000.parquet")
    moa_by_drug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in moa_tbl.to_pylist():
        entry = {
            "mechanism_of_action": row.get("mechanismOfAction"),
            "action_type": row.get("actionType"),
            "target_name": row.get("targetName"),
            "target_type": row.get("targetType"),
            "targets": row.get("targets") or [],
        }
        for cid in row.get("chemblIds") or []:
            moa_by_drug[cid].append(entry)

    # 2. Drug molecules (compact: drop heavy SMILES/molblock/InChIKey geometry fields)
    mol_tbl = pq.read_table(
        ot_dir / "drug_molecule" / "00000000.parquet",
        columns=["id", "name", "drugType", "maximumClinicalStage", "tradeNames", "description"],
    )
    molecules: dict[str, dict[str, Any]] = {}
    for row in mol_tbl.to_pylist():
        did = row["id"]
        molecules[did] = {
            "drug_id": did,
            "name": row.get("name"),
            "drug_type": row.get("drugType"),
            "maximum_clinical_stage": row.get("maximumClinicalStage"),
            "trade_names": row.get("tradeNames") or [],
            "description": row.get("description"),
            "mechanisms": moa_by_drug.get(did, []),
        }

    # 3. Clinical indications rolled up per diseaseId
    ci_tbl = pq.read_table(ot_dir / "clinical_indication" / "00000000.parquet")
    per_disease_ind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in ci_tbl.to_pylist():
        per_disease_ind[row["diseaseId"]].append(
            {
                "drug_id": row["drugId"],
                "max_clinical_stage": row.get("maxClinicalStage") or "UNKNOWN",
                "report_count": len(row.get("clinicalReportIds") or []),
            }
        )

    # 4. Disease nodes (45,896 rows)
    dis_tbl = pq.read_table(
        ot_dir / "disease" / "00000000.parquet",
        columns=["id", "name", "description", "dbXRefs", "therapeuticAreas", "ontology"],
    )
    ta_name_map: dict[str, str] = {}
    dis_rows = dis_tbl.to_pylist()
    for row in dis_rows:
        ont = row.get("ontology") or {}
        if ont.get("isTherapeuticArea"):
            ta_name_map[row["id"]] = row.get("name") or row["id"]

    diseases_out: list[dict[str, Any]] = []
    diseases_with_indications = 0
    diseases_with_approved = 0

    for row in dis_rows:
        did = row["id"]
        ont = row.get("ontology") or {}
        ind_list = per_disease_ind.get(did, [])

        # Deduplicate drug_id per disease keeping highest stage if duplicated
        best_drug_stage: dict[str, dict[str, Any]] = {}
        for ind in ind_list:
            drug_id = ind["drug_id"]
            st = ind["max_clinical_stage"]
            rk = CLINICAL_STAGE_RANK.get(st, 0)
            prev = best_drug_stage.get(drug_id)
            if prev is None or rk > prev["rank"]:
                mol = molecules.get(drug_id, {})
                best_drug_stage[drug_id] = {
                    "drug_id": drug_id,
                    "drug_name": mol.get("name"),
                    "drug_type": mol.get("drug_type"),
                    "max_clinical_stage": st,
                    "rank": rk,
                    "report_count": ind["report_count"],
                }

        stage_counts: Counter[str] = Counter()
        highest_stage = None
        highest_rank = -1
        approved_drugs: list[dict[str, Any]] = []
        pipeline_drugs: list[dict[str, Any]] = []

        for dinfo in sorted(best_drug_stage.values(), key=lambda x: (-x["rank"], x.get("drug_name") or x["drug_id"])):
            st = dinfo["max_clinical_stage"]
            stage_counts[st] += 1
            if dinfo["rank"] > highest_rank:
                highest_rank = dinfo["rank"]
                highest_stage = st
            compact_d = {
                "drug_id": dinfo["drug_id"],
                "drug_name": dinfo["drug_name"],
                "drug_type": dinfo["drug_type"],
                "max_clinical_stage": st,
                "report_count": dinfo["report_count"],
            }
            if st == "APPROVAL":
                approved_drugs.append(compact_d)
            else:
                pipeline_drugs.append(compact_d)

        if best_drug_stage:
            diseases_with_indications += 1
        if approved_drugs:
            diseases_with_approved += 1

        ta_ids = row.get("therapeuticAreas") or []
        diseases_out.append(
            {
                "id": did,
                "name": row.get("name"),
                "description": row.get("description"),
                "db_xrefs": row.get("dbXRefs") or [],
                "is_therapeutic_area": bool(ont.get("isTherapeuticArea")),
                "therapeutic_areas": [{"id": t, "name": ta_name_map.get(t, t)} for t in ta_ids],
                "highest_clinical_stage": highest_stage,
                "highest_clinical_stage_rank": highest_rank if highest_rank >= 0 else None,
                "approved_drug_count": len(approved_drugs),
                "total_clinical_drug_count": len(best_drug_stage),
                "stage_counts": dict(stage_counts),
                "approved_drugs": approved_drugs,
                "pipeline_drugs": pipeline_drugs,
            }
        )

    meta = {
        "source": "Open Targets Platform 26.09 (CC0 1.0 Public Domain, 2026-09-24)",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_diseases": len(diseases_out),
        "therapeutic_area_count": len(ta_name_map),
        "diseases_with_clinical_indications": diseases_with_indications,
        "diseases_with_approved_drugs": diseases_with_approved,
        "total_clinical_indication_rows": ci_tbl.num_rows,
        "total_molecules": len(molecules),
        "total_moa_rows": moa_tbl.num_rows,
    }
    payload = {
        "metadata": meta,
        "therapeutic_areas": [{"id": k, "name": v} for k, v in sorted(ta_name_map.items())],
        "molecules": molecules,
        "diseases": diseases_out,
    }
    write_json(RAW_DIR / "opentargets_diseases_drugs.json", payload, indent=None)
    print("  Open Targets Summary:", json.dumps(meta, ensure_ascii=False))
    return meta


def _fetch_who_sources() -> None:
    """Download fresh WHO GHE & GHO datasets into data/raw/sources/who_ghe/."""
    who_dir = RAW_SOURCES_DIR / "who_ghe"
    who_dir.mkdir(parents=True, exist_ok=True)
    sel = (
        "DIM_YEAR_CODE,DIM_GHECAUSE_CODE,DIM_GHECAUSE_TITLE,"
        "DIM_AGEGROUP_CODE,DIM_SEX_CODE,ATTR_POPULATION_NUMERIC,"
        "VAL_DTHS_COUNT_NUMERIC,VAL_DTHS_COUNT_LOW,VAL_DTHS_COUNT_HIGH,VAL_DTHS_RATE100K_NUMERIC,"
        "VAL_PROP_DTHS_PERCENT,VAL_DALY_COUNT_NUMERIC,VAL_DALY_RATE100K_NUMERIC,VAL_PROP_DALY_PERCENT,"
        "VAL_YLL_COUNT_NUMERIC,VAL_YLD_COUNT_NUMERIC,FLAG_LEVEL,DIM_CAUSE_GROUP,FLAG_SINGLE_CAUSE,FLAG_RANKABLE,FLAG_SHOW"
    )

    def get_odata(ep: str, flt: str, extra: str) -> list[dict[str, Any]]:
        q = urllib.parse.quote(flt, safe="'(),=").replace(" ", "%20")
        url: str | None = f"https://xmart-api-public.who.int/DEX_CMS/{ep}?$filter={q}&$select={extra},{sel}"
        out: list[dict[str, Any]] = []
        while url:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.loads(r.read().decode("utf-8"))
            out.extend(d.get("value", []))
            url = d.get("@odata.nextLink")
        return out

    write_json(who_dir / "ghe_regions_2023_total_age.json", get_odata("GHE_REGIONS", "DIM_YEAR_CODE eq 2023 and DIM_AGEGROUP_CODE eq 'TOTAL'", "DIM_REGION_CODE"), indent=None)
    write_json(who_dir / "ghe_wrld_2000_2023.json", get_odata("GHE_REGIONS", "DIM_REGION_CODE eq 'WRLD' and DIM_AGEGROUP_CODE eq 'TOTAL' and DIM_SEX_CODE eq 'TOTAL'", "DIM_REGION_CODE"), indent=None)
    write_json(who_dir / "ghe_chn_2000_2023.json", get_odata("GHE_FULL", "DIM_COUNTRY_CODE eq 'CHN' and DIM_AGEGROUP_CODE eq 'TOTAL'", "DIM_COUNTRY_CODE"), indent=None)

    gho_inds = ["TB_e_mort_exc_tbhiv_num", "MALARIA_EST_DEATHS", "HIV_0000000006", "NCD_DTH_TOT", "MORT_MATERNALNUM"]
    gho_data = {}
    for ind in gho_inds:
        flt = urllib.parse.quote("SpatialDim in ('GLOBAL','CHN') and TimeDim ge 2015", safe="()'").replace(" ", "%20")
        req = urllib.request.Request(f"https://ghoapi.azureedge.net/api/{ind}?$filter={flt}", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            gho_data[ind] = json.loads(r.read().decode("utf-8")).get("value", [])
    write_json(who_dir / "gho_supplemental_indicators.json", gho_data, indent=None)


def _normalize_ghe_row(r: dict[str, Any], geo_key: str) -> dict[str, Any]:
    return {
        "location_code": r[geo_key],
        "year": r["DIM_YEAR_CODE"],
        "sex": r["DIM_SEX_CODE"],
        "ghe_cause_code": r["DIM_GHECAUSE_CODE"],
        "cause_title": r["DIM_GHECAUSE_TITLE"],
        "cause_level": r["FLAG_LEVEL"],
        "cause_group_id": r["DIM_CAUSE_GROUP"],
        "is_single_cause": bool(r.get("FLAG_SINGLE_CAUSE")),
        "is_rankable": bool(r.get("FLAG_RANKABLE")),
        "population": r.get("ATTR_POPULATION_NUMERIC"),
        "deaths": r.get("VAL_DTHS_COUNT_NUMERIC"),
        "deaths_low": r.get("VAL_DTHS_COUNT_LOW"),
        "deaths_high": r.get("VAL_DTHS_COUNT_HIGH"),
        "deaths_rate_per_100k": r.get("VAL_DTHS_RATE100K_NUMERIC"),
        "deaths_percent_of_total": r.get("VAL_PROP_DTHS_PERCENT"),
        "dalys": r.get("VAL_DALY_COUNT_NUMERIC"),
        "dalys_rate_per_100k": r.get("VAL_DALY_RATE100K_NUMERIC"),
        "dalys_percent_of_total": r.get("VAL_PROP_DALY_PERCENT"),
        "ylls": r.get("VAL_YLL_COUNT_NUMERIC"),
        "ylds": r.get("VAL_YLD_COUNT_NUMERIC"),
    }


def ingest_who_mortality(fetch: bool = False) -> dict[str, Any]:
    """Normalize WHO GHE 2000-2023 + WHO GHO indicators -> data/raw/who_mortality.json."""
    print("\n=== [Stage 5] Building WHO Global & China Mortality Dataset ===")
    who_dir = RAW_SOURCES_DIR / "who_ghe"
    if fetch or not (who_dir / "ghe_regions_2023_total_age.json").exists():
        _fetch_who_sources()

    with (who_dir / "ghe_regions_2023_total_age.json").open("r", encoding="utf-8") as fh:
        reg_2023 = json.load(fh)
    with (who_dir / "ghe_wrld_2000_2023.json").open("r", encoding="utf-8") as fh:
        wrld_ts = json.load(fh)
    with (who_dir / "ghe_chn_2000_2023.json").open("r", encoding="utf-8") as fh:
        chn_ts = json.load(fh)
    with (who_dir / "gho_supplemental_indicators.json").open("r", encoding="utf-8") as fh:
        gho_raw = json.load(fh)

    cause_group_names = {
        1: "Communicable, maternal, perinatal and nutritional conditions",
        2: "Noncommunicable diseases",
        3: "Injuries",
    }

    global_2023 = sorted(
        [_normalize_ghe_row(r, "DIM_REGION_CODE") for r in reg_2023 if r["DIM_REGION_CODE"] == "WRLD"],
        key=lambda x: (x["sex"], x["ghe_cause_code"]),
    )
    regional_2023 = sorted(
        [_normalize_ghe_row(r, "DIM_REGION_CODE") for r in reg_2023 if r["DIM_REGION_CODE"] != "WRLD" and r["DIM_SEX_CODE"] == "TOTAL"],
        key=lambda x: (x["location_code"], x["ghe_cause_code"]),
    )
    china_2023 = sorted(
        [_normalize_ghe_row(r, "DIM_COUNTRY_CODE") for r in chn_ts if r["DIM_YEAR_CODE"] == 2023],
        key=lambda x: (x["sex"], x["ghe_cause_code"]),
    )
    global_ts_norm = sorted(
        [_normalize_ghe_row(r, "DIM_REGION_CODE") for r in wrld_ts],
        key=lambda x: (x["year"], x["ghe_cause_code"]),
    )
    china_ts_norm = sorted(
        [_normalize_ghe_row(r, "DIM_COUNTRY_CODE") for r in chn_ts if r["DIM_SEX_CODE"] == "TOTAL"],
        key=lambda x: (x["year"], x["ghe_cause_code"]),
    )

    cause_catalog_map: dict[int, dict[str, Any]] = {}
    for r in global_2023:
        if r["sex"] == "TOTAL":
            cause_catalog_map[r["ghe_cause_code"]] = {
                "ghe_cause_code": r["ghe_cause_code"],
                "cause_title": r["cause_title"],
                "cause_level": r["cause_level"],
                "cause_group_id": r["cause_group_id"],
                "cause_group_name": cause_group_names.get(r["cause_group_id"], "All / Other"),
                "is_single_cause": r["is_single_cause"],
                "is_rankable": r["is_rankable"],
                "global_deaths_2023": r["deaths"],
                "global_deaths_2023_low": r["deaths_low"],
                "global_deaths_2023_high": r["deaths_high"],
                "global_dalys_2023": r["dalys"],
            }

    gho_supplemental: dict[str, list[dict[str, Any]]] = {}
    for ind, rows in gho_raw.items():
        cleaned = []
        for row in rows:
            cleaned.append(
                {
                    "indicator": ind,
                    "location_code": row.get("SpatialDim"),
                    "year": row.get("TimeDim"),
                    "sex": row.get("Dim1"),
                    "cause_dim": row.get("Dim2"),
                    "numeric_value": row.get("NumericValue"),
                    "low": row.get("Low"),
                    "high": row.get("High"),
                    "display_value": row.get("Value"),
                }
            )
        gho_supplemental[ind] = sorted(cleaned, key=lambda x: (str(x["location_code"]), int(x["year"] or 0), str(x["sex"])))

    all_causes_2023 = next((c["global_deaths_2023"] for c in cause_catalog_map.values() if c["ghe_cause_code"] == 0), None)
    china_all_2023 = next((c["deaths"] for c in china_2023 if c["ghe_cause_code"] == 0 and c["sex"] == "TOTAL"), None)

    meta = {
        "source": "WHO Global Health Estimates 2000-2023 (xmart-api-public.who.int/DEX_CMS) + WHO GHO API",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "latest_ghe_year": 2023,
        "years_covered": "2000-2023",
        "unique_ghe_causes": len(cause_catalog_map),
        "global_2023_all_causes_deaths": all_causes_2023,
        "china_2023_all_causes_deaths": china_all_2023,
        "regions_included": sorted({r["location_code"] for r in regional_2023}),
        "gho_supplemental_indicators": {k: len(v) for k, v in gho_supplemental.items()},
    }
    payload = {
        "metadata": meta,
        "cause_catalog": [cause_catalog_map[k] for k in sorted(cause_catalog_map)],
        "global_2023": global_2023,
        "china_2023": china_2023,
        "regional_2023": regional_2023,
        "global_time_series_2000_2023": global_ts_norm,
        "china_time_series_2000_2023": china_ts_norm,
        "gho_supplemental": gho_supplemental,
    }
    write_json(RAW_DIR / "who_mortality.json", payload, indent=None)
    print("  WHO Mortality Summary:", json.dumps(meta, ensure_ascii=False))
    return meta


def main() -> None:
    parser = argparse.ArgumentParser(description="Salus backbone data ingestion pipeline")
    parser.add_argument(
        "--only",
        choices=["all", "mondo", "orphanet", "opentargets", "who"],
        default="all",
        help="Run a specific stage or all stages",
    )
    parser.add_argument("--fetch-who", action="store_true", help="Re-fetch WHO GHE/GHO API sources")
    args = parser.parse_args()

    summary: dict[str, Any] = {}
    if args.only in ("all", "mondo"):
        summary["mondo"] = ingest_mondo()
    if args.only in ("all", "orphanet"):
        summary["orphanet"] = ingest_orphanet()
    if args.only in ("all", "opentargets"):
        summary["opentargets"] = ingest_opentargets()
    if args.only in ("all", "who"):
        summary["who_mortality"] = ingest_who_mortality(fetch=args.fetch_who)

    print("\n=== ALL REQUESTED INGESTION STAGES COMPLETE ===")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
