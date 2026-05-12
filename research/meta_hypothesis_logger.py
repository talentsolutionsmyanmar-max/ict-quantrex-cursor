#!/usr/bin/env python3
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

DB = Path("data/quantrex_knowledge.db")


def init_db():
    DB.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS trade_postmortems (
            id INTEGER PRIMARY KEY, timestamp TEXT, symbol TEXT, regime TEXT,
            entry_reason TEXT, exit_reason TEXT, r_multiple REAL,
            explanation_md TEXT, hypothesis_id TEXT
        );
        CREATE TABLE IF NOT EXISTS hypotheses (
            id TEXT PRIMARY KEY, text TEXT, test_config TEXT,
            result_pf REAL, result_expectancy REAL, status TEXT, created_at TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def log_trade_postmortem(trade: dict, explanation: str, hypothesis_id: str):
    init_db()
    conn = sqlite3.connect(DB)
    conn.execute(
        """
        INSERT INTO trade_postmortems
        (timestamp, symbol, regime, entry_reason, exit_reason, r_multiple, explanation_md, hypothesis_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(trade.get("timestamp") or datetime.now(timezone.utc).isoformat()),
            str(trade.get("symbol") or ""),
            str(trade.get("regime") or ""),
            str(trade.get("entry_reason") or ""),
            str(trade.get("exit_reason") or ""),
            float(trade.get("r_multiple") or 0.0),
            str(explanation),
            str(hypothesis_id),
        ),
    )
    conn.commit()
    conn.close()


def generate_auto_hypothesis(trade: Dict) -> dict:
    """Auto-generate research hypotheses from trade outcomes."""
    init_db()
    hyp_id = f"hyp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{str(trade.get('symbol','UNK'))[:3]}"
    regime = str(trade.get("regime") or "")
    pnl = float(trade.get("pnl") or 0.0)
    r_multiple = float(trade.get("r_multiple") or 0.0)
    if pnl < 0 and regime == "chop":
        text = "Chop regime losses exceed threshold. Require OB alignment + volume spike."
    elif r_multiple < 1.5 and regime in ["trend_up", "trend_down"]:
        text = "Trend winners capped too early. Remove TP1, trail earlier."
    else:
        text = f"Review {trade.get('symbol')} {regime} entry confluence."

    hypothesis = {
        "id": hyp_id,
        "text": text,
        "test_config": "pending",
        "result_pf": 0.0,
        "result_expectancy": 0.0,
        "status": "QUEUED",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    conn = sqlite3.connect(DB)
    conn.execute(
        "INSERT OR IGNORE INTO hypotheses VALUES (?,?,?,?,?,?,?)",
        (
            hyp_id,
            text,
            "pending",
            0.0,
            0.0,
            "QUEUED",
            hypothesis["created_at"],
        ),
    )
    conn.commit()
    conn.close()
    return hypothesis


if __name__ == "__main__":
    init_db()
    print(str(DB))
