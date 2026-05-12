"""Signal-level audit logging and daily health checks (local SQLite)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

DB_PATH = Path(__file__).resolve().parents[1] / "data" / "signal_audit.db"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signal_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            symbol TEXT NOT NULL,
            regime TEXT,
            signal_strength REAL,
            confluence REAL,
            fvg_ok INTEGER,
            corr_ok INTEGER,
            decision TEXT NOT NULL,
            skip_reason TEXT,
            payload_json TEXT
        )
        """
    )
    conn.commit()
    return conn


def log_signal_decision(decision_data: Dict[str, Any]) -> None:
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO signal_audit
            (ts, symbol, regime, signal_strength, confluence, fvg_ok, corr_ok, decision, skip_reason, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(decision_data.get("ts") or _utc_now()),
                str(decision_data.get("symbol") or ""),
                str(decision_data.get("regime") or ""),
                float(decision_data.get("strength") or 0.0),
                float(decision_data.get("confluence") or 0.0),
                1 if bool(decision_data.get("fvg")) else 0,
                1 if bool(decision_data.get("corr_ok", True)) else 0,
                str(decision_data.get("decision") or "UNKNOWN"),
                str(decision_data.get("skip_reason") or ""),
                json.dumps(decision_data, ensure_ascii=False),
            ),
        )
        conn.commit()
    finally:
        conn.close()


@dataclass
class DailyMetrics:
    entries: int
    signals: int
    skips: int
    skip_top: List[Dict[str, Any]]


def _daily_metrics(since_utc: datetime) -> DailyMetrics:
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM signal_audit WHERE ts >= ?", (since_utc.isoformat(),))
        signals = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM signal_audit WHERE ts >= ? AND decision = 'ENTER'",
            (since_utc.isoformat(),),
        )
        entries = int(cur.fetchone()[0] or 0)
        cur.execute(
            "SELECT COUNT(*) FROM signal_audit WHERE ts >= ? AND decision = 'SKIP'",
            (since_utc.isoformat(),),
        )
        skips = int(cur.fetchone()[0] or 0)
        cur.execute(
            """
            SELECT skip_reason, COUNT(*) c
            FROM signal_audit
            WHERE ts >= ? AND decision='SKIP'
            GROUP BY skip_reason
            ORDER BY c DESC
            LIMIT 5
            """,
            (since_utc.isoformat(),),
        )
        skip_top = [{"reason": str(r or "unknown"), "count": int(c)} for r, c in cur.fetchall()]
        return DailyMetrics(entries=entries, signals=signals, skips=skips, skip_top=skip_top)
    finally:
        conn.close()


def daily_health_check(*, min_entries: int = 1) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    m = _daily_metrics(since)
    passed = True
    checks: List[str] = []
    if m.entries < int(min_entries):
        passed = False
        checks.append(f"entries_below_floor:{m.entries}<{min_entries}")
    out = {
        "checked_at_utc": _utc_now(),
        "window": "24h",
        "pass": passed,
        "metrics": {
            "entries": m.entries,
            "signals": m.signals,
            "skips": m.skips,
            "skip_top": m.skip_top,
        },
        "checks": checks,
    }
    report = Path(__file__).resolve().parents[1] / "reports" / "daily_health_check.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out
