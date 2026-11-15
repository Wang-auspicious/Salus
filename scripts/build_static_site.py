#!/usr/bin/env python3
"""
Salus Pure Static Site Exporter
Builds a self-contained static web bundle in ./dist that runs with zero backend server
(compatible with Aliyun ESA Pages, Tencent EdgeOne Pages, Cloudflare Pages, GitHub Pages).

Usage:
  uv run python scripts/build_static_site.py
  uv run python scripts/build_static_site.py --out dist
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from web import server as salus_server  # noqa: E402


def fnv1a_bucket(key: str) -> str:
    """Deterministic 2-hex-char bucket (00..ff) matching JS TextEncoder FNV-1a."""
    h = 0x811C9DC5
    for b in key.encode("utf-8"):
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"{h & 0xFF:02x}"


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def build_static_site(out_dir: Path) -> dict[str, Any]:
    t0 = time.perf_counter()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    assets_dir = out_dir / "assets"
    data_dir = out_dir / "data"
    shards_dir = data_dir / "diseases"
    assets_dir.mkdir(parents=True, exist_ok=True)
    shards_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy CSS/JS assets and rewrite index.html for relative static serving
    static_src = salus_server.STATIC_DIR
    for item in static_src.iterdir():
        if not item.is_file():
            continue
        if item.name == "index.html":
            continue
        shutil.copy2(item, assets_dir / item.name)

    raw_html = (static_src / "index.html").read_text(encoding="utf-8")
    static_html = raw_html.replace(
        'href="/static/styles.css"', 'href="./assets/styles.css"'
    ).replace(
        '<script type="module" src="/static/app.js"></script>',
        '<script>window.SALUS_STATIC_DATA_BASE = "./data";</script>\n  <script type="module" src="./assets/app.js"></script>',
    )
    (out_dir / "index.html").write_text(static_html, encoding="utf-8")
    (out_dir / "404.html").write_text(static_html, encoding="utf-8")
    (out_dir / ".nojekyll").write_text("", encoding="utf-8")
    _write_json(
        out_dir / "esa.jsonc",
        {
            "name": "salus-atlas",
            "installCommand": "",
            "buildCommand": "",
            "assets": {
                "directory": "./",
                "notFoundStrategy": "singlePageApplication",
            },
        },
    )

    # 2. Export core bootstrap, map points, flow, and roadmap payloads directly from server logic
    salus_server._CACHE.clear()
    bootstrap_payload = json.loads(salus_server.api_bootstrap().body.decode("utf-8"))
    roadmap_payload = json.loads(salus_server.api_roadmap().body.decode("utf-8"))
    atlas_nodes = salus_server.load_json_cached("atlas_nodes.json")
    flow_who = salus_server.load_json_cached("flow_who.json")

    _write_json(data_dir / "bootstrap.json", bootstrap_payload)
    _write_json(data_dir / "map_points.json", atlas_nodes)
    _write_json(data_dir / "flow.json", flow_who)
    _write_json(data_dir / "roadmap.json", roadmap_payload)

    # 3. Export search_extra.json, id_aliases.json, and 256 disease detail shards
    node_lookup = salus_server.get_atlas_node_lookup()
    conn = salus_server.get_db()
    try:
        buckets: dict[str, dict[str, Any]] = defaultdict(dict)
        id_aliases: dict[str, str] = {}
        curated_search_items: list[dict[str, Any]] = []
        curated_blobs: dict[str, str] = {}
        master_meta: dict[str, list[str]] = {}

        # Load master_meta & list of all canonical IDs
        master_ids: list[str] = []
        for r in conn.execute(
            "SELECT canonical_id, mondo_id, omim_id, icd11_primary, genes FROM diseases_master ORDER BY rowid"
        ):
            cid = r["canonical_id"]
            master_ids.append(cid)
            mondo = r["mondo_id"] or ""
            omim = r["omim_id"] or ""
            icd11 = r["icd11_primary"] or ""
            genes = r["genes"] or ""
            if (mondo and mondo != cid) or omim or icd11 or genes:
                master_meta[cid] = [mondo if mondo != cid else "", omim, icd11, genes]

        # Build curated search index and aliases
        curated_rows = conn.execute(
            """
            SELECT id, disease_id, master_canonical_id, name_zh, name_en, aliases_zh_json,
                   icd10_json, treatability_status, raw_record_json
            FROM curated_dossiers
            ORDER BY id
            """
        ).fetchall()

        extra_keys: list[str] = []
        curated_drugs_map: dict[int, list[str]] = defaultdict(list)
        for d in conn.execute("SELECT dossier_id, name_zh, name_en FROM curated_drugs"):
            for col in ("name_zh", "name_en"):
                if d[col]:
                    curated_drugs_map[int(d["dossier_id"])].append(d[col])

        for c in curated_rows:
            cid_num = int(c["id"])
            canonical = c["master_canonical_id"]
            primary_key = canonical if (canonical and canonical in node_lookup) else f"CURATED:{cid_num}"
            if primary_key not in master_ids and primary_key not in extra_keys:
                extra_keys.append(primary_key)

            curated_alias = f"CURATED:{cid_num}"
            if curated_alias != primary_key:
                id_aliases[curated_alias] = primary_key
            if c["disease_id"] and c["disease_id"] != primary_key:
                id_aliases[c["disease_id"]] = primary_key

            aliases_zh = json.loads(c["aliases_zh_json"] or "[]")
            icd10_list = json.loads(c["icd10_json"] or "[]")
            drug_names = curated_drugs_map.get(cid_num, [])
            m_meta = master_meta.get(primary_key, ["", "", "", ""])
            raw_snippet = salus_server._scrub_policy_text(c["raw_record_json"] or "") or ""
            blob_parts = [
                c["name_zh"] or "",
                c["name_en"] or "",
                c["disease_id"] or "",
                " ".join(aliases_zh),
                " ".join(icd10_list),
                " ".join(drug_names),
                m_meta[3] if len(m_meta) > 3 else "",
            ]
            short_blob = " ".join(p for p in blob_parts if p)
            curated_blobs[primary_key] = short_blob
            curated_search_items.append({
                "id": primary_key,
                "treatability_status": c["treatability_status"],
                "search": f"{short_blob} {raw_snippet}".lower(),
            })

        # Serialize all 25,697 payloads cleanly into 256 FNV-1a shards
        all_keys = master_ids + extra_keys
        exported_count = 0
        for key in all_keys:
            payload = salus_server.build_disease_detail_payload(
                conn,
                key,
                include_neighbors=False,
                include_node=False,
            )
            if not payload:
                continue
            bucket = fnv1a_bucket(key)
            buckets[bucket][key] = payload
            exported_count += 1

        for bucket, obj in buckets.items():
            _write_json(shards_dir / f"{bucket}.json", obj)

        _write_json(data_dir / "id_aliases.json", id_aliases)
        _write_json(
            data_dir / "search_extra.json",
            {
                "curated": curated_search_items,
                "curated_blobs": curated_blobs,
                "master_meta": master_meta,
            },
        )
    finally:
        conn.close()

    total_bytes = sum(p.stat().st_size for p in out_dir.rglob("*") if p.is_file())
    max_file = max((p for p in out_dir.rglob("*") if p.is_file()), key=lambda p: p.stat().st_size)
    elapsed = time.perf_counter() - t0
    stats = {
        "out_dir": str(out_dir),
        "exported_records": exported_count,
        "shard_count": len(buckets),
        "total_mb": round(total_bytes / (1024 * 1024), 2),
        "largest_file": max_file.name,
        "largest_file_mb": round(max_file.stat().st_size / (1024 * 1024), 2),
        "elapsed_sec": round(elapsed, 2),
    }
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 100% static Salus bundle into dist/")
    parser.add_argument("--out", default="dist", help="Output directory relative to project root or absolute")
    args = parser.parse_args()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = salus_server.PROJECT_ROOT / out_path
    stats = build_static_site(out_path)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
