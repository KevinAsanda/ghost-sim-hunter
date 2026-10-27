# 📡 Ghost SIM Hunter — Zero-Revenue Active SIMs
**Intelligent Automation & OSS/BSS Revenue Assurance Engine (MTN Hackathon MVP)**

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Mode: Recommend-Only](https://img.shields.io/badge/Guardrail-Zero%20Customer%20Impact%20(Recommend--Only)-10b981.svg)](#)
[![Precision@100: 100%](https://img.shields.io/badge/Backtest%20Precision%40100-100.0%25-ffcc00.svg)](#backtest-scorecard)

---

## 🎯 What Problem Does This Solve?

Telecom operators lose **1–5% of annual revenue** to unbilled network usage ("revenue leakage"). However, a naive threshold report looking for `Active Usage > X AND Direct Revenue == R0` fails in production because thousands of legitimate SIMs show `R0.00` direct revenue in a 30-day window due to:
1. **Multi-month / Rollover Prepaid Bundles** bought in an earlier billing period.
2. **Enterprise Pooled / Master Accounts** where child SIM usage bills to a corporate parent account (`CORP-MASTER-*`).
3. **Whitelisted Zero-Rated Public/Educational APNs** (`mtn.zero.edu`).

**Ghost SIM Hunter** is a **3-Layer Revenue Assurance & RAN Spatial Analytics Engine** that reconciles entitlements first, scores behavioral and spatial anomalies using an **Isolation Forest with per-feature attribution**, and outputs a **Daily Ranked Top-100 Case Queue** with automated **5-point forensic evidence and recommended actions**.

---

## 🏗️ Three-Layer Detection Architecture

1. **Layer 1 — Multi-System SQL Reconciliation & Entitlement Filtering (`src/pure_engine.py`, `src/detection_engine.py`)**
   - Joins **Subscriber Master (HLR vs. OCS/Billing status)**, **30-Day Network CDR Aggregates**, **Billing/Entitlements**, **APN Catalog**, and **Cell Site Metadata**.
   - Explicitly subtracts legitimate rollover bundles, parent corporate pool allocations, and fair-use zero-rated APNs—eliminating false positives before scoring.
   - Evaluates deterministic revenue assurance rules (`HLR_ACTIVE + OCS_MISSING`, `IOT_M2M + Consumer APN`, `Legacy Unrated Tariff`, `Internal Test SIM in Residential Cell`, `SIM Box Signature`).

2. **Layer 2 — Peer-Relative Anomaly Scoring & Attribution (`IsolationForest`)**
   - Runs a 100-tree Isolation Forest across 8 behavioral and spatial features (`unbilled_retail_zar`, `total_data_mb`, `total_voice_mins`, `avg_night_ratio`, `max_unique_destinations`, `max_distinct_imeis`, `cell_concentration_index`, `cell_zero_rev_density`).
   - Computes standardized per-feature attribution shares (`shap_driver_1..3`) so analysts see *why* each SIM scored at the top.
   - Ranks cases by `Priority Score = Uncovered Retail Value (ZAR) × Hybrid Confidence`.

3. **Layer 3 — Automated 5-Point Forensic Investigation & Action Recommendation**
   - Audits **HLR/OCS sync**, **APN/Tariff eligibility**, **Device/IMEI rotation**, **Cell ID / Night usage profile**, and **Parent/Bundle entitlements**.
   - Assigns each SIM a **Root-Cause Hypothesis**, **Owner Team**, **Recommended Action**, and **3-Tier Financial Valuation** (Retail Tariff Value vs. Direct Marginal Network Cost vs. Realistic Recoverable Cash).

---

## 📊 Backtest Scorecard (3,000 SIMs × 30 Days = 90,000 Daily CDRs)

Evaluated against **180 injected leakage SIMs** (across 5 archetypes) and **300 benign `R0 direct revenue` negative controls**:

| Evaluation Metric (at Top-100 Daily Queue) | Ghost SIM Hunter (3-Layer) | Naive `R0 Revenue` Baseline | Improvement |
|---|---|---|---|
| **Precision@100** | **100.0%** (100 / 100) | **23.0%** (23 / 100) | **+77.0 pts (4.3× higher precision)** |
| **Benign Look-Alike False Positives @100** | **0 SIMs (0.0% FPR)** | **77 SIMs (77.0% FPR)** | **100% False-Positive Elimination** |
| **Recall@100** (of all 180 Ghost SIMs) | **55.6%** | **12.8%** | **+42.8 pts** |
| **Share of Total Leakage Value Captured @100** | **68.0%** | **21.1%** | **+46.9 pts value concentration** |
| **Top-100 Unbilled Retail Tariff Value** | **R 443,005 / mo** (`R 5.32M / yr`) | — | Valued at `R0.29/MB`, `R1.20/min` |
| **Top-100 Direct Marginal Network Cost** | **R 99,546 / mo** (`R 1.19M / yr`) | — | `~18%` radio/backhaul + `R0.13/min` ICASA |
| **Top-100 Est. Cash Recoverable (Base Case)** | **R 301,813 / mo** (`R 3.62M / yr`) | — | Range: `R 226k – R 349k / mo` |

---

## 🚀 Quick Start

### 1. Run the CLI Pipeline & Backtest Verification
Zero external C-dependencies required—runs out-of-the-box on Python 3.10+ using the built-in SQLite3 + Pure-Python Isolation Forest engine (or DuckDB/scikit-learn via `app.py`):
```bash
python run_pipeline.py
```
Outputs:
- `data/daily_top_100_queue.csv` — Ranked Top-100 analyst queue with root causes, SHAP drivers, and recommended actions.
- `data/all_evaluated_sims.csv` — Full 3,000-SIM scored dataset.
- `data/backtest_summary.json` — Precision@K curves and financial valuation breakdown.

### 2. Launch the Interactive Web Workbench
```bash
python server.py
```
Then open **[http://127.0.0.1:8501](http://127.0.0.1:8501)** in your browser to explore:
1. **📋 Daily Top-100 Case Queue** (with batch ticketing & CSV export)
2. **🔍 Single-SIM Forensic Inspector** (5-point checklist, feature attribution bars, 30-day usage vs. billing timeline)
3. **🗺️ RAN / Cell-ID Cluster Analytics** (SIM Box & bad provisioning hotspot detection)
4. **📈 Backtest & Defensible Business Case** (Retail Tariff vs. Marginal Cost vs. Recoverable Cash)
5. **🔌 Architecture & Data Contract** (POPIA-safe schema contract)

*(Optional: If you prefer the Streamlit UI, run `streamlit run app.py`.)*

---

## 📁 Repository Structure

```text
├── server.py                  # FastAPI / Starlette live interactive web workbench (http://127.0.0.1:8501)
├── app.py                     # Streamlit interactive dashboard alternative
├── run_pipeline.py            # CLI pipeline runner & backtest scorecard generator
├── SPEC_AND_DEMO_GUIDE.md     # Phase 0 specification & 3-minute hackathon judge demo script
├── src/
│   ├── schema.py              # ZAR tariff assumptions, 5 leakage archetypes, 3 benign classes, cell/APN catalogs
│   ├── pure_engine.py         # Zero-dependency SQLite3 + 100-tree Isolation Forest + Attribution engine
│   ├── data_generator.py      # Pandas/DuckDB synthetic generator & custom CSV/Parquet schema validator
│   ├── detection_engine.py    # DuckDB + scikit-learn 3-layer hybrid detection pipeline
│   ├── backtest.py            # Precision@K, Recall@K, Value Share & FPR evaluation suite
│   └── dashboard.html         # 5-tab interactive frontend UI for server.py
└── data/
    ├── daily_top_100_queue.csv
    └── backtest_summary.json
```
