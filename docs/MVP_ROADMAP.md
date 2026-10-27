# MVP improvement roadmap

Updated: 2026-10-27. Work proceeds with documented handovers, decisions and regression tests in version control.

## 1. Persistent case management — implemented

Saved cases, assignment, notes, UTC timestamps, append-only history, controlled lifecycle, expiring benign dismissals and protection against conflicting edits.

Acceptance: a case decision survives a fresh process; other reviewers cannot overwrite a stale version; invalid batches do not partially save; expired dismissals reopen with history; queue and detail views show saved status.

## 2. Complete the real-data journey — implemented

Unified flow across both web and Streamlit interfaces: **upload subscriber, usage and billing files (or load the synthetic benchmark) → validate schema, types, dates and cross-table keys → review data quality & acknowledge warnings → run detection → investigate cases**. Content-addressed dataset identities (`DatasetStore` in `src/dataset_store.py`) bind each analysis and its cases to a reproducible input/config/engine hash (`dataset:<sha256>`). Ground-truth labels are optional on uploaded data.

Acceptance: nothing is generated automatically on startup; one anonymized upload produces a quality report, navigable queue and evidence ledger; malformed data returns actionable row/column errors (HTTP 422); warnings require explicit acknowledgment before detection runs; identical re-uploads reuse the existing dataset identity and its saved cases while modified uploads or parameters create a separate dataset scope.

## 3. Strengthen reconciliation — implemented

Record-level coverage ledger (`src/reconciliation.py`) replaces blanket SIM-level exemptions:
- Partial bundle entitlements (`entitlement_bundle_mb`, `entitlement_voice_min`) and partial corporate parent allocations (`parent_account_allocated_zar`) subtract only covered amounts and preserve genuine uncovered residual usage.
- Entitlements apply only within `[entitlement_valid_from, entitlement_valid_to]` and matching `entitlement_apn`.
- Billing events apply only when `billing_date <= as_of_date` and within `[usage_period_start, usage_period_end]`, matching previously rated usage before covering additional residuals so rated charges and invoices are not double-counted.
- Usage within `billing_grace_days` of `as_of_date` is separated as `pending_billing_zar` (`AWAITING_BILLING`), and usage without any billing snapshot as of `as_of_date` is separated as `missing_billing_evidence_zar` (`MISSING_BILLING_EVIDENCE`).
- Mixed APN usage is reconciled per usage row (`mtn.zero.edu` zero-rated up to the 8,000 MB cap per window while non-zero-rated APNs on the same SIM retain their residuals).

## 4. Align claims and evidence — implemented

- Internal case decisions are explicitly labeled as local records; no external Remedy ticket is dispatched.
- Queue metrics and case records separate **Estimated opportunity** (unconfirmed mature reference-value residual), **Confirmed leakage** (recorded on `CONFIRMED` or `RESOLVED` cases), **Cash recovered** (recorded on `RESOLVED` cases), and **Cost avoided** (recorded on `RESOLVED` cases).
- Standardized positive feature deviations are named **Deviation indicators** (`deviation_indicator_1..3` / `deviation_indicators`), with explicit UI and code notes that they are not SHAP values or model explanations.
- Prioritization scores are presented as **Risk scores** (`risk_score` / `Risk / 100`), not calibrated probabilities.
- Backtest metrics are computed only for synthetic runs and prominently labeled **Synthetic benchmark results — not validated production accuracy**; uploaded datasets explicitly state that no validated accuracy is available.

## 5. Improve the analyst experience — implemented

- Simplified default queue table (`SIM / hypothesis`, `Estimated opportunity`, `Risk / 100`, `Status`, `Owner`) with a slide-over `<dialog id="inspector">` side panel for full reconciliation ledger, deviation indicators, case decisions, financial outcomes and audit history.
- Search (SIM, MSISDN, account, cell, hypothesis, owner), status/owner/hypothesis/plan filters, view toggle (`Needs review / already saved` vs `All subscribers`), sorting (`priority_score`, `unbilled_retail_zar`, `risk_score`, `sim_id`, `case_updated_at`), ascending/descending order, queue limit (`25`, `100`, `250`, `1000`), multi-select checkboxes with atomic batch updates, and clear loading/error states.
- Dataset window (`window_start` to `window_end`) and `last_successful_run` timestamp displayed above the queue.
- Queue metric totals and `/api/export/queue.csv` dynamically match the active filters, sort order and limit.

## 6. Controlled-pilot foundations — next

Verified identities, role permissions, masking, deployment configuration, protected audit access, external ticketing integration (e.g., Remedy/ITSM webhook with external ticket ID tracking) and operational safeguards.

## 7. Reliability and evaluation — planned

Broader automated checks, independently reviewed evaluation data, performance by leakage type, and reproducible environments. Focused unit and API regression tests (`tests/test_case_store.py`, `tests/test_reconciliation_and_ingestion.py`, `tests/test_case_api.py`) are maintained with each completed increment.
