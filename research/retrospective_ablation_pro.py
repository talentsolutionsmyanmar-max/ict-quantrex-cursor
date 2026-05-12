"""
Retrospective Feature Ablation (Pro)
Statistically rigorous evaluation of ICT/DOM feature predictive power.
Read-only. Zero impact on live execution or config.
"""
import json
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

AUDIT_FILE = Path("data/signal_audit.jsonl")
TRADE_FILE = Path("data/paper_trades_fallback.jsonl")


def load_data():
    if not AUDIT_FILE.exists():
        raise FileNotFoundError("ERROR: data/signal_audit.jsonl not found. Wait for data accumulation.")
    if not TRADE_FILE.exists():
        raise FileNotFoundError("ERROR: data/paper_trades_fallback.jsonl not found. Need paper trades to compute R.")

    audit = pd.DataFrame([json.loads(l) for l in open(AUDIT_FILE, encoding="utf-8") if l.strip()])
    trades = pd.DataFrame([json.loads(l) for l in open(TRADE_FILE, encoding="utf-8") if l.strip()])

    # Standardize timestamps
    audit["ts_std"] = pd.to_datetime(audit.get("ts", audit.get("timestamp")))
    trades["ts_std"] = pd.to_datetime(trades.get("timestamp", trades.get("ts")))
    audit.rename(columns={"signal_strength": "strength"}, inplace=True)

    return audit.sort_values("ts_std"), trades.sort_values("ts_std")


def merge_signals_trades(audit, trades, tolerance_h=1.0):
    """Link trades to signals using time proximity. Warns about limitations."""
    print(f"WARNING: Merging trades to signals using {tolerance_h}h time tolerance.")
    print("   -> Without unique signal_id, ~5-10% misalignment is possible in fast regimes.")

    trade_cols = [c for c in ["ts_std", "symbol", "r_multiple", "exit_reason"] if c in trades.columns]
    audit_base_cols = ["ts_std", "symbol", "regime", "decision", "strength", "confluence", "dom_imbalance"]
    audit_cols = [c for c in audit_base_cols if c in audit.columns]
    merged = pd.merge_asof(
        trades[trade_cols],
        audit[audit_cols],
        on="ts_std",
        by="symbol",
        direction="backward",
        tolerance=pd.Timedelta(hours=tolerance_h),
    )
    return merged.dropna(subset=["r_multiple", "confluence"])  # Only keep matched trades


def run_pro_ablation():
    print("LOADING DATA...")
    audit, trades = load_data()
    merged = merge_signals_trades(audit, trades)
    print(f"Successfully linked {len(merged)} trades to signals.\n")

    if len(merged) < 15:
        print("WARNING: Sample too small for statistical significance. Wait for 30+ linked trades.")
        return

    # Define features to test (auto-detect confidence fields if present)
    base_features = ["confluence", "strength"]
    dom_features = ["dom_imbalance"] if "dom_imbalance" in merged.columns else []
    conf_features = [c for c in merged.columns if c.endswith("_confidence")]

    features_to_test = base_features + dom_features + conf_features
    results = []

    print("FORENSIC FEATURE ABLATION (Spearman rho | Cohen's d | p-value | dR)")
    print("=" * 75)

    for feat in features_to_test:
        if feat not in merged.columns or merged[feat].nunique() < 2:
            continue

        # 1. Spearman Rank Correlation (robust to non-normality/outliers)
        corr, p_corr = stats.spearmanr(merged[feat], merged["r_multiple"])

        # 2. Median Split T-Test (Effect Direction)
        median_val = merged[feat].median()
        high = merged[merged[feat] >= median_val]["r_multiple"]
        low = merged[merged[feat] < median_val]["r_multiple"]
        delta_r = high.mean() - low.mean()
        _t_stat, t_p = stats.ttest_ind(high, low, equal_var=False, nan_policy="omit")

        # 3. Effect Size (Cohen's d)
        pooled_std = np.sqrt(
            ((len(high) - 1) * high.std() ** 2 + (len(low) - 1) * low.std() ** 2)
            / (len(high) + len(low) - 2)
        )
        cohens_d = delta_r / pooled_std if pooled_std > 0 else 0.0

        # 4. Baseline Shuffle Test (Is this just noise?)
        shuffle_p = 1.0
        try:
            perm_stats = stats.permutation_test(
                (merged[feat].values, merged["r_multiple"].values),
                lambda x, y: np.abs(stats.spearmanr(x, y)[0]),
                alternative="greater",
                n_resamples=1000,
            )
            shuffle_p = perm_stats.pvalue
        except Exception:
            pass

        results.append(
            {
                "feature": feat,
                "spearman_rho": round(corr, 3),
                "p_value_spearman": round(p_corr, 4),
                "delta_R": round(delta_r, 3),
                "t_p_value": round(t_p, 4),
                "cohens_d": round(cohens_d, 3),
                "shuffle_p": round(shuffle_p, 4),
            }
        )

        print(
            f"{feat:20s} | ρ: {corr:+.3f} (p={p_corr:.3f}) | ΔR: {delta_r:+.3f} "
            f"| t-p={t_p:.3f} | d={cohens_d:.3f} | shuffle-p={shuffle_p:.3f}"
        )

    print("=" * 75)
    print("INTERPRETATION GUIDE:")
    print("   KEEP:     rho > +0.20, p < 0.05, Cohen's d > 0.30, dR > +0.10")
    print("   MONITOR:  0.05 < |rho| < 0.20, p > 0.05 (needs more data)")
    print("   RETIRE:   rho ~= 0.00 or negative, shuffle-p > 0.10 (indistinguishable from noise)")

    # Save for audit
    Path("reports/ablation_pro_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("\nResults saved to reports/ablation_pro_results.json")
    return results


if __name__ == "__main__":
    try:
        run_pro_ablation()
    except Exception as e:
        print(f"Ablation failed: {e}")
