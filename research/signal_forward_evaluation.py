"""
Signal Forward Evaluation (Ablation Proxy)
Evaluates historical signal audit rows against subsequent 15m price action.
Bypasses trade logs. Tests pure predictive power of confluence/strength/regime.
Read-only. Zero impact on live execution or config.
"""
import json
from datetime import timedelta
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd


def run_forward_evaluation():
    AUDIT_FILE = Path("data/signal_audit.jsonl")
    if not AUDIT_FILE.exists():
        print("No signal audit data found.")
        return

    print("LOADING SIGNAL AUDIT...")
    audits = pd.DataFrame([json.loads(l) for l in open(AUDIT_FILE, encoding="utf-8") if l.strip()])
    audits["ts"] = pd.to_datetime(audits["ts"], utc=True, errors="coerce")
    audits = audits.dropna(subset=["ts"]).sort_values("ts")
    print(f"   Loaded {len(audits)} signal evaluations.")

    # Fetch 15m candles covering audit window + buffer
    print("FETCHING HISTORICAL 15M PRICE ACTION (Binance)...")
    ex = ccxt.binance({"enableRateLimit": True})
    since_ts = int((audits["ts"].min() - timedelta(hours=2)).timestamp() * 1000)
    end_ts = int((audits["ts"].max() + timedelta(hours=4)).timestamp() * 1000)

    candles = []
    current = since_ts
    while current < end_ts:
        chunk = ex.fetch_ohlcv("BTC/USDT", "15m", since=current, limit=1000)
        if not chunk:
            break
        candles.extend(chunk)
        current = chunk[-1][0] + 1

    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp")
    print(f"   Fetched {len(df)} candles. Aligning to signals...")

    # Merge audits to nearest prior candle close
    merged = pd.merge_asof(audits.sort_values("ts"), df, left_on="ts", right_on="timestamp", direction="backward")

    # Calculate forward performance
    # 1R proxy: 15m ATR (simplified) or fixed 0.15% for micro-moves
    merged["atr"] = (merged["high"] - merged["low"]).rolling(14).mean().fillna(0.001)

    # Forward returns: 1h (4 candles), 2h (8 candles), 4h (16 candles)
    for fwd in [4, 8, 16]:
        merged[f"fwd_{fwd//4}h"] = (merged["close"].shift(-fwd) - merged["close"]) / merged["atr"]

    # Clean forward NaNs (last few signals have no future data)
    merged = merged.dropna(subset=["fwd_1h"])

    print(f"\nEVALUATING {len(merged)} SIGNALS WITH FORWARD DATA...")
    print("=" * 85)
    print(f"{'CONFLUENCE':<12} | {'REGIME':<12} | {'N':>4} | {'1h FWD R':>8} | {'2h FWD R':>8} | {'4h FWD R':>8} | {'WIN RATE':>8}")
    print("-" * 85)

    for conf, grp_df in merged.groupby("confluence"):
        for reg, reg_df in grp_df.groupby("regime"):
            if len(reg_df) < 3:
                continue  # Ignore tiny samples
            n = len(reg_df)
            r1 = reg_df["fwd_1h"].mean()
            r2 = reg_df["fwd_2h"].mean()
            r4 = reg_df["fwd_4h"].mean()
            wr = (reg_df["fwd_1h"] > 0).mean()
            print(f"{str(conf):<12} | {str(reg):<12} | {n:>4} | {r1:+.3f}R    | {r2:+.3f}R    | {r4:+.3f}R    | {wr:.0%}")

    print("=" * 85)
    print("INTERPRETATION GUIDE:")
    print("   STRONG: Forward R > +0.30 at 2h/4h with WR > 55%")
    print("   NEUTRAL: Forward R between -0.10 and +0.10, WR ~50%")
    print("   TOXIC: Forward R < -0.20 or WR < 40% (inverts on entry)")
    print("\nIf high confluence/strength shows positive forward R, your signal engine has real predictive power.")

    Path("reports/signal_forward_eval.json").write_text(merged.to_json(orient="records", indent=2), encoding="utf-8")
    print("Raw evaluation saved to reports/signal_forward_eval.json")


if __name__ == "__main__":
    run_forward_evaluation()
