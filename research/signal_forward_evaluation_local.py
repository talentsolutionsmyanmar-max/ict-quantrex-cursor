"""
Signal Forward Evaluation (Local Data Only)
Evaluates historical signal audit rows against subsequent price action
using ONLY locally collected DOM/CVD logs. Zero external API calls.
Read-only. Zero impact on live execution or config.
"""
import json
from pathlib import Path

import pandas as pd


def run_local_forward_evaluation():
    AUDIT_FILE = Path("data/signal_audit.jsonl")
    # Try multi-asset first, fall back to single-asset BTC
    DOM_FILES = [
        Path("data/dom_btc_audit.jsonl"),
        Path("data/dom_cvd_audit.jsonl"),
        Path("data/dom_BTCUSDT_audit.jsonl"),
    ]

    if not AUDIT_FILE.exists():
        print("No signal audit data found.")
        return

    # Find available DOM file
    dom_path = next((p for p in DOM_FILES if p.exists()), None)
    if not dom_path:
        print("No DOM price data found. Need dom_*_audit.jsonl for forward eval.")
        return

    print(f"LOADING SIGNAL AUDIT ({AUDIT_FILE.name})...")
    audits = pd.DataFrame([json.loads(l) for l in open(AUDIT_FILE, encoding="utf-8") if l.strip()])
    audits["ts"] = pd.to_datetime(audits["ts"], utc=True, errors="coerce")
    audits = audits.dropna(subset=["ts"])
    print(f"   Loaded {len(audits)} signal evaluations.")

    print(f"LOADING DOM PRICE DATA ({dom_path.name})...")
    dom_data = pd.DataFrame([json.loads(l) for l in open(dom_path, encoding="utf-8") if l.strip()])
    dom_data["ts"] = pd.to_datetime(dom_data["ts"], utc=True, errors="coerce")
    dom_data = dom_data.dropna(subset=["ts"])

    # Keep only BTC rows if multi-asset
    if "symbol" in dom_data.columns and dom_data["symbol"].nunique() > 1:
        dom_data = dom_data[dom_data["symbol"].astype(str).str.contains("BTC", case=False, na=False)]
        print(f"   Filtered to BTC: {len(dom_data)} rows")

    # Ensure price column exists
    price_col = "price" if "price" in dom_data.columns else ("mid_price" if "mid_price" in dom_data.columns else None)
    if not price_col:
        print("DOM data missing price column. Expected 'price' or 'mid_price'.")
        return

    print(f"   Loaded {len(dom_data)} DOM snapshots with price data.")

    # Merge signals to nearest prior DOM snapshot (15m alignment)
    print("ALIGNING SIGNALS TO DOM PRICE SERIES...")
    dom_for_merge = dom_data[["ts", price_col]].rename(columns={price_col: "dom_price"}).sort_values("ts")
    merged = pd.merge_asof(
        audits.sort_values("ts"),
        dom_for_merge,
        on="ts",
        direction="backward",
        tolerance=pd.Timedelta(minutes=20),  # Allow slight clock drift
    )
    merged = merged.dropna(subset=["dom_price"]).rename(columns={"dom_price": "entry_price"})

    if len(merged) < 10:
        print(f"Only {len(merged)} signals aligned. Need more overlap for stats.")
        return

    print(f"Aligned {len(merged)} signals to DOM price series.")

    # Calculate forward returns using DOM price series
    # Forward windows: 1h (4x15m), 2h (8x), 4h (16x)
    merged = merged.sort_values("ts").reset_index(drop=True)

    for fwd_candles in [4, 8, 16]:  # 1h, 2h, 4h
        fwd_col = f"fwd_{fwd_candles//4}h_R"
        # Future price N candles ahead
        merged[fwd_col] = (merged["entry_price"].shift(-fwd_candles) - merged["entry_price"]) / merged["entry_price"].abs()
        # Normalize by typical 15m move (~0.15% for BTC) as R proxy
        typical_move = merged["entry_price"].pct_change().abs().median() * 4  # 1h typical move
        if typical_move and typical_move > 0:
            merged[fwd_col] = merged[fwd_col] / typical_move

    # Clean forward NaNs (last N signals have no future data)
    merged = merged.dropna(subset=["fwd_1h_R"])

    print(f"\nEVALUATING {len(merged)} SIGNALS WITH FORWARD LOCAL DATA...")
    print("=" * 95)
    print(f"{'CONFLUENCE':<12} | {'REGIME':<12} | {'N':>4} | {'1h FWD R':>9} | {'2h FWD R':>9} | {'4h FWD R':>9} | {'WIN RATE':>9}")
    print("-" * 95)

    results = []
    for conf, grp_df in merged.groupby("confluence"):
        for reg, reg_df in grp_df.groupby("regime"):
            if len(reg_df) < 3:
                continue
            n = len(reg_df)
            r1 = reg_df["fwd_1h_R"].mean()
            r2 = reg_df["fwd_2h_R"].mean()
            r4 = reg_df["fwd_4h_R"].mean()
            wr = (reg_df["fwd_1h_R"] > 0).mean()

            print(f"{str(conf):<12} | {str(reg):<12} | {n:>4} | {r1:+.3f}R     | {r2:+.3f}R     | {r4:+.3f}R     | {wr:.0%}")
            results.append(
                {
                    "confluence": int(conf),
                    "regime": reg,
                    "n": n,
                    "fwd_1h_R": round(r1, 3),
                    "fwd_2h_R": round(r2, 3),
                    "fwd_4h_R": round(r4, 3),
                    "win_rate_1h": round(wr, 3),
                }
            )

    print("=" * 95)
    print("INTERPRETATION GUIDE:")
    print("   STRONG: Forward R > +0.30 at 2h/4h with WR > 55%")
    print("   NEUTRAL: Forward R between -0.10 and +0.10, WR ~50%")
    print("   TOXIC: Forward R < -0.20 or WR < 40% (inverts on entry)")
    print("\nIf high confluence shows positive forward R in YOUR data, your edge is real.")

    # Save results
    Path("reports/signal_forward_eval_local.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("Results saved to reports/signal_forward_eval_local.json")

    return results


if __name__ == "__main__":
    run_local_forward_evaluation()
