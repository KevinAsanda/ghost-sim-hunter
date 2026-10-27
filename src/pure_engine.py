"""Synthetic telecom fixtures, pure-Python Isolation Forest and benchmark evaluation.
Both CLI and web flows use the record-level ledger in src.reconciliation.
"""

import math
import random
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
    "total_data_mb": "Analysis-window Data Volume (MB)",
    "total_voice_mins": "Analysis-window Voice Volume (Mins)",
    "avg_night_ratio": "Off-Peak / Night Usage Ratio",
    "max_unique_destinations": "Unique Called/IP Destinations",
    "max_distinct_imeis": "Distinct IMEIs Rotated on SIM",
    "cell_concentration_index": "Static Cell Pinning Index",
    "cell_zero_rev_density": "Cell Residual-Usage Density",
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
    - Top 3 standardized feature deviation indicators (not model explanations) per SIM
    """
    rng = random.Random(seed)
    n = len(rows)
    if not rows:
        return
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
        r["deviation_indicator_1"] = f"{FEATURE_LABELS[FEATURE_COLS[ordered_idx[0]]]} ({shares[ordered_idx[0]]*100:.0f}%)"
        r["deviation_indicator_2"] = f"{FEATURE_LABELS[FEATURE_COLS[ordered_idx[1]]]} ({shares[ordered_idx[1]]*100:.0f}%)"
        r["deviation_indicator_3"] = f"{FEATURE_LABELS[FEATURE_COLS[ordered_idx[2]]]} ({shares[ordered_idx[2]]*100:.0f}%)"


# ---------------------------------------------------------------------------
# Synthetic Data Generation + SQLite3 Multi-System Reconciliation
# ---------------------------------------------------------------------------
def generate_synthetic_tables(
    n_sims: int = 3000,
    n_days: int = 30,
    seed: int = 42,
    top_k: int = 100,
) -> Dict[str, List[Dict[str, Any]]]:
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

    # Explicit validity and billing-period provenance for the shared ledger.
    for subscriber in subscribers:
        subscriber["entitlement_valid_from"] = start_date.isoformat()
        subscriber["entitlement_valid_to"] = end_date.isoformat()
    for event in billing_events:
        event["usage_period_start"] = start_date.isoformat()
        event["usage_period_end"] = end_date.isoformat()
    return dict(subscribers=subscribers, usage_daily=usage_daily, billing_events=billing_events)


def generate_and_run_pipeline(n_sims=3000, n_days=30, seed=42, top_k=100):
    from src.ingestion import validate_tables
    from src.reconciliation import detect
    tables = generate_synthetic_tables(n_sims, n_days, seed, top_k)
    config = {"as_of_date": "2026-10-30", "billing_grace_days": 3}
    normalized, report = validate_tables(tables, {k: list(v[0]) if v else [] for k, v in tables.items()}, config)
    if not report["valid"]:
        raise ValueError(report["issues"])
    result = detect(normalized, config, synthetic=True, top_k=top_k)
    return result["rows"], result["benchmark"], result["daily"]


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
                "estimated_opportunity_zar": 0.0,
            }
        g = arch_groups[lbl]
        g["total_sims"] += 1
        if r["hunter_rank"] <= top_k:
            g["in_hunter_top_k"] += 1
        if r["naive_rank"] <= top_k:
            g["in_naive_top_k"] += 1
        g["total_retail_zar"] = round(g["total_retail_zar"] + r["total_retail_equiv_zar"], 2)
        g["unbilled_retail_zar"] = round(g["unbilled_retail_zar"] + r["unbilled_retail_zar"], 2)
        g["estimated_opportunity_zar"] = round(g["estimated_opportunity_zar"] + r["estimated_opportunity_zar"], 2)

    return {
        "label": "Synthetic benchmark results — not validated production accuracy",
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
        "hunter_benign_share_k": round(h_fp_benign / max(1, len(hunter_top)), 4),
        "naive_benign_share_k": round(n_fp_benign / max(1, len(naive_top)), 4),
        "curves": curves,
        "archetype_summary": sorted(arch_groups.values(), key=lambda x: x["unbilled_retail_zar"], reverse=True),
    }
