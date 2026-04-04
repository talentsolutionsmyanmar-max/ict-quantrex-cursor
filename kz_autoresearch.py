"""
Kill-zone exit → debounced evolutionary autoresearch (Karpathy-style loop cadence).
Poll from a background thread when KZ_AUTO_RESEARCH=1, or call run_kz_research_once() from cron.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from config import Config
from research_lab import run_evolution
from session_clock import get_session_state

_STATE_PATH = Path(__file__).resolve().parent / "data" / "kz_autoresearch_poll_state.json"

_job_lock = threading.Lock()
_running = False


def _load_poll_state() -> Dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {"last_in_kz": False, "zones_while_in": [], "last_job_unix": 0.0}
    try:
        with open(_STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_in_kz": False, "zones_while_in": [], "last_job_unix": 0.0}


def _save_poll_state(st: Dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=0)


def _clear_pending(st: Dict[str, Any]) -> None:
    st.pop("pending_kz_job", None)
    _save_poll_state(st)


def get_next_kz_trigger_event() -> Optional[Dict[str, Any]]:
    """
    Return a pending KZ-exit job if one is queued; else detect fresh exit and queue it.
    Pending survives debounce/busy skips until the job completes.
    """
    st = _load_poll_state()
    pending = st.get("pending_kz_job")
    if isinstance(pending, dict) and pending.get("tag"):
        return dict(pending)

    s = get_session_state()
    prev_in = bool(st.get("last_in_kz", False))
    zones_snap: List[str] = list(st.get("zones_while_in", []))
    in_kz = bool(s.get("in_kill_zone"))
    active = list(s.get("active_kill_zones") or [])

    if in_kz:
        st["last_in_kz"] = True
        st["zones_while_in"] = active
        _save_poll_state(st)
        return None

    if prev_in:
        tag = ",".join(zones_snap) if zones_snap else "kill_zone_exit"
        st["pending_kz_job"] = {
            "tag": tag,
            "zones_exited": zones_snap,
            "utc_iso": s.get("utc_iso"),
        }
        st["last_in_kz"] = False
        st["zones_while_in"] = []
        _save_poll_state(st)
        return dict(st["pending_kz_job"])

    st["last_in_kz"] = False
    _save_poll_state(st)
    return None


def _rolling_dates(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=max(30, int(days)))
    return start.isoformat(), end.isoformat()


def run_kz_research_job(
    cfg: Config,
    *,
    trigger_tag: str,
    zones_exited: List[str],
    lookback_days: Optional[int] = None,
    population: Optional[int] = None,
    generations: Optional[int] = None,
    verify_top_k: Optional[int] = None,
) -> Dict[str, Any]:
    from kz_research_store import insert_kz_run

    lb = lookback_days if lookback_days is not None else int(os.getenv("KZ_RESEARCH_LOOKBACK_DAYS", "120"))
    pop = population if population is not None else int(os.getenv("KZ_RESEARCH_POPULATION", "6"))
    gen = generations if generations is not None else int(os.getenv("KZ_RESEARCH_GENERATIONS", "1"))
    vk = verify_top_k if verify_top_k is not None else int(os.getenv("KZ_RESEARCH_VERIFY_TOP", "1"))
    pop = max(4, min(pop, 14))
    gen = max(1, min(gen, 3))
    vk = max(1, min(vk, 3))

    start_d, end_d = _rolling_dates(lb)
    sym = cfg.SYMBOL
    tf = cfg.TIMEFRAME
    cap = float(cfg.INITIAL_CAPITAL)

    err: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    try:
        result = run_evolution(
            symbol=sym,
            timeframe=tf,
            start_date=start_d,
            end_date=end_d,
            initial_capital=cap,
            population=pop,
            generations=gen,
            seed=None,
            verify_top_k_crisis=vk,
            runtime_cfg=cfg,
        )
    except Exception as e:
        err = str(e)

    rid = insert_kz_run(
        trigger_tag=trigger_tag,
        zones_exited=zones_exited,
        symbol=sym,
        timeframe=tf,
        backtest_start=start_d,
        backtest_end=end_d,
        population=pop,
        generations=gen,
        result=result,
        error=err,
    )
    return {"run_id": rid, "error": err, "result": result, "trigger_tag": trigger_tag}


def _debounced(min_gap_sec: float) -> bool:
    st = _load_poll_state()
    now = time.time()
    last = float(st.get("last_job_unix", 0) or 0)
    if now - last < min_gap_sec:
        return False
    st["last_job_unix"] = now
    _save_poll_state(st)
    return True


def run_kz_research_once(
    cfg: Config,
    *,
    force_tag: str = "manual",
    zones_exited: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Immediate job (API / cron)."""
    global _running
    if not _job_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "job already running"}
    try:
        _running = True
        ze = zones_exited if zones_exited is not None else []
        return run_kz_research_job(cfg, trigger_tag=force_tag, zones_exited=ze)
    finally:
        _running = False
        _job_lock.release()


def try_run_on_kz_exit(cfg: Config) -> Optional[Dict[str, Any]]:
    """If a KZ-exit event is pending and debounce + lock OK, run and clear pending."""
    ev = get_next_kz_trigger_event()
    if not ev:
        return None
    min_gap = float(os.getenv("KZ_RESEARCH_MIN_GAP_SEC", "2700"))
    if not _debounced(min_gap):
        return {"skipped": True, "reason": "debounce", "event": ev}
    global _running
    if not _job_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "job busy", "event": ev}
    try:
        _running = True
        out = run_kz_research_job(
            cfg,
            trigger_tag=str(ev["tag"]),
            zones_exited=list(ev.get("zones_exited") or []),
        )
        st = _load_poll_state()
        _clear_pending(st)
        return out
    finally:
        _running = False
        _job_lock.release()


def start_background_poller(cfg_getter: Callable[[], Config], interval_sec: float = 60.0) -> None:
    """Daemon thread: queue on KZ exit; run evolution when debounce allows."""

    def loop():
        while True:
            try:
                try_run_on_kz_exit(cfg_getter())
            except Exception as e:
                print(f"KZ autoresearch poller error: {e}")
            time.sleep(max(15.0, float(interval_sec)))

    t = threading.Thread(target=loop, name="kz-autoresearch", daemon=True)
    t.start()
    print("KZ autoresearch poller started (set KZ_AUTO_RESEARCH=1). Interval ~{}s".format(int(interval_sec)))
