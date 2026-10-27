# Decision log

## ADR-001 — Persistent analyst cases

Date: 2026-10-27. Status: implemented for improvement 1; extended by ADR-002.

### Context

The web workbench stored decisions in a process dictionary; Streamlit stored them in browser-session state. Neither produced durable case records or a decision history. The UI described external ticket dispatch even though no integration existed.

### Decisions and reasons

1. Use Python's built-in SQLite through `src/case_store.py`. This gives atomic saves and local persistence without a new service or Python dependency. It is appropriate for the local demo; a shared deployed pilot may need a server database. Do not synchronize a live SQLite file between teammates or open it across machines through a network/cloud drive.
2. The default database is `<project>/data/cases.sqlite3`, independent of the server's working directory. `GHOST_SIM_CASE_DB` can point to an absolute path outside a cloud-synced checkout. Ignore the database and SQLite companion files in Git. Source sharing and operational-data sharing are separate.
3. Key a case by `(dataset scope, sim_id)` and create it on first saved action. Unsaved cases appear as `NEW`, version 0.
4. Use `NEW → INVESTIGATING → CONFIRMED → RESOLVED`, with dismissal available from NEW or INVESTIGATING. CONFIRMED can return to INVESTIGATING; DISMISSED and RESOLVED can reopen to INVESTIGATING. Staying at the current status permits notes and reassignment. No direct NEW-to-RESOLVED shortcut. All transitions are enforced in storage, including callers outside the web API.
5. Require an owner for investigation, confirmation and resolution. Confirmation, resolution and reopening terminal cases require a note. Dismissal requires a reason and future timezone-aware expiry. The UI chooses a date meaning 00:00 UTC on that date, not the end of the local day. An existing dismissed case may have its reason/expiry revised with an audit event.
6. On the next case read or update after expiry, reopen a dismissed case to INVESTIGATING and record a system event. There is no scheduler in this increment. An automatically reopened case may be unassigned if it was dismissed without an owner; it must receive an owner before the next investigation save. Retain the original reason and expiry in history.
7. Store self-reported reviewer names, notes, UTC creation/update times, versions and before/after records. Save an initial evidence summary and valuation snapshot without adding separate IMSI/MSISDN fields to the case schema. Audit rows are append-only through the application and SQLite triggers. A database administrator can still alter the file; this is not a tamper-proof or authenticated audit system. Login and authorization belong to improvement 6.
8. Require expected versions on every save. Reject stale edits with HTTP 409; the browser preserves the draft so the reviewer can copy it and reload. Save batch updates and their events in one transaction, with no partial success. Expiry processing is a separate committed operation, so a rejected stale update cannot undo an expired whitelist reopening.

---

## ADR-002 — Unified real-data workflow, record-level reconciliation ledger, and aligned financial/model claims

Date: 2026-10-27. Status: implemented for improvements 2–5.

### Context

Following ADR-001, four product and credibility gaps remained:
1. The main web app auto-generated synthetic data on startup while the optional Streamlit app accepted file uploads into a separate table view without shared validation, data-quality review, or case persistence.
2. Layer 1 reconciliation exempted an entire SIM when bundle coverage reached 90% or corporate parent allocation reached 50%, ignored entitlement validity windows and billing delays, and collapsed multi-APN usage to a single APN per SIM.
3. Financial and ML labels overstated what the code computed: standardized positive feature deviations were labeled `SHAP`, prioritization scores were labeled `Confidence`, synthetic backtest metrics were presented without a synthetic-only disclaimer, and estimated residuals were conflated with confirmed leakage, recovered cash, and avoided cost.
4. The daily queue table showed 13+ columns with global summary cards and static CSV exports that did not reflect active filters.

### Decisions and reasons

1. **Content-addressed dataset store (`src/dataset_store.py`) and explicit 4-step workflow:**
   - Nothing is generated automatically on startup in either `server.py` or `app.py`.
   - Every dataset (whether uploaded via `POST /api/datasets/upload` or staged via `POST /api/datasets/demo`) is normalized through `src/ingestion.py` and stored in `datasets.sqlite3` (`GHOST_SIM_DATA_DB`) under a SHA-256 hash of its canonicalized rows, `config` (`as_of_date`, `billing_grace_days`), `source`, and `ENGINE_VERSION` (`ledger-v2`).
   - Cases in `CaseStore` use `scope = "dataset:" + dataset_id`. Re-uploading identical data with the same configuration reuses the dataset ID and its existing case history; uploading modified data or changing `as_of_date` / `billing_grace_days` produces a distinct dataset ID and case scope without corrupting prior investigations.
2. **Row- and cross-table validation before detection (`src/ingestion.py`):**
   - Blocking errors (HTTP 422) prevent staging or running detection when required columns/values are missing, numbers are negative/non-finite, count columns are non-integers, `night_usage_ratio > 1`, dates are invalid (`YYYY-MM-DD`), `usage_date < activation_date`, `usage_date > as_of_date`, the analysis window exceeds 31 days, subscriber IDs are duplicated, `usage_daily`/`billing_events` reference unmatched `sim_id`s, usage/billing composite keys are duplicated, or entitlement/billing period bounds are missing/inverted.
   - Non-blocking warnings (e.g., missing optional rate columns defaulting to reference rates, unknown APNs/cells, subscribers with zero usage rows, or used SIMs without a billing event by `as_of_date`) require explicit analyst acknowledgment (`acknowledge_warnings=True`) before `DatasetStore.run()` executes detection.
3. **Record-level coverage ledger (`src/reconciliation.py`):**
   - Each daily usage record (`sim_id`, `usage_date`, `primary_apn`, `primary_cell_id`) is reconciled in order:
     1. Zero-rated APN data (`mtn.zero.edu`) up to the 8,000 MB analysis-window cap (`zero_rated_covered_zar`).
     2. Valid subscriber bundle entitlements (`entitlement_bundle_mb`, `entitlement_voice_min`) only when `entitlement_valid_from <= usage_date <= entitlement_valid_to` and `entitlement_apn` matches (`entitlement_covered_zar`).
     3. Direct OCS rated amount (`rated_covered_zar = min(chargeable, rated_amount_zar)`).
     4. Billing events (`billing_date <= as_of_date` and `usage_period_start <= usage_date <= usage_period_end`): `invoice_billed_zar` and `parent_account_allocated_zar` first match `rated_credit_zar` (avoiding double-counting rated charges and invoices for the same usage) and then cover remaining residuals (`invoice_covered_zar`, `parent_covered_zar`).
   - Remaining uncovered value on each record is classified by date and billing evidence:
     - `pending_billing_zar` (`AWAITING_BILLING`) if `(as_of_date - usage_date).days < billing_grace_days`.
     - `missing_billing_evidence_zar` (`MISSING_BILLING_EVIDENCE`) if outside the grace window and the SIM has no billing events as of `as_of_date`.
     - `unbilled_retail_zar` / `estimated_opportunity_zar` (mature residual) if outside the grace window and billing evidence is present.
4. **Honest financial outcomes (`CaseStore` v2) and model terminology:**
   - `CaseStore` upgrades to `PRAGMA user_version = 2`, adding nullable `confirmed_leakage_zar`, `cash_recovered_zar`, and `cost_avoided_zar` columns to `cases`.
   - Recording `confirmed_leakage_zar` requires `CONFIRMED` or `RESOLVED` status; recording `cash_recovered_zar` or `cost_avoided_zar` requires `RESOLVED` status; any financial outcome change requires an explanatory note. Reopening a case to `INVESTIGATING` requires clearing those amounts to `null` (while append-only `case_events` retains historical values).
   - Feature deviation shares are named `deviation_indicator_1..3` / `deviation_indicators` (not `SHAP`).
   - Combined rule + Isolation Forest scores are named `risk_score` (not `confidence_score`).
   - Backtest accuracy is computed only when `source == "synthetic"` and labeled `"Synthetic benchmark results — not validated production accuracy"`.
5. **Simplified queue, side-panel inspector, and filter-matched totals/exports:**
   - Both `server.py` (`src/dashboard.html`) and `app.py` use `select_queue()` and `totals()` so the visible queue, the 4 financial summary metrics, and `/api/export/queue.csv` always reflect the exact active search, status, owner, hypothesis, plan, view (`review` vs `all`), sort order, and `top_k` limit.
