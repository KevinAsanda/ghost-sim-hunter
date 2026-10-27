"""
CLI Runner & Verification Suite for Ghost SIM Hunter (Zero-Revenue Active SIMs).
Executes the synthetic telecom benchmark through the shared validation and
record-level reconciliation ledger (`src/ingestion.py` + `src/reconciliation.py`).
"""

import csv
import json
import os
from src.pure_engine import generate_and_run_pipeline


def main():
    print("=" * 80)
    print(" GHOST SIM HUNTER — ZERO-REVENUE ACTIVE SIMs (SYNTHETIC BENCHMARK RUN)")
    print("=" * 80)

    print("\n[1/4] Generating synthetic telecom dataset (3,000 SIMs x 30 days = 90,000 CDR daily rows)...")
    print("[2/4] Validating tables & executing record-level reconciliation ledger...")
    print("[3/4] Running Isolation Forest risk scoring & standardized deviation indicators...")
    print("[4/4] Evaluating synthetic benchmark against ground truth & Naive R0 baseline...")

    rows, bt, _ = generate_and_run_pipeline(n_sims=3000, n_days=30, seed=42, top_k=100)

    os.makedirs("data", exist_ok=True)
    if rows:
        fieldnames = list(rows[0].keys())
        with open("data/daily_top_100_queue.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows[:100])

        with open("data/all_evaluated_sims.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    with open("data/backtest_summary.json", "w", encoding="utf-8") as f:
        json.dump(bt, f, indent=2)

    top_k = bt["top_k"]
    top_rows = rows[:top_k]
    top_opportunity_zar = sum(r["estimated_opportunity_zar"] for r in top_rows)
    top_pending_zar = sum(r["pending_billing_zar"] for r in top_rows)
    top_missing_zar = sum(r["missing_billing_evidence_zar"] for r in top_rows)

    print("\n" + "-" * 80)
    print(f" {bt['label'].upper()}")
    print("-" * 80)
    print(f" Total Population Evaluated          : {bt['total_sims']:,} SIMs (30-Day Window, 90,000 Daily CDRs)")
    print(f" True Injected Leakage SIMs          : {bt['total_true_leakage_sims']:,} SIMs (5 Leakage Archetypes)")
    print(f" Benign 'R0 Direct Revenue' SIMs     : {bt['total_benign_lookalikes']:,} SIMs (Bundles, Enterprise Pools, Zero-Rated APNs)")
    print("-" * 80)
    print(" Metric                              | Ghost SIM Hunter | Naive R0 Baseline")
    print("-" * 80)
    print(f" Precision@{top_k:<3d}                       | {bt['hunter_precision_k']*100:14.1f}% | {bt['naive_precision_k']*100:15.1f}%")
    print(f" Recall@{top_k:<3d} (of all 180 Ghost SIMs)  | {bt['hunter_recall_k']*100:14.1f}% | {bt['naive_recall_k']*100:15.1f}%")
    print(f" Share of Total Leakage Value @{top_k:<3d}   | {bt['hunter_value_share_k']*100:14.1f}% | {bt['naive_value_share_k']*100:15.1f}%")
    print(f" Benign Look-Alikes in Top {top_k:<3d}       | {bt['hunter_benign_fp_k']:15d} | {bt['naive_benign_fp_k']:16d}")
    print(f" Benign Share in Top {top_k:<3d}             | {bt['hunter_benign_share_k']*100:14.1f}% | {bt['naive_benign_share_k']*100:15.1f}%")
    print("-" * 80)
    print(" QUEUE OPPORTUNITY SUMMARY (UNCONFIRMED REFERENCE-VALUE RESIDUALS)")
    print("-" * 80)
    print(f" Top-{top_k} Estimated Opportunity (ZAR) : R {top_opportunity_zar:12,.2f} (mature unbilled residual)")
    print(f" Top-{top_k} Awaiting Billing (ZAR)      : R {top_pending_zar:12,.2f} (within billing delay allowance)")
    print(f" Top-{top_k} Missing Billing Evidence    : R {top_missing_zar:12,.2f} (no billing snapshot as of run date)")
    print(" Confirmed Leakage / Cash Recovered / Cost Avoided are recorded only after analyst review.")
    print("=" * 80)
    print("\nTop 5 Ranked Ghost SIM Cases:")
    for r in rows[:5]:
        print(
            f"  #{r['hunter_rank']:2d} | {r['sim_id']} ({r['msisdn']}) | {r['predicted_archetype_name']} | "
            f"Est. Opportunity: R{r['estimated_opportunity_zar']:,.2f} | "
            f"Risk: {r['risk_score']*100:.0f}/100 | Top Deviation: {r['deviation_indicator_1']}"
        )


if __name__ == "__main__":
    main()
