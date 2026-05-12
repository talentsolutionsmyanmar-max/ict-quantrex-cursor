#!/usr/bin/env python3
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
import uvicorn

app = FastAPI()
MOCK_LOG = Path("data/atas_mock_signals.log")


@app.post("/signal")
async def forward_to_atas(payload: dict):
    allowed = {"BTCUSDT": ["trend_up", "trend_down"], "ETHUSDT": ["trend_down"]}
    sym = str(payload.get("symbol") or "").upper().replace("/", "")
    regime = str(payload.get("regime") or "")
    if sym not in allowed:
        return {"status": "blocked", "reason": "symbol_not_in_universe"}
    if regime not in allowed[sym]:
        return {"status": "blocked", "reason": "regime_disabled"}
    MOCK_LOG.parent.mkdir(parents=True, exist_ok=True)
    ts = str(payload.get("timestamp") or datetime.now(timezone.utc).isoformat())
    with MOCK_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{ts} | {sym} | {regime} | MOCK_FORWARD\n")
    return {"status": "mocked", "note": "ATAS listener not active. Logged locally."}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9000)
