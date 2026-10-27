# 📡 Ghost SIM Hunter — Zero-Revenue Active SIMs
**Intelligent Automation & OSS/BSS Revenue Assurance Engine (MTN Hackathon MVP)**

**MVP progress:** Improvements 1–5 are implemented: persistent case management, the unified real-data ingestion & quality-gate workflow, record-level entitlement reconciliation (`ledger-v2`), honest terminology (`estimated_opportunity_zar`, `risk_score`, `deviation_indicator_1..3`, explicit synthetic benchmark labels, and verified financial outcome fields), and the simplified daily analyst workbench. Start with the [team documentation](docs/README.md), [seven-step roadmap](docs/MVP_ROADMAP.md), [decision log](docs/DECISIONS.md), and [change log](docs/CHANGELOG.md).

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Mode: Recommend-Only](https://img.shields.io/badge/Guardrail-Zero%20Customer%20Impact%20(Recommend--Only)-10b981.svg)](#)
[![Synthetic Benchmark Precision@100: 100%](https://img.shields.io/badge/Synthetic%20Benchmark%20Precision%40100-100.0%25-ffcc00.svg)](#synthetic-benchmark-scorecard)

---

## 🎯 What Problem Does This Solve?

Telecom operators lose **1–5% of annual revenue** to unbilled network usage ("revenue leakage"). However, a naive threshold report looking for `Active Usage > X AND Direct Revenue == R0` fails in production because thousands of legitimate SIMs show `R0.00` direct revenue in a 30-day window due to:
1. **Multi-month / Rollover Prepaid Bundles** bought in an earlier billing period.
2. **Enterprise Pooled / Master Accounts** where child SIM usage bills to a corporate parent account (`CORP-MASTER-*`).
3. **Whitelisted Zero-Rated Public/Educational APNs** (`mtn.zero.edu`).
4. **Delayed Billing Cycles / Grace Windows** where valid usage has not yet settled on an invoice.

**Ghost SIM Hunter** provides a consistent four-step analyst workflow (**Upload or Load Sample → Validate & Review Data Quality → Run Detection → Investigate Cases**) backed by a **3-Layer Revenue Assurance & RAN Spatial Analytics Engine**.

---

## 🏗️ Three-Layer Detection Architecture (`ledger-v2`)

1. **Layer 1 — Record-Level Coverage Ledger & Entitlement Reconciliation (`src/reconciliation.py`, `src/ingestion.py`)**
   - Validates `subscriber`, `usage`, and `billing` inputs for duplicates, valid `YYYY-MM-DD` dates, usage before activation, unmatched `sim_id`s, 31-day window limits, and non-negative numeric ranges before scoring.
   - Evaluates usage **day-by-day and row-by-row** rather than applying SIM-level blanket exemptions:
     - **Mixed APN traffic** is split per usage record (`mtn.zero.edu` usage is covered up to fair-use limits while paid APN usage on the same SIM remains billable).
     - **Rollover bundle balances** (`entitlement_bundle_mb`, `entitlement_voice_min`) are consumed chronologically only within `[entitlement_valid_from, entitlement_valid_to]` and matching `entitlement_apn`, preserving any uncovered residual usage.
     - **Direct billed revenue** (`billed_revenue_zar`) and **parent enterprise allocations** (`parent_account_allocated_zar`) reduce remaining retail value without wiping out uncovered residuals.
     - **Delayed billing** (`pending_billing_zar` within `billing_grace_days` of `as_of_date`) and **missing billing evidence** (`missing_billing_evidence_zar` when no billing row exists) are separated from mature **estimated opportunity** (`estimated_opportunity_zar` / `unbilled_retail_zar`).

2. **Layer 2 — Peer-Relative Anomaly Scoring & Standardized Deviation Indicators**
   - Scores behavioral and spatial features (`unbilled_retail_zar`, `total_data_mb`, `total_voice_mins`, `avg_night_ratio`, `max_unique_destinations`, `max_distinct_imeis`, `cell_concentration_index`, `cell_zero_rev_density`).
   - Outputs an uncalibrated **Risk Score (`/ 100`)** (`risk_score`, not a calibrated probability) and standardized positive feature **Deviation Indicators** (`deviation_indicator_1..3`, replacing the earlier "SHAP" label).
   - Ranks the review queue by `Priority Score = Estimated Opportunity (ZAR) × (Risk Score / 100)`.

3. **Layer 3 — Automated 5-Point Forensic Investigation & Durable Internal Case Management (`src/case_store.py`)**
   - Audits **HLR/OCS sync**, **APN/Tariff eligibility**, **Device/IMEI rotation**, **Cell ID / Night usage profile**, and **Entitlement / Billing completeness**.
   - Tracks internal analyst workflow states (`NEW`, `INVESTIGATING`, `CONFIRMED_LEAKAGE`, `DISMISSED_BENIGN`) in SQLite (`data/cases.sqlite3`) and separates model **Estimated Opportunity** from human-verified **Confirmed Leakage**, **Cash Recovered**, and **Cost Avoided**.

---

## 📊 Synthetic Benchmark Scorecard (3,000 SIMs × 30 Days = 90,000 Daily CDRs)

> **Disclaimer:** *Synthetic benchmark results — not validated production accuracy.* Evaluated on the controlled 3,000-SIM synthetic dataset (`180` injected leakage SIMs across 5 archetypes and `300` benign `R0 direct revenue` negative controls). Uploaded datasets do not display accuracy claims unless ground-truth labels are present.

| Evaluation Metric (at Top-100 Daily Queue) | Ghost SIM Hunter (`ledger-v2`) | Naive `R0 Revenue` Baseline | Improvement |
|---|---|---|---|
| **Precision@100 (Synthetic Benchmark)** | **100.0%** (100 / 100) | **23.0%** (23 / 100) | **+77.0 pts (4.3× higher precision)** |
| **Benign Look-Alike Share @100** | **0 SIMs (0.0%)** | **77 SIMs (77.0%)** | **100% False-Positive Elimination** |
| **Recall@100** (of all 180 Ghost SIMs) | **55.6%** | **12.8%** | **+42.8 pts** |
| **Share of Total Leakage Value Captured @100** | **68.0%** | **21.1%** | **+46.9 pts value concentration** |
| **Top-100 Mature Estimated Opportunity** | **R 443,005 / 30d** | — | Valued at `R0.29/MB`, `R1.20/min`, `R0.50/SMS` |

---

## 🚀 Quick Start

### 1. Launch the Interactive Web Workbench
Install the web requirements in your Python environment (`.\.venv\Scripts\python.exe -m pip install -r requirements.txt`). For Windows setup details and configuring `GHOST_SIM_CASE_DB` / `GHOST_SIM_DATASET_DB` outside OneDrive, follow the [case management guide](docs/CASE_MANAGEMENT.md).

```bash
.\.venv\Scripts\python.exe server.py
```
Then open **[http://127.0.0.1:8501](http://127.0.0.1:8501)** in your browser to walk through the 4-step workbench:
1. **1. Data & Quality** — Upload `subscriber`, `usage`, and `billing` CSV/Parquet files (or stage the 4-SIM edge-case sample or 3,000-SIM synthetic benchmark), download CSV templates, review blocking errors and data-quality warnings, and run detection.
2. **2. Queue** — Streamlined 6-column analyst queue (`Select`, `SIM / hypothesis`, `Estimated opportunity`, `Risk / 100`, `Status`, `Owner`) with search, sorting, status/owner/hypothesis/plan filters, filter-matched KPI totals, filter-matched CSV export, batch actions, and a slide-over **Single-SIM Forensic Inspector** (`<dialog>`) with the 5-point evidence table, coverage ledger, deviation indicators, and verified financial outcome fields.
3. **3. Cell Hotspots** — RAN / Cell-ID hotspot clustering for SIM Box and bad provisioning detection.
4. **4. Benchmark** — Synthetic benchmark Precision@K diagnostics and data contract reference.

*(Optional: If you prefer the Streamlit UI, run `streamlit run app.py` — it shares the exact same `DatasetStore`, `CaseStore`, validation rules, and 4-step workflow.)*

### 2. Run the Automated Test Suite & CLI Benchmark Pipeline
Run the 26 unit and API tests covering case persistence, schema migration, record-level reconciliation, ingestion validation, and filter-matched API exports:
```bash
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
To regenerate the standalone synthetic benchmark CSVs (`data/daily_top_100_queue.csv`, `data/all_evaluated_sims.csv`, `data/backtest_summary.json`):
```bash
.\.venv\Scripts\python.exe run_pipeline.py
```

---

## 📁 Repository Structure

```text
├── server.py                  # Starlette / Uvicorn interactive web workbench (http://127.0.0.1:8501)
├── app.py                     # Unified Streamlit alternative sharing DatasetStore & CaseStore
├── run_pipeline.py            # CLI synthetic benchmark runner & scorecard generator
├── SPEC_AND_DEMO_GUIDE.md     # Specification & hackathon judge demo walkthrough
├── docs/                      # Roadmap, ADR decision log, changelog, and case management guide
├── src/
│   ├── ingestion.py           # CSV/Parquet parser, schema/date/duplicate/cross-table quality validator
│   ├── reconciliation.py      # Record-level coverage ledger (ledger-v2), risk score & deviation indicators
│   ├── dataset_store.py       # Content-addressed SQLite dataset & run store (datasets.sqlite3)
│   ├── case_store.py          # Durable SQLite case workflow & financial outcome store (cases.sqlite3)
│   ├── sample_data.py         # 4-SIM edge-case validation & reconciliation fixtures (EXAMPLE-A..D)
│   ├── schema.py              # Tariff constants, leakage archetypes, cell & APN catalogs
│   ├── pure_engine.py         # Synthetic benchmark generator & evaluation wrapper
│   └── dashboard.html         # 4-step interactive analyst workbench & slide-over forensic inspector
└── tests/
    ├── test_case_store.py
    ├── test_reconciliation_and_ingestion.py
    └── test_case_api.py
```
