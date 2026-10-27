"""
Backtesting and Evaluation Suite for Ghost SIM Hunter.
Compares the 3-Layer Ghost SIM Hunter pipeline against a Naive Threshold Baseline
("Direct SIM Revenue == R0 sorted by Total Retail Usage Value") across:
- Precision@K (especially Precision@100)
- Recall@K of true injected leakage SIMs
- Share of Total Leakage Value (ZAR) captured in Top K
- False-Positive Rate (FPR) on Benign Zero-Revenue Look-Alikes (Bundles, Enterprise Pools, Zero-Rated APNs)
- Estimated Recoverable Cash Range (Conservative / Base / Optimistic) vs Marginal Network Cost
"""

from typing import Dict, Any
import numpy as np
import pandas as pd


def evaluate_backtest(df: pd.DataFrame, top_k: int = 100) -> Dict[str, Any]:
    """
    Evaluate Ghost SIM Hunter ranking vs Naive Threshold Baseline against ground-truth labels.
    """
    total_true_leakage_sims = int(df["is_true_leakage"].sum())
    total_true_leakage_retail_zar = float(
        df.loc[df["is_true_leakage"], "total_retail_equiv_zar"].sum()
    )

    benign_lookalike_mask = df["ground_truth_label"].isin([
        "BENIGN_BUNDLE_COVERED",
        "BENIGN_ENTERPRISE_POOLED",
        "BENIGN_ZERO_RATED_APN",
    ])
    total_benign_lookalikes = int(benign_lookalike_mask.sum())

    # Top-K slices
    hunter_top_k = df.sort_values("hunter_rank").head(top_k)
    naive_top_k = df.sort_values("naive_rank").head(top_k)

    # 1. Precision@K
    hunter_tp_k = int(hunter_top_k["is_true_leakage"].sum())
    naive_tp_k = int(naive_top_k["is_true_leakage"].sum())

    hunter_precision_k = hunter_tp_k / float(top_k)
    naive_precision_k = naive_tp_k / float(top_k)

    # 2. Recall@K
    hunter_recall_k = hunter_tp_k / max(1, total_true_leakage_sims)
    naive_recall_k = naive_tp_k / max(1, total_true_leakage_sims)

    # 3. Share of Total True Leakage Retail Value Captured in Top-K
    hunter_val_captured = float(
        hunter_top_k.loc[hunter_top_k["is_true_leakage"], "total_retail_equiv_zar"].sum()
    )
    naive_val_captured = float(
        naive_top_k.loc[naive_top_k["is_true_leakage"], "total_retail_equiv_zar"].sum()
    )

    hunter_value_share_k = hunter_val_captured / max(1.0, total_true_leakage_retail_zar)
    naive_value_share_k = naive_val_captured / max(1.0, total_true_leakage_retail_zar)

    # 4. False Positives on Benign Look-Alikes in Top-K
    hunter_benign_fp_k = int(
        hunter_top_k["ground_truth_label"].isin([
            "BENIGN_BUNDLE_COVERED",
            "BENIGN_ENTERPRISE_POOLED",
            "BENIGN_ZERO_RATED_APN",
        ]).sum()
    )
    naive_benign_fp_k = int(
        naive_top_k["ground_truth_label"].isin([
            "BENIGN_BUNDLE_COVERED",
            "BENIGN_ENTERPRISE_POOLED",
            "BENIGN_ZERO_RATED_APN",
        ]).sum()
    )

    hunter_benign_fpr_k = hunter_benign_fp_k / float(top_k)
    naive_benign_fpr_k = naive_benign_fp_k / float(top_k)

    # 5. Financial Breakdown for Top-K Queue (Monthly & Annualised)
    top_k_retail_zar = float(hunter_top_k["unbilled_retail_zar"].sum())
    top_k_marginal_cost_zar = float(hunter_top_k["marginal_network_cost_zar"].sum())
    top_k_rec_low_zar = float(hunter_top_k["est_recoverable_low_zar"].sum())
    top_k_rec_mid_zar = float(hunter_top_k["est_recoverable_mid_zar"].sum())
    top_k_rec_high_zar = float(hunter_top_k["est_recoverable_high_zar"].sum())

    # Full detected population (all flagged SIMs with priority_score > 0)
    flagged_df = df[df["priority_score"] > 0]
    all_retail_zar = float(flagged_df["unbilled_retail_zar"].sum())
    all_marginal_cost_zar = float(flagged_df["marginal_network_cost_zar"].sum())
    all_rec_low_zar = float(flagged_df["est_recoverable_low_zar"].sum())
    all_rec_mid_zar = float(flagged_df["est_recoverable_mid_zar"].sum())
    all_rec_high_zar = float(flagged_df["est_recoverable_high_zar"].sum())

    # Curve across K = 10..300 for charting
    k_values = list(range(10, min(350, len(df)), 10))
    curve_rows = []
    hunter_sorted = df.sort_values("hunter_rank")
    naive_sorted = df.sort_values("naive_rank")

    for k in k_values:
        h_slice = hunter_sorted.head(k)
        n_slice = naive_sorted.head(k)
        curve_rows.append({
            "K": k,
            "Hunter_Precision": h_slice["is_true_leakage"].mean(),
            "Naive_Precision": n_slice["is_true_leakage"].mean(),
            "Hunter_Recall": h_slice["is_true_leakage"].sum() / max(1, total_true_leakage_sims),
            "Naive_Recall": n_slice["is_true_leakage"].sum() / max(1, total_true_leakage_sims),
            "Hunter_Value_Share": h_slice.loc[h_slice["is_true_leakage"], "total_retail_equiv_zar"].sum()
            / max(1.0, total_true_leakage_retail_zar),
            "Naive_Value_Share": n_slice.loc[n_slice["is_true_leakage"], "total_retail_equiv_zar"].sum()
            / max(1.0, total_true_leakage_retail_zar),
        })

    # Archetype-level breakdown
    archetype_summary = (
        df.groupby("ground_truth_label", as_index=False)
        .agg(
            total_sims=("sim_id", "count"),
            in_hunter_top_k=("hunter_rank", lambda s: int((s <= top_k).sum())),
            in_naive_top_k=("naive_rank", lambda s: int((s <= top_k).sum())),
            total_retail_zar=("total_retail_equiv_zar", "sum"),
            unbilled_retail_zar=("unbilled_retail_zar", "sum"),
            est_recoverable_mid_zar=("est_recoverable_mid_zar", "sum"),
        )
        .sort_values("unbilled_retail_zar", ascending=False)
    )

    return {
        "label": "Synthetic benchmark results — not validated production accuracy",
        "top_k": top_k,
        "total_sims": len(df),
        "total_true_leakage_sims": total_true_leakage_sims,
        "total_benign_lookalikes": total_benign_lookalikes,
        "total_true_leakage_retail_zar": round(total_true_leakage_retail_zar, 2),
        "hunter_precision_k": round(hunter_precision_k, 4),
        "naive_precision_k": round(naive_precision_k, 4),
        "hunter_recall_k": round(hunter_recall_k, 4),
        "naive_recall_k": round(naive_recall_k, 4),
        "hunter_value_share_k": round(hunter_value_share_k, 4),
        "naive_value_share_k": round(naive_value_share_k, 4),
        "hunter_benign_fp_k": hunter_benign_fp_k,
        "naive_benign_fp_k": naive_benign_fp_k,
        "hunter_benign_fpr_k": round(hunter_benign_fpr_k, 4),
        "naive_benign_fpr_k": round(naive_benign_fpr_k, 4),
        "financials": {
            "top_k_retail_zar": round(top_k_retail_zar, 2),
            "top_k_marginal_cost_zar": round(top_k_marginal_cost_zar, 2),
            "top_k_rec_low_zar": round(top_k_rec_low_zar, 2),
            "top_k_rec_mid_zar": round(top_k_rec_mid_zar, 2),
            "top_k_rec_high_zar": round(top_k_rec_high_zar, 2),
            "all_flagged_count": len(flagged_df),
            "all_retail_zar": round(all_retail_zar, 2),
            "all_marginal_cost_zar": round(all_marginal_cost_zar, 2),
            "all_rec_low_zar": round(all_rec_low_zar, 2),
            "all_rec_mid_zar": round(all_rec_mid_zar, 2),
            "all_rec_high_zar": round(all_rec_high_zar, 2),
        },
        "curves_df": pd.DataFrame(curve_rows),
        "archetype_summary_df": archetype_summary,
    }
