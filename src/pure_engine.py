"""
Zero-Dependency Pure-Python + SQLite3 Engine & Isolation Forest Implementation
for Ghost SIM Hunter (Zero-Revenue Active SIMs).

Why this module exists:
- Runs 100% out-of-the-box on standard Python (using built-in `sqlite3`, `math`, `random`, `csv`, `json`)
  even when offline or without external C-extensions, while also exporting data compatible with
  the DuckDB/scikit-learn/Streamlit stack.
- Implements:
  1. Synthetic Telecom Generator (3,000 SIMs x 30 days = 90,000 CDR daily rows across 5 injected
     revenue leakage archetypes + 3 benign zero-revenue look-alike negative controls).
  2. Layer 1 Multi-System SQL Reconciliation (SQLite3 in-memory / file engine) with explicit
     entitlement filtering (Rollover Bundles, Enterprise Master Accounts, Whitelisted Zero-Rated APNs).
  3. Layer 2 Pure-Python Isolation Forest (100 randomized isolation trees with path-length scoring
     + standardized per-feature attribution drivers).
  4. Layer 3 Automated 5-Point Forensic Investigation & Recommended Action generator.
  5. Full Backtest Evaluation vs. Ground Truth & Naive R0 Threshold Baseline.
"""

import csv
import json
import math
import os
import random
import sqlite3
from datetime import date, timedelta
from typing import Dict, List, Tuple, Any

from src.schema import (
    TARIFF_ASSUMPTIONS,
    LEAKAGE_ARCHETYPES,
    BENIGN_ARCHETYPES,
    CELL_SITES,
    APN_CATALOG,
)

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


# ---------------------------------------------------------------------------
# Pure-Python Isolation Forest + Feature Attribution
# ---------------------------------------------------------------------------
class PureIsolationTree:
    def __init__(self, height_limit: int, rng: random.Random):
        self.height_limit = height_limit
        self.rng = rng
        self.root = None

    def fit(self, X: List[List[float]], current_height: int = 0) -> dict:
        n_samples = len(X)
        if current_height >= self.height_limit or n_samples <= 1:
            return {"type": "leaf", "size": n_samples}

        n_features = len(X[0])
        feat_idx = self.rng.randrange(n_features)
        vals = [row[feat_idx] for row in X]
        min_v, max_v = min(vals), max(vals)
        if max_v - min_v < 1e-9:
            return {"type": "leaf", "size": n_samples}

        split_val = self.rng.uniform(min_v, max_v)
        left_X = [row for row in X if row[feat_idx] < split_val]
        right_X = [row for row in X if row[feat_idx] >= split_val]

        if not left_X or not right_X:
            return {"type": "leaf", "size": n_samples}

        return {
            "type": "node",
            "feat": feat_idx,
            "split": split_val,
            "left": self.fit(left_X, current_height + 1),
            "right": self.fit(right_X, current_height + 1),
        }

    def path_length(self, x: List[float], node: dict, current_height: int = 0) -> float:
        if node["type"] == "leaf":
            size = node["size"]
            if size <= 1:
                return float(current_height)
            return float(current_height) + _c_factor(size)
        if x[node["feat"]] < node["split"]:
            return self.path_length(x, node["left"], current_height + 1)
        return self.path_length(x, node["right"], current_height + 1)


def _c_factor(n: int) -> float:
    if n <= 1:
        return 0.0
    if n == 2:
        return 1.0
    return 2.0 * (math.log(n - 1.0) + 0.5772156649) - (2.0 * (n - 1.0) / n)


def run_pure_isolation_forest(
    rows: List[Dict[str, Any]],
    n_trees: int = 100,
    sample_size: int = 256,
    seed: int = 42,
) -> None:
    """
    Fit a 100-tree Isolation Forest on FEATURE_COLS and compute both:
    - layer2_ml_score in [0, 1]
    - Top 3 SHAP-style standardized deviation feature attributions per SIM
    """
    rng = random.Random(seed)
    n = len(rows)
    n_feat = len(FEATURE_COLS)

    # Compute mean and standard deviation (with minimum floor) per feature
    means = []
    stds = []
    for f in FEATURE_COLS:
        col_vals = [float(r[f]) for r in rows]
        mean_v = sum(col_vals) / float(n)
        var_v = sum((v - mean_v) ** 2 for v in col_vals) / float(n)
        std_v = max(1e-4, math.sqrt(var_v))
        means.append(mean_v)
        stds.append(std_v)

    X_scaled = []
    for r in rows:
        vec = [min(8.0, max(-4.0, (float(r[FEATURE_COLS[j]]) - means[j]) / stds[j])) for j in range(n_feat)]
        X_scaled.append(vec)

    height_limit = int(math.ceil(math.log2(max(2, sample_size))))
    trees = []
    for _ in range(n_trees):
        sub_X = rng.sample(X_scaled, min(sample_size, n))
        t = PureIsolationTree(height_limit=height_limit, rng=rng)
        t.root = t.fit(sub_X)
        trees.append(t)

    c_norm = max(1e-6, _c_factor(min(sample_size, n)))
    raw_scores = []
    for vec in X_scaled:
        avg_path = sum(t.path_length(vec, t.root) for t in trees) / float(n_trees)
        # Standard Isolation Forest anomaly score: 2^(-E(h(x)) / c(psi))
        score = math.pow(2.0, -avg_path / c_norm)
        raw_scores.append(score)

    min_s, max_s = min(raw_scores), max(raw_scores)
    for i, r in enumerate(rows):
        ml_score = (raw_scores[i] - min_s) / max(1e-9, max_s - min_s)
        r["layer2_ml_score"] = round(ml_score, 4)

        # Positive standardized anomaly attributions
        vec = X_scaled[i]
        pos = [max(0.0, v) for v in vec]
        total_pos = sum(pos) + 1e-9
        shares = [p / total_pos for p in pos]
        for j, fcol in enumerate(FEATURE_COLS):
            r[f"contrib_{fcol}"] = round(shares[j], 4)

        ordered_idx = sorted(range(n_feat), key=lambda k: shares[k], reverse=True)
        r["shap_driver_1"] = f"{FEATURE_LABELS[FEATURE_COLS[ordered_idx[0]]]} ({shares[ordered_idx[0]]*100:.0f}%)"
        r["shap_driver_2"] = f"{FEATURE_LABELS[FEATURE_COLS[ordered_idx[1]]]} ({shares[ordered_idx[1]]*100:.0f}%)"
        r["shap_driver_3"] = f"{FEATURE_LABELS[FEATURE_COLS[ordered_idx[2]]]} ({shares[ordered_idx[2]]*100:.0f}%)"


# ---------------------------------------------------------------------------
# Synthetic Data Generation + SQLite3 Multi-System Reconciliation
# ---------------------------------------------------------------------------
def generate_and_run_pipeline(
    n_sims: int = 3000,
    n_days: int = 30,
    seed: int = 42,
    top_k: int = 100,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, List[Dict[str, Any]]]]:
    rng = random.Random(seed)
    end_date = date(2026, 10, 26)
    start_date = end_date - timedelta(days=n_days - 1)
    dates = [(start_date + timedelta(days=d)).isoformat() for d in range(n_days)]

    scale = n_sims / 3000.0
    counts = {
        "PROVISIONING_ORPHAN": max(5, int(round(45 * scale))),
        "RATING_MISCONFIG": max(5, int(round(40 * scale))),
        "FREE_INTERNAL_ABUSE": max(5, int(round(30 * scale))),
        "IOT_WRONG_APN": max(5, int(round(35 * scale))),
        "SIM_BOX_FRAUD": max(5, int(round(30 * scale))),
        "BENIGN_BUNDLE_COVERED": max(10, int(round(120 * scale))),
        "BENIGN_ENTERPRISE_POOLED": max(10, int(round(100 * scale))),
        "BENIGN_ZERO_RATED_APN": max(10, int(round(80 * scale))),
    }
    counts["NORMAL_SUBSCRIBER"] = max(50, n_sims - sum(counts.values()))

    labels = []
    for k, v in counts.items():
        labels.extend([k] * v)
    rng.shuffle(labels)

    cell_ids = [c["cell_id"] for c in CELL_SITES]
    sim_box_cells = ["JHB-HILLBROW-004", "JHB-SOWETO-003"]
    residential_cells = ["JHB-SOWETO-003", "DBN-PHOENIX-009", "CPT-BELLVILLE-007"]

    retail_data_rate = TARIFF_ASSUMPTIONS["retail_data_per_mb_zar"]
    retail_voice_rate = TARIFF_ASSUMPTIONS["retail_voice_per_min_zar"]
    retail_sms_rate = TARIFF_ASSUMPTIONS["retail_sms_per_msg_zar"]

    subscribers = []
    usage_daily = []
    billing_events = []
    sample_daily_by_sim: Dict[str, List[Dict[str, Any]]] = {}

    for idx, label in enumerate(labels):
        sim_id = f"SIM-{idx + 100001}"
        imsi = f"65510{rng.randint(1000000000, 9999999999)}"
        msisdn = f"+2783{rng.randint(1000000, 9999999)}"
        activation_date = (end_date - timedelta(days=rng.randint(35, 720))).isoformat()

        account_id = f"ACC-{idx + 500001}"
        parent_account_id = ""
        is_enterprise_child = 0
        hlr_status = "ACTIVE"
        billing_status = "ACTIVE"
        channel = rng.choices(["MTN_STORE", "RETAIL_PARTNER", "ONLINE", "ENTERPRISE_DESK"], weights=[40, 35, 15, 10])[0]
        entitlement_bundle_mb = 0.0
        entitlement_voice_min = 0.0
        primary_cell = rng.choice(cell_ids)

        if label == "NORMAL_SUBSCRIBER":
            plan_type = rng.choices(["PREPAID", "POSTPAID", "ENTERPRISE", "IOT_M2M"], weights=[65, 22, 8, 5])[0]
            tariff_code = f"STD_{plan_type}_01"
            primary_apn = "mtn.iot.m2m" if plan_type == "IOT_M2M" else ("mtn.corp.vpn" if plan_type == "ENTERPRISE" else "myMTN")
            device_type = "IOT_MODEM" if plan_type == "IOT_M2M" else "SMARTPHONE"
            daily_data_mean = 18.0 if plan_type == "IOT_M2M" else rng.uniform(40.0, 380.0)
            daily_voice_mean = 0.5 if plan_type == "IOT_M2M" else rng.uniform(3.0, 28.0)
            night_ratio_mean = 0.18
            distinct_imeis = 1
            distinct_cells = rng.randint(2, 6)
            unique_dest = rng.randint(5, 30)

        elif label == "BENIGN_BUNDLE_COVERED":
            plan_type = "PREPAID"
            tariff_code = "PREPAID_LONG_BUNDLE_90D"
            primary_apn = "myMTN"
            device_type = rng.choices(["SMARTPHONE", "LTE_ROUTER"], weights=[70, 30])[0]
            daily_data_mean = rng.uniform(180.0, 650.0)
            daily_voice_mean = rng.uniform(5.0, 25.0)
            entitlement_bundle_mb = daily_data_mean * n_days * rng.uniform(1.15, 1.50)
            entitlement_voice_min = daily_voice_mean * n_days * rng.uniform(1.15, 1.40)
            night_ratio_mean = 0.22
            distinct_imeis = 1
            distinct_cells = rng.randint(2, 5)
            unique_dest = rng.randint(8, 25)

        elif label == "BENIGN_ENTERPRISE_POOLED":
            plan_type = "ENTERPRISE"
            tariff_code = "ENT_SHARED_POOL_100"
            parent_account_id = f"CORP-MASTER-{rng.randint(100, 135)}"
            is_enterprise_child = 1
            channel = "ENTERPRISE_DESK"
            primary_apn = "mtn.corp.vpn"
            device_type = rng.choices(["SMARTPHONE", "LTE_ROUTER"], weights=[60, 40])[0]
            daily_data_mean = rng.uniform(220.0, 800.0)
            daily_voice_mean = rng.uniform(15.0, 55.0)
            night_ratio_mean = 0.12
            distinct_imeis = 1
            distinct_cells = rng.randint(3, 8)
            unique_dest = rng.randint(15, 45)

        elif label == "BENIGN_ZERO_RATED_APN":
            plan_type = "PREPAID"
            tariff_code = "STD_PREPAID_01"
            primary_apn = "mtn.zero.edu"
            device_type = "SMARTPHONE"
            daily_data_mean = rng.uniform(60.0, 210.0)
            daily_voice_mean = 0.0
            night_ratio_mean = 0.15
            distinct_imeis = 1
            distinct_cells = rng.randint(1, 4)
            unique_dest = rng.randint(2, 5)

        elif label == "PROVISIONING_ORPHAN":
            plan_type = rng.choices(["POSTPAID", "ENTERPRISE", "PREPAID"], weights=[55, 25, 20])[0]
            tariff_code = f"STD_{plan_type}_01"
            hlr_status = "ACTIVE"
            billing_status = rng.choice(["MISSING_IN_OCS", "PENDING_ACTIVATION", "SUSPENDED_BILLING_ONLY"])
            account_id = "UNLINKED_ORPHAN" if billing_status == "MISSING_IN_OCS" else account_id
            primary_apn = rng.choice(["internet", "myMTN"])
            device_type = rng.choices(["LTE_ROUTER", "SMARTPHONE"], weights=[60, 40])[0]
            daily_data_mean = rng.uniform(155.0, 340.0)
            daily_voice_mean = rng.uniform(8.0, 25.0)
            night_ratio_mean = 0.28
            distinct_imeis = rng.choices([1, 2], weights=[80, 20])[0]
            distinct_cells = rng.randint(2, 6)
            unique_dest = rng.randint(12, 40)

        elif label == "RATING_MISCONFIG":
            plan_type = rng.choices(["POSTPAID", "PREPAID"], weights=[60, 40])[0]
            tariff_code = "LEGACY_PROMO_ZERO_BUG_99"
            primary_apn = rng.choice(["legacy.promo.unrated", "internet"])
            device_type = rng.choice(["LTE_ROUTER", "SMARTPHONE"])
            daily_data_mean = rng.uniform(170.0, 380.0)
            daily_voice_mean = rng.uniform(5.0, 20.0)
            night_ratio_mean = 0.30
            distinct_imeis = 1
            distinct_cells = rng.randint(2, 6)
            unique_dest = rng.randint(10, 35)

        elif label == "FREE_INTERNAL_ABUSE":
            plan_type = "INTERNAL_TEST"
            tariff_code = "ENG_TEST_UNLIMITED"
            channel = "INTERNAL_IT"
            primary_apn = rng.choice(["internet", "mtn.eng.test"])
            device_type = "5G_CPE_ROUTER"
            primary_cell = rng.choice(residential_cells)
            daily_data_mean = rng.uniform(280.0, 620.0)
            daily_voice_mean = rng.uniform(10.0, 35.0)
            night_ratio_mean = rng.uniform(0.52, 0.78)
            distinct_imeis = rng.randint(2, 4)
            distinct_cells = 1
            unique_dest = rng.randint(45, 110)

        elif label == "IOT_WRONG_APN":
            plan_type = "IOT_M2M"
            tariff_code = "IOT_TELEMETRY_ONLY"
            channel = "ENTERPRISE_DESK"
            primary_apn = "internet"
            device_type = rng.choice(["LTE_ROUTER", "SMARTPHONE", "MODIFIED_IOT_GATEWAY"])
            daily_data_mean = rng.uniform(180.0, 420.0)
            daily_voice_mean = rng.uniform(2.0, 18.0)
            night_ratio_mean = rng.uniform(0.40, 0.68)
            distinct_imeis = rng.randint(2, 4)
            distinct_cells = rng.randint(1, 3)
            unique_dest = rng.randint(30, 85)

        elif label == "SIM_BOX_FRAUD":
            plan_type = "PREPAID"
            tariff_code = "PROMO_ONNET_UNLIMITED_BYPASS"
            channel = "STREET_AGENT_BATCH"
            primary_apn = "myMTN"
            device_type = "SIMBOX_MODEM_BANK"
            primary_cell = rng.choice(sim_box_cells)
            daily_data_mean = rng.uniform(15.0, 65.0)
            daily_voice_mean = rng.uniform(75.0, 165.0)
            night_ratio_mean = rng.uniform(0.35, 0.60)
            distinct_imeis = rng.randint(5, 14)
            distinct_cells = 1
            unique_dest = rng.randint(95, 260)

        is_leakage = 1 if label in LEAKAGE_ARCHETYPES else 0

        subscribers.append({
            "sim_id": sim_id,
            "imsi": imsi,
            "msisdn": msisdn,
            "account_id": account_id,
            "parent_account_id": parent_account_id,
            "plan_type": plan_type,
            "hlr_status": hlr_status,
            "billing_status": billing_status,
            "tariff_code": tariff_code,
            "activation_date": activation_date,
            "channel": channel,
            "entitlement_bundle_mb": round(entitlement_bundle_mb, 2),
            "entitlement_voice_min": round(entitlement_voice_min, 2),
            "is_enterprise_child": is_enterprise_child,
            "ground_truth_label": label,
            "is_true_leakage": is_leakage,
        })

        total_sim_retail_zar = 0.0
        sim_days_list = []
        for d_str in dates:
            data_mb = max(0.0, rng.gauss(daily_data_mean, daily_data_mean * 0.22))
            voice_mins = max(0.0, rng.gauss(daily_voice_mean, max(0.5, daily_voice_mean * 0.25)))
            sms_count = max(0, int(rng.gauss(2 if label != "SIM_BOX_FRAUD" else 18, 2)))
            session_count = max(1, int(rng.gauss(12 if data_mb > 50 else 4, 2)))
            night_ratio = min(0.98, max(0.01, rng.gauss(night_ratio_mean, 0.06)))

            retail_equiv_zar = (
                data_mb * retail_data_rate
                + voice_mins * retail_voice_rate
                + sms_count * retail_sms_rate
            )

            if is_leakage or label in ("BENIGN_ZERO_RATED_APN", "BENIGN_BUNDLE_COVERED"):
                rated_amount_zar = 0.0
            elif label == "BENIGN_ENTERPRISE_POOLED":
                rated_amount_zar = round(retail_equiv_zar * 0.75, 2)
            else:
                rated_amount_zar = round(retail_equiv_zar * rng.uniform(0.65, 1.0), 2)

            total_sim_retail_zar += retail_equiv_zar

            row_u = {
                "sim_id": sim_id,
                "usage_date": d_str,
                "data_mb": round(data_mb, 2),
                "voice_mins": round(voice_mins, 2),
                "sms_count": sms_count,
                "session_count": session_count,
                "night_usage_ratio": round(night_ratio, 3),
                "unique_destinations": unique_dest,
                "distinct_cells": distinct_cells,
                "primary_cell_id": primary_cell,
                "distinct_imeis": distinct_imeis,
                "device_type": device_type,
                "primary_apn": primary_apn,
                "rated_amount_zar": round(rated_amount_zar, 2),
                "retail_equivalent_zar": round(retail_equiv_zar, 2),
            }
            usage_daily.append(row_u)
            if is_leakage or label != "NORMAL_SUBSCRIBER":
                sim_days_list.append(row_u)

        if sim_days_list:
            sample_daily_by_sim[sim_id] = sim_days_list

        if is_leakage or label in ("BENIGN_BUNDLE_COVERED", "BENIGN_ZERO_RATED_APN"):
            topup_zar = bundle_zar = invoice_zar = parent_alloc_zar = 0.0
        elif label == "BENIGN_ENTERPRISE_POOLED":
            topup_zar = bundle_zar = invoice_zar = 0.0
            parent_alloc_zar = round(total_sim_retail_zar * 0.78, 2)
        else:
            effective_rev = round(total_sim_retail_zar * rng.uniform(0.70, 1.05), 2)
            if plan_type == "PREPAID":
                topup_zar = round(effective_rev * 0.4, 2)
                bundle_zar = round(effective_rev * 0.6, 2)
                invoice_zar = parent_alloc_zar = 0.0
            else:
                topup_zar = bundle_zar = parent_alloc_zar = 0.0
                invoice_zar = effective_rev

        total_rev = round(topup_zar + bundle_zar + invoice_zar + parent_alloc_zar, 2)
        billing_events.append({
            "sim_id": sim_id,
            "billing_date": end_date.isoformat(),
            "topup_zar": topup_zar,
            "bundle_purchase_zar": bundle_zar,
            "invoice_billed_zar": invoice_zar,
            "parent_account_allocated_zar": parent_alloc_zar,
            "total_revenue_zar": total_rev,
        })

    # -----------------------------------------------------------------------
    # Layer 1: SQLite3 Multi-System SQL Join & Reconciliation
    # -----------------------------------------------------------------------
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE subscribers (
            sim_id TEXT, imsi TEXT, msisdn TEXT, account_id TEXT, parent_account_id TEXT,
            plan_type TEXT, hlr_status TEXT, billing_status TEXT, tariff_code TEXT,
            activation_date TEXT, channel TEXT, entitlement_bundle_mb REAL,
            entitlement_voice_min REAL, is_enterprise_child INTEGER,
            ground_truth_label TEXT, is_true_leakage INTEGER
        )
    """)
    cur.executemany(
        "INSERT INTO subscribers VALUES (:sim_id,:imsi,:msisdn,:account_id,:parent_account_id,:plan_type,:hlr_status,:billing_status,:tariff_code,:activation_date,:channel,:entitlement_bundle_mb,:entitlement_voice_min,:is_enterprise_child,:ground_truth_label,:is_true_leakage)",
        subscribers,
    )

    cur.execute("""
        CREATE TABLE usage_daily (
            sim_id TEXT, usage_date TEXT, data_mb REAL, voice_mins REAL, sms_count INTEGER,
            session_count INTEGER, night_usage_ratio REAL, unique_destinations INTEGER,
            distinct_cells INTEGER, primary_cell_id TEXT, distinct_imeis INTEGER,
            device_type TEXT, primary_apn TEXT, rated_amount_zar REAL, retail_equivalent_zar REAL
        )
    """)
    cur.executemany(
        "INSERT INTO usage_daily VALUES (:sim_id,:usage_date,:data_mb,:voice_mins,:sms_count,:session_count,:night_usage_ratio,:unique_destinations,:distinct_cells,:primary_cell_id,:distinct_imeis,:device_type,:primary_apn,:rated_amount_zar,:retail_equivalent_zar)",
        usage_daily,
    )

    cur.execute("""
        CREATE TABLE billing_events (
            sim_id TEXT, billing_date TEXT, topup_zar REAL, bundle_purchase_zar REAL,
            invoice_billed_zar REAL, parent_account_allocated_zar REAL, total_revenue_zar REAL
        )
    """)
    cur.executemany(
        "INSERT INTO billing_events VALUES (:sim_id,:billing_date,:topup_zar,:bundle_purchase_zar,:invoice_billed_zar,:parent_account_allocated_zar,:total_revenue_zar)",
        billing_events,
    )

    cur.execute("CREATE TABLE cell_sites (cell_id TEXT, province TEXT, area_type TEXT, lat REAL, lon REAL)")
    cur.executemany("INSERT INTO cell_sites VALUES (:cell_id,:province,:area_type,:lat,:lon)", CELL_SITES)

    cur.execute("CREATE TABLE apn_catalog (apn TEXT, apn_type TEXT, is_zero_rated_eligible INTEGER)")
    cur.executemany(
        "INSERT INTO apn_catalog VALUES (:apn,:apn_type,:is_zero_rated_eligible)",
        [{"apn": a["apn"], "apn_type": a["apn_type"], "is_zero_rated_eligible": int(a["is_zero_rated_eligible"])} for a in APN_CATALOG],
    )

    reconcile_sql = """
    WITH usage_agg AS (
        SELECT
            sim_id,
            COUNT(DISTINCT usage_date) AS active_days,
            ROUND(SUM(data_mb), 2) AS total_data_mb,
            ROUND(SUM(voice_mins), 2) AS total_voice_mins,
            SUM(sms_count) AS total_sms_count,
            SUM(session_count) AS total_sessions,
            ROUND(AVG(night_usage_ratio), 4) AS avg_night_ratio,
            MAX(unique_destinations) AS max_unique_destinations,
            MAX(distinct_cells) AS max_distinct_cells,
            MAX(primary_cell_id) AS primary_cell_id,
            MAX(distinct_imeis) AS max_distinct_imeis,
            MAX(device_type) AS device_type,
            MAX(primary_apn) AS primary_apn,
            ROUND(SUM(rated_amount_zar), 2) AS total_rated_zar,
            ROUND(SUM(retail_equivalent_zar), 2) AS total_retail_equiv_zar
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
            ROUND(SUM(topup_zar + bundle_purchase_zar + invoice_billed_zar), 2) AS direct_sim_revenue_zar
        FROM billing_events
        GROUP BY sim_id
    )
    SELECT
        s.*,
        u.active_days, u.total_data_mb, u.total_voice_mins, u.total_sms_count,
        u.total_sessions, u.avg_night_ratio, u.max_unique_destinations,
        u.max_distinct_cells, u.primary_cell_id, u.max_distinct_imeis,
        u.device_type, u.primary_apn, u.total_rated_zar, u.total_retail_equiv_zar,
        b.topup_zar, b.bundle_purchase_zar, b.invoice_billed_zar,
        b.parent_account_allocated_zar, b.total_revenue_zar, b.direct_sim_revenue_zar,
        COALESCE(a.apn_type, 'UNKNOWN') AS apn_type,
        COALESCE(a.is_zero_rated_eligible, 0) AS is_zero_rated_eligible,
        COALESCE(c.province, 'Unknown') AS cell_province,
        COALESCE(c.area_type, 'Unknown') AS cell_area_type,
        c.lat AS cell_lat, c.lon AS cell_lon
    FROM subscribers s
    JOIN usage_agg u ON s.sim_id = u.sim_id
    JOIN billing_agg b ON s.sim_id = b.sim_id
    LEFT JOIN apn_catalog a ON u.primary_apn = a.apn
    LEFT JOIN cell_sites c ON u.primary_cell_id = c.cell_id
    """
    rows = [dict(r) for r in cur.execute(reconcile_sql).fetchall()]

    # Compute cell density stats
    cell_totals: Dict[str, int] = {}
    cell_zero_rev: Dict[str, int] = {}
    for r in rows:
        cid = r["primary_cell_id"]
        cell_totals[cid] = cell_totals.get(cid, 0) + 1
        if r["direct_sim_revenue_zar"] == 0 and r["total_retail_equiv_zar"] > 500:
            cell_zero_rev[cid] = cell_zero_rev.get(cid, 0) + 1

    # Layer 1 Entitlement & Deterministic Rule Evaluation
    for r in rows:
        cid = r["primary_cell_id"]
        r["cell_zero_rev_density"] = round(cell_zero_rev.get(cid, 0) / max(1, cell_totals.get(cid, 1)), 4)
        r["cell_concentration_index"] = round(1.0 / max(1, r["max_distinct_cells"]), 4)

        cov_data = min(r["total_data_mb"], r["entitlement_bundle_mb"])
        cov_voice = min(r["total_voice_mins"], r["entitlement_voice_min"])
        bundle_val_zar = cov_data * retail_data_rate + cov_voice * retail_voice_rate

        r["is_bundle_entitled"] = (
            r["entitlement_bundle_mb"] >= r["total_data_mb"] * 0.90
            and r["entitlement_voice_min"] >= r["total_voice_mins"] * 0.90
            and r["entitlement_bundle_mb"] > 0
        )
        r["is_enterprise_covered"] = (
            bool(r["is_enterprise_child"])
            and r["parent_account_id"] != ""
            and r["parent_account_allocated_zar"] >= r["total_retail_equiv_zar"] * 0.50
        )
        r["is_legit_zero_rated_apn"] = (
            bool(r["is_zero_rated_eligible"])
            and r["primary_apn"] == "mtn.zero.edu"
            and r["total_data_mb"] <= 8000.0
            and r["total_voice_mins"] <= 5.0
        )
        r["is_benign_exempt"] = (
            r["is_bundle_entitled"] or r["is_enterprise_covered"] or r["is_legit_zero_rated_apn"]
        )

        raw_unbilled = r["total_retail_equiv_zar"] - r["total_revenue_zar"] - bundle_val_zar
        r["unbilled_retail_zar"] = 0.0 if r["is_benign_exempt"] else round(max(0.0, raw_unbilled), 2)

        r["rule_hlr_ocs_orphan"] = (r["hlr_status"] == "ACTIVE") and (r["billing_status"] != "ACTIVE")
        r["rule_unrated_tariff"] = (
            r["total_rated_zar"] == 0
            and r["unbilled_retail_zar"] > 300
            and any(k in r["tariff_code"] for k in ("ZERO", "UNRATED", "BYPASS", "LEGACY"))
        )
        r["rule_iot_apn_mismatch"] = (r["plan_type"] == "IOT_M2M") and (r["primary_apn"] != "mtn.iot.m2m")
        r["rule_internal_sim_abuse"] = (
            r["plan_type"] == "INTERNAL_TEST"
            and (r["cell_area_type"] == "Residential" or r["avg_night_ratio"] > 0.45 or r["total_data_mb"] > 5000)
        )
        r["rule_simbox_signature"] = (
            r["total_voice_mins"] > 1200
            and r["max_distinct_imeis"] >= 4
            and r["max_unique_destinations"] >= 80
            and r["direct_sim_revenue_zar"] == 0
        )

        rules_triggered = sum([
            int(r["rule_hlr_ocs_orphan"]),
            int(r["rule_unrated_tariff"]),
            int(r["rule_iot_apn_mismatch"]),
            int(r["rule_internal_sim_abuse"]),
            int(r["rule_simbox_signature"]),
        ])
        r["rules_triggered_count"] = rules_triggered
        r["layer1_rule_score"] = 0.0 if r["is_benign_exempt"] else round(
            min(1.0, rules_triggered * 0.45 + (0.35 if r["unbilled_retail_zar"] > 500 else 0.0)), 4
        )

    # -----------------------------------------------------------------------
    # Layer 2: Pure-Python Isolation Forest + Feature Attributions
    # -----------------------------------------------------------------------
    run_pure_isolation_forest(rows, n_trees=100, sample_size=256, seed=seed)

    for r in rows:
        if r["is_benign_exempt"]:
            conf = 0.02 * r["layer2_ml_score"]
        else:
            conf = 0.60 * r["layer1_rule_score"] + 0.40 * r["layer2_ml_score"]
        r["confidence_score"] = round(min(0.99, max(0.0, conf)), 4)
        r["priority_score"] = round(r["unbilled_retail_zar"] * r["confidence_score"], 2)
        r["naive_score"] = r["total_retail_equiv_zar"] if r["direct_sim_revenue_zar"] == 0 else 0.0

    # Rank by Priority Score (Hunter) vs Naive Baseline Score
    rows.sort(key=lambda x: x["priority_score"], reverse=True)
    for i, r in enumerate(rows):
        r["hunter_rank"] = i + 1

    naive_sorted = sorted(rows, key=lambda x: x["naive_score"], reverse=True)
    naive_rank_map = {r["sim_id"]: i + 1 for i, r in enumerate(naive_sorted)}
    for r in rows:
        r["naive_rank"] = naive_rank_map[r["sim_id"]]

    # -----------------------------------------------------------------------
    # Layer 3: Automated Root-Cause Investigation & Financial Valuations
    # -----------------------------------------------------------------------
    rec_map = TARIFF_ASSUMPTIONS["recoverable_rate_by_archetype"]
    marginal_ratio = TARIFF_ASSUMPTIONS["marginal_network_cost_ratio"]
    interconnect_rate = TARIFF_ASSUMPTIONS["interconnect_termination_per_min_zar"]

    for r in rows:
        if r["is_benign_exempt"]:
            if r["is_bundle_entitled"]:
                hyp = "BENIGN_BUNDLE_COVERED"
                name = BENIGN_ARCHETYPES[hyp]["name"]
                act = "No action required (Auto-suppressed — covered by valid rollover bundle)."
                own = "Automated Entitlement Filter"
                ev = f"Direct revenue is R0, but SIM holds {r['entitlement_bundle_mb']:,.0f} MB rollover bundle covering {r['total_data_mb']:,.0f} MB usage."
            elif r["is_enterprise_covered"]:
                hyp = "BENIGN_ENTERPRISE_POOLED"
                name = BENIGN_ARCHETYPES[hyp]["name"]
                act = "No action required (Auto-suppressed — billed via Corporate Parent Account)."
                own = "Automated Entitlement Filter"
                ev = f"Child SIM has R0 direct billing, but Parent Account {r['parent_account_id']} was billed R{r['parent_account_allocated_zar']:,.2f}."
            else:
                hyp = "BENIGN_ZERO_RATED_APN"
                name = BENIGN_ARCHETYPES[hyp]["name"]
                act = "No action required (Auto-suppressed — whitelisted educational APN within cap)."
                own = "Automated Entitlement Filter"
                ev = f"Traffic strictly confined to zero-rated APN '{r['primary_apn']}' ({r['total_data_mb']:,.0f} MB)."
            rec_rate = 0.0
        elif r["rule_simbox_signature"]:
            hyp = "SIM_BOX_FRAUD"
            info = LEAKAGE_ARCHETYPES[hyp]
            name, act, own = info["name"], info["recommended_action"], info["owner_team"]
            ev = f"Cell '{r['primary_cell_id']}' cluster: {r['total_voice_mins']:,.0f} voice mins across {r['max_unique_destinations']} unique callees, rotating {r['max_distinct_imeis']} IMEIs on '{r['device_type']}' with R0 revenue."
            rec_rate = rec_map[hyp]
        elif r["rule_hlr_ocs_orphan"]:
            hyp = "PROVISIONING_ORPHAN"
            info = LEAKAGE_ARCHETYPES[hyp]
            name, act, own = info["name"], info["recommended_action"], info["owner_team"]
            ev = f"HLR Status is '{r['hlr_status']}' while Billing/OCS Status is '{r['billing_status']}' (Account: {r['account_id']}). Consumed {r['total_data_mb']/1024:.2f} GB & {r['total_voice_mins']:,.0f} mins (R{r['unbilled_retail_zar']:,.2f} retail value) unbilled."
            rec_rate = rec_map[hyp]
        elif r["rule_iot_apn_mismatch"]:
            hyp = "IOT_WRONG_APN"
            info = LEAKAGE_ARCHETYPES[hyp]
            name, act, own = info["name"], info["recommended_action"], info["owner_team"]
            ev = f"Plan is 'IOT_M2M' ({r['tariff_code']}) but SIM is transmitting high-volume data on consumer APN '{r['primary_apn']}' using '{r['device_type']}' ({r['total_data_mb']/1024:.2f} GB unbilled)."
            rec_rate = rec_map[hyp]
        elif r["rule_internal_sim_abuse"]:
            hyp = "FREE_INTERNAL_ABUSE"
            info = LEAKAGE_ARCHETYPES[hyp]
            name, act, own = info["name"], info["recommended_action"], info["owner_team"]
            ev = f"Internal Test SIM ('{r['tariff_code']}') pinned to {r['cell_area_type']} cell '{r['primary_cell_id']}' on '{r['device_type']}' with {r['total_data_mb']/1024:.2f} GB usage ({r['avg_night_ratio']*100:.0f}% night traffic)."
            rec_rate = rec_map[hyp]
        elif r["rule_unrated_tariff"] or (
            r["direct_sim_revenue_zar"] == 0
            and r["total_revenue_zar"] == 0
            and not r["is_benign_exempt"]
            and r["unbilled_retail_zar"] > 600
        ):
            hyp = "RATING_MISCONFIG"
            info = LEAKAGE_ARCHETYPES[hyp]
            name, act, own = info["name"], info["recommended_action"], info["owner_team"]
            ev = f"Active SIM on tariff '{r['tariff_code']}' and APN '{r['primary_apn']}' generated {r['total_sessions']} sessions ({r['total_data_mb']/1024:.2f} GB) rated at R0.00 in OCS (Retail value: R{r['unbilled_retail_zar']:,.2f})."
            rec_rate = rec_map[hyp]
        else:
            hyp = "NORMAL_SUBSCRIBER"
            name = BENIGN_ARCHETYPES["NORMAL_SUBSCRIBER"]["name"]
            act = "No action required (Reconciled within normal bundle/tariff discount tolerance)."
            own = "None"
            ev = f"Billed R{r['total_revenue_zar']:,.2f} against R{r['total_retail_equiv_zar']:,.2f} retail usage."
            rec_rate = 0.0
            r["unbilled_retail_zar"] = 0.0
            r["priority_score"] = 0.0

        r["predicted_archetype_code"] = hyp
        r["predicted_archetype_name"] = name
        r["recommended_action"] = act
        r["owner_team"] = own
        r["investigation_summary"] = ev
        r["recoverable_ratio"] = rec_rate

        r["marginal_network_cost_zar"] = round(
            (r["unbilled_retail_zar"] * marginal_ratio + r["total_voice_mins"] * interconnect_rate)
            if r["unbilled_retail_zar"] > 0 else 0.0,
            2,
        )
        r["est_recoverable_mid_zar"] = round(r["unbilled_retail_zar"] * rec_rate, 2)
        r["est_recoverable_low_zar"] = round(r["est_recoverable_mid_zar"] * 0.75, 2)
        r["est_recoverable_high_zar"] = round(min(r["unbilled_retail_zar"], r["est_recoverable_mid_zar"] * 1.20), 2)

    # Re-sort rows after Layer 3 reconciliation adjustments
    rows.sort(key=lambda x: x["priority_score"], reverse=True)
    for i, r in enumerate(rows):
        r["hunter_rank"] = i + 1

    # -----------------------------------------------------------------------
    # Backtest Evaluation vs. Ground Truth
    # -----------------------------------------------------------------------
    bt = compute_pure_backtest(rows, top_k=top_k)
    return rows, bt, sample_daily_by_sim


def compute_pure_backtest(rows: List[Dict[str, Any]], top_k: int = 100) -> Dict[str, Any]:
    total_true = sum(r["is_true_leakage"] for r in rows)
    total_true_retail_zar = sum(r["total_retail_equiv_zar"] for r in rows if r["is_true_leakage"])
    benign_codes = {"BENIGN_BUNDLE_COVERED", "BENIGN_ENTERPRISE_POOLED", "BENIGN_ZERO_RATED_APN"}
    total_benign = sum(1 for r in rows if r["ground_truth_label"] in benign_codes)

    hunter_top = [r for r in rows if r["hunter_rank"] <= top_k]
    naive_top = sorted(rows, key=lambda x: x["naive_rank"])[:top_k]

    h_tp = sum(r["is_true_leakage"] for r in hunter_top)
    n_tp = sum(r["is_true_leakage"] for r in naive_top)

    h_val = sum(r["total_retail_equiv_zar"] for r in hunter_top if r["is_true_leakage"])
    n_val = sum(r["total_retail_equiv_zar"] for r in naive_top if r["is_true_leakage"])

    h_fp_benign = sum(1 for r in hunter_top if r["ground_truth_label"] in benign_codes)
    n_fp_benign = sum(1 for r in naive_top if r["ground_truth_label"] in benign_codes)

    flagged = [r for r in rows if r["priority_score"] > 0]

    # Curves across K = 10..300
    naive_all_sorted = sorted(rows, key=lambda x: x["naive_rank"])
    curves = []
    for k in range(10, min(310, len(rows)), 10):
        hk = rows[:k]
        nk = naive_all_sorted[:k]
        curves.append({
            "K": k,
            "Hunter_Precision": round(sum(r["is_true_leakage"] for r in hk) / k * 100, 1),
            "Naive_Precision": round(sum(r["is_true_leakage"] for r in nk) / k * 100, 1),
            "Hunter_Recall": round(sum(r["is_true_leakage"] for r in hk) / max(1, total_true) * 100, 1),
            "Naive_Recall": round(sum(r["is_true_leakage"] for r in nk) / max(1, total_true) * 100, 1),
            "Hunter_Value_Share": round(sum(r["total_retail_equiv_zar"] for r in hk if r["is_true_leakage"]) / max(1.0, total_true_retail_zar) * 100, 1),
            "Naive_Value_Share": round(sum(r["total_retail_equiv_zar"] for r in nk if r["is_true_leakage"]) / max(1.0, total_true_retail_zar) * 100, 1),
        })

    # Archetype summary
    arch_groups: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        lbl = r["ground_truth_label"]
        if lbl not in arch_groups:
            arch_groups[lbl] = {
                "ground_truth_label": lbl,
                "total_sims": 0,
                "in_hunter_top_k": 0,
                "in_naive_top_k": 0,
                "total_retail_zar": 0.0,
                "unbilled_retail_zar": 0.0,
                "est_recoverable_mid_zar": 0.0,
            }
        g = arch_groups[lbl]
        g["total_sims"] += 1
        if r["hunter_rank"] <= top_k:
            g["in_hunter_top_k"] += 1
        if r["naive_rank"] <= top_k:
            g["in_naive_top_k"] += 1
        g["total_retail_zar"] = round(g["total_retail_zar"] + r["total_retail_equiv_zar"], 2)
        g["unbilled_retail_zar"] = round(g["unbilled_retail_zar"] + r["unbilled_retail_zar"], 2)
        g["est_recoverable_mid_zar"] = round(g["est_recoverable_mid_zar"] + r["est_recoverable_mid_zar"], 2)

    return {
        "top_k": top_k,
        "total_sims": len(rows),
        "total_true_leakage_sims": total_true,
        "total_benign_lookalikes": total_benign,
        "total_true_leakage_retail_zar": round(total_true_retail_zar, 2),
        "hunter_precision_k": round(h_tp / top_k, 4),
        "naive_precision_k": round(n_tp / top_k, 4),
        "hunter_recall_k": round(h_tp / max(1, total_true), 4),
        "naive_recall_k": round(n_tp / max(1, total_true), 4),
        "hunter_value_share_k": round(h_val / max(1.0, total_true_retail_zar), 4),
        "naive_value_share_k": round(n_val / max(1.0, total_true_retail_zar), 4),
        "hunter_benign_fp_k": h_fp_benign,
        "naive_benign_fp_k": n_fp_benign,
        "hunter_benign_fpr_k": round(h_fp_benign / top_k, 4),
        "naive_benign_fpr_k": round(n_fp_benign / top_k, 4),
        "financials": {
            "top_k_retail_zar": round(sum(r["unbilled_retail_zar"] for r in hunter_top), 2),
            "top_k_marginal_cost_zar": round(sum(r["marginal_network_cost_zar"] for r in hunter_top), 2),
            "top_k_rec_low_zar": round(sum(r["est_recoverable_low_zar"] for r in hunter_top), 2),
            "top_k_rec_mid_zar": round(sum(r["est_recoverable_mid_zar"] for r in hunter_top), 2),
            "top_k_rec_high_zar": round(sum(r["est_recoverable_high_zar"] for r in hunter_top), 2),
            "all_flagged_count": len(flagged),
            "all_retail_zar": round(sum(r["unbilled_retail_zar"] for r in flagged), 2),
            "all_marginal_cost_zar": round(sum(r["marginal_network_cost_zar"] for r in flagged), 2),
            "all_rec_low_zar": round(sum(r["est_recoverable_low_zar"] for r in flagged), 2),
            "all_rec_mid_zar": round(sum(r["est_recoverable_mid_zar"] for r in flagged), 2),
            "all_rec_high_zar": round(sum(r["est_recoverable_high_zar"] for r in flagged), 2),
        },
        "curves": curves,
        "archetype_summary": sorted(arch_groups.values(), key=lambda x: x["unbilled_retail_zar"], reverse=True),
    }
