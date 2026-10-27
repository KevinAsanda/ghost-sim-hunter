"""Record-level coverage ledger shared by uploaded data and the synthetic benchmark.

Residuals are reference-value estimates, not findings of collectible debt.
"""
from collections import Counter, defaultdict
from datetime import date
from src.schema import CELL_SITES, LEAKAGE_ARCHETYPES
from src.pure_engine import run_pure_isolation_forest, compute_pure_backtest

ENGINE_VERSION = "ledger-v2"


def detect(tables, config, synthetic=False, top_k=100):
    usage, billing = defaultdict(list), defaultdict(list)
    for row in tables["usage_daily"]:
        usage[row["sim_id"]].append(row)
    for row in tables["billing_events"]:
        if row["billing_date"] <= config["as_of_date"]:
            billing[row["sim_id"]].append(row)
    cells = {c["cell_id"]: c for c in CELL_SITES}
    as_of = date.fromisoformat(config["as_of_date"])
    rows, histories = [], {}
    for subscriber in tables["subscribers"]:
        sid = subscriber["sim_id"]
        daily = sorted(usage[sid], key=lambda d: (d["usage_date"], d["primary_apn"], d["primary_cell_id"]))
        events = sorted(billing[sid], key=lambda b: (b["billing_date"], b.get("billing_event_id", ""),
                                                    b.get("usage_period_start", ""), b.get("usage_period_end", "")))
        quota_mb, quota_voice = subscriber["entitlement_bundle_mb"], subscriber["entitlement_voice_min"]
        zero_mb = 8000.0  # Explicit educational-APN data cap per <=31-day analysis window.
        ledger = []
        for source in daily:
            d = dict(source)
            dr, vr, sr = (d[k] for k in ("data_rate_zar_per_mb", "voice_rate_zar_per_min", "sms_rate_zar_per_msg"))
            gross = d["data_mb"] * dr + d["voice_mins"] * vr + d["sms_count"] * sr
            zero = min(zero_mb, d["data_mb"]) if d["primary_apn"] == "mtn.zero.edu" else 0.0
            zero_mb -= zero
            covered_mb = covered_voice = 0.0
            valid = (subscriber.get("entitlement_valid_from", "9999") <= d["usage_date"]
                     <= subscriber.get("entitlement_valid_to", "0000") and
                     subscriber.get("entitlement_apn", "") in ("", d["primary_apn"]))
            if valid:
                covered_mb = min(quota_mb, d["data_mb"] - zero)
                covered_voice = min(quota_voice, d["voice_mins"])
                quota_mb -= covered_mb
                quota_voice -= covered_voice
            zero_value = zero * dr
            bundle_value = covered_mb * dr + covered_voice * vr
            chargeable = max(0, gross - zero_value - bundle_value)
            rated = min(chargeable, d["rated_amount_zar"])
            d.update(retail_equivalent_zar=gross, zero_rated_covered_zar=zero_value,
                     entitlement_covered_zar=bundle_value, rated_covered_zar=rated,
                     invoice_covered_zar=0.0, parent_covered_zar=0.0,
                     remaining_zar=chargeable - rated, rated_credit_zar=rated)
            ledger.append(d)
        unused_billing = 0.0
        for event in events:
            applicable = [d for d in ledger if event.get("usage_period_start", "9999")
                          <= d["usage_date"] <= event.get("usage_period_end", "0000")]
            # Rated charges and invoices are two observations of the same charge.
            # Match previously covered rated value before consuming additional residuals.
            for amount_key, coverage_key in (("invoice_billed_zar", "invoice_covered_zar"),
                                             ("parent_account_allocated_zar", "parent_covered_zar")):
                budget = event[amount_key]
                for d in applicable:
                    matched = min(budget, d["rated_credit_zar"])
                    d["rated_credit_zar"] -= matched
                    budget -= matched
                for d in applicable:
                    covered = min(budget, d["remaining_zar"])
                    d["remaining_zar"] -= covered
                    d[coverage_key] += covered
                    budget -= covered
                unused_billing += budget
        for d in ledger:
            remaining = max(0.0, d.pop("remaining_zar"))
            d.pop("rated_credit_zar")
            within_delay = (as_of - date.fromisoformat(d["usage_date"])).days < config["billing_grace_days"]
            d["pending_billing_zar"] = remaining if within_delay else 0.0
            d["missing_billing_evidence_zar"] = remaining if not within_delay and not events else 0.0
            d["unbilled_retail_zar"] = remaining if not within_delay and events else 0.0
            for key, value in d.items():
                if isinstance(value, float):
                    d[key] = round(value, 6)  # Keep sub-cent ledger amounts until per-SIM totals.
        histories[sid] = ledger
        cell_weights, apn_weights = Counter(), Counter()
        for d in ledger:
            weight = max(1, d["session_count"])
            cell_weights[d["primary_cell_id"]] += weight
            apn_weights[d["primary_apn"]] += weight
        primary_cell = cell_weights.most_common(1)[0][0] if cell_weights else "Unknown"
        primary_apn = apn_weights.most_common(1)[0][0] if apn_weights else "Unknown"
        cell = cells.get(primary_cell, {})
        r = dict(subscriber)
        for key in ("imsi", "msisdn", "account_id", "parent_account_id", "channel"):
            r.setdefault(key, "")
        r.update(primary_cell_id=primary_cell, primary_apn=primary_apn,
                 observed_apns=sorted(apn_weights), cell_province=cell.get("province", "Unknown"),
                 cell_area_type=cell.get("area_type", "Unknown"), active_days=len({d["usage_date"] for d in ledger}),
                 total_data_mb=round(sum(d["data_mb"] for d in ledger), 2),
                 total_voice_mins=round(sum(d["voice_mins"] for d in ledger), 2),
                 total_sms_count=sum(d["sms_count"] for d in ledger),
                 total_sessions=sum(d["session_count"] for d in ledger),
                 avg_night_ratio=sum(d["night_usage_ratio"] * max(1, d["session_count"]) for d in ledger)
                     / max(1, sum(max(1, d["session_count"]) for d in ledger)),
                 max_unique_destinations=max((d["unique_destinations"] for d in ledger), default=0),
                 max_distinct_imeis=max((d["distinct_imeis"] for d in ledger), default=0),
                 cell_concentration_index=max(cell_weights.values(), default=0) / max(1, sum(cell_weights.values())),
                 total_retail_equiv_zar=round(sum(d["retail_equivalent_zar"] for d in ledger), 2),
                 unused_billing_value_zar=round(unused_billing, 2),
                 billing_evidence_present=bool(events),
                 direct_sim_revenue_zar=round(sum(b["invoice_billed_zar"] for b in events), 2))
        for key in ("zero_rated_covered_zar", "entitlement_covered_zar", "rated_covered_zar", "invoice_covered_zar",
                    "parent_covered_zar", "unbilled_retail_zar", "pending_billing_zar", "missing_billing_evidence_zar"):
            r[key] = round(sum(d[key] for d in ledger), 2)
        r["total_rated_zar"] = r["rated_covered_zar"]
        r["is_benign_exempt"] = not any(r[k] > 0 for k in
            ("unbilled_retail_zar", "pending_billing_zar", "missing_billing_evidence_zar"))
        r["rule_hlr_ocs_orphan"] = r["hlr_status"] == "ACTIVE" and r["billing_status"] != "ACTIVE"
        r["rule_unrated_tariff"] = r["rated_covered_zar"] == 0 and any(
            k in r["tariff_code"].upper() for k in ("ZERO", "UNRATED", "BYPASS", "LEGACY"))
        r["rule_iot_apn_mismatch"] = r["plan_type"] == "IOT_M2M" and any(
            d["primary_apn"] != "mtn.iot.m2m" and d["unbilled_retail_zar"] > 0 for d in ledger)
        r["rule_internal_sim_abuse"] = r["plan_type"] == "INTERNAL_TEST" and (
            r["cell_area_type"] == "Residential" or r["avg_night_ratio"] > .45 or r["total_data_mb"] > 5000)
        r["rule_simbox_signature"] = (r["total_voice_mins"] > 1200 and r["max_distinct_imeis"] >= 4
                                      and r["max_unique_destinations"] >= 80 and r["direct_sim_revenue_zar"] == 0)
        rules = [("rule_simbox_signature", "SIM_BOX_FRAUD"), ("rule_hlr_ocs_orphan", "PROVISIONING_ORPHAN"),
                 ("rule_iot_apn_mismatch", "IOT_WRONG_APN"), ("rule_internal_sim_abuse", "FREE_INTERNAL_ABUSE"),
                 ("rule_unrated_tariff", "RATING_MISCONFIG")]
        r["rules_triggered_count"] = sum(bool(r[key]) for key, _ in rules)
        if r["unbilled_retail_zar"] > 0:
            code = next((code for rule, code in rules if r[rule]), "UNRECONCILED_USAGE")
        elif r["missing_billing_evidence_zar"] > 0:
            code = "MISSING_BILLING_EVIDENCE"
        elif r["pending_billing_zar"] > 0:
            code = "AWAITING_BILLING"
        else:
            code = "RECONCILED" if ledger else "NO_USAGE_SUPPLIED"
        info = LEAKAGE_ARCHETYPES.get(code, {})
        r.update(predicted_archetype_code=code, predicted_archetype_name=info.get("name", code.replace("_", " ").title()),
                 owner_team=info.get("owner_team", "Revenue Assurance"),
                 recommended_action=info.get("recommended_action", "Review source evidence and reconciliation ledger before deciding."))
        r["investigation_summary"] = (
            f"Reference usage value R{r['total_retail_equiv_zar']:.2f}; mature residual R{r['unbilled_retail_zar']:.2f}; "
            f"awaiting billing R{r['pending_billing_zar']:.2f}; missing billing evidence R{r['missing_billing_evidence_zar']:.2f}. "
            "A rule match is a hypothesis; verify before confirming leakage.")
        # Keep all residuals, including partial entitlement/pool coverage with no named rule.
        r["estimated_opportunity_zar"] = r["unbilled_retail_zar"]
        r["naive_score"] = r["total_retail_equiv_zar"] if r["direct_sim_revenue_zar"] == 0 else 0
        if synthetic:
            r["ground_truth_label"] = subscriber.get("ground_truth_label", "UNKNOWN")
            r["is_true_leakage"] = r["ground_truth_label"] in LEAKAGE_ARCHETYPES
        else:
            r.pop("ground_truth_label", None)
        rows.append(r)
    totals, residuals = Counter(), Counter()
    for r in rows:
        totals[r["primary_cell_id"]] += 1
        residuals[r["primary_cell_id"]] += int(r["unbilled_retail_zar"] > 0)
    for r in rows:
        r["cell_zero_rev_density"] = residuals[r["primary_cell_id"]] / totals[r["primary_cell_id"]]
    if rows:
        run_pure_isolation_forest(rows)
    for r in rows:
        r["risk_score"] = round(min(.99, .2 + .25 * r["rules_triggered_count"] + .3 * r["layer2_ml_score"]), 4) if r["unbilled_retail_zar"] else 0
        r["priority_score"] = round(r["unbilled_retail_zar"] * r["risk_score"], 2)
    rows.sort(key=lambda r: (-r["priority_score"], r["sim_id"]))
    naive = {r["sim_id"]: i + 1 for i, r in enumerate(sorted(rows, key=lambda r: (-r["naive_score"], r["sim_id"])))}
    for index, r in enumerate(rows, 1):
        r["hunter_rank"], r["naive_rank"] = index, naive[r["sim_id"]]
    benchmark = compute_pure_backtest(rows, top_k=min(top_k, len(rows))) if synthetic and rows else None
    return dict(rows=rows, daily=histories, benchmark=benchmark, engine_version=ENGINE_VERSION)
