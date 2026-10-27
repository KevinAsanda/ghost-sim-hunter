# Ghost SIM Hunter: Zero-Revenue Active SIMs
**Intelligent Automation & Revenue Assurance MVP (Phase 0 Specification & Demo Guide)**

---

## 1. Executive Summary & Positioning

- **Brand & Subtitle:** **Ghost SIM Hunter — Zero-Revenue Active SIMs** *(explicitly distinguished from KYC/identity "ghost subscribers" to prevent confusion during judging)*.
- **Category Alignment:** Positioned under **Intelligent Automation (OSS/BSS Revenue Assurance)** with a secondary **RAN Spatial Analytics** capability that clusters unbilled traffic by `Cell_ID` to pinpoint **SIM Box interconnect bypass** and **regional bad provisioning batches**.
- **Core Value Proposition:** Given network usage CDRs, subscriber master records, and billing/OCS data in a **daily batch run**, Ghost SIM Hunter surfaces the **Top-100 highest-risk unbilled SIMs**, filters out legitimate zero-revenue look-alikes, and outputs an **automated 5-point forensic investigation and recommended action** per SIM—with **zero customer impact** (recommend-only mode, no auto-blocking).

---

## 2. Precise Definition of "R0 Revenue Leakage" (Handling Benign Look-Alikes)

A naive SQL query for `Revenue == R0 AND Usage > X` produces massive false positives in a real telecom environment. Our **Layer 1 DuckDB Reconciliation Engine** defines true unbilled leakage by explicitly reconciling and excluding three benign classes before scoring:

| Class | Why Direct SIM Revenue is R0.00 in Current Window | How Ghost SIM Hunter Filters It Out |
|---|---|---|
| **1. Rollover / Multi-Month Bundles** | Prepaid/hybrid subscriber bought a 90-day or annual data bundle in a prior billing period. | Joins `entitlement_bundle_mb` and `entitlement_voice_min` balances; subtracts covered entitlement volume from gross usage. |
| **2. Enterprise Pooled / Master Accounts** | Corporate child SIM usage is billed to a parent master account (`CORP-MASTER-*`), leaving child SIM direct revenue at R0. | Traverses `parent_account_id` hierarchy and verifies `parent_account_allocated_zar` coverage. |
| **3. Whitelisted Zero-Rated APNs** | Subscriber accesses zero-rated public/educational/banking endpoints (`mtn.zero.edu`) within fair-use volume limits. | Cross-checks `primary_apn` against `apn_catalog.is_zero_rated_eligible` and enforces normal volume/voice caps. |

---

## 3. Five Injected Leakage Archetypes & Automated Root-Cause Playbook

| Archetype Code | Root-Cause Hypothesis | Key Detection Signatures (Rules + Isolation Forest) | Recommended Action (Human-in-the-Loop) | Owner Team |
|---|---|---|---|---|
| `PROVISIONING_ORPHAN` | **HLR/OCS Desync Orphan** | `HLR_Status = ACTIVE` while `Billing_Status IN (MISSING_IN_OCS, PENDING, SUSPENDED_BILLING_ONLY)`. | Sync IMSI/MSISDN to Billing CRM or suspend HLR profile pending contract linkage. | Core Provisioning & Revenue Assurance |
| `RATING_MISCONFIG` | **Tariff / APN Rating Bug** | Active production sessions rated at `R0.00` on legacy/promo tariff codes (`LEGACY_PROMO_ZERO_BUG_99`). | Patch OCS rating table mapping and trigger 30-day CDR re-rating. | Billing Configuration & Rating Team |
| `IOT_WRONG_APN` | **IoT SIM on Unbilled Consumer APN** | `Plan = IOT_M2M` transmitting high-volume broadband on `internet` APN instead of `mtn.iot.m2m`. | Lock IMEI/APN binding to dedicated M2M APN and invoice enterprise overage. | Enterprise IoT Operations |
| `FREE_INTERNAL_ABUSE` | **Test / Staff SIM Abuse** | `INTERNAL_TEST` SIM pinned to residential cells with heavy off-peak/night streaming (>50% night ratio). | Revoke unlimited test entitlement, apply fair-usage cap, and audit custodian department. | Internal Audit & Service Operations |
| `SIM_BOX_FRAUD` | **SIM Box Interconnect Bypass** | Extreme voice minutes (>1,200 mins/mo), >80 unique callees, rapid IMEI rotation (5–14 IMEIs), clustered in specific Cell IDs. | Escalate to Fraud Management System (FMS); quarantine SIM batch at Cell ID and block IMEI pool. | Fraud Management & RAN Security |

---

## 4. Transparent Financial Model (Retail Value vs. Marginal Cost vs. Recoverable Cash)

Judges will probe headline numbers like *"R150k per 100 SIMs"* (~R1,500/SIM/month). Ghost SIM Hunter separates three distinct financial views for every SIM and queue slice:

1. **Unbilled Usage at Retail Tariff (`unbilled_retail_zar`):** Valued at standard out-of-bundle/retail reference rates (`R0.29/MB` data, `R1.20/min` voice, `R0.50/SMS`). Represents gross unbilled service value delivered.
2. **Direct Marginal Network & Interconnect Cost (`marginal_network_cost_zar`):** Estimated at `18%` of retail value for radio/backhaul capacity plus `R0.13/min` ICASA interconnect termination cost for voice.
3. **Estimated Recoverable / Avoidable Cash (`est_recoverable_low/mid/high_zar`):** Applies archetype-specific recovery probabilities (`65%` for provisioning orphans via back-billing, `85%` forward recovery for rating fixes, `75%` for enterprise IoT true-ups, `90%` cost avoidance on SIM Box termination bypass, `40%` on internal test SIM abuse).

---

## 5. Live Hackathon Demo Script (3 Minutes)

1. **Open Tab 1 (`Daily Top-100 Queue`):** Point out the KPI banner—Ghost SIM Hunter achieves **~100% Precision@100** and **0% False Positives on Benign Look-Alikes**, compared to the Naive `R0 Revenue` report which pollutes the Top 100 with legitimate Enterprise Pooled and Rollover Bundle subscribers.
2. **Open Tab 2 (`SIM Forensic Drill-Down`):** Select a top-ranked case (e.g., a `Provisioning Orphan` or `SIM Box Fraud` SIM). Show the **5-Point Automated Diagnostic Checklist**, the **30-Day Usage vs. OCS Billed Timeline**, and the **Feature Attribution bar chart** explaining *why* the SIM scored at the top. Click **Approve & Create Remedy Ticket** and watch the **Session Actioned Recovery** KPI increment in real time.
3. **Open Tab 3 (`RAN / Cell-ID Clustering`):** Show the South Africa cell-site map highlighting high-density zero-revenue clusters in `JHB-HILLBROW-004` and `JHB-SOWETO-003` driven by SIM Box banks.
4. **Open Tab 4 (`Backtest & Business Case`):** Walk judges through the Precision@K curve and the 3-tier Financial Valuation table (Retail Value vs. Marginal Cost vs. Conservative/Base/Optimistic Cash Recovery).
