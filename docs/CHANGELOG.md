# Change log

## 2026-10-27 — Improvements 2–5: real-data journey, record-level reconciliation ledger, aligned claims, and simplified analyst workflow

### Problem and resulting behavior

1. **Real-data journey:** The workbench previously auto-generated synthetic data on startup and handled custom uploads separately in Streamlit without deep validation or persistent cases. Both interfaces (`server.py` and `app.py`) now share one content-addressed `DatasetStore` (`src/dataset_store.py`) and 4-step workflow: **upload subscriber, usage and billing files (or load the synthetic benchmark) → validate schema, dates, duplicates and cross-table references → review data quality & acknowledge warnings → run detection → investigate cases**.
2. **Reconciliation accuracy:** Previously, 90% bundle coverage or 50% enterprise parent allocation exempted an entire SIM (`unbilled_retail_zar = 0`). The new record-level ledger (`src/reconciliation.py`) subtracts only covered amounts, enforces entitlement validity windows (`entitlement_valid_from` to `entitlement_valid_to`) and APN scope (`entitlement_apn`), reconciles mixed APN rows per SIM/date/APN/cell, matches rated charges against invoice/parent amounts without double-counting, and separates `pending_billing_zar` (within `billing_grace_days`) and `missing_billing_evidence_zar` from mature `unbilled_retail_zar`.
3. **Aligned claims and financial outcomes:** Standardized feature deviations are now named `deviation_indicator_1..3` / `deviation_indicators` (not `SHAP`), prioritization scores are named `risk_score` (not `confidence_score`), benchmark metrics are labeled `"Synthetic benchmark results — not validated production accuracy"` and omitted for uploaded datasets, and `CaseStore` v2 (`PRAGMA user_version = 2`) tracks `confirmed_leakage_zar`, `cash_recovered_zar`, and `cost_avoided_zar` separately from unconfirmed `estimated_opportunity_zar`.
4. **Simplified analyst experience:** The queue displays a concise 6-column table (`Select`, `SIM / hypothesis`, `Estimated opportunity`, `Risk / 100`, `Status`, `Owner`) with a slide-over `<dialog id="inspector">` side panel for the per-record coverage ledger, deviation indicators, case decisions, financial outcomes and history. Search, status/owner/hypothesis/plan filters, view toggle (`review` vs `all`), sorting, batch selection, dataset window and `last_successful_run` are built in, and both visible-queue totals and `/api/export/queue.csv` dynamically match active filters.

### Changes

- `src/ingestion.py`: bounded CSV/Parquet parsing (`MAX_FILE_BYTES = 15 MB`, `MAX_ROWS = 100,000`) and row/cross-table validation (`validate_tables`) covering missing columns/values, numeric bounds, integer counts, `YYYY-MM-DD` dates, activation vs usage dates, `as_of_date`, 31-day window limit, duplicate subscriber/usage/billing keys, unmatched `sim_id`s, entitlement/billing validity periods, and explicit warnings.
- `src/reconciliation.py`: shared record-level coverage ledger (`detect`, `ENGINE_VERSION = "ledger-v2"`).
- `src/dataset_store.py`: content-addressed SQLite store (`datasets.sqlite3`) staging validated inputs, enforcing warning acknowledgment before running detection, and storing immutable results and `last_successful_run`.
- `src/sample_data.py`: 4-SIM example dataset (`EXAMPLE-A`..`EXAMPLE-D`) illustrating partial bundle coverage, partial parent allocation, mixed APN usage, and delayed billing within grace days.
- `src/case_store.py`: schema v2 migration adding `confirmed_leakage_zar`, `cash_recovered_zar`, and `cost_avoided_zar` with status/note validation rules.
- `src/pure_engine.py`, `src/detection_engine.py`, `src/backtest.py`, `run_pipeline.py`: aligned with `src/reconciliation.py`, `deviation_indicator_1..3`, `risk_score`, `estimated_opportunity_zar`, and synthetic benchmark labeling.
- `server.py`, `src/dashboard.html`, `app.py`: unified 4-step dataset/quality/detection/investigation workflow, slide-over inspector dialog, filter-matched totals and CSV export.
- `tests/test_case_store.py`, `tests/test_reconciliation_and_ingestion.py`, `tests/test_case_api.py`: 26 automated tests covering case lifecycle, financial outcome rules, schema v1→v2 migration, ingestion validation errors/warnings, partial/mixed/delayed reconciliation edge cases, content-addressed dataset staging, and filtered API/CSV round trips.

### Verification

- 26 automated tests passed (`.\.venv\Scripts\python.exe -m unittest discover -s tests -v`).
- CLI benchmark runner (`.\.venv\Scripts\python.exe run_pipeline.py`) executed end-to-end and updated `data/daily_top_100_queue.csv`, `data/all_evaluated_sims.csv`, and `data/backtest_summary.json`.

---

## 2026-10-27 — Improvement 1: persistent case management

### Problem and resulting behavior

Case decisions previously vanished on restart or when a Streamlit session ended. Cases now persist in a versioned SQLite database, with owner, status, notes, UTC timestamps, initial evidence and an append-only decision history. Benign dismissals need a reason and expiry; expired dismissals reopen for review on the next case access.

### Changes

- `src/case_store.py`: shared storage, schema initialization, lifecycle validation, transactional batches, expected-version checks, expiry processing and history.
- `server.py`: persistent status in queue/detail APIs, validated case actions, controlled 400/409 responses, current saved case fields in CSV exports, spreadsheet-safe text export and paths anchored to the project. Also validates queue size so malformed requests fail cleanly.
- `src/dashboard.html`: reviewer entry, owner/status/note form, dismissal fields, decision history, draft-preserving error handling, batch-start review, current owner column and accurate internal-review wording.
- `app.py`: optional Streamlit interface uses the same persistence and lifecycle.
- `.gitignore`: ignores SQLite transaction/sidecar files as well as the database.
- `tests/test_case_store.py`, `tests/test_case_api.py`: focused regression tests for this feature.
- `docs/`: roadmap, decision log, change log and usage/backup/API guide; README and demo script updated to reference the actual workflow.

Decision rationale: [ADR-001](DECISIONS.md), [ADR-002](DECISIONS.md#adr-002--unified-real-data-workflow-record-level-reconciliation-ledger-and-aligned-financialmodel-claims). Next increment: [improvement 6](MVP_ROADMAP.md#6-controlled-pilot-foundations--next).
