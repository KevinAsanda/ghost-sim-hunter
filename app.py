"""
Optional Streamlit Workbench for Ghost SIM Hunter (Zero-Revenue Active SIMs).
Uses the exact same DatasetStore, CaseStore, ingestion validator, and record-level
reconciliation ledger as `server.py`:
1. Select a saved dataset, load the synthetic benchmark, or upload subscriber, usage, and billing files
2. Validate and review data quality (errors, warnings, assumptions)
3. Run detection
4. Investigate cases (simplified queue, filter-matched totals/CSV export, coverage ledger,
   standardized deviation indicators, and persistent internal case + financial outcome records)
"""

import base64
from datetime import date, datetime, timedelta, timezone
import streamlit as st

from src.case_store import CaseValidationError, OUTCOME_FIELDS
from src.ingestion import UploadError, parse_file, validate_tables
from src.pure_engine import FEATURE_COLS, FEATURE_LABELS, generate_synthetic_tables
from src.sample_data import example_tables
from server import CASE_STORE, DATASETS, case_scope, csv_response, select_queue, totals, with_case

st.set_page_config(
    page_title="Ghost SIM Hunter · Revenue assurance",
    page_icon="📡",
    layout="wide",
)


def fmt_zar(val):
    if val is None:
        return "Not recorded"
    return f"R {float(val):,.2f}"


def main():
    st.markdown(
        """
        <style>
        @font-face {
          font-family: 'MTNBrighterSans';
          src: url("https://cdn.prod.website-files.com/63e5615ad9f7af935042c808/63e5628447c67e5a7d80c50b_MTNBrighterSans-Regular.otf") format("opentype");
          font-weight: 400;
        }
        @font-face {
          font-family: 'MTNBrighterSans';
          src: url("https://cdn.prod.website-files.com/63e5615ad9f7af935042c808/63e5628447c67e13d580c509_MTNBrighterSans-Bold.otf") format("opentype");
          font-weight: 700;
        }
        html, body, [class*="css"] {
          font-family: 'MTNBrighterSans', -apple-system, BlinkMacSystemFont, sans-serif;
        }
        .mtn-hero {
          background: linear-gradient(135deg, #ffcb05 0%, #f6d15f 100%);
          color: #121212;
          border-radius: 20px;
          padding: 22px 28px;
          margin-bottom: 18px;
          border: 1px solid #121212;
        }
        .mtn-oval-badge {
          display: inline-block;
          background: #121212;
          color: #ffcb05;
          border-radius: 999px;
          padding: 4px 16px;
          font-weight: 800;
          font-style: italic;
          letter-spacing: 1.5px;
          margin-right: 10px;
        }
        .mtn-pill {
          display: inline-block;
          background: #121212;
          color: #ffcb05;
          border-radius: 100px 100px 100px 0;
          padding: 4px 14px;
          font-size: 11.5px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 1px;
        }
        </style>
        <div class="mtn-hero">
          <div style="margin-bottom:8px">
            <span class="mtn-oval-badge">MTN</span>
            <span class="mtn-pill">Revenue Assurance · Ledger-v2</span>
          </div>
          <h2 style="margin:0 0 6px;color:#121212;font-weight:800">Ghost SIM Hunter — Zero-Revenue Active SIMs</h2>
          <div style="font-size:14px;color:#222">
            <strong>4-Step Workflow:</strong> 1. Connect Dataset · 2. Review Data Quality Gate · 3. Run Record-Level Reconciliation · 4. Investigate Cases &amp; Record Verified Outcomes
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    datasets = DATASETS.list()
    dataset_map = {d["id"]: d for d in datasets}

    col_sel, col_sample, col_demo = st.columns([3, 1, 1])
    with col_sample:
        if st.button("🧪 Load 4-SIM Sample", use_container_width=True):
            tables = example_tables()
            config = {"as_of_date": "2026-10-27", "billing_grace_days": 3}
            normalized, report = validate_tables(
                tables, {k: list(v[0]) for k, v in tables.items()}, config
            )
            staged = DATASETS.stage(
                "4-SIM Edge-Case Sample (EXAMPLE-A..D)", "uploaded", normalized, config, report
            )
            st.session_state["selected_dataset_id"] = staged["id"]
            st.rerun()
    with col_demo:
        if st.button("⚡ Load 3,000-SIM Benchmark", use_container_width=True):
            tables = generate_synthetic_tables()
            config = {"as_of_date": "2026-10-30", "billing_grace_days": 3}
            normalized, report = validate_tables(
                tables, {k: list(v[0]) for k, v in tables.items()}, config
            )
            staged = DATASETS.stage(
                "Synthetic benchmark - 3,000 SIMs", "synthetic", normalized, config, report
            )
            st.session_state["selected_dataset_id"] = staged["id"]
            st.rerun()

    options = [""] + [d["id"] for d in datasets]
    default_id = st.session_state.get("selected_dataset_id", "")
    idx = options.index(default_id) if default_id in options else 0
    with col_sel:
        chosen_id = st.selectbox(
            "Saved dataset",
            options=options,
            index=idx,
            format_func=lambda x: "Choose a dataset"
            if not x
            else f"{dataset_map[x]['name']} · {'Completed' if dataset_map[x]['status'] == 'COMPLETED' else 'Quality review'}",
        )
    st.session_state["selected_dataset_id"] = chosen_id

    with st.expander("Upload subscriber, usage and billing files", expanded=not bool(chosen_id)):
        examples = example_tables()
        tcol1, tcol2, tcol3 = st.columns(3)
        for col_ui, tname in zip((tcol1, tcol2, tcol3), ("subscribers", "usage_daily", "billing_events")):
            rows_ex = examples[tname]
            resp = csv_response(rows_ex, list(rows_ex[0]), f"{tname}.csv")
            col_ui.download_button(
                f"Download example {tname}.csv",
                data=resp.body,
                file_name=f"{tname}.csv",
                mime="text/csv",
                use_container_width=True,
            )

        with st.form("upload_form"):
            u_name = st.text_input("Dataset name", value="Uploaded analysis", max_chars=120)
            c1, c2 = st.columns(2)
            u_as_of = c1.date_input("As-of date", value=date(2026, 10, 27))
            u_grace = c2.number_input("Billing delay allowance (days)", min_value=0, max_value=60, value=3, step=1)
            f1, f2, f3 = st.columns(3)
            sub_up = f1.file_uploader("Subscribers (.csv or .parquet)", type=["csv", "parquet"])
            usg_up = f2.file_uploader("Daily usage (.csv or .parquet)", type=["csv", "parquet"])
            bil_up = f3.file_uploader("Billing events (.csv or .parquet)", type=["csv", "parquet"])
            if st.form_submit_button("Validate files"):
                if not (sub_up and usg_up and bil_up):
                    st.error("Choose all three files.")
                else:
                    try:
                        parsed = {}
                        for key, uploaded in (
                            ("subscribers", sub_up),
                            ("usage_daily", usg_up),
                            ("billing_events", bil_up),
                        ):
                            b64 = base64.b64encode(uploaded.getvalue()).decode("ascii")
                            parsed[key] = parse_file({"name": uploaded.name, "content_base64": b64})
                        cfg = {"as_of_date": u_as_of.isoformat(), "billing_grace_days": int(u_grace)}
                        norm, qual = validate_tables(
                            {k: v[0] for k, v in parsed.items()},
                            {k: v[1] for k, v in parsed.items()},
                            cfg,
                        )
                        st.session_state["last_validation_quality"] = qual
                        if not qual["valid"]:
                            st.error("Correct the data errors and upload again.")
                        else:
                            staged = DATASETS.stage(u_name.strip(), "uploaded", norm, cfg, qual)
                            st.session_state["selected_dataset_id"] = staged["id"]
                            st.session_state.pop("last_validation_quality", None)
                            st.rerun()
                    except UploadError as exc:
                        st.error(str(exc))

        if "last_validation_quality" in st.session_state and not chosen_id:
            q_err = st.session_state["last_validation_quality"]
            st.subheader("Data quality review (failed validation)")
            st.write(
                f"{q_err['error_count']} error(s), {q_err['warning_count']} warning(s) · "
                f"Window {q_err['window_start'] or 'Unknown'} to {q_err['window_end'] or 'Unknown'}"
            )
            for item in q_err["issues"]:
                st.write(
                    f"- **{item['severity'].upper()}** · `{item['table']}`"
                    f"{' row ' + str(item['row']) if item['row'] else ''}"
                    f"{' / ' + item['column'] if item['column'] else ''}: {item['message']}"
                )

    if not chosen_id:
        st.info("Upload an anonymized dataset or load the synthetic benchmark. Nothing is generated automatically.")
        return

    dataset = DATASETS.get(chosen_id)
    q = dataset["quality"]
    st.subheader("Data quality review")
    counts_str = " · ".join(f"{k.replace('_', ' ')}: {v:,}" for k, v in q["row_counts"].items())
    st.write(
        f"**{dataset['name']}** ({'SYNTHETIC BENCHMARK' if dataset['source'] == 'synthetic' else 'UPLOADED DATA'}) · "
        f"{counts_str} | {q['error_count']} error(s), {q['warning_count']} warning(s) | "
        f"Window {q['window_start'] or 'Unknown'} to {q['window_end'] or 'Unknown'} · "
        f"As of {dataset['config']['as_of_date']} · Billing allowance {dataset['config']['billing_grace_days']} days"
    )
    if q["issues"]:
        with st.expander(f"Quality issues ({len(q['issues'])} shown)", expanded=dataset["status"] != "COMPLETED"):
            for item in q["issues"]:
                st.write(
                    f"- **{item['severity'].upper()}** · `{item['table']}`"
                    f"{' row ' + str(item['row']) if item['row'] else ''}"
                    f"{' / ' + item['column'] if item['column'] else ''}: {item['message']}"
                )

    ack = True
    if q["warning_count"] > 0 and dataset["status"] != "COMPLETED":
        ack = st.checkbox("I reviewed the warnings and understand the stated assumptions.")

    if dataset["status"] != "COMPLETED":
        if st.button("Run detection", disabled=not q["valid"] or (q["warning_count"] > 0 and not ack)):
            try:
                DATASETS.run(dataset["id"], acknowledge_warnings=ack)
                st.rerun()
            except UploadError as exc:
                st.error(str(exc))
        return

    # Step 4: Investigation workspace
    st.markdown("---")
    st.subheader("Investigation queue")
    st.caption(
        f"Dataset {q['window_start']} – {q['window_end']} · "
        f"Last successful run {dataset['last_successful_run']} · Totals cover the visible queue only"
    )

    reviewer = st.text_input("Reviewer name (self-reported)", max_chars=120, key="reviewer_name")

    all_cases = CASE_STORE.list_cases(case_scope(dataset))
    all_rows_with_cases = [with_case(r, all_cases) for r in dataset["result"]["rows"]]
    owner_opts = ["ALL", "UNASSIGNED"] + sorted({r["assigned_to"] for r in all_rows_with_cases if r["assigned_to"]})
    arch_opts = ["ALL"] + sorted({r["predicted_archetype_code"] for r in all_rows_with_cases})
    plan_opts = ["ALL"] + sorted({r["plan_type"] for r in all_rows_with_cases})

    fcol1, fcol2, fcol3, fcol4, fcol5 = st.columns(5)
    search_q = fcol1.text_input("Search", placeholder="SIM, account, owner or cell")
    status_q = fcol2.selectbox("Status", ["ALL", "NEW", "INVESTIGATING", "CONFIRMED", "DISMISSED", "RESOLVED"])
    owner_q = fcol3.selectbox("Owner", owner_opts)
    arch_q = fcol4.selectbox("Root-cause hypothesis", arch_opts)
    plan_q = fcol5.selectbox("Plan", plan_opts)

    scol1, scol2, scol3, scol4 = st.columns(4)
    view_q = scol1.selectbox("Show", ["review", "all"], format_func=lambda v: "Needs review / already saved" if v == "review" else "All subscribers")
    sort_q = scol2.selectbox("Sort by", ["priority_score", "unbilled_retail_zar", "risk_score", "sim_id", "case_updated_at"])
    order_q = scol3.selectbox("Order", ["desc", "asc"])
    topk_q = scol4.selectbox("Queue limit", [25, 100, 250, 1000], index=1)

    params = {
        "search": search_q,
        "status": status_q,
        "owner": owner_q,
        "archetype": arch_q,
        "plan": plan_q,
        "view": view_q,
        "sort": sort_q,
        "order": order_q,
        "top_k": str(topk_q),
    }
    queue_rows, matching_count, _ = select_queue(dataset, params)
    queue_totals = totals(queue_rows)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Estimated opportunity", fmt_zar(queue_totals["estimated_opportunity_zar"]), "Mature reference-value residual; unconfirmed")
    m2.metric("Confirmed leakage", fmt_zar(queue_totals["confirmed_leakage_zar"]), f"{queue_totals['confirmed_leakage_zar_recorded_count']} case(s) recorded")
    m3.metric("Cash recovered", fmt_zar(queue_totals["cash_recovered_zar"]), f"{queue_totals['cash_recovered_zar_recorded_count']} case(s) recorded")
    m4.metric("Cost avoided", fmt_zar(queue_totals["cost_avoided_zar"]), f"{queue_totals['cost_avoided_zar_recorded_count']} case(s) recorded")
    st.caption(
        f"Awaiting billing: {fmt_zar(queue_totals['pending_billing_zar'])} · "
        f"Missing billing evidence: {fmt_zar(queue_totals['missing_billing_evidence_zar'])}. "
        "Neither is included in estimated opportunity. Risk scores are prioritization scores, not probabilities."
    )

    bm = dataset["result"]["benchmark"]
    if bm:
        with st.expander("Synthetic benchmark results"):
            st.write(
                f"**{bm['label']}.** Whole synthetic dataset, independent of queue filters. "
                f"Precision@{bm['top_k']}: {bm['hunter_precision_k']*100:.1f}%; "
                f"Recall@{bm['top_k']}: {bm['hunter_recall_k']*100:.1f}%; "
                f"Benign share in queue: {bm['hunter_benign_share_k']*100:.1f}%."
            )
    else:
        st.caption("No validated accuracy is available for this uploaded dataset. Risk scores are prioritization scores, not probabilities.")

    export_cols = [
        "sim_id", "plan_type", "predicted_archetype_name", "risk_score", "priority_score",
        "estimated_opportunity_zar", "pending_billing_zar", "missing_billing_evidence_zar",
        "case_status", "assigned_to", "case_version", "case_updated_at", "whitelist_expires_at",
        *OUTCOME_FIELDS, "deviation_indicator_1",
    ]
    export_resp = csv_response(queue_rows, export_cols, "ghost_sim_filtered_queue.csv")
    st.download_button(
        f"Export visible queue ({len(queue_rows)} of {matching_count} matching cases)",
        data=export_resp.body,
        file_name="ghost_sim_filtered_queue.csv",
        mime="text/csv",
    )

    if not queue_rows:
        st.info("No cases match these filters.")
        return

    col_q, col_insp = st.columns([3, 2])
    with col_q:
        st.dataframe(
            [
                {
                    "SIM ID": r["sim_id"],
                    "Hypothesis": r["predicted_archetype_name"],
                    "Estimated opportunity (ZAR)": r["estimated_opportunity_zar"],
                    "Risk / 100": round(r["risk_score"] * 100),
                    "Status": r["case_status"],
                    "Owner": r["assigned_to"] or "Unassigned",
                }
                for r in queue_rows
            ],
            use_container_width=True,
            height=420,
        )

    with col_insp:
        sim_ids = [r["sim_id"] for r in queue_rows]
        selected_sim_id = st.selectbox("Inspect SIM case", options=sim_ids)
        sim_row = next(r for r in queue_rows if r["sim_id"] == selected_sim_id)
        scope = case_scope(dataset)
        draft_key = f"case_draft:{scope}:{selected_sim_id}"
        if st.button("Reload saved case", key=f"reload:{draft_key}"):
            st.session_state.pop(draft_key, None)
        if draft_key not in st.session_state:
            st.session_state[draft_key] = CASE_STORE.get_case(scope, selected_sim_id)
        case = st.session_state[draft_key]

        st.markdown(f"### {sim_row['sim_id']} · {sim_row['predicted_archetype_name']} · Risk {sim_row['risk_score']*100:.0f}/100")
        st.write(sim_row["investigation_summary"])
        st.caption(f"Suggested review action: {sim_row['recommended_action']}")

        with st.expander("Reconciliation coverage summary & daily ledger"):
            st.json({
                "Reference usage value": fmt_zar(sim_row["total_retail_equiv_zar"]),
                "Zero-rated data coverage": fmt_zar(sim_row["zero_rated_covered_zar"]),
                "Valid entitlement coverage": fmt_zar(sim_row["entitlement_covered_zar"]),
                "Rated charge coverage": fmt_zar(sim_row["rated_covered_zar"]),
                "Additional invoice coverage": fmt_zar(sim_row["invoice_covered_zar"]),
                "Additional parent coverage": fmt_zar(sim_row["parent_covered_zar"]),
                "Mature residual": fmt_zar(sim_row["unbilled_retail_zar"]),
                "Awaiting billing": fmt_zar(sim_row["pending_billing_zar"]),
                "Missing billing evidence": fmt_zar(sim_row["missing_billing_evidence_zar"]),
                "Observed APNs": sim_row["observed_apns"],
                "Primary cell": sim_row["primary_cell_id"],
            })
            st.dataframe(dataset["result"]["daily"].get(selected_sim_id, []), use_container_width=True)

        with st.expander("Deviation indicators"):
            st.caption("Share of positive standardized deviation across this dataset. These are not SHAP values or model explanations.")
            for fcol in FEATURE_COLS:
                st.write(f"- **{FEATURE_LABELS[fcol]}**: {sim_row[f'contrib_{fcol}']*100:.1f}%")

        edit_key = f"edit:{draft_key}:{case['version']}"
        st.caption(f"Version {case['version']} · Created {case['created_at'] or 'Not yet saved'} · Updated {case['updated_at'] or 'Not yet saved'}")
        with st.form(f"form:{edit_key}"):
            assigned = st.text_input("Assigned owner", value=case["assigned_to"] or sim_row["owner_team"], max_chars=120)
            status = st.selectbox("Status", [case["status"], *case["allowed_transitions"]])
            note = st.text_area("New note / evidence", max_chars=4000)
            reason = st.text_area("Reason for benign dismissal", value=case["dismissal_reason"], max_chars=2000)
            expiry = st.date_input(
                "Whitelist expiry (00:00 UTC)",
                value=datetime.fromisoformat(case["whitelist_expires_at"]).date()
                if case["whitelist_expires_at"]
                else (datetime.now(timezone.utc) + timedelta(days=30)).date(),
            )
            oc1, oc2, oc3 = st.columns(3)
            c_leak = oc1.text_input("Confirmed leakage (ZAR)", value="" if case["confirmed_leakage_zar"] is None else str(case["confirmed_leakage_zar"]))
            c_rec = oc2.text_input("Cash recovered (ZAR)", value="" if case["cash_recovered_zar"] is None else str(case["cash_recovered_zar"]))
            c_avoid = oc3.text_input("Cost avoided (ZAR)", value="" if case["cost_avoided_zar"] is None else str(case["cost_avoided_zar"]))
            if st.form_submit_button("Save case decision"):
                try:
                    payload = {
                        "sim_ids": [selected_sim_id],
                        "status": status,
                        "actor": reviewer,
                        "assigned_to": assigned,
                        "note": note,
                        "expected_versions": {selected_sim_id: case["version"]},
                        "confirmed_leakage_zar": None if not c_leak.strip() else float(c_leak),
                        "cash_recovered_zar": None if not c_rec.strip() else float(c_rec),
                        "cost_avoided_zar": None if not c_avoid.strip() else float(c_avoid),
                    }
                    if status == "DISMISSED":
                        payload["dismissal_reason"] = reason
                        payload["whitelist_expires_at"] = f"{expiry.isoformat()}T00:00:00+00:00"
                    CASE_STORE.update_cases(
                        scope, {r["sim_id"]: r for r in dataset["result"]["rows"]}, payload
                    )
                    st.session_state.pop(draft_key, None)
                    st.rerun()
                except (CaseValidationError, ValueError) as exc:
                    st.error(str(exc))

        with st.expander("Decision history"):
            for event in reversed(CASE_STORE.get_case(scope, selected_sim_id)["history"]):
                st.text(
                    f"{event['occurred_at']} · {event['actor']} · {event['event_type']}\n"
                    f"{event['before'].get('status', 'Unrecorded')} → {event['after']['status']} · "
                    f"Owner: {event['after']['assigned_to'] or 'Unassigned'}\n{event['note']}"
                )


if __name__ == "__main__":
    main()
