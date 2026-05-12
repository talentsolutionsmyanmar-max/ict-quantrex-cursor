#!/usr/bin/env python3
import json
import sqlite3
import time
from pathlib import Path

import pandas as pd

DB = Path("data/signal_audit.db")
REPORT = Path("reports/daily_health.json")


def daily_health_check():
    if not DB.exists():
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "entries_24h": 0,
            "skips_24h": 0,
            "portfolio_pf": 0.0,
            "top_skip_reasons": {},
            "status": "FAIL",
            "reason": "no_audit_db",
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("No audit data yet. Ensure paper loop is running with v2.1.")
        return False

    conn = sqlite3.connect(DB)
    cols = pd.read_sql("PRAGMA table_info(signal_audit)", conn)
    names = set(cols["name"].tolist()) if not cols.empty else set()
    ts_col = "ts" if "ts" in names else ("timestamp" if "timestamp" in names else None)
    if ts_col is None:
        print("signal_audit schema missing ts/timestamp column")
        conn.close()
        return False
    df = pd.read_sql(
        f"""
        SELECT
          {ts_col} AS timestamp,
          decision,
          skip_reason,
          payload_json
        FROM signal_audit
        WHERE {ts_col} >= datetime('now', '-24 hours')
        """,
        conn,
    )
    conn.close()

    if df.empty:
        report = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "entries_24h": 0,
            "skips_24h": 0,
            "portfolio_pf": 0.0,
            "top_skip_reasons": {},
            "status": "FAIL",
            "reason": "zero_signals_24h",
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print("Zero signals in 24h. Check session gates / regime filters.")
        return False

    entries = df[df["decision"] == "ENTER"].copy()
    skips = df[df["decision"] == "SKIP"].copy()
    pnl_values = []
    for raw in entries.get("payload_json", []):
        try:
            obj = json.loads(raw) if isinstance(raw, str) else {}
            pnl_values.append(float(obj.get("pnl") or 0.0))
        except Exception:
            pnl_values.append(0.0)
    e = pd.DataFrame({"pnl": pnl_values})
    losses = e[e["pnl"] <= 0]
    pf = (
        e[e["pnl"] > 0]["pnl"].sum() / abs(losses["pnl"].sum())
        if len(losses) > 0 and abs(losses["pnl"].sum()) > 0
        else 999
    )

    report = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entries_24h": int(len(entries)),
        "skips_24h": int(len(skips)),
        "portfolio_pf": round(float(pf), 3),
        "top_skip_reasons": skips["skip_reason"].value_counts().head(3).to_dict(),
        "status": "PASS" if pf >= 1.0 else "FAIL",
        "atas_bridge_status": "unknown",
    }

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if report["status"] == "FAIL":
        print("PF < 1.0 triggered. Auto-revert hook placeholder.")
        return False
    print(f"Health check passed. PF: {pf}, Entries: {len(entries)}")
    return True


if __name__ == "__main__":
    raise SystemExit(0 if daily_health_check() else 2)
