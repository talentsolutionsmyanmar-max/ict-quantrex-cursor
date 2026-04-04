"""
SQLite ledger for paper/backtest events: entries, scale-outs, stops — with reason text for review.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DB_PATH = Path(__file__).resolve().parent / "data" / "playbook.sqlite3"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def ensure_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS playbook_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                event_type TEXT NOT NULL,
                side TEXT,
                entry_price REAL,
                exit_price REAL,
                position_fraction REAL,
                pnl REAL,
                capital_after REAL NOT NULL,
                bar_time TEXT,
                entry_reason_json TEXT,
                entry_reason_text TEXT,
                exit_reason_text TEXT
            )
            """
        )
        c.commit()


def record_playbook_event(
    *,
    mode: str,
    symbol: str,
    timeframe: str,
    event_type: str,
    side: Optional[str],
    entry_price: Optional[float],
    exit_price: Optional[float],
    position_fraction: Optional[float],
    pnl: Optional[float],
    capital_after: float,
    bar_time: Optional[str],
    entry_reason_json: Optional[str],
    entry_reason_text: Optional[str],
    exit_reason_text: Optional[str],
) -> int:
    ensure_db()
    created = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO playbook_events (
                created_at, mode, symbol, timeframe, event_type, side,
                entry_price, exit_price, position_fraction, pnl, capital_after,
                bar_time, entry_reason_json, entry_reason_text, exit_reason_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                mode.upper(),
                symbol,
                timeframe,
                event_type,
                side,
                entry_price,
                exit_price,
                position_fraction,
                pnl,
                float(capital_after),
                bar_time,
                entry_reason_json,
                entry_reason_text,
                exit_reason_text,
            ),
        )
        c.commit()
        return int(cur.lastrowid)


def list_playbook_events(*, limit: int = 40, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_db()
    limit = max(1, min(int(limit), 200))
    with _conn() as c:
        if symbol:
            rows = c.execute(
                """
                SELECT * FROM playbook_events
                WHERE symbol = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (symbol.upper().replace("/", ""), limit),
            ).fetchall()
        else:
            rows = c.execute(
                """
                SELECT * FROM playbook_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("entry_reason_json"):
            try:
                d["entry_reason_parsed"] = json.loads(d["entry_reason_json"])
            except json.JSONDecodeError:
                d["entry_reason_parsed"] = None
        out.append(d)
    return out
