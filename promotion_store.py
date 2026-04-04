"""
Persist promotion decisions (GO/HOLD) for auditability.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).resolve().parent / "data" / "promotion_decisions.sqlite3"


def _ensure_cqe_ack_column(c: sqlite3.Connection) -> None:
    rows = c.execute("PRAGMA table_info(promotion_decisions)").fetchall()
    names = {r[1] for r in rows}
    if "cqe_ack" not in names:
        c.execute("ALTER TABLE promotion_decisions ADD COLUMN cqe_ack INTEGER NOT NULL DEFAULT 0")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS promotion_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at_utc TEXT NOT NULL,
            decision TEXT NOT NULL,
            note TEXT NOT NULL,
            rank1_genes_json TEXT NOT NULL,
            aggregate_json TEXT NOT NULL,
            verify_window_json TEXT NOT NULL,
            symbols_json TEXT NOT NULL,
            source TEXT NOT NULL
        )
        """
    )
    _ensure_cqe_ack_column(c)
    c.commit()
    return c


def _safe_json(obj: Any) -> str:
    return json.dumps(obj if obj is not None else {}, separators=(",", ":"), sort_keys=True)


def insert_promotion_decision(
    *,
    decision: str,
    note: str,
    rank1_genes: Optional[Dict[str, Any]],
    aggregate: Optional[Dict[str, Any]],
    verify_window: Optional[List[str]],
    symbols: Optional[List[str]],
    source: str = "dashboard",
    cqe_ack: bool = False,
) -> int:
    d = str(decision or "").strip().upper()
    if d not in {"GO", "HOLD"}:
        raise ValueError("decision must be GO or HOLD")
    ts = datetime.now(timezone.utc).isoformat()
    ack = 1 if cqe_ack else 0
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO promotion_decisions(
                created_at_utc, decision, note, rank1_genes_json, aggregate_json,
                verify_window_json, symbols_json, source, cqe_ack
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                d,
                str(note or ""),
                _safe_json(rank1_genes or {}),
                _safe_json(aggregate or {}),
                _safe_json(verify_window or []),
                _safe_json(symbols or []),
                str(source or "dashboard"),
                ack,
            ),
        )
        c.commit()
        return int(cur.lastrowid)


def list_promotion_decisions(*, limit: int = 25) -> List[Dict[str, Any]]:
    lim = max(1, min(int(limit), 200))
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, created_at_utc, decision, note, rank1_genes_json,
                   aggregate_json, verify_window_json, symbols_json, source, cqe_ack
            FROM promotion_decisions
            ORDER BY id DESC
            LIMIT ?
            """,
            (lim,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        try:
            ack = bool(int(r["cqe_ack"] or 0))
        except (KeyError, TypeError, ValueError):
            ack = False
        out.append(
            {
                "id": int(r["id"]),
                "created_at_utc": r["created_at_utc"],
                "decision": r["decision"],
                "note": r["note"],
                "rank1_genes": json.loads(r["rank1_genes_json"]),
                "aggregate": json.loads(r["aggregate_json"]),
                "verify_window": json.loads(r["verify_window_json"]),
                "symbols": json.loads(r["symbols_json"]),
                "source": r["source"],
                "cqe_ack": ack,
            }
        )
    return out
