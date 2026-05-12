#!/usr/bin/env python3
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from research.meta_hypothesis_logger import init_db


def backfill_from_backtest(backtest_json_path: str, db_path: str = "data/quantrex_knowledge.db"):
    p = Path(backtest_json_path)
    if not p.exists():
        print("Backtest JSON not found.")
        return 0
    data = json.loads(p.read_text(encoding="utf-8"))
    per_symbol = (((data.get("result") or {}).get("per_symbol")) or {}) if isinstance(data, dict) else {}
    trades = []
    for sym, obj in per_symbol.items():
        for t in (obj or {}).get("trades") or []:
            if isinstance(t, dict):
                trades.append(
                    {
                        "timestamp": t.get("entry_time") or t.get("exit_time") or "2026-04-28T00:00:00Z",
                        "symbol": sym,
                        "regime": t.get("entry_regime_state") or "trend_up",
                        "entry_reason": t.get("entry_reason_text") or "FVG + OB retest",
                        "exit_reason": t.get("exit_type") or "TP_hit_or_trail",
                        "r_multiple": float(t.get("r_multiple") or 0.0),
                    }
                )
    init_db()
    conn = sqlite3.connect(db_path)
    n = min(len(trades), 50)
    for i, t in enumerate(trades[:n]):
        hypothesis_id = f"v2.3_backfill_{i}"
        conn.execute(
            """
            INSERT OR IGNORE INTO trade_postmortems
            (timestamp, symbol, regime, entry_reason, exit_reason, r_multiple, explanation_md, hypothesis_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t.get("timestamp", "2026-04-28T00:00:00Z"),
                t.get("symbol", "BTCUSDT"),
                t.get("regime", "trend_up"),
                t.get("entry_reason", "FVG + OB retest"),
                t.get("exit_reason", "TP_hit_or_trail"),
                float(t.get("r_multiple", 0.0)),
                f"Backfilled v2.3 test trade #{i}",
                hypothesis_id,
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO hypotheses (id, text, test_config, result_pf, result_expectancy, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                hypothesis_id,
                f"Validate v2.3 regime isolation on trade #{i}",
                "regime_isolation_v2.3.yaml",
                0.0,
                0.0,
                "QUEUED",
                "2026-04-28T00:00:00Z",
            ),
        )
    conn.commit()
    conn.close()
    print(f"Backfilled {n} trades into knowledge DB.")
    return n


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/backfill_knowledge_db.py <backtest_json_path>")
        raise SystemExit(2)
    backfill_from_backtest(sys.argv[1])
