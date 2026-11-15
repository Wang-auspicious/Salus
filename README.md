# Salus — Global Human Disease Atlas & Frontier Therapeutic Landscape

**愿天下再无疾病 · 全球人类疾病认知与攻关图谱**  
*May the world be free of disease — an open-science atlas mapping all known human diseases, their real-world lethality, standard-of-care accessibility, emergency warning protocols, and the scientific breakthroughs still needed for true cures.*

---

## 1. Mission & Motivation / 使命与初衷

Salus is built in memory of a 17-year-old friend lost within five minutes to hereditary thrombophilia, with a simple conviction: **every patient and family has the right to clearly understand a disease in plain language — how dangerous it is, what to do in an acute crisis, how it is monitored and managed today, and what scientific breakthroughs stand between current care and a genuine cure — while giving AI4LS (AI for Life Sciences) researchers a structured map of high-impact unmet problems.**

Salus 致力于消除生命科学知识与患者/家属认知之间的鸿沟：
- **看懂每种病与生死窗口**：清晰标注“数分钟突发致命 / 急危重症 / 亚急性 / 慢性进展”风险、家庭急救红线与长期防线；
- **看清现状与可及性**：客观梳理已有常规治疗药物、非药物手段、国内可及参考费用及科研临床进展；
- **锚定 AI4LS 攻关突破口**：提炼跨疾病共性技术难题（如睡眠与突发危象无创实时预警、单基因遗传缺陷源头修复、无创早期筛查、靶向递送等），帮助研究者找到最值得投入的方向。

---

## 2. Scale & Data Sources / 数据规模与权威来源

Built from normalized global biomedical ontologies, clinical drug-trial registries, global mortality surveillance, and verified deep dossiers:

| Layer | Current Scale | Source & License |
| --- | --- | --- |
| **Master Disease Ontology** | **25,696** canonical diseases (`25,697` mapped atlas nodes across **14** domains) | [MONDO](https://mondo.monarchinitiative.org/) (CC BY 4.0) · [Orphanet](https://www.orpha.net/) (CC BY 4.0) · [Open Targets 26.09](https://platform.opentargets.org/) (CC0 1.0) |
| **Deep Curated Dossiers** | **264** structured bilingual dossiers & **833** clinical drugs (actively expanding) | Peer-reviewed guidelines, ClinicalTrials.gov, WHO, CDE/NMPA, PubMed/NEJM/Lancet |
| **Global & China Mortality** | **14,137** WHO GHE (2000–2023, `226` causes) & GHO records | [WHO Global Health Estimates & GHO](https://www.who.int/data/gho) |
| **Rare & Genetic Annotations** | **18,528** rare diseases, **6,821** inheritance modes, **4,220** causal gene links | Orphanet (`en_product1/6/9`) & MONDO |

> All quantitative epidemiology metrics and clinical evidence citations follow strict verification rules (`SCHEMA.md`): unknown counts remain explicit (`null`) rather than fabricated, and every dossier is validated against `scripts/verify_salus.py`.

---

## 3. Repository Structure / 项目结构

```text
Salus/
├── SCHEMA.md                      # Canonical JSON schema & zero-hallucination curation rules
├── data/
│   ├── curated/                   # Deep curated bilingual disease dossiers (JSON, verified)
│   ├── raw/                       # Normalized MONDO/Orphanet/OpenTargets/WHO extracts (gitignored)
│   └── salus.db                   # Rebuilt unified SQLite + FTS5 database (gitignored)
├── scripts/
│   ├── ingest_backbone.py         # Normalizes MONDO, Orphanet, Open Targets & WHO datasets
│   ├── build_salus_db.py          # Builds unified SQLite + bilingual FTS5 search index
│   ├── prepare_web_atlas.py       # Computes 2D manifold atlas coordinates & multi-view aggregates
│   └── verify_salus.py            # Automated schema, DB integrity & FTS5 benchmark suite
└── web/
    ├── server.py                  # FastAPI + GZip backend serving atlas, search & dossiers
    ├── data/                      # Precomputed web atlas coordinates & hierarchy JSONs
    └── static/                    # Interactive Canvas Map, Flow, Directory & Roadmap UI (Light/Dark)
```

---

## 4. Quick Start / 本地运行与重建

Prerequisites: Python 3.11+ and [`uv`](https://docs.astral.sh/uv/).

```bash
# 1. Start the interactive web atlas (uses precomputed web/data/ + static UI;
#    if data/salus.db is built, full dossier API & FTS5 search are enabled)
uv run --with fastapi --with uvicorn python web/server.py --host 127.0.0.1 --port 8765
```

Then open `http://127.0.0.1:8765` in your browser.

To rebuild `data/salus.db` and `web/data/*.json` after adding or updating curated dossiers in `data/curated/*.json`:

```bash
uv run python scripts/build_salus_db.py
uv run python scripts/prepare_web_atlas.py
uv run python scripts/verify_salus.py
```

---

## 5. Architecture & Views / 前端四大视图

- **Map 星图全景**：25,697 种人类疾病二维聚类星图，按 14 个人体系统/机制大类与治疗推进阶段着色，支持中英文及 ICD-10/11 实时搜索、焦点高亮与邻近疾病联动。
- **Flow 趋势**：WHO 2000–2023 全球与中国 226 类死因时序流速图与高致死疾病专题对照。
- **Directory 目录**：四栏级联（治疗推进阶段 → 人体系统 → 疾病子群 → 疾病详情）分层浏览目录。
- **Roadmap 攻关与动态**：跨疾病 5 大核心技术赛道（睡梦实时监测预警、单基因源头修复、无创早筛诊断、精准靶向递送、危重器官保护）与前沿研究/临床试验动态追踪。

---

## 6. Attribution / 数据署名

When using integrated target-disease and drug clinical stage annotations from Open Targets Platform 26.09 (CC0 1.0, see `.licenses/opentargets_database_LICENSE.txt`), please cite:

> Buniello A, Suveges D, Cruz-Castillo C, et al. *"Open Targets Platform: facilitating therapeutic hypotheses building in drug discovery."* Nucleic Acids Res. 2025 Jan; 53(D1): D1467–D1475. DOI: `10.1093/nar/gkae1128` · PMID: `39657122`.
