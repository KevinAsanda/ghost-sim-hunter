"""
Ghost SIM Hunter — Zero-Revenue Active SIMs
Intelligent Automation & Revenue Assurance Workbench for MTN Hackathon MVP.

Provides:
1. Executive Overview & Daily Top-N Case Queue (Ranked by Value at Risk x Confidence)
2. Deep-Dive Single-SIM Forensic Inspector (HLR vs OCS, Entitlements, SHAP Drivers, Daily Timeline, Action)
3. RAN & Cell-ID Cluster Analytics (Spatial anomaly clustering for SIM Box & Bad Provisioning batches)
4. Backtest vs. Ground Truth & Transparent Business Case Calculator (Retail Tariff vs Marginal Cost vs Recoverable Cash)
5. Real / Anonymised Data Adapter (Upload custom CSV/Parquet files or export synthetic datasets)
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go

from src.schema import TARIFF_ASSUMPTIONS, LEAKAGE_ARCHETYPES, BENIGN_ARCHETYPES
from src.data_generator import (
    generate_synthetic_dataset,
    load_into_duckdb,
    validate_custom_files,
)
from src.detection_engine import run_full_pipeline, FEATURE_COLS, FEATURE_LABELS
from src.backtest import evaluate_backtest

st.set_page_config(
    page_title="Ghost SIM Hunter | Zero-Revenue Active SIMs",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom styling with MTN-inspired yellow/dark-slate accents
st.markdown(
    """
    <style>
    .main-header {
        background: linear-gradient(90deg, #0f172a 0%, #1e293b 100%);
        padding: 1.2rem 1.6rem;
        border-radius: 10px;
        border-left: 6px solid #ffcc00;
        color: #f8fafc;
        margin-bottom: 1.2rem;
    }
    .main-header h1 {
        margin: 0;
        font-size: 1.85rem;
        color: #ffcc00;
    }
    .main-header p {
        margin: 0.35rem 0 0 0;
        font-size: 0.98rem;
        color: #cbd5e1;
    }
    .badge-safe {
        background-color: #065f46;
        color: #ecfdf5;
        padding: 0.25rem 0.65rem;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    .badge-warn {
        background-color: #92400e;
        color: #fef3c7;
        padding: 0.25rem 0.65rem;
        border-radius: 999px;
        font-size: 0.8rem;
        font-weight: 600;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(show_spinner="Generating synthetic CDRs, subscriber entitlements & running 3-layer DuckDB/ML pipeline...")
def get_cached_pipeline_data(n_sims: int, n_days: int, seed: int, top_k: int):
    datasets = generate_synthetic_dataset(n_sims=n_sims, n_days=n_days, seed=seed)
    con = load_into_duckdb(datasets)
    results_df = run_full_pipeline(con)
    backtest_metrics = evaluate_backtest(results_df, top_k=top_k)
    return datasets, results_df, backtest_metrics


def main():
    # -----------------------------------------------------------------------
    # Sidebar Configuration
    # -----------------------------------------------------------------------
    with st.sidebar:
        st.markdown("### ⚙️ Batch Pipeline Controls")
        data_mode = st.radio(
            "Data Source Mode",
            ["Synthetic Benchmark (Ground Truth)", "Custom CSV / Parquet Adapter"],
            index=0,
        )

        n_sims = st.slider("Subscriber Population Size", min_value=1000, max_value=10000, value=3000, step=500)
        top_k = st.slider("Daily Analyst Queue Size (Top-N)", min_value=25, max_value=250, value=100, step=25)
        seed = st.number_input("Simulation Random Seed", min_value=1, max_value=9999, value=42)

        st.markdown("---")
        st.markdown("### 🛡️ Operating Guardrails")
        st.markdown(
            "<span class='badge-safe'>READ-ONLY RECOMMEND MODE</span><br>"
            "<small>Auto-blocking is disabled in MVP to guarantee <b>Zero Customer Impact</b>. "
            "All cases output evidence-backed actions for Revenue Assurance & Fraud SMEs.</small>",
            unsafe_allow_html=True,
        )

        if "resolved_cases" not in st.session_state:
            st.session_state["resolved_cases"] = {}

    # Load data
    datasets, results_df, bt = get_cached_pipeline_data(n_sims=n_sims, n_days=30, seed=seed, top_k=top_k)

    # -----------------------------------------------------------------------
    # Header Banner
    # -----------------------------------------------------------------------
    st.markdown(
        """
        <div class="main-header">
            <h1>📡 Ghost SIM Hunter — Zero-Revenue Active SIMs</h1>
            <p>
                <b>Intelligent Automation & OSS/BSS Revenue Assurance Engine</b> ·
                Multi-System Reconciliation (HLR / OCS / Billing) + Isolation Forest Anomaly Scoring + Automated Root-Cause Diagnostics
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # -----------------------------------------------------------------------
    # Top KPI Strip
    # -----------------------------------------------------------------------
    fin = bt["financials"]
    resolved_ids = list(st.session_state["resolved_cases"].keys())
    recovered_so_far_zar = float(
        results_df.loc[results_df["sim_id"].isin(resolved_ids), "est_recoverable_mid_zar"].sum()
    )

    kpi1, kpi2, kpi3, kpi4, kpi5 = st.columns(5)
    kpi1.metric(
        f"Precision@{top_k} (vs Baseline)",
        f"{bt['hunter_precision_k']*100:.1f}%",
        delta=f"+{(bt['hunter_precision_k'] - bt['naive_precision_k'])*100:.1f}% vs Naive R0 Report",
    )
    kpi2.metric(
        f"Benign Look-Alike FPR@{top_k}",
        f"{bt['hunter_benign_fpr_k']*100:.1f}%",
        delta=f"-{(bt['naive_benign_fpr_k'] - bt['hunter_benign_fpr_k'])*100:.1f}% False Positives",
        delta_color="normal",
    )
    kpi3.metric(
        f"Top-{top_k} Unbilled Retail Value",
        f"R {fin['top_k_retail_zar']:,.0f} /mo",
        help="Monthly usage valued at standard retail tariff rates (R0.29/MB data, R1.20/min voice).",
    )
    kpi4.metric(
        f"Top-{top_k} Est. Cash Recoverable",
        f"R {fin['top_k_rec_mid_zar']:,.0f} /mo",
        delta=f"Range: R{fin['top_k_rec_low_zar']/1000:.0f}k – R{fin['top_k_rec_high_zar']/1000:.0f}k",
        help="Realistic recoverable/avoidable cash after accounting for recovery probability per root cause.",
    )
    kpi5.metric(
        "Session Actioned Recovery",
        f"R {recovered_so_far_zar:,.0f} /mo",
        delta=f"{len(resolved_ids)} SIMs Ticketed",
    )

    # -----------------------------------------------------------------------
    # Main Navigation Tabs
    # -----------------------------------------------------------------------
    tab_queue, tab_drill, tab_ran, tab_backtest, tab_data = st.tabs([
        f"📋 1. Daily Top-{top_k} Queue",
        "🔍 2. SIM Forensic Drill-Down",
        "🗺️ 3. RAN / Cell-ID Clustering",
        "📈 4. Backtest & Business Case",
        "🔌 5. Data Adapter & Schema",
    ])

    # =======================================================================
    # TAB 1: DAILY TOP-N CASE QUEUE
    # =======================================================================
    with tab_queue:
        st.subheader(f"Daily Ranked Revenue Assurance Queue (Top {top_k} SIMs)")
        st.caption(
            "Ranked by **Priority Score = Uncovered Retail Value (ZAR) × Hybrid Confidence (Layer 1 Rules + Layer 2 Isolation Forest)**. "
            "Legitimate R0-revenue look-alikes (Rollover Bundles, Enterprise Pooled Accounts, Zero-Rated APNs) are automatically reconciled and suppressed."
        )

        col_f1, col_f2, col_f3 = st.columns([2, 2, 1])
        with col_f1:
            archetype_filter = st.multiselect(
                "Filter by Root-Cause Hypothesis",
                options=sorted(results_df["predicted_archetype_name"].unique()),
                default=[a["name"] for a in LEAKAGE_ARCHETYPES.values()],
            )
        with col_f2:
            plan_filter = st.multiselect(
                "Filter by Plan Type",
                options=sorted(results_df["plan_type"].unique()),
                default=sorted(results_df["plan_type"].unique()),
            )
        with col_f3:
            if st.button("⚡ Batch-Ticket Top 25 Cases", use_container_width=True):
                for sid in results_df.head(25)["sim_id"]:
                    st.session_state["resolved_cases"][sid] = "TICKETED"
                st.rerun()

        filtered_queue = results_df[
            results_df["predicted_archetype_name"].isin(archetype_filter)
            & results_df["plan_type"].isin(plan_filter)
        ].head(top_k).copy()

        filtered_queue["case_status"] = filtered_queue["sim_id"].map(
            lambda x: st.session_state["resolved_cases"].get(x, "OPEN_REVIEW")
        )

        display_cols = [
            "hunter_rank",
            "sim_id",
            "msisdn",
            "plan_type",
            "hlr_status",
            "billing_status",
            "predicted_archetype_name",
            "unbilled_retail_zar",
            "marginal_network_cost_zar",
            "est_recoverable_mid_zar",
            "confidence_score",
            "priority_score",
            "primary_cell_id",
            "owner_team",
            "case_status",
        ]

        st.dataframe(
            filtered_queue[display_cols].style.format({
                "unbilled_retail_zar": "R {:,.2f}",
                "marginal_network_cost_zar": "R {:,.2f}",
                "est_recoverable_mid_zar": "R {:,.2f}",
                "confidence_score": "{:.1%}",
                "priority_score": "{:,.1f}",
            }),
            use_container_width=True,
            height=420,
        )

        # Summary by Root Cause in the Queue
        c_chart1, c_chart2 = st.columns(2)
        with c_chart1:
            rc_summary = (
                filtered_queue.groupby("predicted_archetype_name", as_index=False)
                .agg(
                    sim_count=("sim_id", "count"),
                    unbilled_retail_zar=("unbilled_retail_zar", "sum"),
                    est_recoverable_mid_zar=("est_recoverable_mid_zar", "sum"),
                )
                .sort_values("unbilled_retail_zar", ascending=True)
            )
            fig_rc = px.bar(
                rc_summary,
                x=["est_recoverable_mid_zar", "unbilled_retail_zar"],
                y="predicted_archetype_name",
                orientation="h",
                barmode="group",
                title=f"Value at Risk by Root-Cause Hypothesis (Top {len(filtered_queue)} Queue)",
                labels={"value": "Monthly Amount (ZAR)", "predicted_archetype_name": "Root-Cause Hypothesis"},
                color_discrete_sequence=["#10b981", "#f59e0b"],
            )
            st.plotly_chart(fig_rc, use_container_width=True)

        with c_chart2:
            # Show suppressed Benign Look-Alikes saved from false-positive investigation
            benign_df = results_df[results_df["is_benign_exempt"]]
            benign_summary = (
                benign_df.groupby("predicted_archetype_name", as_index=False)
                .agg(
                    suppressed_sims=("sim_id", "count"),
                    retail_usage_zar=("total_retail_equiv_zar", "sum"),
                )
            )
            fig_benign = px.pie(
                benign_summary,
                values="suppressed_sims",
                names="predicted_archetype_name",
                hole=0.45,
                title=f"Auto-Suppressed Benign 'R0 Direct Revenue' SIMs ({len(benign_df)} False Positives Avoided)",
                color_discrete_sequence=["#3b82f6", "#06b6d4", "#8b5cf6"],
            )
            st.plotly_chart(fig_benign, use_container_width=True)

    # =======================================================================
    # TAB 2: SINGLE-SIM FORENSIC DRILL-DOWN
    # =======================================================================
    with tab_drill:
        st.subheader("🔍 Automated SIM Forensic Investigation & Evidence Pack")

        top_candidates = results_df.head(max(top_k, 150))
        sim_options = [
            f"#{int(r['hunter_rank'])} | {r['sim_id']} ({r['msisdn']}) — {r['predicted_archetype_name']} [R{r['unbilled_retail_zar']:,.0f}]"
            for _, r in top_candidates.iterrows()
        ]
        selected_label = st.selectbox("Select Case from Queue to Inspect:", options=sim_options, index=0)
        selected_sim_id = selected_label.split("|")[1].strip().split(" ")[0]

        sim_row = results_df[results_df["sim_id"] == selected_sim_id].iloc[0]
        sim_daily = datasets["usage_daily"][datasets["usage_daily"]["sim_id"] == selected_sim_id].sort_values("usage_date")

        # Action Bar
        act_col1, act_col2, act_col3 = st.columns([3, 1, 1])
        with act_col1:
            st.info(
                f"**Root-Cause Diagnosis:** {sim_row['predicted_archetype_name']}  \n"
                f"**Automated Evidence Summary:** {sim_row['investigation_summary']}  \n"
                f"**Recommended Action ({sim_row['owner_team']}):** {sim_row['recommended_action']}"
            )
        with act_col2:
            current_state = st.session_state["resolved_cases"].get(selected_sim_id, "OPEN_REVIEW")
            st.metric("Current Case Status", current_state)
            if st.button("✅ Approve & Create Remedy Ticket", key=f"btn_approve_{selected_sim_id}", use_container_width=True):
                st.session_state["resolved_cases"][selected_sim_id] = "TICKETED_TO_SME"
                st.rerun()
        with act_col3:
            st.metric("Ground Truth Label (Validation)", sim_row["ground_truth_label"])
            if st.button("🚫 Mark as Benign Whitelist", key=f"btn_dismiss_{selected_sim_id}", use_container_width=True):
                st.session_state["resolved_cases"][selected_sim_id] = "WHITELISTED"
                st.rerun()

        # 5-Point Automated Diagnostic Checklist (from Pitch Deck)
        st.markdown("#### 5-Point Automated OSS/BSS Diagnostic Checklist")
        chk1, chk2, chk3, chk4, chk5 = st.columns(5)
        chk1.metric(
            "1. HLR vs Billing Status",
            f"{sim_row['hlr_status']} / {sim_row['billing_status']}",
            delta="DESYNC ORPHAN" if sim_row["rule_hlr_ocs_orphan"] else "Synced",
            delta_color="inverse" if sim_row["rule_hlr_ocs_orphan"] else "off",
        )
        chk2.metric(
            "2. APN & Tariff Check",
            f"{sim_row['primary_apn']}",
            delta=f"Tariff: {sim_row['tariff_code']}",
            delta_color="inverse" if (sim_row["rule_iot_apn_mismatch"] or sim_row["rule_unrated_tariff"]) else "off",
        )
        chk3.metric(
            "3. Device & IMEI Rotation",
            f"{sim_row['device_type']}",
            delta=f"{int(sim_row['max_distinct_imeis'])} Distinct IMEI(s)",
            delta_color="inverse" if sim_row["max_distinct_imeis"] >= 3 else "off",
        )
        chk4.metric(
            "4. Cell Site & Night Profile",
            f"{sim_row['primary_cell_id']}",
            delta=f"{sim_row['avg_night_ratio']*100:.0f}% Night Traffic ({sim_row['cell_area_type']})",
            delta_color="inverse" if sim_row["avg_night_ratio"] > 0.45 else "off",
        )
        chk5.metric(
            "5. Entitlement & Parent Check",
            f"R {sim_row['total_revenue_zar']:,.2f} Billed",
            delta=f"Bundle: {sim_row['entitlement_bundle_mb']:,.0f} MB | Parent: {sim_row['parent_account_id'] or 'None'}",
            delta_color="off",
        )

        # Charts: 30-day usage vs billing timeline + Feature Attribution (SHAP-style drivers)
        d_col1, d_col2 = st.columns([3, 2])
        with d_col1:
            fig_timeline = go.Figure()
            fig_timeline.add_trace(
                go.Bar(
                    x=sim_daily["usage_date"],
                    y=sim_daily["retail_equivalent_zar"],
                    name="Daily Retail Usage Value (ZAR)",
                    marker_color="#f59e0b",
                )
            )
            fig_timeline.add_trace(
                go.Scatter(
                    x=sim_daily["usage_date"],
                    y=sim_daily["rated_amount_zar"],
                    name="OCS Rated / Billed Amount (ZAR)",
                    mode="lines+markers",
                    line=dict(color="#10b981", width=3),
                )
            )
            fig_timeline.update_layout(
                title=f"30-Day Network Usage Value vs. OCS Billed Amount ({selected_sim_id})",
                xaxis_title="Usage Date",
                yaxis_title="ZAR (R)",
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                height=360,
            )
            st.plotly_chart(fig_timeline, use_container_width=True)

        with d_col2:
            contrib_rows = []
            for fcol in FEATURE_COLS:
                contrib_rows.append({
                    "Feature": FEATURE_LABELS[fcol],
                    "Attribution_Share": float(sim_row[f"contrib_{fcol}"]) * 100.0,
                })
            contrib_df = pd.DataFrame(contrib_rows).sort_values("Attribution_Share", ascending=True)
            fig_attr = px.bar(
                contrib_df,
                x="Attribution_Share",
                y="Feature",
                orientation="h",
                title=f"Layer 2 Anomaly Feature Attribution (Score: {sim_row['confidence_score']:.1%})",
                labels={"Attribution_Share": "% Contribution to Anomaly Score", "Feature": ""},
                color="Attribution_Share",
                color_continuous_scale="YlOrRd",
            )
            fig_attr.update_layout(height=360, coloraxis_showscale=False)
            st.plotly_chart(fig_attr, use_container_width=True)

    # =======================================================================
    # TAB 3: RAN / CELL-ID CLUSTERING
    # =======================================================================
    with tab_ran:
        st.subheader("🗺️ RAN Cell-ID Zero-Revenue Traffic Clustering")
        st.caption(
            "Supports the **Intelligent Automation + RAN** angle: clustering unbilled active SIMs by Cell ID "
            "pinpoints **SIM Box fraud banks** (e.g., Hillbrow/Soweto high-density cells) and **localised bad provisioning batches**."
        )

        flagged_only = results_df[results_df["unbilled_retail_zar"] > 0]
        cell_agg = (
            results_df.groupby(["primary_cell_id", "cell_province", "cell_area_type", "cell_lat", "cell_lon"], as_index=False)
            .agg(
                total_sims=("sim_id", "count"),
                ghost_sims=("unbilled_retail_zar", lambda s: int((s > 200).sum())),
                simbox_sims=("rule_simbox_signature", "sum"),
                unbilled_retail_zar=("unbilled_retail_zar", "sum"),
                est_recoverable_mid_zar=("est_recoverable_mid_zar", "sum"),
                total_voice_mins=("total_voice_mins", "sum"),
            )
            .sort_values("unbilled_retail_zar", ascending=False)
        )
        cell_agg["ghost_sim_ratio"] = (cell_agg["ghost_sims"] / cell_agg["total_sims"]).round(4)

        r_col1, r_col2 = st.columns([3, 2])
        with r_col1:
            fig_map = px.scatter_mapbox(
                cell_agg,
                lat="cell_lat",
                lon="cell_lon",
                size="unbilled_retail_zar",
                color="ghost_sim_ratio",
                hover_name="primary_cell_id",
                hover_data={
                    "cell_province": True,
                    "cell_area_type": True,
                    "ghost_sims": True,
                    "simbox_sims": True,
                    "unbilled_retail_zar": ":,.0f",
                    "ghost_sim_ratio": ":.1%",
                    "cell_lat": False,
                    "cell_lon": False,
                },
                color_continuous_scale="Turbo",
                size_max=35,
                zoom=4.6,
                center={"lat": -28.5, "lon": 25.0},
                mapbox_style="carto-positron",
                title="South Africa Cell Site Unbilled Revenue Hotspots (Bubble Size = Unbilled ZAR, Color = Ghost SIM Density)",
            )
            fig_map.update_layout(height=450, margin={"r": 0, "t": 40, "l": 0, "b": 0})
            st.plotly_chart(fig_map, use_container_width=True)

        with r_col2:
            fig_cell_bar = px.bar(
                cell_agg.sort_values("unbilled_retail_zar", ascending=True),
                x="unbilled_retail_zar",
                y="primary_cell_id",
                color="simbox_sims",
                orientation="h",
                title="Cell Sites Ranked by Unbilled Retail Leakage (ZAR)",
                labels={
                    "unbilled_retail_zar": "Unbilled Retail Value (ZAR)",
                    "primary_cell_id": "Cell ID",
                    "simbox_sims": "SIM Box Count",
                },
                color_continuous_scale="Reds",
            )
            fig_cell_bar.update_layout(height=450)
            st.plotly_chart(fig_cell_bar, use_container_width=True)

        st.dataframe(
            cell_agg.style.format({
                "unbilled_retail_zar": "R {:,.2f}",
                "est_recoverable_mid_zar": "R {:,.2f}",
                "ghost_sim_ratio": "{:.1%}",
                "total_voice_mins": "{:,.0f}",
            }),
            use_container_width=True,
        )

    # =======================================================================
    # TAB 4: BACKTEST & TRANSPARENT BUSINESS CASE
    # =======================================================================
    with tab_backtest:
        st.subheader("📈 Backtest vs. Ground Truth & Defensible Financial Model")
        st.markdown(
            """
            **Why a simple 'R0 Revenue' SQL threshold fails in production:**  
            In a real operator, thousands of active SIMs show `R0.00` direct revenue in a 30-day window because they are covered by **multi-month prepaid bundles**, **corporate master accounts**, or **zero-rated public APNs**.  
            - The **Naive Threshold Baseline** (`direct_sim_revenue == 0 ORDER BY retail_usage DESC`) floods analysts with false positives.  
            - **Ghost SIM Hunter** reconciles entitlements first, then combines deterministic OSS/BSS rules with Isolation Forest anomaly scoring.
            """
        )

        b_col1, b_col2 = st.columns(2)
        with b_col1:
            curves_df = bt["curves_df"]
            fig_prec = go.Figure()
            fig_prec.add_trace(
                go.Scatter(
                    x=curves_df["K"],
                    y=curves_df["Hunter_Precision"] * 100,
                    name="Ghost SIM Hunter (3-Layer Hybrid)",
                    line=dict(color="#10b981", width=3),
                )
            )
            fig_prec.add_trace(
                go.Scatter(
                    x=curves_df["K"],
                    y=curves_df["Naive_Precision"] * 100,
                    name="Naive R0 Threshold Baseline",
                    line=dict(color="#ef4444", width=3, dash="dash"),
                )
            )
            fig_prec.update_layout(
                title="Precision@K Curve (True Leakage % in Top-K Queue)",
                xaxis_title="Queue Depth (Top K SIMs)",
                yaxis_title="Precision (%)",
                height=360,
            )
            st.plotly_chart(fig_prec, use_container_width=True)

        with b_col2:
            fig_val = go.Figure()
            fig_val.add_trace(
                go.Scatter(
                    x=curves_df["K"],
                    y=curves_df["Hunter_Value_Share"] * 100,
                    name="Ghost SIM Hunter (% of Total Leakage Captured)",
                    line=dict(color="#f59e0b", width=3),
                )
            )
            fig_val.add_trace(
                go.Scatter(
                    x=curves_df["K"],
                    y=curves_df["Naive_Value_Share"] * 100,
                    name="Naive R0 Threshold Baseline",
                    line=dict(color="#94a3b8", width=3, dash="dash"),
                )
            )
            fig_val.update_layout(
                title="Cumulative Share of Total Leakage Value Captured (%)",
                xaxis_title="Queue Depth (Top K SIMs)",
                yaxis_title="% of Total True Leakage Value",
                height=360,
            )
            st.plotly_chart(fig_val, use_container_width=True)

        st.markdown("---")
        st.markdown("#### 💰 Transparent Financial Valuation (Addressing Judge Scrutiny on R150k / R1.5M Figures)")
        st.caption(
            "Usage valued at retail tariff is **not** identical to cash recoverable. Below we separate: "
            "**(1) Retail Tariff Equivalent**, **(2) Direct Marginal Network & Interconnect Cost**, and **(3) Realistic Recoverable / Avoidable Cash Range**."
        )

        fin_table = pd.DataFrame([
            {
                "Valuation Metric": "1. Unbilled Usage at Retail Tariff (R0.29/MB, R1.20/min)",
                f"Daily Top-{top_k} Queue (Monthly)": f"R {fin['top_k_retail_zar']:,.2f}",
                f"Daily Top-{top_k} Queue (Annualised)": f"R {fin['top_k_retail_zar']*12:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Monthly)": f"R {fin['all_retail_zar']:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Annualised)": f"R {fin['all_retail_zar']*12:,.2f}",
            },
            {
                "Valuation Metric": "2. Direct Marginal Network & Interconnect Cost (~18% + R0.13/min ICASA)",
                f"Daily Top-{top_k} Queue (Monthly)": f"R {fin['top_k_marginal_cost_zar']:,.2f}",
                f"Daily Top-{top_k} Queue (Annualised)": f"R {fin['top_k_marginal_cost_zar']*12:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Monthly)": f"R {fin['all_marginal_cost_zar']:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Annualised)": f"R {fin['all_marginal_cost_zar']*12:,.2f}",
            },
            {
                "Valuation Metric": "3a. Est. Recoverable Cash — Conservative (Low Bound)",
                f"Daily Top-{top_k} Queue (Monthly)": f"R {fin['top_k_rec_low_zar']:,.2f}",
                f"Daily Top-{top_k} Queue (Annualised)": f"R {fin['top_k_rec_low_zar']*12:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Monthly)": f"R {fin['all_rec_low_zar']:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Annualised)": f"R {fin['all_rec_low_zar']*12:,.2f}",
            },
            {
                "Valuation Metric": "3b. Est. Recoverable Cash — Base Case (Archetype Weighted)",
                f"Daily Top-{top_k} Queue (Monthly)": f"R {fin['top_k_rec_mid_zar']:,.2f}",
                f"Daily Top-{top_k} Queue (Annualised)": f"R {fin['top_k_rec_mid_zar']*12:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Monthly)": f"R {fin['all_rec_mid_zar']:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Annualised)": f"R {fin['all_rec_mid_zar']*12:,.2f}",
            },
            {
                "Valuation Metric": "3c. Est. Recoverable Cash — Optimistic (High Bound)",
                f"Daily Top-{top_k} Queue (Monthly)": f"R {fin['top_k_rec_high_zar']:,.2f}",
                f"Daily Top-{top_k} Queue (Annualised)": f"R {fin['top_k_rec_high_zar']*12:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Monthly)": f"R {fin['all_rec_high_zar']:,.2f}",
                f"All {fin['all_flagged_count']} Flagged Ghost SIMs (Annualised)": f"R {fin['all_rec_high_zar']*12:,.2f}",
            },
        ])
        st.table(fin_table)

        st.markdown("#### Ground-Truth Archetype Capture Breakdown")
        st.dataframe(
            bt["archetype_summary_df"].style.format({
                "total_retail_zar": "R {:,.2f}",
                "unbilled_retail_zar": "R {:,.2f}",
                "est_recoverable_mid_zar": "R {:,.2f}",
            }),
            use_container_width=True,
        )

    # =======================================================================
    # TAB 5: DATA ADAPTER & SCHEMA EXPORT
    # =======================================================================
    with tab_data:
        st.subheader("🔌 Real / Anonymised MTN Data Ingestion Adapter & POPIA Compliance")
        st.markdown(
            """
            Ghost SIM Hunter is architected so the exact same DuckDB SQL reconciliation and Isolation Forest pipeline runs on **anonymised MTN production extracts** (CSV or Parquet).
            - **POPIA Compliance:** All `IMSI`, `MSISDN`, and `Account_ID` identifiers can be salted-SHA256 pseudonymised prior to ingestion.
            - **Required 3-Table Contract:** `subscribers`, `usage_daily`, and `billing_events`.
            """
        )

        exp_col1, exp_col2, exp_col3 = st.columns(3)
        with exp_col1:
            st.download_button(
                "⬇️ Download Sample `subscribers.csv`",
                data=datasets["subscribers"].head(500).to_csv(index=False).encode("utf-8"),
                file_name="mtn_sample_subscribers.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with exp_col2:
            st.download_button(
                "⬇️ Download Sample `usage_daily.csv`",
                data=datasets["usage_daily"].head(1500).to_csv(index=False).encode("utf-8"),
                file_name="mtn_sample_usage_daily.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with exp_col3:
            st.download_button(
                "⬇️ Download Sample `billing_events.csv`",
                data=datasets["billing_events"].head(500).to_csv(index=False).encode("utf-8"),
                file_name="mtn_sample_billing_events.csv",
                mime="text/csv",
                use_container_width=True,
            )

        st.markdown("#### Upload Anonymised Extracts to Run Pipeline")
        u1, u2, u3 = st.columns(3)
        sub_file = u1.file_uploader("Upload `subscribers` (CSV/Parquet)", type=["csv", "parquet"])
        usg_file = u2.file_uploader("Upload `usage_daily` (CSV/Parquet)", type=["csv", "parquet"])
        bil_file = u3.file_uploader("Upload `billing_events` (CSV/Parquet)", type=["csv", "parquet"])

        if sub_file and usg_file and bil_file:
            sub_df = pd.read_parquet(sub_file) if sub_file.name.endswith(".parquet") else pd.read_csv(sub_file)
            usg_df = pd.read_parquet(usg_file) if usg_file.name.endswith(".parquet") else pd.read_csv(usg_file)
            bil_df = pd.read_parquet(bil_file) if bil_file.name.endswith(".parquet") else pd.read_csv(bil_file)

            ok, errs = validate_custom_files(sub_df, usg_df, bil_df)
            if not ok:
                for e in errs:
                    st.error(e)
            else:
                st.success("✅ Schema validated! Running 3-Layer Ghost SIM Hunter pipeline on uploaded dataset...")
                custom_con = load_into_duckdb({
                    "subscribers": sub_df,
                    "usage_daily": usg_df,
                    "billing_events": bil_df,
                    "cell_sites": datasets["cell_sites"],
                    "apn_catalog": datasets["apn_catalog"],
                })
                custom_res = run_full_pipeline(custom_con)
                st.dataframe(custom_res.head(50), use_container_width=True)


if __name__ == "__main__":
    main()
