"""
Persist backtest runs to SQLite for Atlas-style feedback loops (compare outcomes by regime).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DB_PATH = Path(__file__).resolve().parent / "data" / "runs.sqlite3"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def ensure_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                initial_capital REAL NOT NULL,
                regime_tag TEXT NOT NULL,
                regime_json TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                config_json TEXT NOT NULL,
                had_error INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        c.commit()


def _json_safe(obj: Any) -> str:
    return json.dumps(obj, default=str)


def insert_run(
    *,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: float,
    regime: Dict[str, Any],
    metrics: Dict[str, Any],
    config_snapshot: Dict[str, Any],
) -> int:
    ensure_db()
    had_error = 1 if metrics.get("error") else 0
    created = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO backtest_runs (
                created_at, symbol, timeframe, start_date, end_date, initial_capital,
                regime_tag, regime_json, metrics_json, config_json, had_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                symbol,
                timeframe,
                start_date,
                end_date,
                float(initial_capital),
                str(regime.get("tag", "unknown")),
                _json_safe(regime),
                _json_safe(metrics),
                _json_safe(config_snapshot),
                had_error,
            ),
        )
        c.commit()
        return int(cur.lastrowid)


def list_runs(limit: int = 20) -> List[Dict[str, Any]]:
    ensure_db()
    limit = max(1, min(int(limit), 200))
    with _conn() as c:
        rows = c.execute(
            """
            SELECT id, created_at, symbol, timeframe, start_date, end_date,
                   initial_capital, regime_tag, regime_json, metrics_json, config_json, had_error
            FROM backtest_runs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "symbol": r["symbol"],
                "timeframe": r["timeframe"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "initial_capital": r["initial_capital"],
                "regime_tag": r["regime_tag"],
                "regime": json.loads(r["regime_json"]),
                "metrics": json.loads(r["metrics_json"]),
                "config": json.loads(r["config_json"]),
                "had_error": bool(r["had_error"]),
            }
        )
    return out


def get_run(run_id: int) -> Optional[Dict[str, Any]]:
    ensure_db()
    with _conn() as c:
        r = c.execute(
            """
            SELECT id, created_at, symbol, timeframe, start_date, end_date,
                   initial_capital, regime_tag, regime_json, metrics_json, config_json, had_error
            FROM backtest_runs WHERE id = ?
            """,
            (run_id,),
        ).fetchone()
    if not r:
        return None
    return {
        "id": r["id"],
        "created_at": r["created_at"],
        "symbol": r["symbol"],
        "timeframe": r["timeframe"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "initial_capital": r["initial_capital"],
        "regime_tag": r["regime_tag"],
        "regime": json.loads(r["regime_json"]),
        "metrics": json.loads(r["metrics_json"]),
        "config": json.loads(r["config_json"]),
        "had_error": bool(r["had_error"]),
    }
