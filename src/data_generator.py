"""
Synthetic Data Generator & Real Data Adapter for Ghost SIM Hunter.
Generates realistic South African telecom usage (CDRs aggregated daily),
subscriber master records (HLR vs Billing state), and revenue records across:
- Normal billed subscribers (Prepaid, Postpaid, Enterprise, IoT)
- 3 Benign zero-direct-revenue look-alikes (negative controls to test false positives)
- 5 Injected revenue leakage archetypes (ground truth positives)
"""

import os
from datetime import date, timedelta
from typing import Dict, Optional, Tuple
import numpy as np
import pandas as pd
import duckdb

from src.schema import (
    TARIFF_ASSUMPTIONS,
    LEAKAGE_ARCHETYPES,
    BENIGN_ARCHETYPES,
    CELL_SITES,
    APN_CATALOG,
    REQUIRED_TABLES_SCHEMA,
)


def generate_synthetic_dataset(
    n_sims: int = 3000,
    n_days: int = 30,
    seed: int = 42,
    end_date: Optional[date] = None,
) -> Dict[str, pd.DataFrame]:
    """
    Generate synthetic subscriber master, daily network usage, and billing tables.

    Distribution of SIMs (default 3,000 SIMs):
    - ~84% Normal Billed Subscribers (2,520 SIMs)
    - ~10% Benign Zero-Revenue Look-Alikes (300 SIMs):
        * 120 BENIGN_BUNDLE_COVERED (90-day / annual bundle or rollover wallet)
        * 100 BENIGN_ENTERPRISE_POOLED (Corporate child SIM billed to master account)
        *  80 BENIGN_ZERO_RATED_APN (Whitelisted education/banking zero-rated APN)
    - ~6% True Revenue Leakage Ghost SIMs (180 SIMs):
        *  45 PROVISIONING_ORPHAN (HLR=ACTIVE, Billing=MISSING/PENDING)
        *  40 RATING_MISCONFIG (Active on legacy/broken tariff code rating at R0)
        *  30 FREE_INTERNAL_ABUSE (Staff/Test SIM running massive consumer streaming in residential cells)
        *  35 IOT_WRONG_APN (IoT SIM on consumer broadband APN streaming high volume unbilled)
        *  30 SIM_BOX_FRAUD (Concentrated in JHB-HILLBROW-004 & JHB-SOWETO-003, high voice mins, IMEI rotation)
    """
    rng = np.random.default_rng(seed)
    if end_date is None:
        end_date = date(2026, 10, 26)
    start_date = end_date - timedelta(days=n_days - 1)
    dates = [start_date + timedelta(days=d) for d in range(n_days)]

    # Scale archetype counts proportionally to n_sims
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
    allocated = sum(counts.values())
    counts["NORMAL_SUBSCRIBER"] = max(50, n_sims - allocated)

    labels = []
    for label, count in counts.items():
        labels.extend([label] * count)
    rng.shuffle(labels)

    cell_ids = [c["cell_id"] for c in CELL_SITES]
    sim_box_cells = ["JHB-HILLBROW-004", "JHB-SOWETO-003"]
    residential_cells = ["JHB-SOWETO-003", "DBN-PHOENIX-009", "CPT-BELLVILLE-007"]

    subscribers_rows = []
    usage_rows = []
    billing_rows = []

    retail_data_rate = TARIFF_ASSUMPTIONS["retail_data_per_mb_zar"]
    retail_voice_rate = TARIFF_ASSUMPTIONS["retail_voice_per_min_zar"]
    retail_sms_rate = TARIFF_ASSUMPTIONS["retail_sms_per_msg_zar"]

    for idx, label in enumerate(labels):
        sim_id = f"SIM-{idx + 100001}"
        imsi = f"65510{rng.integers(1000000000, 9999999999)}"
        msisdn = f"+2783{rng.integers(1000000, 9999999)}"
        act_days_ago = int(rng.integers(35, 720))
        activation_date = (end_date - timedelta(days=act_days_ago)).isoformat()

        # Default attributes
        account_id = f"ACC-{idx + 500001}"
        parent_account_id = ""
        is_enterprise_child = False
        hlr_status = "ACTIVE"
        billing_status = "ACTIVE"
        channel = rng.choice(["MTN_STORE", "RETAIL_PARTNER", "ONLINE", "ENTERPRISE_DESK"], p=[0.4, 0.35, 0.15, 0.1])
        entitlement_bundle_mb = 0.0
        entitlement_voice_min = 0.0
        primary_cell = rng.choice(cell_ids)

        if label == "NORMAL_SUBSCRIBER":
            plan_type = rng.choice(["PREPAID", "POSTPAID", "ENTERPRISE", "IOT_M2M"], p=[0.65, 0.22, 0.08, 0.05])
            tariff_code = f"STD_{plan_type}_01"
            primary_apn = "mtn.iot.m2m" if plan_type == "IOT_M2M" else ("mtn.corp.vpn" if plan_type == "ENTERPRISE" else "myMTN")
            device_type = "IOT_MODEM" if plan_type == "IOT_M2M" else "SMARTPHONE"
            daily_data_mean = 18.0 if plan_type == "IOT_M2M" else rng.uniform(40.0, 380.0)
            daily_voice_mean = 0.5 if plan_type == "IOT_M2M" else rng.uniform(3.0, 28.0)
            night_ratio_mean = 0.18
            distinct_imeis = 1
            distinct_cells = int(rng.integers(2, 7))
            unique_dest = int(rng.integers(5, 30))

        elif label == "BENIGN_BUNDLE_COVERED":
            plan_type = "PREPAID"
            tariff_code = "PREPAID_LONG_BUNDLE_90D"
            primary_apn = "myMTN"
            device_type = rng.choice(["SMARTPHONE", "LTE_ROUTER"], p=[0.7, 0.3])
            daily_data_mean = rng.uniform(180.0, 650.0)
            daily_voice_mean = rng.uniform(5.0, 25.0)
            # Carried-over valid bundle covering 30-day usage
            entitlement_bundle_mb = daily_data_mean * n_days * rng.uniform(1.15, 1.50)
            entitlement_voice_min = daily_voice_mean * n_days * rng.uniform(1.15, 1.40)
            night_ratio_mean = 0.22
            distinct_imeis = 1
            distinct_cells = int(rng.integers(2, 5))
            unique_dest = int(rng.integers(8, 25))

        elif label == "BENIGN_ENTERPRISE_POOLED":
            plan_type = "ENTERPRISE"
            tariff_code = "ENT_SHARED_POOL_100"
            parent_account_id = f"CORP-MASTER-{rng.integers(100, 135)}"
            is_enterprise_child = True
            channel = "ENTERPRISE_DESK"
            primary_apn = "mtn.corp.vpn"
            device_type = rng.choice(["SMARTPHONE", "LTE_ROUTER"], p=[0.6, 0.4])
            daily_data_mean = rng.uniform(220.0, 800.0)
            daily_voice_mean = rng.uniform(15.0, 55.0)
            night_ratio_mean = 0.12
            distinct_imeis = 1
            distinct_cells = int(rng.integers(3, 8))
            unique_dest = int(rng.integers(15, 45))

        elif label == "BENIGN_ZERO_RATED_APN":
            plan_type = "PREPAID"
            tariff_code = "STD_PREPAID_01"
            primary_apn = "mtn.zero.edu"
            device_type = "SMARTPHONE"
            daily_data_mean = rng.uniform(60.0, 210.0)
            daily_voice_mean = 0.0
            night_ratio_mean = 0.15
            distinct_imeis = 1
            distinct_cells = int(rng.integers(1, 4))
            unique_dest = int(rng.integers(2, 5))

        elif label == "PROVISIONING_ORPHAN":
            plan_type = rng.choice(["POSTPAID", "ENTERPRISE", "PREPAID"], p=[0.55, 0.25, 0.20])
            tariff_code = f"STD_{plan_type}_01"
            hlr_status = "ACTIVE"
            billing_status = rng.choice(["MISSING_IN_OCS", "PENDING_ACTIVATION", "SUSPENDED_BILLING_ONLY"])
            account_id = "UNLINKED_ORPHAN" if billing_status == "MISSING_IN_OCS" else account_id
            primary_apn = rng.choice(["internet", "myMTN"])
            device_type = rng.choice(["LTE_ROUTER", "SMARTPHONE"], p=[0.6, 0.4])
            # High usage generating ~R1,100 to R2,400/month retail value
            daily_data_mean = rng.uniform(140.0, 320.0)
            daily_voice_mean = rng.uniform(8.0, 25.0)
            night_ratio_mean = 0.28
            distinct_imeis = int(rng.choice([1, 2], p=[0.8, 0.2]))
            distinct_cells = int(rng.integers(2, 6))
            unique_dest = int(rng.integers(12, 40))

        elif label == "RATING_MISCONFIG":
            plan_type = rng.choice(["POSTPAID", "PREPAID"], p=[0.6, 0.4])
            tariff_code = "LEGACY_PROMO_ZERO_BUG_99"
            primary_apn = rng.choice(["legacy.promo.unrated", "internet"])
            device_type = rng.choice(["LTE_ROUTER", "SMARTPHONE"], p=[0.5, 0.5])
            daily_data_mean = rng.uniform(160.0, 380.0)
            daily_voice_mean = rng.uniform(5.0, 20.0)
            night_ratio_mean = 0.30
            distinct_imeis = 1
            distinct_cells = int(rng.integers(2, 6))
            unique_dest = int(rng.integers(10, 35))

        elif label == "FREE_INTERNAL_ABUSE":
            plan_type = "INTERNAL_TEST"
            tariff_code = "ENG_TEST_UNLIMITED"
            channel = "INTERNAL_IT"
            primary_apn = rng.choice(["internet", "mtn.eng.test"])
            device_type = "5G_CPE_ROUTER"
            primary_cell = rng.choice(residential_cells)
            # Heavy night/weekend residential streaming/P2P on an internal test SIM
            daily_data_mean = rng.uniform(280.0, 620.0)
            daily_voice_mean = rng.uniform(10.0, 35.0)
            night_ratio_mean = rng.uniform(0.52, 0.78)
            distinct_imeis = int(rng.integers(2, 4))
            distinct_cells = 1
            unique_dest = int(rng.integers(45, 110))

        elif label == "IOT_WRONG_APN":
            plan_type = "IOT_M2M"
            tariff_code = "IOT_TELEMETRY_ONLY"
            channel = "ENTERPRISE_DESK"
            # Provisioned on consumer broadband APN instead of mtn.iot.m2m, bypassing M2M rating
            primary_apn = "internet"
            device_type = rng.choice(["LTE_ROUTER", "SMARTPHONE", "MODIFIED_IOT_GATEWAY"])
            daily_data_mean = rng.uniform(180.0, 420.0)
            daily_voice_mean = rng.uniform(2.0, 18.0)
            night_ratio_mean = rng.uniform(0.40, 0.68)
            distinct_imeis = int(rng.integers(2, 4))
            distinct_cells = int(rng.integers(1, 3))
            unique_dest = int(rng.integers(30, 85))

        elif label == "SIM_BOX_FRAUD":
            plan_type = "PREPAID"
            tariff_code = "PROMO_ONNET_UNLIMITED_BYPASS"
            channel = "STREET_AGENT_BATCH"
            primary_apn = "myMTN"
            device_type = "SIMBOX_MODEM_BANK"
            primary_cell = rng.choice(sim_box_cells)
            # High voice minutes, hundreds of distinct callees, static cell site, rapid IMEI cycling
            daily_data_mean = rng.uniform(15.0, 65.0)
            daily_voice_mean = rng.uniform(75.0, 165.0)
            night_ratio_mean = rng.uniform(0.35, 0.60)
            distinct_imeis = int(rng.integers(5, 14))
            distinct_cells = 1
            unique_dest = int(rng.integers(95, 260))

        is_leakage = label in LEAKAGE_ARCHETYPES

        subscribers_rows.append({
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
            "entitlement_bundle_mb": round(float(entitlement_bundle_mb), 2),
            "entitlement_voice_min": round(float(entitlement_voice_min), 2),
            "is_enterprise_child": bool(is_enterprise_child),
            "ground_truth_label": label,
            "is_true_leakage": bool(is_leakage),
        })

        # Generate daily usage and billing aggregates over the 30-day window
        total_sim_retail_zar = 0.0
        for d in dates:
            data_mb = max(0.0, float(rng.normal(daily_data_mean, daily_data_mean * 0.22)))
            voice_mins = max(0.0, float(rng.normal(daily_voice_mean, max(0.5, daily_voice_mean * 0.25))))
            sms_count = int(max(0, rng.poisson(2 if label != "SIM_BOX_FRAUD" else 18)))
            session_count = int(max(1, rng.poisson(12 if data_mb > 50 else 4)))
            night_ratio = float(np.clip(rng.normal(night_ratio_mean, 0.06), 0.01, 0.98))

            retail_equiv_zar = (
                data_mb * retail_data_rate
                + voice_mins * retail_voice_rate
                + sms_count * retail_sms_rate
            )

            # Rated amount in OCS/Billing vs Retail Value
            if is_leakage or label in ("BENIGN_ZERO_RATED_APN",):
                rated_amount_zar = 0.0
            elif label == "BENIGN_BUNDLE_COVERED":
                rated_amount_zar = 0.0  # Covered by existing bundle balance
            elif label == "BENIGN_ENTERPRISE_POOLED":
                rated_amount_zar = round(retail_equiv_zar * 0.75, 2)  # Rated to parent pool
            else:
                # Normal subscriber: rated at blended effective rate
                rated_amount_zar = round(retail_equiv_zar * rng.uniform(0.65, 1.0), 2)

            total_sim_retail_zar += retail_equiv_zar

            usage_rows.append({
                "sim_id": sim_id,
                "usage_date": d.isoformat(),
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
            })

        # Monthly billing summary event for the 30-day window
        if is_leakage:
            topup_zar = 0.0
            bundle_zar = 0.0
            invoice_zar = 0.0
            parent_alloc_zar = 0.0
        elif label == "BENIGN_BUNDLE_COVERED":
            # Paid in prior month or via rollover bundle, R0 direct top-up this month
            topup_zar = 0.0
            bundle_zar = 0.0
            invoice_zar = 0.0
            parent_alloc_zar = 0.0
        elif label == "BENIGN_ENTERPRISE_POOLED":
            topup_zar = 0.0
            bundle_zar = 0.0
            invoice_zar = 0.0
            parent_alloc_zar = round(total_sim_retail_zar * 0.78, 2)
        elif label == "BENIGN_ZERO_RATED_APN":
            topup_zar = 0.0
            bundle_zar = 0.0
            invoice_zar = 0.0
            parent_alloc_zar = 0.0
        else:
            # Normal billed subscriber
            effective_rev = round(total_sim_retail_zar * rng.uniform(0.70, 1.05), 2)
            if plan_type == "PREPAID":
                topup_zar = round(effective_rev * 0.4, 2)
                bundle_zar = round(effective_rev * 0.6, 2)
                invoice_zar = 0.0
                parent_alloc_zar = 0.0
            else:
                topup_zar = 0.0
                bundle_zar = 0.0
                invoice_zar = effective_rev
                parent_alloc_zar = 0.0

        total_rev = round(topup_zar + bundle_zar + invoice_zar + parent_alloc_zar, 2)
        billing_rows.append({
            "sim_id": sim_id,
            "billing_date": end_date.isoformat(),
            "topup_zar": topup_zar,
            "bundle_purchase_zar": bundle_zar,
            "invoice_billed_zar": invoice_zar,
            "parent_account_allocated_zar": parent_alloc_zar,
            "total_revenue_zar": total_rev,
        })

    return {
        "subscribers": pd.DataFrame(subscribers_rows),
        "usage_daily": pd.DataFrame(usage_rows),
        "billing_events": pd.DataFrame(billing_rows),
        "cell_sites": pd.DataFrame(CELL_SITES),
        "apn_catalog": pd.DataFrame(APN_CATALOG),
    }


def load_into_duckdb(
    datasets: Dict[str, pd.DataFrame],
    db_path: str = ":memory:",
) -> duckdb.DuckDBPyConnection:
    """Register all DataFrames as tables inside a DuckDB connection."""
    con = duckdb.connect(db_path)
    for table_name, df in datasets.items():
        con.register(f"df_{table_name}", df)
        con.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM df_{table_name}")
    return con


def validate_custom_files(
    subscribers_df: pd.DataFrame,
    usage_df: pd.DataFrame,
    billing_df: pd.DataFrame,
) -> Tuple[bool, list]:
    """
    Validate user-uploaded CSV/Parquet files against the required schema for real data ingestion.
    """
    errors = []
    for name, df in [
        ("subscribers", subscribers_df),
        ("usage_daily", usage_df),
        ("billing_events", billing_df),
    ]:
        required = set(REQUIRED_TABLES_SCHEMA[name]) - {"ground_truth_label"}
        missing = required - set(df.columns)
        if missing:
            errors.append(f"Table '{name}' is missing required columns: {sorted(missing)}")
    return (len(errors) == 0, errors)
