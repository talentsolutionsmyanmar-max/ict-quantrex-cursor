"""
Paper trading alert helpers.

Safe-by-default behavior:
- If Telegram env vars are missing, functions are no-ops and return cleanly.
- This keeps paper/live loops running on fresh VPS deployments.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

import requests


def _telegram_creds() -> tuple[str, str]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return token, chat_id


def send_telegram_sync(text: str) -> Tuple[bool, Optional[str]]:
    token, chat_id = _telegram_creds()
    if not token or not chat_id:
        return False, "Telegram not configured"

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    try:
        r = requests.post(url, json=payload, timeout=10)
        if r.ok:
            return True, None
        return False, f"Telegram HTTP {r.status_code}"
    except Exception as e:
        return False, str(e)


def notify_paper_open(
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    confluence: int,
    reason_text: Optional[str],
) -> None:
    msg = (
        f"[PAPER OPEN] {symbol} {side}\n"
        f"entry={entry_price:.4f} sl={stop_loss:.4f} confluence={confluence}\n"
        f"{reason_text or ''}"
    )
    send_telegram_sync(msg)


def notify_paper_exit(
    symbol: str,
    exit_type: str,
    pnl: float,
    exit_price: float,
    reason_text: Optional[str],
) -> None:
    msg = (
        f"[PAPER EXIT] {symbol} {exit_type}\n"
        f"exit={exit_price:.4f} pnl={pnl:.2f}\n"
        f"{reason_text or ''}"
    )
    send_telegram_sync(msg)
