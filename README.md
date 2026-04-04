# ICT Trading Dashboard (AutoResearchClaw)

## Setup (automated)

From this directory:

```bash
./scripts/setup.sh
```

This creates `venv/`, installs `requirements.txt`, copies `.env.example` → `.env` if `.env` is missing, and runs a short import smoke test.

## Run

```bash
source venv/bin/activate
python app.py
```

Open **http://127.0.0.1:5050/** (or the port in your `.env` / `PORT`).

Backtests use **public Binance** klines; API keys in `.env` are only needed for paper/live and optional features.

## Optional

- `requirements-optional.txt` — extra deps (e.g. `ta-lib`, `ccxt`); install only if you use those paths.
- Regime / evolution notes: `WORKSPACE_SESSION_REGIME_AND_EVOLUTION.md`.
