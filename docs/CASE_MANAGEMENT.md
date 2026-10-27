# Workbench & case management guide

## Start the main workbench

Run from the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

Open http://127.0.0.1:8501. Nothing is generated automatically on startup; you can choose a saved dataset, load the synthetic benchmark, or upload your own three files (`subscribers`, `usage_daily`, `billing_events`).

Default local databases are `data/datasets.sqlite3` and `data/cases.sqlite3`. For a checkout in OneDrive, configure local database paths outside the synced directory before running the application:

```powershell
$env:GHOST_SIM_CASE_DB = Join-Path $env:LOCALAPPDATA 'GhostSimHunter\cases.sqlite3'
$env:GHOST_SIM_DATA_DB = Join-Path $env:LOCALAPPDATA 'GhostSimHunter\datasets.sqlite3'
.\.venv\Scripts\python.exe server.py
```

Both `server.py` and the optional Streamlit interface (`app.py`) share the same `DatasetStore` and `CaseStore`.

## Four-step analyst workflow

1. **Upload files or load the synthetic benchmark:**
   - Download the example templates (`subscribers.csv`, `usage_daily.csv`, `billing_events.csv`) or click **Load synthetic benchmark**.
   - When uploading files, choose an **As-of date** (`YYYY-MM-DD`) and **Billing delay allowance** (`0–60` days).
2. **Review data quality:**
   - Blocking errors (missing required columns/values, duplicate subscriber or usage/billing keys, unmatched `sim_id`s, invalid dates, usage before activation or after `as_of_date`, >31-day window) must be corrected before staging.
   - Non-blocking warnings (missing optional rate columns using reference rates, unknown APNs/cells, used SIMs with no billing event by `as_of_date`) must be explicitly acknowledged via the checkbox before running detection.
3. **Run detection:**
   - Executes the record-level coverage ledger (`src/reconciliation.py`) and Isolation Forest prioritization (`src/pure_engine.py`), storing the immutable run timestamp (`last_successful_run`).
4. **Investigate cases:**
   - Filter the queue by search term, status, owner, hypothesis, plan, view (`Needs review / already saved` vs `All subscribers`), sort field/order, and queue limit (`top_k`).
   - Notice that the four summary metrics (**Estimated opportunity**, **Confirmed leakage**, **Cash recovered**, **Cost avoided**) and **Export visible queue** (`/api/export/queue.csv`) always match the currently visible queue.
   - Click any SIM ID to open the side-panel inspector showing the coverage breakdown, daily usage/coverage ledger, **Deviation indicators** (standardized positive feature deviations, not SHAP values), and the persistent case decision form.
   - Record financial outcomes (`confirmed_leakage_zar` on `CONFIRMED` or `RESOLVED` cases; `cash_recovered_zar` and `cost_avoided_zar` on `RESOLVED` cases) with a mandatory explanatory note.

## Automated verification

Run all 26 unit and API tests:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Storage, ingestion, and reconciliation suites also run with standard Python (no web dependencies needed):

```powershell
python -m unittest discover -s tests -p "test_case_store.py" -v
python -m unittest discover -s tests -p "test_reconciliation_and_ingestion.py" -v
```

## API contract for teammates

- `GET /api/datasets`: lists staged and completed datasets.
- `POST /api/datasets/upload`: validates and stages `{name, as_of_date, billing_grace_days, files: {subscribers, usage_daily, billing_events}}` (returns HTTP 422 with `quality` report if validation fails).
- `POST /api/datasets/demo`: stages the 3,000-SIM synthetic benchmark dataset.
- `POST /api/datasets/run`: runs detection for `{dataset_id, acknowledge_warnings}`.
- `GET /api/summary?dataset_id=<id>&...`: returns filtered `queue`, `matching_count`, visible-queue `totals`, `benchmark` (synthetic only), and `filter_options`.
- `GET /api/sim/{sim_id}?dataset_id=<id>`: returns `sim`, `case` (with version, `allowed_transitions`, `history`), `daily` coverage ledger, and `deviation_indicators`.
- `POST /api/action`: updates 1–100 cases atomically for a completed dataset:

```json
{
  "dataset_id": "<sha256>",
  "sim_ids": ["SIM-101269"],
  "status": "CONFIRMED",
  "actor": "Reviewer name",
  "assigned_to": "Revenue Assurance",
  "note": "Verified OCS rating gap across 30-day window",
  "confirmed_leakage_zar": 6956.59,
  "expected_versions": {"SIM-101269": 1}
}
```

- `GET /api/export/queue.csv?dataset_id=<id>&...`: exports the filtered visible queue with spreadsheet-safe escaping.
