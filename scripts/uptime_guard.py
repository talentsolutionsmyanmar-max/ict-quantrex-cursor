#!/usr/bin/env python3
"""
v2.7.6 — Lightweight uptime watchdog: ensure paper loop + DOM collector stay running.
Read-only re process list; restarts use same CLI as production. No config edits.
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "uptime_guard.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)

CHECK_INTERVAL_SEC = 60

# Substrings that must appear in the same python/pythonw command line (case-insensitive)
WATCHLIST: list[tuple[str, list[str]]] = [
    (
        "paper_loop",
        ["paper_trader.py", "--live", "config/v2.6_live_micro.yaml"],
    ),
    (
        "dom_collector",
        ["dom_cvd_collector.py", "--interval", "900"],
    ),
]


def _python_command_lines() -> str:
    blobs: list[str] = []
    for exe in ("python.exe", "pythonw.exe"):
        try:
            r = subprocess.run(
                ["wmic", "process", "where", f"name='{exe}'", "get", "commandline"],
                capture_output=True,
                text=True,
                timeout=45,
                encoding="utf-8",
                errors="ignore",
            )
            blobs.append((r.stdout or "") + (r.stderr or ""))
        except Exception as e:
            logging.warning("wmic failed for %s: %s", exe, e)
    return "\n".join(blobs)


def process_running(needles: list[str]) -> bool:
    hay = _python_command_lines().lower()
    line_lower = hay  # whole blob; each cmd on its own line typically
    return all(n.lower() in line_lower for n in needles)


def restart_paper_loop() -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(ROOT / "paper_trader.py"),
            "--live",
            "--config",
            "config/v2.6_live_micro.yaml",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def restart_dom_collector() -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-u",
            str(ROOT / "core" / "dom_cvd_collector.py"),
            "--symbol",
            "BTC/USDT",
            "--interval",
            "900",
        ],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )


def run_watchdog() -> None:
    logging.info("Uptime guard started (check every %ss) | log=%s", CHECK_INTERVAL_SEC, LOG_FILE)
    starters = {
        "paper_loop": restart_paper_loop,
        "dom_collector": restart_dom_collector,
    }
    while True:
        for name, needles in WATCHLIST:
            if not process_running(needles):
                logging.warning("%s missing; restarting...", name)
                try:
                    starters[name]()
                    logging.info("Restart issued for %s", name)
                except Exception as e:
                    logging.error("Failed to restart %s: %s", name, e)
        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    try:
        run_watchdog()
    except KeyboardInterrupt:
        logging.info("Uptime guard stopped (Ctrl+C)")
