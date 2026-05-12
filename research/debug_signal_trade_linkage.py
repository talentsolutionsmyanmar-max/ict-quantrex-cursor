"""
Forensic Debug: Signal-Trade Linkage
Diagnoses why paper trades aren't linking to signal audits.
Read-only. Zero impact on live execution.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

AUDIT_FILE = Path("data/signal_audit.jsonl")
TRADE_FILE = Path("data/paper_trades_fallback.jsonl")


def load_and_inspect():
    print("LOADING & INSPECTING DATA SCHEMAS...\n")

    # Load audit
    if not AUDIT_FILE.exists():
        print("signal_audit.jsonl not found")
        return None, None
    with open(AUDIT_FILE, encoding="utf-8") as f:
        audit_rows = [json.loads(l) for l in f if l.strip()]
    audit_df = pd.DataFrame(audit_rows)

    # Load trades
    if not TRADE_FILE.exists():
        print("paper_trades_fallback.jsonl not found")
        return audit_df, None
    with open(TRADE_FILE, encoding="utf-8") as f:
        trade_rows = [json.loads(l) for l in f if l.strip()]
    trade_df = pd.DataFrame(trade_rows)

    # Schema inspection
    print("SIGNAL AUDIT SCHEMA:")
    print(f"   Columns: {list(audit_df.columns)}")
    print(f"   Sample ts: {audit_df['ts'].iloc[0] if 'ts' in audit_df.columns and len(audit_df) else 'MISSING'}")
    print(f"   Sample symbol: {audit_df['symbol'].iloc[0] if 'symbol' in audit_df.columns and len(audit_df) else 'MISSING'}")
    print(f"   Total rows: {len(audit_df)}\n")

    print("PAPER TRADE SCHEMA:")
    print(f"   Columns: {list(trade_df.columns)}")
    ts_col = "timestamp" if "timestamp" in trade_df.columns else ("ts" if "ts" in trade_df.columns else "MISSING")
    print(f"   Sample {ts_col}: {trade_df[ts_col].iloc[0] if ts_col != 'MISSING' and len(trade_df) else 'N/A'}")
    sym_col = "symbol" if "symbol" in trade_df.columns else "MISSING"
    print(f"   Sample symbol: {trade_df[sym_col].iloc[0] if sym_col != 'MISSING' and len(trade_df) else 'N/A'}")
    print(f"   Total rows: {len(trade_df)}\n")

    # Standardize timestamps for comparison
    def parse_ts(df, col_name):
        if col_name not in df.columns:
            return pd.Series([None] * len(df))
        return pd.to_datetime(df[col_name], errors="coerce", utc=True)

    audit_df["ts_parsed"] = parse_ts(audit_df, "ts")
    trade_df["ts_parsed"] = parse_ts(trade_df, "timestamp" if "timestamp" in trade_df.columns else "ts")

    # Standardize symbols
    def normalize_symbol(s):
        if pd.isna(s):
            return None
        s = str(s).upper().replace("/", "").replace(":", "").replace("USDT", "")
        return s + "USDT" if not s.endswith("USDT") else s

    if "symbol" in audit_df.columns:
        audit_df["symbol_norm"] = audit_df["symbol"].apply(normalize_symbol)
    if "symbol" in trade_df.columns:
        trade_df["symbol_norm"] = trade_df["symbol"].apply(normalize_symbol)

    # Attempt linkage with multiple tolerances
    print("ATTEMPTING LINKAGE WITH VARYING TOLERANCES...")
    for tol_h in [0.5, 1.0, 2.0, 4.0]:
        merged = pd.merge_asof(
            trade_df.sort_values("ts_parsed"),
            audit_df.sort_values("ts_parsed"),
            on="ts_parsed",
            by="symbol_norm" if "symbol_norm" in trade_df.columns and "symbol_norm" in audit_df.columns else None,
            direction="backward",
            tolerance=pd.Timedelta(hours=tol_h),
        )
        linked = merged.dropna(subset=["confluence"]).shape[0] if "confluence" in merged.columns else 0
        print(f"   Tolerance {tol_h}h: {linked}/{len(trade_df)} trades linked")

    # Show sample mismatch
    if len(trade_df) > 0 and len(audit_df) > 0:
        print("\nSAMPLE MISMATCH DIAGNOSIS:")
        t_sample = trade_df.iloc[0]
        a_sample = audit_df.iloc[0]
        print(f"   Trade ts: {t_sample.get('timestamp', t_sample.get('ts'))}")
        print(f"   Audit ts: {a_sample.get('ts')}")
        print(f"   Trade symbol: {t_sample.get('symbol')}")
        print(f"   Audit symbol: {a_sample.get('symbol')}")
        print(f"   Trade has confluence?: {'confluence' in t_sample}")
        print(f"   Audit has confluence?: {'confluence' in a_sample}")

    return audit_df, trade_df


if __name__ == "__main__":
    load_and_inspect()
