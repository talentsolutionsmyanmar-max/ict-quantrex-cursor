"""
Persist Karpathy-style autoresearch runs triggered at kill-zone exits (UTC).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_DB_PATH = Path(__file__).resolve().parent / "data" / "kz_research.sqlite3"


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB_PATH))
    c.row_factory = sqlite3.Row
    return c


def ensure_db() -> None:
    with _conn() as c:
        c.execute(
            """
            CREATE TABLE IF NOT EXISTS kz_research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                trigger_tag TEXT NOT NULL,
                zones_exited_json TEXT NOT NULL,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                backtest_start TEXT NOT NULL,
                backtest_end TEXT NOT NULL,
                population INTEGER NOT NULL,
                generations INTEGER NOT NULL,
                top_genes_json TEXT,
                fitness_fast REAL,
                fitness_crisis REAL,
                history_json TEXT,
                top_json TEXT,
                error TEXT
            )
            """
        )
        c.commit()


def insert_kz_run(
    *,
    trigger_tag: str,
    zones_exited: List[str],
    symbol: str,
    timeframe: str,
    backtest_start: str,
    backtest_end: str,
    population: int,
    generations: int,
    result: Optional[Dict[str, Any]] = None,
    error: Optional[str] = None,
) -> int:
    ensure_db()
    created = datetime.now(timezone.utc).isoformat()
    top_genes = None
    fit_f = fit_c = None
    hist_j = top_j = None
    if result and not error:
        top = result.get("top") or []
        if top:
            top_genes = json.dumps(top[0].get("genes"), default=str)
            fit_f = top[0].get("composite_fitness_fast")
            fit_c = top[0].get("composite_fitness_with_crisis")
        hist_j = json.dumps(result.get("history"), default=str)
        top_j = json.dumps(top, default=str)
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO kz_research_runs (
                created_at, trigger_tag, zones_exited_json, symbol, timeframe,
                backtest_start, backtest_end, population, generations,
                top_genes_json, fitness_fast, fitness_crisis, history_json, top_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                trigger_tag,
                json.dumps(zones_exited),
                symbol,
                timeframe,
                backtest_start,
                backtest_end,
                population,
                generations,
                top_genes,
                fit_f,
                fit_c,
                hist_j,
                top_j,
                error,
            ),
        )
        c.commit()
        return int(cur.lastrowid)


def list_kz_runs(limit: int = 30) -> List[Dict[str, Any]]:
    ensure_db()
    limit = max(1, min(int(limit), 100))
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM kz_research_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    out: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        for k in ("zones_exited_json", "top_genes_json", "history_json", "top_json"):
            if d.get(k):
                try:
                    d[k.replace("_json", "_parsed")] = json.loads(d[k])
                except json.JSONDecodeError:
                    d[k.replace("_json", "_parsed")] = None
        out.append(d)
    return out
