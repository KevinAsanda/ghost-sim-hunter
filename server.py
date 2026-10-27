"""Local workbench: validate, review, reconcile and investigate."""
import csv
import io
import json
import os
import sqlite3
from functools import wraps
from pathlib import Path
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
import uvicorn
from src.case_store import CaseStore, CaseValidationError, CaseConflictError, OUTCOME_FIELDS, TRANSITIONS
from src.dataset_store import DatasetStore
from src.ingestion import UploadError, REQUIRED, parse_file, validate_tables
from src.pure_engine import generate_synthetic_tables, FEATURE_COLS, FEATURE_LABELS
from src.sample_data import example_tables

PROJECT_DIR = Path(__file__).resolve().parent
CASE_STORE = CaseStore()
DATASETS = DatasetStore(os.environ.get("GHOST_SIM_DATA_DB") or CASE_STORE.path.with_name("datasets.sqlite3"))


def api(handler):
    @wraps(handler)
    async def wrapped(request):
        try:
            return await handler(request)
        except CaseConflictError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except KeyError:
            return JSONResponse({"error": "Dataset or SIM not found."}, status_code=404)
        except (UploadError, CaseValidationError, ValueError, UnicodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except sqlite3.OperationalError:
            return JSONResponse({"error": "Local storage is unavailable or busy. Check the database location and retry."}, status_code=503)
    return wrapped


async def read_json(request):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 64 * 1024 * 1024:
            raise UploadError("Upload request exceeds 64 MB.")
    body = json.loads(content)
    if not isinstance(body, dict):
        raise UploadError("Request must be a JSON object.")
    return body


def dataset_for(request, completed=True):
    dataset_id = request.query_params.get("dataset_id")
    if not dataset_id:
        raise UploadError("Select a dataset first.")
    dataset = DATASETS.get(dataset_id)
    if completed and dataset["result"] is None:
        raise UploadError("Review data quality and run detection first.")
    return dataset


def case_scope(dataset):
    return "dataset:" + dataset["id"]


def with_case(row, cases):
    case = cases.get(row["sim_id"], CASE_STORE.empty_case(row["sim_id"]))
    return dict(row, case_status=case["status"], assigned_to=case["assigned_to"],
        case_version=case["version"], case_updated_at=case["updated_at"],
        whitelist_expires_at=case["whitelist_expires_at"], **{f: case.get(f) for f in OUTCOME_FIELDS})


def select_queue(dataset, params):
    try:
        limit = int(params.get("top_k", 100))
        if not 1 <= limit <= 1000:
            raise ValueError()
    except ValueError:
        raise UploadError("top_k must be between 1 and 1000.")
    cases = CASE_STORE.list_cases(case_scope(dataset))
    all_rows = [with_case(r, cases) for r in dataset["result"]["rows"]]
    search, owner = params.get("search", "").casefold().strip(), params.get("owner", "ALL")
    status, archetype, plan = (params.get(key, "ALL") for key in ("status", "archetype", "plan"))
    if status != "ALL" and status not in TRANSITIONS:
        raise UploadError("Unknown status filter.")
    view = params.get("view", "review")
    if view not in ("review", "all"):
        raise UploadError("view must be review or all.")
    matching = []
    for r in all_rows:
        if view == "review" and not any(r[k] > 0 for k in
                ("unbilled_retail_zar", "pending_billing_zar", "missing_billing_evidence_zar")) and r["case_version"] == 0:
            continue
        if status != "ALL" and r["case_status"] != status:
            continue
        if owner != "ALL" and r["assigned_to"] != ("" if owner == "UNASSIGNED" else owner):
            continue
        if archetype != "ALL" and r["predicted_archetype_code"] != archetype:
            continue
        if plan != "ALL" and r["plan_type"] != plan:
            continue
        if search and search not in " ".join(str(r.get(key, "")) for key in
                ("sim_id", "msisdn", "account_id", "primary_cell_id", "predicted_archetype_name", "assigned_to")).casefold():
            continue
        matching.append(r)
    sort = params.get("sort", "priority_score")
    if sort not in ("priority_score", "unbilled_retail_zar", "risk_score", "sim_id", "case_updated_at"):
        raise UploadError("Unknown sort field.")
    order = params.get("order", "desc")
    if order not in ("asc", "desc"):
        raise UploadError("Sort order must be asc or desc.")
    matching.sort(key=lambda r: r["sim_id"])
    matching.sort(key=lambda r: r[sort] or ("" if sort in ("sim_id", "case_updated_at") else 0), reverse=order == "desc")
    return matching[:limit], len(matching), all_rows


def totals(rows):
    values = {key: round(sum(r[key] for r in rows), 2) for key in
              ("estimated_opportunity_zar", "pending_billing_zar", "missing_billing_evidence_zar")}
    for key in OUTCOME_FIELDS:
        recorded = [r[key] for r in rows if r[key] is not None]
        values[key] = round(sum(recorded), 2) if recorded else None
        values[key + "_recorded_count"] = len(recorded)
    values["case_count"] = len(rows)
    return values


def cell_hotspots(rows):
    by_cell = {}
    for r in rows:
        cid = r.get("primary_cell_id") or "UNKNOWN"
        bucket = by_cell.setdefault(cid, {
            "cell_id": cid, "sim_count": 0, "flagged_sim_count": 0,
            "estimated_opportunity_zar": 0.0, "pending_billing_zar": 0.0,
            "risk_sum": 0.0, "archetypes": {},
        })
        bucket["sim_count"] += 1
        if r.get("estimated_opportunity_zar", 0) > 0 or r.get("pending_billing_zar", 0) > 0 or r.get("missing_billing_evidence_zar", 0) > 0:
            bucket["flagged_sim_count"] += 1
        bucket["estimated_opportunity_zar"] = round(bucket["estimated_opportunity_zar"] + r.get("estimated_opportunity_zar", 0.0), 2)
        bucket["pending_billing_zar"] = round(bucket["pending_billing_zar"] + r.get("pending_billing_zar", 0.0), 2)
        bucket["risk_sum"] += r.get("risk_score", 0.0)
        arch = r.get("predicted_archetype_name", "Normal")
        if r.get("predicted_archetype_code") != "NORMAL":
            bucket["archetypes"][arch] = bucket["archetypes"].get(arch, 0) + 1
    out = []
    for b in by_cell.values():
        top_arch = max(b["archetypes"].items(), key=lambda kv: kv[1])[0] if b["archetypes"] else "Normal / Covered"
        out.append({
            "cell_id": b["cell_id"],
            "sim_count": b["sim_count"],
            "flagged_sim_count": b["flagged_sim_count"],
            "estimated_opportunity_zar": b["estimated_opportunity_zar"],
            "pending_billing_zar": b["pending_billing_zar"],
            "avg_risk_pct": round((b["risk_sum"] / max(b["sim_count"], 1)) * 100, 1),
            "top_hypothesis": top_arch,
        })
    out.sort(key=lambda c: (-c["estimated_opportunity_zar"], -c["flagged_sim_count"], c["cell_id"]))
    return out[:25]


def csv_response(rows, columns, filename):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(("=", "+", "-", "@"))
                         else v for k, v in row.items()})
    return Response(out.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@api
async def api_datasets(request):
    return JSONResponse({"datasets": await run_in_threadpool(DATASETS.list)})


@api
async def api_upload(request):
    body = await read_json(request)
    files = body.get("files")
    if not isinstance(files, dict) or set(files) != set(REQUIRED):
        raise UploadError("Provide subscribers, usage_daily and billing_events files.")
    name = body.get("name", "Uploaded dataset")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 120:
        raise UploadError("Dataset name must be 1-120 characters.")
    config = {"as_of_date": body.get("as_of_date"), "billing_grace_days": body.get("billing_grace_days", 3)}

    def validate():
        parsed = {key: parse_file(value) for key, value in files.items()}
        return validate_tables({k: v[0] for k, v in parsed.items()}, {k: v[1] for k, v in parsed.items()}, config)

    tables, quality = await run_in_threadpool(validate)
    if not quality["valid"]:
        return JSONResponse({"error": "Correct the data errors and upload again.", "quality": quality}, status_code=422)
    dataset = await run_in_threadpool(DATASETS.stage, name.strip(), "uploaded", tables, config, quality)
    return JSONResponse({"dataset": dataset, "quality": quality})


@api
async def api_demo(request):
    def stage_demo():
        tables = generate_synthetic_tables()
        config = {"as_of_date": "2026-10-30", "billing_grace_days": 3}
        normalized, report = validate_tables(tables, {k: list(v[0]) for k, v in tables.items()}, config)
        return DATASETS.stage("Synthetic benchmark - 3,000 SIMs", "synthetic", normalized, config, report)
    return JSONResponse({"dataset": await run_in_threadpool(stage_demo)})


@api
async def api_sample(request):
    def stage_sample():
        tables = example_tables()
        config = {"as_of_date": "2026-10-27", "billing_grace_days": 3}
        normalized, report = validate_tables(tables, {k: list(v[0]) for k, v in tables.items()}, config)
        return DATASETS.stage("4-SIM Edge-Case Sample (EXAMPLE-A..D)", "uploaded", normalized, config, report)
    return JSONResponse({"dataset": await run_in_threadpool(stage_sample)})


@api
async def api_run(request):
    body = await read_json(request)
    dataset_id = body.get("dataset_id")
    if not isinstance(dataset_id, str):
        raise UploadError("Select a dataset first.")
    dataset = await run_in_threadpool(DATASETS.run, dataset_id, body.get("acknowledge_warnings", False))
    return JSONResponse({"dataset": dataset})


@api
async def api_template(request):
    rows = example_tables()[request.path_params["table"]]
    return csv_response(rows, list(rows[0]), f'{request.path_params["table"]}.csv')


@api
async def api_summary(request):
    dataset = await run_in_threadpool(dataset_for, request)
    rows, count, all_rows = await run_in_threadpool(select_queue, dataset, request.query_params)
    return JSONResponse({"dataset": {k: v for k, v in dataset.items() if k not in ("tables", "result")},
        "queue": rows, "matching_count": count, "totals": totals(rows),
        "benchmark": dataset["result"]["benchmark"],
        "cell_hotspots": cell_hotspots(all_rows),
        "filter_options": {"owners": sorted({r["assigned_to"] for r in all_rows if r["assigned_to"]}),
                           "plans": sorted({r["plan_type"] for r in all_rows}),
                           "archetypes": sorted({r["predicted_archetype_code"] for r in all_rows})}})


@api
async def api_sim_detail(request):
    dataset = await run_in_threadpool(dataset_for, request)
    sid = request.path_params["sim_id"]
    row = next((r for r in dataset["result"]["rows"] if r["sim_id"] == sid), None)
    if row is None:
        raise KeyError(sid)
    case = await run_in_threadpool(CASE_STORE.get_case, case_scope(dataset), sid)
    indicators = sorted([{"feature": FEATURE_LABELS[f], "share_pct": round(row[f"contrib_{f}"] * 100, 1)}
                         for f in FEATURE_COLS], key=lambda d: -d["share_pct"])
    return JSONResponse({"sim": with_case(row, {sid: case}), "case": case,
                         "daily": dataset["result"]["daily"].get(sid, []), "deviation_indicators": indicators})


@api
async def api_action_case(request):
    body = await read_json(request)
    dataset_id = body.pop("dataset_id", None)
    if not isinstance(dataset_id, str):
        raise UploadError("Every case action must specify its dataset_id.")
    dataset = await run_in_threadpool(DATASETS.get, dataset_id)
    if dataset["result"] is None:
        raise UploadError("Run detection before investigating cases.")
    cases = await run_in_threadpool(CASE_STORE.update_cases, case_scope(dataset),
                                    {r["sim_id"]: r for r in dataset["result"]["rows"]}, body)
    return JSONResponse({"ok": True, "cases": cases})


@api
async def api_download_csv(request):
    dataset = await run_in_threadpool(dataset_for, request)
    rows, _, _ = await run_in_threadpool(select_queue, dataset, request.query_params)
    columns = ["sim_id", "plan_type", "predicted_archetype_name", "risk_score", "priority_score",
               "estimated_opportunity_zar", "pending_billing_zar", "missing_billing_evidence_zar",
               "case_status", "assigned_to", "case_version", "case_updated_at", "whitelist_expires_at",
               *OUTCOME_FIELDS, "deviation_indicator_1"]
    return csv_response(rows, columns, "ghost_sim_filtered_queue.csv")


async def index_page(request):
    return HTMLResponse((PROJECT_DIR / "src" / "dashboard.html").read_text(encoding="utf-8-sig"))


app = Starlette(debug=False, routes=[
    Route("/", index_page), Route("/api/datasets", api_datasets),
    Route("/api/datasets/upload", api_upload, methods=["POST"]),
    Route("/api/datasets/demo", api_demo, methods=["POST"]),
    Route("/api/datasets/sample", api_sample, methods=["POST"]),
    Route("/api/datasets/run", api_run, methods=["POST"]),
    Route("/api/templates/{table}.csv", api_template),
    Route("/api/summary", api_summary), Route("/api/sim/{sim_id}", api_sim_detail),
    Route("/api/action", api_action_case, methods=["POST"]),
    Route("/api/export/top100.csv", api_download_csv), Route("/api/export/queue.csv", api_download_csv),
])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8501, log_level="info")
