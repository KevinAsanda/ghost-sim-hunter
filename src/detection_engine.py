"""
Three-Layer Detection Engine for Ghost SIM Hunter (Zero-Revenue Active SIMs):

Layer 1: Multi-System Reconciliation & Legitimate Entitlement Filtering (DuckDB SQL)
    - Aggregates 30-day network usage, rated value, retail tariff value, and captured revenue.
    - Explicitly checks and filters out benign "R0 direct revenue" look-alikes:
        1) Valid rollover / long-validity bundle balances (entitlement_bundle_mb)
        2) Enterprise child SIMs billed via a corporate master account (parent_account_allocated_zar)
        3) Whitelisted zero-rated public/educational APNs within normal consumer volume limits
    - Evaluates deterministic revenue assurance reconciliation rules.

Layer 2: Peer-Relative Anomaly Scoring & Value-at-Risk Ranking (Isolation Forest + Feature Attribution)
    - Engineers behavioral features (data/voice volume, night usage ratio, destination dispersion,
      IMEI rotation, Cell ID concentration, unbilled retail gap).
    - Fits scikit-learn IsolationForest and computes per-feature z-score / attribution vectors
      (model-exact tree path & standardized deviation drivers) so every score is explainable.
    - Combines Rule Confidence + ML Anomaly Probability into a Unified Confidence Score.
    - Ranks SIMs by Expected Value-at-Risk (Retail Tariff Value vs Marginal Cost vs Recoverable Cash).

Layer 3: Automated Root-Cause Investigation & Actionable Case Summary
    - Runs automated diagnostic checks across HLR/OCS status, APN eligibility, Tariff config,
      Device/IMEI behavior, and Cell ID clustering (detecting SIM Box / RAN anomalies).
    - Assigns a primary Root-Cause Hypothesis, Owner Team, Recommended Action, and Analyst Narrative.
"""

from typing import Dict, List, Tuple
import duckdb
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import RobustScaler

from src.schema import TARIFF_ASSUMPTIONS, LEAKAGE_ARCHETYPES, BENIGN_ARCHETYPES


FEATURE_COLS = [
    "unbilled_retail_zar",
    "total_data_mb",
    "total_voice_mins",
    "avg_night_ratio",
    "max_unique_destinations",
    "max_distinct_imeis",
    "cell_concentration_index",
    "cell_zero_rev_density",
]

FEATURE_LABELS = {
    "unbilled_retail_zar": "Uncovered Retail Usage Value (ZAR)",
    "total_data_mb": "30-Day Data Volume (MB)",
    "total_voice_mins": "30-Day Voice Volume (Mins)",
    "avg_night_ratio": "Off-Peak / Night Usage Ratio",
    "max_unique_destinations": "Unique Called/IP Destinations",
    "max_distinct_imeis": "Distinct IMEIs Rotated on SIM",
    "cell_concentration_index": "Static Cell Pinning Index",
    "cell_zero_rev_density": "Cell ID Zero-Revenue Cluster Density",
}


def run_layer1_reconciliation(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """
    Execute Layer 1 Reconciliation in DuckDB:
    Joins Subscriber Master, 30-day Daily Usage, Billing Events, APN Catalog, and Cell Site metadata.
    """
    query = """
    WITH usage_agg AS (
        SELECT
            sim_id,
            COUNT(DISTINCT usage_date) AS active_days,
            SUM(data_mb) AS total_data_mb,
            SUM(voice_mins) AS total_voice_mins,
            SUM(sms_count) AS total_sms_count,
            SUM(session_count) AS total_sessions,
            AVG(night_usage_ratio) AS avg_night_ratio,
            MAX(unique_destinations) AS max_unique_destinations,
            MAX(distinct_cells) AS max_distinct_cells,
            MODE(primary_cell_id) AS primary_cell_id,
            MAX(distinct_imeis) AS max_distinct_imeis,
            MODE(device_type) AS device_type,
            MODE(primary_apn) AS primary_apn,
            SUM(rated_amount_zar) AS total_rated_zar,
            SUM(retail_equivalent_zar) AS total_retail_equiv_zar
        FROM usage_daily
        GROUP BY sim_id
    ),
    billing_agg AS (
        SELECT
            sim_id,
            SUM(topup_zar) AS topup_zar,
            SUM(bundle_purchase_zar) AS bundle_purchase_zar,
            SUM(invoice_billed_zar) AS invoice_billed_zar,
            SUM(parent_account_allocated_zar) AS parent_account_allocated_zar,
            SUM(total_revenue_zar) AS total_revenue_zar,
            SUM(topup_zar + bundle_purchase_zar + invoice_billed_zar) AS direct_sim_revenue_zar
        FROM billing_events
        GROUP BY sim_id
    ),
    joined AS (
        SELECT
            s.sim_id,
            s.imsi,
            s.msisdn,
            s.account_id,
            s.parent_account_id,
            s.plan_type,
            s.hlr_status,
            s.billing_status,
            s.tariff_code,
            s.activation_date,
            s.channel,
            s.entitlement_bundle_mb,
            s.entitlement_voice_min,
            s.is_enterprise_child,
            COALESCE(s.ground_truth_label, 'UNKNOWN') AS ground_truth_label,
            COALESCE(s.is_true_leakage, FALSE) AS is_true_leakage,
            u.active_days,
            u.total_data_mb,
            u.total_voice_mins,
            u.total_sms_count,
            u.total_sessions,
            u.avg_night_ratio,
            u.max_unique_destinations,
            u.max_distinct_cells,
            u.primary_cell_id,
            u.max_distinct_imeis,
            u.device_type,
            u.primary_apn,
            u.total_rated_zar,
            u.total_retail_equiv_zar,
            b.topup_zar,
            b.bundle_purchase_zar,
            b.invoice_billed_zar,
            b.parent_account_allocated_zar,
            b.total_revenue_zar,
            b.direct_sim_revenue_zar,
            COALESCE(a.apn_type, 'UNKNOWN') AS apn_type,
            COALESCE(a.is_zero_rated_eligible, FALSE) AS is_zero_rated_eligible,
            COALESCE(c.province, 'Unknown') AS cell_province,
            COALESCE(c.area_type, 'Unknown') AS cell_area_type,
            c.lat AS cell_lat,
            c.lon AS cell_lon
        FROM subscribers s
        JOIN usage_agg u ON s.sim_id = u.sim_id
        JOIN billing_agg b ON s.sim_id = b.sim_id
        LEFT JOIN apn_catalog a ON u.primary_apn = a.apn
        LEFT JOIN cell_sites c ON u.primary_cell_id = c.cell_id
    ),
    cell_stats AS (
        SELECT
            primary_cell_id,
            COUNT(*) AS total_sims_in_cell,
            SUM(CASE WHEN direct_sim_revenue_zar = 0 AND total_retail_equiv_zar > 500 THEN 1 ELSE 0 END) AS zero_rev_high_usage_sims_in_cell
        FROM joined
        GROUP BY primary_cell_id
    )
    SELECT
        j.*,
        cs.total_sims_in_cell,
        cs.zero_rev_high_usage_sims_in_cell,
        CAST(cs.zero_rev_high_usage_sims_in_cell AS DOUBLE) / GREATEST(cs.total_sims_in_cell, 1) AS cell_zero_rev_density
    FROM joined j
    LEFT JOIN cell_stats cs ON j.primary_cell_id = cs.primary_cell_id
    """
    df = con.execute(query).df()

    # -----------------------------------------------------------------------
    # Explicit Entitlement & Legitimacy Filtering (False-Positive Prevention)
    # -----------------------------------------------------------------------
    retail_data_rate = TARIFF_ASSUMPTIONS["retail_data_per_mb_zar"]
    retail_voice_rate = TARIFF_ASSUMPTIONS["retail_voice_per_min_zar"]

    # 1. Subtract only covered Rollover / Long-validity Bundle volume (never exempt residual overage)
    covered_data_mb = np.minimum(df["total_data_mb"], df["entitlement_bundle_mb"])
    covered_voice_min = np.minimum(df["total_voice_mins"], df["entitlement_voice_min"])
    bundle_covered_value_zar = covered_data_mb * retail_data_rate + covered_voice_min * retail_voice_rate

    df["is_bundle_entitled"] = (
        (df["entitlement_bundle_mb"] >= df["total_data_mb"])
        & (df["entitlement_voice_min"] >= df["total_voice_mins"])
        & (df["entitlement_bundle_mb"] > 0)
    )

    # 2. Check full Enterprise Parent Pool Coverage (preserve residual if parent allocation is partial)
    df["is_enterprise_covered"] = (
        df["is_enterprise_child"]
        & (df["parent_account_id"] != "")
        & (df["parent_account_allocated_zar"] >= df["total_retail_equiv_zar"])
    )

    # 3. Check Legitimate Public Zero-Rated APN (strictly zero-rated APN, normal volume <= 8GB/mo, 0 voice/SMS)
    df["is_legit_zero_rated_apn"] = (
        df["is_zero_rated_eligible"]
        & (df["primary_apn"] == "mtn.zero.edu")
        & (df["total_data_mb"] <= 8000.0)
        & (df["total_voice_mins"] == 0.0)
        & (df["total_sms_count"] == 0)
    )

    zero_rated_value_zar = np.where(
        df["is_zero_rated_eligible"] & (df["primary_apn"] == "mtn.zero.edu"),
        np.minimum(df["total_data_mb"], 8000.0) * retail_data_rate,
        0.0,
    )

    # Compute Uncovered / Unbilled Retail Value (ZAR) after subtracting actual covered amounts
    raw_unbilled = (
        df["total_retail_equiv_zar"]
        - df["total_revenue_zar"]
        - bundle_covered_value_zar
        - zero_rated_value_zar
    )
    df["unbilled_retail_zar"] = np.maximum(0.0, raw_unbilled).round(2)
    df["estimated_opportunity_zar"] = df["unbilled_retail_zar"]
    df["is_benign_exempt"] = df["unbilled_retail_zar"] == 0.0

    # -----------------------------------------------------------------------
    # Deterministic Reconciliation Rules (Layer 1 Flags)
    # -----------------------------------------------------------------------
    df["rule_hlr_ocs_orphan"] = (df["hlr_status"] == "ACTIVE") & (df["billing_status"] != "ACTIVE")
    df["rule_unrated_tariff"] = (
        (df["total_rated_zar"] == 0)
        & (df["unbilled_retail_zar"] > 300)
        & (df["tariff_code"].str.contains("ZERO|UNRATED|BYPASS|LEGACY", regex=True))
    )
    df["rule_iot_apn_mismatch"] = (df["plan_type"] == "IOT_M2M") & (df["primary_apn"] != "mtn.iot.m2m")
    df["rule_internal_sim_abuse"] = (
        (df["plan_type"] == "INTERNAL_TEST")
        & ((df["cell_area_type"] == "Residential") | (df["avg_night_ratio"] > 0.45) | (df["total_data_mb"] > 5000))
    )
    df["rule_simbox_signature"] = (
        (df["total_voice_mins"] > 1200)
        & (df["max_distinct_imeis"] >= 4)
        & (df["max_unique_destinations"] >= 80)
        & (df["direct_sim_revenue_zar"] == 0)
    )

    rule_cols = [
        "rule_hlr_ocs_orphan",
        "rule_unrated_tariff",
        "rule_iot_apn_mismatch",
        "rule_internal_sim_abuse",
        "rule_simbox_signature",
    ]
    df["rules_triggered_count"] = df[rule_cols].sum(axis=1)

    # Layer 1 Rule Score (0.0 to 1.0)
    df["layer1_rule_score"] = np.clip(
        np.where(df["is_benign_exempt"], 0.0, df["rules_triggered_count"] * 0.45 + (df["unbilled_retail_zar"] > 500) * 0.35),
        0.0,
        1.0,
    ).round(4)

    return df


def run_layer2_anomaly_scoring(df: pd.DataFrame, random_state: int = 42) -> pd.DataFrame:
    """
    Execute Layer 2 Peer-Relative Anomaly Scoring (Isolation Forest + Feature Attribution)
    and compute the Naive Baseline vs Ghost SIM Hunter Hybrid Ranking.
    """
    df = df.copy()
    df["cell_concentration_index"] = (1.0 / np.maximum(1.0, df["max_distinct_cells"])).round(4)

    X = df[FEATURE_COLS].fillna(0.0).values
    scaler = RobustScaler()
    X_scaled = scaler.fit_transform(X)

    iso = IsolationForest(
        n_estimators=150,
        contamination=0.065,
        random_state=random_state,
    )
    iso.fit(X_scaled)

    # decision_function returns negative for outliers; invert and normalize to [0, 1]
    raw_scores = -iso.decision_function(X_scaled)
    min_s, max_s = raw_scores.min(), raw_scores.max()
    ml_anomaly_score = (raw_scores - min_s) / (max_s - min_s + 1e-9)
    df["layer2_ml_score"] = np.round(ml_anomaly_score, 4)

    # Compute per-SIM feature attribution (standardized deviation weighted by anomaly direction)
    # so analysts can see the top 3 mathematical drivers for each SIM's anomaly score
    pos_deviation = np.maximum(0.0, X_scaled)
    row_sums = pos_deviation.sum(axis=1, keepdims=True) + 1e-9
    normalized_contrib = pos_deviation / row_sums

    top_driver_1, top_driver_2, top_driver_3 = [], [], []
    for i in range(len(df)):
        order = np.argsort(normalized_contrib[i])[::-1]
        f1, f2, f3 = FEATURE_COLS[order[0]], FEATURE_COLS[order[1]], FEATURE_COLS[order[2]]
        top_driver_1.append(f"{FEATURE_LABELS[f1]} ({normalized_contrib[i, order[0]]*100:.0f}%)")
        top_driver_2.append(f"{FEATURE_LABELS[f2]} ({normalized_contrib[i, order[1]]*100:.0f}%)")
        top_driver_3.append(f"{FEATURE_LABELS[f3]} ({normalized_contrib[i, order[2]]*100:.0f}%)")

    df["deviation_indicator_1"] = top_driver_1
    df["deviation_indicator_2"] = top_driver_2
    df["deviation_indicator_3"] = top_driver_3

    for idx_col, col_name in enumerate(FEATURE_COLS):
        df[f"contrib_{col_name}"] = np.round(normalized_contrib[:, idx_col], 4)

    # Combined Risk Score (Rules + ML prioritization score, not calibrated probability)
    combined_risk = np.where(
        df["unbilled_retail_zar"] <= 0,
        0.0,
        0.60 * df["layer1_rule_score"] + 0.40 * df["layer2_ml_score"],
    )
    df["risk_score"] = np.clip(combined_risk, 0.0, 0.99).round(4)

    # Priority Score = Unbilled Retail Value (ZAR) * Risk Score
    df["priority_score"] = (df["unbilled_retail_zar"] * df["risk_score"]).round(2)

    # -----------------------------------------------------------------------
    # Baseline Comparison: Naive Threshold Report ("R0 direct SIM revenue, sort by retail usage")
    # The naive report does NOT reconcile parent enterprise accounts or rollover bundle balances!
    # -----------------------------------------------------------------------
    df["naive_zero_rev_flag"] = df["direct_sim_revenue_zar"] == 0.0
    df["naive_score"] = np.where(df["naive_zero_rev_flag"], df["total_retail_equiv_zar"], 0.0)

    df = df.sort_values("priority_score", ascending=False).reset_index(drop=True)
    df["hunter_rank"] = np.arange(1, len(df) + 1)

    naive_order = df.sort_values("naive_score", ascending=False).index
    naive_ranks = pd.Series(np.arange(1, len(df) + 1), index=naive_order)
    df["naive_rank"] = df.index.map(naive_ranks)

    return df


def run_layer3_investigation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Execute Layer 3 Automated Investigation:
    Assigns Root-Cause Hypothesis, Recommended Action, Owner Team, Financial Valuations
    (Retail Tariff vs Marginal Network Cost vs Recoverable Cash), and Case Narrative.
    """
    df = df.copy()

    hypotheses = []
    actions = []
    owners = []
    evidence_summaries = []
    recoverable_ratios = []

    rec_map = TARIFF_ASSUMPTIONS["recoverable_rate_by_archetype"]
    marginal_ratio = TARIFF_ASSUMPTIONS["marginal_network_cost_ratio"]
    interconnect_rate = TARIFF_ASSUMPTIONS["interconnect_termination_per_min_zar"]

    for _, row in df.iterrows():
        if row["is_benign_exempt"]:
            if row["is_bundle_entitled"]:
                hyp = "BENIGN_BUNDLE_COVERED"
                name = BENIGN_ARCHETYPES[hyp]["name"]
                act = "No action required (Suppress alert — covered by valid rollover bundle)."
                own = "Automated Entitlement Filter"
                ev = (
                    f"Direct revenue is R0, but SIM holds {row['entitlement_bundle_mb']:,.0f} MB valid rollover bundle "
                    f"covering {row['total_data_mb']:,.0f} MB usage."
                )
            elif row["is_enterprise_covered"]:
                hyp = "BENIGN_ENTERPRISE_POOLED"
                name = BENIGN_ARCHETYPES[hyp]["name"]
                act = "No action required (Suppress alert — billed via Parent Corporate Account)."
                own = "Automated Entitlement Filter"
                ev = (
                    f"Child SIM has R0 direct billing, but Parent Account {row['parent_account_id']} "
                    f"was billed R{row['parent_account_allocated_zar']:,.2f}."
                )
            else:
                hyp = "BENIGN_ZERO_RATED_APN"
                name = BENIGN_ARCHETYPES[hyp]["name"]
                act = "No action required (Suppress alert — whitelisted educational APN within cap)."
                own = "Automated Entitlement Filter"
                ev = f"Traffic strictly on zero-rated APN '{row['primary_apn']}' ({row['total_data_mb']:,.0f} MB)."
            rec_rate = 0.0

        elif row["rule_simbox_signature"]:
            hyp = "SIM_BOX_FRAUD"
            info = LEAKAGE_ARCHETYPES[hyp]
            name = info["name"]
            act = info["recommended_action"]
            own = info["owner_team"]
            ev = (
                f"Cell '{row['primary_cell_id']}' anomaly: {row['total_voice_mins']:,.0f} voice mins across "
                f"{int(row['max_unique_destinations'])} unique destinations, rotating {int(row['max_distinct_imeis'])} IMEIs "
                f"on '{row['device_type']}' with R0 billed revenue."
            )
            rec_rate = rec_map[hyp]

        elif row["rule_hlr_ocs_orphan"]:
            hyp = "PROVISIONING_ORPHAN"
            info = LEAKAGE_ARCHETYPES[hyp]
            name = info["name"]
            act = info["recommended_action"]
            own = info["owner_team"]
            ev = (
                f"HLR Status is '{row['hlr_status']}' while Billing/OCS Status is '{row['billing_status']}' "
                f"(Account: {row['account_id']}). Consumed {row['total_data_mb']/1024:.2f} GB & "
                f"{row['total_voice_mins']:,.0f} mins (R{row['unbilled_retail_zar']:,.2f} retail value) with R0 billed."
            )
            rec_rate = rec_map[hyp]

        elif row["rule_iot_apn_mismatch"]:
            hyp = "IOT_WRONG_APN"
            info = LEAKAGE_ARCHETYPES[hyp]
            name = info["name"]
            act = info["recommended_action"]
            own = info["owner_team"]
            ev = (
                f"Plan is 'IOT_M2M' ({row['tariff_code']}) but SIM is transmitting on consumer APN '{row['primary_apn']}' "
                f"using device '{row['device_type']}' ({row['total_data_mb']/1024:.2f} GB unbilled, "
                f"{row['avg_night_ratio']*100:.0f}% night traffic)."
            )
            rec_rate = rec_map[hyp]

        elif row["rule_internal_sim_abuse"]:
            hyp = "FREE_INTERNAL_ABUSE"
            info = LEAKAGE_ARCHETYPES[hyp]
            name = info["name"]
            act = info["recommended_action"]
            own = info["owner_team"]
            ev = (
                f"Internal Test SIM ('{row['tariff_code']}') pinned to {row['cell_area_type']} cell '{row['primary_cell_id']}' "
                f"on '{row['device_type']}' with {row['total_data_mb']/1024:.2f} GB usage "
                f"({row['avg_night_ratio']*100:.0f}% night/off-peak ratio)."
            )
            rec_rate = rec_map[hyp]

        elif row["rule_unrated_tariff"] or row["unbilled_retail_zar"] > 400:
            hyp = "RATING_MISCONFIG"
            info = LEAKAGE_ARCHETYPES[hyp]
            name = info["name"]
            act = info["recommended_action"]
            own = info["owner_team"]
            ev = (
                f"Active SIM on tariff '{row['tariff_code']}' and APN '{row['primary_apn']}' generated "
                f"{row['total_sessions']} sessions ({row['total_data_mb']/1024:.2f} GB) rated at R0.00 in OCS "
                f"(Retail equivalent: R{row['unbilled_retail_zar']:,.2f})."
            )
            rec_rate = rec_map[hyp]

        else:
            hyp = "NORMAL_SUBSCRIBER"
            name = BENIGN_ARCHETYPES["NORMAL_SUBSCRIBER"]["name"]
            act = "No action required (Reconciled within normal tolerance)."
            own = "None"
            ev = f"Billed R{row['total_revenue_zar']:,.2f} against R{row['total_retail_equiv_zar']:,.2f} retail usage."
            rec_rate = 0.0

        hypotheses.append(name)
        actions.append(act)
        owners.append(own)
        evidence_summaries.append(ev)
        recoverable_ratios.append(rec_rate)

    df["predicted_archetype_name"] = hypotheses
    df["recommended_action"] = actions
    df["owner_team"] = owners
    df["investigation_summary"] = evidence_summaries
    df["recoverable_ratio"] = recoverable_ratios

    # -----------------------------------------------------------------------
    # Financial Transparency: Retail Value vs Marginal Cost vs Recoverable Cash
    # -----------------------------------------------------------------------
    # 1. Retail Tariff Equivalent Value (unbilled_retail_zar)
    # 2. Marginal Network Cost to MTN (radio/backhaul + interconnect termination cash cost)
    df["marginal_network_cost_zar"] = np.where(
        df["unbilled_retail_zar"] > 0,
        df["unbilled_retail_zar"] * marginal_ratio + df["total_voice_mins"] * interconnect_rate,
        0.0,
    ).round(2)

    # 3. Realistic Monthly Recoverable / Avoidable Cash (Conservative vs Optimistic range)
    df["est_recoverable_mid_zar"] = (df["unbilled_retail_zar"] * df["recoverable_ratio"]).round(2)
    df["est_recoverable_low_zar"] = (df["est_recoverable_mid_zar"] * 0.75).round(2)
    df["est_recoverable_high_zar"] = np.minimum(
        df["unbilled_retail_zar"], df["est_recoverable_mid_zar"] * 1.20
    ).round(2)

    return df


def run_full_pipeline(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Run all three detection layers end-to-end and return the enriched SIM DataFrame."""
    df_l1 = run_layer1_reconciliation(con)
    df_l2 = run_layer2_anomaly_scoring(df_l1)
    df_l3 = run_layer3_investigation(df_l2)
    return df_l3
