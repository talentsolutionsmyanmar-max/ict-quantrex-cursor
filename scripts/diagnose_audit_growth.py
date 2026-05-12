#!/usr/bin/env python3
"""
v2.7.5 — Read-only signal_audit vs dom_cvd growth diagnostic (Golden Lock safe).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

AUDIT_LOG = ROOT / "data" / "signal_audit.jsonl"
DOM_LOG = ROOT / "data" / "dom_cvd_audit.jsonl"


def diagnose_growth():
    print("AUDIT GROWTH DIAGNOSTIC")
    print("=" * 50)

    if not AUDIT_LOG.exists():
        print("signal_audit.jsonl not found")
        return

    audit_rows = []
    for line in AUDIT_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                audit_rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not audit_rows:
        print("signal_audit.jsonl is empty")
        return

    audit_df = pd.DataFrame(audit_rows)
    audit_df["ts"] = pd.to_datetime(audit_df["ts"], utc=True, errors="coerce")
    audit_df = audit_df.dropna(subset=["ts"])

    if not DOM_LOG.exists():
        print("dom_cvd_audit.jsonl not found")
        return

    dom_rows = []
    for line in DOM_LOG.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                dom_rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    if not dom_rows:
        print("dom_cvd_audit.jsonl is empty")
        return

    dom_df = pd.DataFrame(dom_rows)
    dom_df["ts"] = pd.to_datetime(dom_df["ts"], utc=True, errors="coerce")
    dom_df = dom_df.dropna(subset=["ts"]).sort_values("ts")

    audit_span = (audit_df["ts"].max() - audit_df["ts"].min()).total_seconds() / 3600
    dom_span = (dom_df["ts"].max() - dom_df["ts"].min()).total_seconds() / 3600

    audit_rate = len(audit_df) / max(audit_span, 0.1)
    dom_rate = len(dom_df) / max(dom_span, 0.1)

    print("SIGNAL AUDIT")
    print(f"   Total rows: {len(audit_df)}")
    print(f"   Time span: {audit_span:.1f} hours")
    print(f"   Growth rate: {audit_rate:.2f} rows/hour (target: ~4.0 for 15m candles)")
    print(f"   Expected rows for 168h (7d): ~672")
    print()

    print("DOM/CVD AUDIT")
    print(f"   Total rows: {len(dom_df)}")
    print(f"   Time span: {dom_span:.1f} hours")
    print(f"   Growth rate: {dom_rate:.2f} rows/hour (target: 4.0 for 15m interval)")
    now = datetime.now(timezone.utc)
    cut1h = now - timedelta(hours=1)
    dom_1h = dom_df[dom_df["ts"] >= cut1h]
    aud_1h = audit_df[audit_df["ts"] >= cut1h]
    print(f"   Rolling 1h (wall clock UTC): DOM rows={len(dom_1h)}, signal rows={len(aud_1h)} (targets ~4 each)")
    print()

    if audit_rate < 2.0:
        print("SIGNAL AUDIT GROWTH TOO SLOW")
        print("   Possible causes:")
        print("   - Paper loop not running continuously")
        print("   - No new 15m candle boundaries detected (feed/check_new_candle)")
        print("   - Audit writer errors (check terminal running paper_trader)")
        print("   - Long gaps in wall-clock time (machine sleep, process stopped)")
    elif audit_rate > 6.0:
        print("SIGNAL AUDIT GROWTH TOO FAST")
        print("   - Audit may be logging on every price poll (should be candle-close only)")
    else:
        print("Signal audit growth rate looks healthy (~4 rows/hour)")

    gap_sec = None
    if len(dom_df) >= 2:
        t_last = dom_df["ts"].iloc[-1]
        t_prev = dom_df["ts"].iloc[-2]
        gap_sec = (t_last - t_prev).total_seconds()
        print(f"   Last two DOM snapshots: {gap_sec:.0f}s apart (target 900s for 15m production)")
    if dom_rate > 20:
        if gap_sec is not None and 700 <= gap_sec <= 1100:
            print("Recent DOM spacing matches 15m production; full-file rate still inflated by older fast snapshots.")
        elif len(dom_1h) <= 6:
            print("DOM full-history rate skewed by past fast cadence; check rolling 1h above for current pace.")
        else:
            print("DOM collector likely at test cadence (e.g. 2-min interval)")
            print("   Restart: python core/dom_cvd_collector.py --interval 900")
    elif dom_rate < 3:
        print("DOM collector slow or intermittent")

    print("\n" + "=" * 50)
    print("RECOMMENDATION")
    if audit_rate < 2.0:
        print("1. Verify paper_trader --live is running (wmic / tasklist)")
        print("2. Keep machine awake; avoid killing the paper process")
        print("3. Confirm Binance feed returns new 15m candles (no prolonged API errors)")
    if dom_rate > 20:
        print("4. Restart DOM collector with --interval 900")
    print("5. Re-run this diagnostic after 1h to confirm rates")


if __name__ == "__main__":
    diagnose_growth()
