# Baseline Truth Report v1

Date: 2026-04-23  
Scope: 30-day Validation Sprint anchor benchmark (no feature changes)

## Controlled Benchmark Setup

- Spec: `strategy/spec.yaml` (current locked baseline)
- Symbol: `BTCUSDT`
- Timeframe: `15m`
- Window: `2024-04-01` to `2024-06-30`
- Capital: `10000`
- Runtime: VPS production stack, same execution realism path used in current architecture

## Core Metrics (Baseline v1)

- Closed trades: `3`
- Expectancy (per closed trade): `-1.38`
- Max drawdown: `-0.16%`
- Win rate: `66.67%`
- Profit factor: `0.26`

## SL / TP Quality Snapshot

- SL hit count: `1`
- SL hit rate (on closed trades): `33.33%`
- Average SL loss: `-16.15`
- TP1 captures: `1` (`33.33%` of closed trades), avg TP1 pnl `+4.71`
- TP2 captures: `0`
- TP3 captures: `0`

Interpretation: current distribution is dominated by shallow TP realization and rare but outsized SL impact, which compresses profit factor despite a high nominal win rate.

## Regime Pain Map (Baseline v1)

- `trend_down`: 1 trade, expectancy `-16.15`, win rate `0%`  **(primary pain)**
- `ranging`: 2 trades, expectancy `+6.00`, win rate `100%`  **(currently healthy but low sample)**

Interpretation: losses are concentrated in bearish trend regime handling; ranging behavior is positive but not statistically reliable yet due to low trade count.

## Top 3 Fixes (Ranked by Impact/Complexity)

1. **Downtrend risk throttle first**
   - Apply stricter risk scalar in `trend_down` (size reduction + tighter entry quality gate).
   - Why first: direct mitigation of the only negative regime cluster in baseline.

2. **TP ladder re-balance for realized R**
   - Shift partial distribution to improve realized payoff before stop risk dominates (e.g. increment TP1 lock-in without collapsing runner probability).
   - Why second: current TP2/TP3 capture is zero; PF needs payoff re-shaping.

3. **Trade-depth expansion without quality decay**
   - Increase valid sample count via controlled signal throughput in healthy regimes (especially ranging) while preserving floor constraints.
   - Why third: current `n=3` is too thin for governance confidence and stable promotion decisions.

## Validation Rule for Next Iterations

Any proposed change in this sprint must be judged against this report by re-running the same controlled window and comparing:

- SL hit rate and avg SL loss
- TP1/TP2/TP3 capture rates
- Expectancy
- Max drawdown
- Regime-level expectancy shifts (especially `trend_down`)

No promotion path should advance unless this baseline is improved with acceptable drawdown behavior and improved trade-depth confidence.
