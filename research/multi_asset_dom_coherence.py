"""
v2.8.0 Hypothesis #1: multi-asset DOM coherence (read-only research).
Reads data/dom_*_audit.jsonl, writes research/multi_asset_dom_coherence_report.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

REPORT_DIR = BASE / "research"
REPORT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_JSON = REPORT_DIR / "multi_asset_dom_coherence_report.json"

DOM_FILES = {
    "BTCUSDT": BASE / "data" / "dom_btc_audit.jsonl",
    "ETHUSDT": BASE / "data" / "dom_eth_audit.jsonl",
    "SOLUSDT": BASE / "data" / "dom_sol_audit.jsonl",
}
COL_MAP = {"BTCUSDT": "btc_imb", "ETHUSDT": "eth_imb", "SOLUSDT": "sol_imb"}


try:
    from statsmodels.tsa.stattools import grangercausalitytests

    _HAS_SM = True
except ImportError:
    _HAS_SM = False


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return rows


def load_multi_dom() -> pd.DataFrame:
    dfs = []
    for sym, path in DOM_FILES.items():
        raw = _load_jsonl(path)
        if not raw:
            continue
        df = pd.DataFrame(raw)
        if df.empty or "ts" not in df.columns:
            continue
        df["ts"] = pd.to_datetime(df["ts"], utc=True, errors="coerce")
        df = df.dropna(subset=["ts"])
        df["symbol"] = sym
        if "imbalance" not in df.columns:
            continue
        dfs.append(df[["ts", "symbol", "imbalance", "cvd_delta"]].copy())
    if not dfs:
        return pd.DataFrame()
    return pd.concat(dfs, ignore_index=True).sort_values("ts")


def wide_merge_asof(long_df: pd.DataFrame, tolerance_sec: int = 120) -> pd.DataFrame:
    """
    Align imbalances on raw snapshot timestamps (nearest within tolerance).
    Use for short test runs; for long runs you can also resample to 15m after this.
    """
    if long_df.empty:
        return pd.DataFrame()
    tol = pd.Timedelta(seconds=tolerance_sec)

    def branch(sym: str) -> pd.DataFrame | None:
        sub = long_df[long_df["symbol"] == sym][["ts", "imbalance"]].sort_values("ts")
        if sub.empty:
            return None
        sub = sub.rename(columns={"imbalance": COL_MAP[sym]})
        return sub.drop_duplicates(subset=["ts"], keep="last")

    b = branch("BTCUSDT")
    if b is None:
        return pd.DataFrame()
    wide = b.copy()
    for sym in ("ETHUSDT", "SOLUSDT"):
        o = branch(sym)
        if o is None:
            continue
        wide = pd.merge_asof(
            wide.sort_values("ts"),
            o.sort_values("ts"),
            on="ts",
            direction="nearest",
            tolerance=tol,
        )
    imb_cols = [c for c in wide.columns if c.endswith("_imb")]
    if len(imb_cols) < 2:
        return pd.DataFrame()
    return wide.dropna(subset=imb_cols, how="any")


def engineer_coherence_features(wide: pd.DataFrame) -> pd.DataFrame:
    if wide.empty:
        return wide
    pivot = wide.copy()
    n = len(pivot)
    imb_cols = [c for c in pivot.columns if c.endswith("_imb")]
    max_lag = 3 if n > 80 else (2 if n > 35 else 1)
    roll_w = min(8, max(3, max(n // 5, 3)))
    roll_min = min(4, max(2, n // 8))
    for c in imb_cols:
        for lag in range(1, max_lag + 1):
            pivot[f"{c}_lag{lag}"] = pivot[c].shift(lag)
        pivot[f"{c}_slope"] = pivot[c].diff()
    for i in range(len(imb_cols)):
        for j in range(i + 1, len(imb_cols)):
            a, b = imb_cols[i], imb_cols[j]
            pivot[f"{a}_{b}_diff"] = pivot[a] - pivot[b]
            pivot[f"{a}_{b}_roll_corr"] = pivot[a].rolling(roll_w, min_periods=roll_min).corr(pivot[b])
    return pivot.dropna()


def min_granger_p(
    data: pd.DataFrame, y_col: str, x_col: str, maxlag: int = 3
) -> float | None:
    if not _HAS_SM:
        return None
    sub = data[[y_col, x_col]].dropna()
    if len(sub) < maxlag + 10:
        return None
    try:
        g = grangercausalitytests(sub.values, maxlag=maxlag, verbose=False)
        pvals = []
        for lag in range(1, maxlag + 1):
            if lag in g and g[lag][0].get("ssr_ftest") is not None:
                pvals.append(float(g[lag][0]["ssr_ftest"][1]))
        return min(pvals) if pvals else None
    except Exception:
        return None


def run_negative_control(data: pd.DataFrame, cols: list[str], seed: int = 42) -> pd.DataFrame:
    ctrl = data[cols].copy()
    rng = np.random.default_rng(seed)
    for c in cols:
        ctrl[c] = rng.permutation(ctrl[c].values)
    return ctrl


def walk_forward_granger(
    pivot: pd.DataFrame,
    y_col: str,
    x_col: str,
    window: int | None = None,
    step: int | None = None,
    maxlag: int = 3,
) -> list[dict] | dict:
    if not _HAS_SM:
        return {"status": "STATSMODELS_MISSING", "hint": "pip install statsmodels"}
    cols = [c for c in (y_col, x_col) if c in pivot.columns]
    if len(cols) < 2:
        return {"status": "MISSING_COLUMNS", "need": [y_col, x_col]}

    sub = pivot[[y_col, x_col]].dropna()
    n = len(sub)
    if window is None:
        window = min(500, max(120, n // 2))
    if step is None:
        step = max(50, window // 4)
    need = window + maxlag * 3 + 10
    if n < need:
        return {
            "status": "INSUFFICIENT_DATA",
            "bars_available": n,
            "bars_recommended": need,
            "window_used": window,
        }

    results = []
    for start in range(0, n - window, step):
        train = sub.iloc[start : start + window]
        if len(train) < window:
            break
        p = min_granger_p(train, y_col, x_col, maxlag=maxlag)
        if p is None:
            continue
        results.append(
            {
                "window_end_ts": str(train.index[-1]),
                "granger_p_min": p,
                "significant": p < 0.05,
            }
        )
    return results


def run_research():
    print("Loading multi-asset DOM (dom_btc/eth/sol_audit.jsonl)...")
    df = load_multi_dom()
    report: dict = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_rows_long": int(len(df)),
        "rows_by_symbol": df.groupby("symbol").size().to_dict() if not df.empty else {},
        "statsmodels_available": _HAS_SM,
    }

    if df.empty or len(df) < 50:
        report["status"] = "NO_DATA"
        report["message"] = "No multi-asset DOM files or too few rows. Run core/dom_collector_multi.py."
        REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print(f"Insufficient data ({len(df)} rows). Report: {REPORT_JSON}")
        return report

    wide = wide_merge_asof(df)
    report["wide_aligned_rows"] = int(len(wide))

    if wide.empty or len(wide) < 8:
        report["status"] = "INSUFFICIENT_ALIGNED"
        report["message"] = "Need overlapping aligned rows (BTC/ETH/SOL within tolerance) for at least 2 assets."
        REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
        print("Insufficient aligned wide panel. Continue accumulation.")
        return report

    feat = engineer_coherence_features(wide)
    report["aligned_feature_rows"] = int(len(feat))
    print(f"Wide aligned rows: {len(wide)} | Feature rows (after lags): {len(feat)}")

    imb_cols = [c for c in ["btc_imb", "eth_imb", "sol_imb"] if c in feat.columns]
    report["imbalance_columns_present"] = imb_cols
    maxlag_nc = 2 if len(feat) < 40 else 3

    # --- Negative control (shuffle) ---
    if len(imb_cols) >= 2 and _HAS_SM:
        ctrl = run_negative_control(feat, imb_cols[:3] if len(imb_cols) >= 3 else imb_cols)
        p_real = (
            min_granger_p(feat, "eth_imb", "btc_imb", maxlag=maxlag_nc)
            if "eth_imb" in feat.columns and "btc_imb" in feat.columns
            else None
        )
        p_ctrl = None
        if "eth_imb" in ctrl.columns and "btc_imb" in ctrl.columns:
            ctrl_btc_eth = ctrl[["eth_imb", "btc_imb"]].dropna()
            if len(ctrl_btc_eth) > 10:
                p_ctrl = min_granger_p(
                    ctrl_btc_eth, "eth_imb", "btc_imb", maxlag=maxlag_nc
                )
        report["negative_control"] = {
            "granger_p_btc_to_eth_observed": p_real,
            "granger_p_btc_to_eth_shuffled": p_ctrl,
            "interpretation": "Expect shuffled p not systematically < 0.05 if methodology sound",
        }

    # --- Walk-forward Granger BTC -> ETH ---
    gw = walk_forward_granger(feat, "eth_imb", "btc_imb", window=None, step=None)
    report["walk_forward_granger_btc_eth"] = gw
    if isinstance(gw, dict) and gw.get("status") == "INSUFFICIENT_DATA" and _HAS_SM:
        p_full = min_granger_p(feat, "eth_imb", "btc_imb", maxlag=maxlag_nc)
        report["full_sample_granger_btc_eth_p_min"] = p_full
    if isinstance(gw, list) and gw:
        sig = sum(1 for r in gw if r.get("significant"))
        report["walk_forward_granger_summary"] = {
            "windows": len(gw),
            "significant_share": sig / len(gw),
        }
        print(
            "Walk-forward Granger (BTC -> ETH):",
            len(gw),
            "windows; significant share:",
            f"{sig / len(gw):.1%}",
        )
    else:
        print("Walk-forward / Granger:", json.dumps(gw, default=str)[:400])

    if len(df) < 500:
        report["status"] = "ACCUMULATE"
        report["hint"] = "Grok guideline: prefer 500+ long-format rows (~15k+ aligned for robust multi-window)."
    else:
        report["status"] = "OK_PARTIAL" if isinstance(gw, dict) and gw.get("status") else "OK"

    REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"Research report saved: {REPORT_JSON}")
    return report


if __name__ == "__main__":
    run_research()
