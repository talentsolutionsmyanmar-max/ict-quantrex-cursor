# QUANTREX 30-DAY LIVE VALIDATION PROTOCOL

## Phase 1: Days 1-7 (Survival and Logging)
- Goal: verify execution pipeline, regime detection, exit logic, and logging stability.
- Risk: `risk_per_trade = 0.005`.
- Pass criteria:
  - At least 5 signals evaluated.
  - `data/signal_audit.jsonl` grows with candle-bound logs.
  - Max drawdown <= 1.5%.
  - Supabase/JSONL fallback logs 100% of exits.
- Action if fail: pause, review `skip_reason` histogram, fix regime/signal gate.

## Phase 2: Days 8-14 (Edge Confirmation)
- Goal: confirm live expectancy aligns with backtest (`0.20R <= Exp <= 0.35R`).
- Risk: keep `0.005`; add `ETHUSDT` only if BTC correlation < 0.88.
- Pass criteria:
  - PF >= 1.8 (rolling 7d).
  - `BREAKEVEN_SL` <= 40% of exits.
  - Regime flips detected within 1-2 candles of trend shift.

## Phase 3: Days 15-30 (Scaling and Live Prep)
- Goal: scale size if edge holds and prepare live capital transition.
- Risk: increase to `0.008` only if cumulative PnL > 0 and DD < 2.5%.
- Pass criteria:
  - Live expectancy >= 0.22R.
  - No catastrophic stop breaches (>1.1R).
  - `/live-monitor` reflects real-time state accurately.
- Promotion:
  - Start with $100-$500.
  - Monitor daily.
  - Halve size if 2 consecutive days hit `max_daily_loss_r`.
