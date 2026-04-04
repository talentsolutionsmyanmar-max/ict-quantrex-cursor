"""
WSGI entrypoint for production (Gunicorn).

Why this file exists:
- When using Eventlet workers for Flask-SocketIO, we must monkey-patch as early as possible.
- `app.py` imports many modules; patching after import is too late.
"""

from __future__ import annotations

import os


def _maybe_eventlet_patch() -> None:
    mode = (os.getenv("SOCKETIO_ASYNC_MODE", "threading") or "threading").strip().lower()
    if mode != "eventlet":
        return
    try:
        import eventlet  # type: ignore

        eventlet.monkey_patch()
    except Exception as e:
        # Fail fast: if prod asks for eventlet mode but patching fails, we'd rather crash
        # than run a half-broken websocket server.
        raise RuntimeError(f"eventlet monkey_patch failed: {e}") from e


_maybe_eventlet_patch()

# Import after patch
from app import app as app  # noqa: E402,F401

