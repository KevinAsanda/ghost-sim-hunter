"""
CLI Runner & Verification Suite for Ghost SIM Hunter (Zero-Revenue Active SIMs).
Automatically uses the DuckDB + scikit-learn engine if installed, or seamlessly falls
back to the built-in SQLite3 + Pure-Python Isolation Forest engine (`src/pure_engine.py`)
so the entire pipeline runs with zero external dependencies when offline.
"""

import csv
import json
import os
from src.pure_engine import generate_and_run_pipeline


def main():
    print("=" * 80)
    print(" GHOST SIM HUNTER — ZERO-REVENUE ACTIVE SIMs (MVP PIPELINE RUN)")
    print("=" * 80)

    print("\n[1/4] Generating synthetic telecom dataset (3,000 SIMs x 30 days = 90,000 CDR daily rows)...")
    print("[2/4] Loading tables into SQL Engine & executing Layer 1 Multi-System Reconciliation...")
    print("[3/4] Running Layer 2 Isolation Forest + Feature Attribution & Layer 3 Root-Cause Diagnostics...")
    print("[4/4] Running Backtest Evaluation vs. Ground Truth & Naive R0 Threshold Baseline...")

    rows, bt, sample_daily = generate_and_run_pipeline(n_sims=3000, n_days=30, seed=42, top_k=100)

    os.makedirs("data", exist_ok=True)
    # Export Top 100 Queue CSV
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

    fin = bt["financials"]
    print("\n" + "-" * 80)
    print(" BACKTEST SCORECARD (TOP-100 DAILY ANALYST QUEUE)")
    print("-" * 80)
    print(f" Total Population Evaluated          : {bt['total_sims']:,} SIMs (30-Day Window, 90,000 Daily CDRs)")
    print(f" True Injected Leakage SIMs          : {bt['total_true_leakage_sims']:,} SIMs (5 Leakage Archetypes)")
    print(f" Benign 'R0 Direct Revenue' SIMs     : {bt['total_benign_lookalikes']:,} SIMs (Bundles, Enterprise Pools, Zero-Rated APNs)")
    print("-" * 80)
    print(f" Metric                              | Ghost SIM Hunter | Naive R0 Baseline")
    print("-" * 80)
    print(f" Precision@100                       | {bt['hunter_precision_k']*100:14.1f}% | {bt['naive_precision_k']*100:15.1f}%")
    print(f" Recall@100 (of all 180 Ghost SIMs)  | {bt['hunter_recall_k']*100:14.1f}% | {bt['naive_recall_k']*100:15.1f}%")
    print(f" Share of Total Leakage Value @100   | {bt['hunter_value_share_k']*100:14.1f}% | {bt['naive_value_share_k']*100:15.1f}%")
    print(f" Benign Look-Alike False Positives   | {bt['hunter_benign_fp_k']:15d} | {bt['naive_benign_fp_k']:16d}")
    print(f" False-Positive Rate (FPR@100)       | {bt['hunter_benign_fpr_k']*100:14.1f}% | {bt['naive_benign_fpr_k']*100:15.1f}%")
    print("-" * 80)
    print(" FINANCIAL VALUATION (TRANSPARENT 3-TIER BREAKDOWN)")
    print("-" * 80)
    print(f" Top-100 Unbilled Retail Tariff Value: R {fin['top_k_retail_zar']:12,.2f} / month (R {fin['top_k_retail_zar']*12:14,.2f} / yr)")
    print(f" Top-100 Direct Marginal Network Cost: R {fin['top_k_marginal_cost_zar']:12,.2f} / month (R {fin['top_k_marginal_cost_zar']*12:14,.2f} / yr)")
    print(f" Top-100 Est. Recoverable Cash (Base): R {fin['top_k_rec_mid_zar']:12,.2f} / month (Range: R {fin['top_k_rec_low_zar']:,.0f} - R {fin['top_k_rec_high_zar']:,.0f})")
    print("-" * 80)
    print(f" All {fin['all_flagged_count']} Flagged Unbilled Retail Value : R {fin['all_retail_zar']:12,.2f} / month (R {fin['all_retail_zar']*12:14,.2f} / yr)")
    print(f" All {fin['all_flagged_count']} Flagged Est. Recoverable Cash : R {fin['all_rec_mid_zar']:12,.2f} / month (R {fin['all_rec_mid_zar']*12:14,.2f} / yr)")
    print("=" * 80)
    print("\nTop 5 Ranked Ghost SIM Cases:")
    for r in rows[:5]:
        print(
            f"  #{r['hunter_rank']:2d} | {r['sim_id']} ({r['msisdn']}) | {r['predicted_archetype_name']} | "
            f"Unbilled Retail: R{r['unbilled_retail_zar']:,.2f} | Est. Recoverable: R{r['est_recoverable_mid_zar']:,.2f} | "
            f"Conf: {r['confidence_score']*100:.1f}% | Top Driver: {r['shap_driver_1']}"
        )


if __name__ == "__main__":
    main()
