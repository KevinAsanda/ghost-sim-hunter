# Ghost SIM Hunter: Zero-Revenue Active SIMs
**Intelligent Automation & Revenue Assurance MVP (Specification & Demo Guide)**

Implementation update (2026-10-27): Improvements 1–5 are complete. See the [team documentation](docs/README.md), [roadmap](docs/MVP_ROADMAP.md), [decision log](docs/DECISIONS.md), and [change log](docs/CHANGELOG.md) for full engineering details.

---

## 1. Executive Summary & Positioning

- **Brand & Subtitle:** **Ghost SIM Hunter — Zero-Revenue Active SIMs** *(explicitly distinguished from KYC/identity "ghost subscribers" to prevent confusion during judging)*.
- **Category Alignment:** Positioned under **Intelligent Automation (OSS/BSS Revenue Assurance)** with a secondary **RAN Spatial Analytics** capability that clusters unbilled traffic by `Cell_ID` to pinpoint **SIM Box interconnect bypass** and **regional bad provisioning batches**.
- **Core Value Proposition:** Through a single consistent workflow (**upload subscriber, usage, and billing files → validate → review data quality → run detection → investigate cases**), Ghost SIM Hunter reconciles record-level entitlements, surfaces the highest-risk unbilled SIMs, and provides a 5-point forensic investigation and durable internal case workflow—with **zero customer impact** (recommend-only mode, no auto-blocking).

---

## 2. Record-Level Reconciliation of "R0 Revenue Leakage" (`ledger-v2`)

A naive SQL query for `Revenue == R0 AND Usage > X` produces massive false positives, while simplistic SIM-level exemptions (e.g., exempting an entire SIM if 90% of usage is covered by a bundle) hide genuine residual leakage. Our **Layer 1 Record-Level Coverage Ledger (`src/reconciliation.py`)** reconciles every usage row chronologically:

| Scenario | Why Naive Reports Fail | How `ledger-v2` Handles It Accurately |
|---|---|---|
| **1. Rollover / Multi-Month Bundles** | Prepaid/hybrid subscriber bought a bundle in a prior period, or exceeds their bundle mid-month. | Consumes `entitlement_bundle_mb` and `entitlement_voice_min` row-by-row only within `[entitlement_valid_from, entitlement_valid_to]` and matching `entitlement_apn`. Uncovered overage remains billable (`EXAMPLE-A`). |
| **2. Enterprise Pooled / Master Accounts** | Corporate child SIM usage is partially or fully billed to `CORP-MASTER-*`. | Subtracts valid `parent_account_allocated_zar` from remaining retail value and preserves any uncovered residual (`EXAMPLE-B`). |
| **3. Mixed APN & Whitelisted Zero-Rated Traffic** | A single SIM uses both zero-rated (`mtn.zero.edu`) and paid (`internet`) APNs in the same window. | Evaluates APN eligibility per usage record rather than per SIM, zero-rating eligible rows up to fair-use limits while keeping paid APN usage exposed (`EXAMPLE-C`). |
| **4. Delayed Billing vs. Missing Evidence** | Usage near the end of the cycle has not settled yet, or a billing feed extract is incomplete. | Usage within `billing_grace_days` of `as_of_date` is bucketed into `pending_billing_zar` (`EXAMPLE-D`), while SIMs missing from the billing extract are flagged under `missing_billing_evidence_zar` instead of inflating mature `estimated_opportunity_zar`. |

---

## 3. Five Leakage Archetypes & Automated Root-Cause Playbook

| Archetype Code | Root-Cause Hypothesis | Key Detection Signatures (Rules + Peer Deviation) | Recommended Action (Human-in-the-Loop) | Owner Team |
|---|---|---|---|---|
| `PROVISIONING_ORPHAN` | **HLR/OCS Desync Orphan** | `HLR_Status = ACTIVE` while `Billing_Status IN (MISSING_IN_OCS, PENDING, SUSPENDED_BILLING_ONLY)`. | Sync IMSI/MSISDN to Billing CRM or suspend HLR profile pending contract linkage. | Core Provisioning & Revenue Assurance |
| `RATING_MISCONFIG` | **Tariff / APN Rating Bug** | Active production sessions rated at `R0.00` on legacy/promo tariff codes (`LEGACY_PROMO_ZERO_BUG_99`). | Patch OCS rating table mapping and trigger 30-day CDR re-rating. | Billing Configuration & Rating Team |
| `IOT_WRONG_APN` | **IoT SIM on Unbilled Consumer APN** | `Plan = IOT_M2M` transmitting high-volume broadband on `internet` APN instead of `mtn.iot.m2m`. | Lock IMEI/APN binding to dedicated M2M APN and invoice enterprise overage. | Enterprise IoT Operations |
| `FREE_INTERNAL_ABUSE` | **Test / Staff SIM Abuse** | `INTERNAL_TEST` SIM pinned to residential cells with heavy off-peak/night streaming (>50% night ratio). | Revoke unlimited test entitlement, apply fair-usage cap, and audit custodian department. | Internal Audit & Service Operations |
| `SIM_BOX_FRAUD` | **SIM Box Interconnect Bypass** | Extreme voice minutes (>1,200 mins/mo), >80 unique callees, rapid IMEI rotation (5–14 IMEIs), clustered in specific Cell IDs. | Escalate to Fraud Management System (FMS); quarantine SIM batch at Cell ID and block IMEI pool. | Fraud Management & RAN Security |

---

## 4. Honest Financial & Scoring Terminology

To ensure every claim matches the implemented behavior:
1. **Estimated Opportunity (`estimated_opportunity_zar`):** Mature unbilled retail tariff value (`R0.29/MB` data, `R1.20/min` voice, `R0.50/SMS`) after subtracting valid zero-rated APN usage, in-window bundle coverage, direct billed revenue, parent allocations, pending billing in grace windows, and missing billing evidence.
2. **Analyst-Verified Outcomes (`confirmed_leakage_zar`, `cash_recovered_zar`, `cost_avoided_zar`):** Recorded in the persistent SQLite case store only when an analyst investigates a case and logs verified ZAR figures. Never auto-claimed by the model.
3. **Risk Score (`risk_score` / 100):** Hybrid rule + anomaly ranking score used to prioritize review—explicitly labeled as an uncalibrated risk score rather than a probability.
4. **Deviation Indicators (`deviation_indicator_1..3`):** Standardized positive feature deviations (z-scores above peer averages) explaining why a SIM stands out.
5. **Synthetic Benchmark Disclaimer:** Backtest precision/recall metrics are prominently labeled `"Synthetic benchmark results — not validated production accuracy"` and are hidden for uploaded datasets without ground-truth labels.

---

## 5. Live Hackathon Demo Script (3 Minutes)

1. **Step 1 (`1. Data & Quality`):** Show that the workbench starts cleanly without fabricating startup numbers. Click **Load 4-SIM validation sample** to demonstrate how `EXAMPLE-A..D` catch partial bundle overages, partial enterprise allocations, mixed APN usage, and delayed billing grace periods—or click **Generate 3,000-SIM synthetic benchmark** and run detection.
2. **Step 2 (`2. Queue` & Slide-Over Inspector):** Show the dataset window (`2026-09-28 to 2026-10-27`), last run timestamp, and filter-matched KPI strip (**Estimated opportunity**, **Confirmed leakage**, **Cash recovered**, **Cost avoided**). Filter or search the queue, click **Inspect** on a top SIM to open the slide-over forensic panel, review the coverage ledger and **Deviation indicators**, and record an analyst decision (`CONFIRMED_LEAKAGE`) with verified `confirmed_leakage_zar` and `cash_recovered_zar`. Export the filtered queue via **Export visible CSV**.
3. **Step 3 (`3. Cell Hotspots`):** Show the cell-site hotspot table highlighting high-density zero-revenue clusters in `JHB-HILLBROW-004` and `JHB-SOWETO-003` driven by SIM Box banks.
4. **Step 4 (`4. Benchmark`):** Walk judges through the **Synthetic benchmark results** (`Precision@100 = 100.0%` vs. `23.0%` naive baseline) and the three-table upload data contract.
