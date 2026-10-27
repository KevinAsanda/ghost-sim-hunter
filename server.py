"""
FastAPI / Uvicorn Web Workbench for Ghost SIM Hunter (Zero-Revenue Active SIMs).
Runs natively on the already-installed Uvicorn/Starlette/Python 3.14 stack with zero
compilation or external wheel downloads needed, while providing a rich, interactive
5-tab Revenue Assurance & Intelligent Automation Dashboard.
"""

import json
import os
from typing import Dict, Any
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route
from starlette.requests import Request
import uvicorn

from src.pure_engine import generate_and_run_pipeline, FEATURE_COLS, FEATURE_LABELS
from src.schema import CELL_SITES, LEAKAGE_ARCHETYPES, BENIGN_ARCHETYPES

# Global in-memory state for fast interactive hackathon demo
STATE: Dict[str, Any] = {
    "n_sims": 3000,
    "top_k": 100,
    "seed": 42,
    "rows": [],
    "bt": {},
    "sample_daily": {},
    "resolved_cases": {},  # sim_id -> status ("TICKETED_TO_SME" | "WHITELISTED")
}


def refresh_pipeline(n_sims: int = 3000, top_k: int = 100, seed: int = 42) -> None:
    rows, bt, sample_daily = generate_and_run_pipeline(
        n_sims=n_sims, n_days=30, seed=seed, top_k=top_k
    )
    STATE["n_sims"] = n_sims
    STATE["top_k"] = top_k
    STATE["seed"] = seed
    STATE["rows"] = rows
    STATE["bt"] = bt
    STATE["sample_daily"] = sample_daily


refresh_pipeline()


async def api_summary(request: Request) -> JSONResponse:
    top_k = int(request.query_params.get("top_k", STATE["top_k"]))
    archetype = request.query_params.get("archetype", "ALL")
    plan = request.query_params.get("plan", "ALL")

    rows = STATE["rows"]
    filtered = []
    for r in rows:
        if archetype != "ALL" and r["predicted_archetype_code"] != archetype:
            continue
        if plan != "ALL" and r["plan_type"] != plan:
            continue
        r_copy = dict(r)
        r_copy["case_status"] = STATE["resolved_cases"].get(r["sim_id"], "OPEN_REVIEW")
        filtered.append(r_copy)
        if len(filtered) >= top_k:
            break

    # Cell aggregation for Tab 3 (RAN Clustering)
    cell_map: Dict[str, Dict[str, Any]] = {}
    for c in CELL_SITES:
        cell_map[c["cell_id"]] = {
            "cell_id": c["cell_id"],
            "province": c["province"],
            "area_type": c["area_type"],
            "lat": c["lat"],
            "lon": c["lon"],
            "total_sims": 0,
            "ghost_sims": 0,
            "simbox_sims": 0,
            "unbilled_retail_zar": 0.0,
            "est_recoverable_mid_zar": 0.0,
            "total_voice_mins": 0.0,
        }
    for r in rows:
        cid = r["primary_cell_id"]
        if cid in cell_map:
            cm = cell_map[cid]
            cm["total_sims"] += 1
            if r["unbilled_retail_zar"] > 200 and r["predicted_archetype_code"] in LEAKAGE_ARCHETYPES:
                cm["ghost_sims"] += 1
            if r["rule_simbox_signature"]:
                cm["simbox_sims"] += 1
            cm["unbilled_retail_zar"] = round(cm["unbilled_retail_zar"] + r["unbilled_retail_zar"], 2)
            cm["est_recoverable_mid_zar"] = round(cm["est_recoverable_mid_zar"] + r["est_recoverable_mid_zar"], 2)
            cm["total_voice_mins"] = round(cm["total_voice_mins"] + r["total_voice_mins"], 1)

    cells_list = sorted(cell_map.values(), key=lambda x: x["unbilled_retail_zar"], reverse=True)
    for cm in cells_list:
        cm["ghost_density_pct"] = round(cm["ghost_sims"] / max(1, cm["total_sims"]) * 100, 1)

    # Actioned recovery total
    actioned_zar = sum(
        r["est_recoverable_mid_zar"]
        for r in rows
        if STATE["resolved_cases"].get(r["sim_id"]) == "TICKETED_TO_SME"
    )
    actioned_count = sum(1 for v in STATE["resolved_cases"].values() if v == "TICKETED_TO_SME")

    # Root-cause breakdown in Top-K
    rc_totals: Dict[str, Dict[str, Any]] = {}
    for r in rows[:top_k]:
        code = r["predicted_archetype_code"]
        if code not in rc_totals:
            rc_totals[code] = {
                "code": code,
                "name": r["predicted_archetype_name"],
                "count": 0,
                "unbilled_retail_zar": 0.0,
                "est_recoverable_mid_zar": 0.0,
            }
        rc_totals[code]["count"] += 1
        rc_totals[code]["unbilled_retail_zar"] = round(rc_totals[code]["unbilled_retail_zar"] + r["unbilled_retail_zar"], 2)
        rc_totals[code]["est_recoverable_mid_zar"] = round(rc_totals[code]["est_recoverable_mid_zar"] + r["est_recoverable_mid_zar"], 2)

    return JSONResponse({
        "backtest": STATE["bt"],
        "queue": filtered,
        "cells": cells_list,
        "rc_breakdown": list(rc_totals.values()),
        "actioned_zar": round(actioned_zar, 2),
        "actioned_count": actioned_count,
    })


async def api_sim_detail(request: Request) -> JSONResponse:
    sim_id = request.path_params["sim_id"]
    for r in STATE["rows"]:
        if r["sim_id"] == sim_id:
            r_copy = dict(r)
            r_copy["case_status"] = STATE["resolved_cases"].get(sim_id, "OPEN_REVIEW")
            daily = STATE["sample_daily"].get(sim_id, [])
            attributions = [
                {"feature": FEATURE_LABELS[f], "share_pct": round(r[f"contrib_{f}"] * 100, 1)}
                for f in FEATURE_COLS
            ]
            attributions.sort(key=lambda x: x["share_pct"], reverse=True)
            return JSONResponse({
                "sim": r_copy,
                "daily": daily,
                "attributions": attributions,
            })
    return JSONResponse({"error": "SIM not found"}, status_code=404)


async def api_action_case(request: Request) -> JSONResponse:
    body = await request.json()
    sim_ids = body.get("sim_ids", [])
    status = body.get("status", "TICKETED_TO_SME")
    for sid in sim_ids:
        STATE["resolved_cases"][sid] = status
    actioned_zar = sum(
        r["est_recoverable_mid_zar"]
        for r in STATE["rows"]
        if STATE["resolved_cases"].get(r["sim_id"]) == "TICKETED_TO_SME"
    )
    actioned_count = sum(1 for v in STATE["resolved_cases"].values() if v == "TICKETED_TO_SME")
    return JSONResponse({
        "ok": True,
        "actioned_zar": round(actioned_zar, 2),
        "actioned_count": actioned_count,
    })


async def api_download_csv(request: Request) -> Response:
    path = "data/daily_top_100_queue.csv"
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return Response(
            content,
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=mtn_ghost_sim_top100_queue.csv"},
        )
    return Response("File not found", status_code=404)


async def index_page(request: Request) -> HTMLResponse:
    with open("src/dashboard.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


routes = [
    Route("/", index_page),
    Route("/api/summary", api_summary),
    Route("/api/sim/{sim_id}", api_sim_detail),
    Route("/api/action", api_action_case, methods=["POST"]),
    Route("/api/export/top100.csv", api_download_csv),
]

app = Starlette(debug=True, routes=routes)

if __name__ == "__main__":
    print("Starting Ghost SIM Hunter Live Workbench at http://127.0.0.1:8501 ...")
    uvicorn.run(app, host="127.0.0.1", port=8501, log_level="info")
