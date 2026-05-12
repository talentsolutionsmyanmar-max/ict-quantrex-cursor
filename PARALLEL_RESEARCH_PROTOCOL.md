# Parallel Research Protocol (QuantRex vs Vibe)

Goal: run both stacks in parallel, compare fairly, and only promote edges that survive the same robustness filters.

## Research stance

- QuantRex remains the execution and risk-control source of truth.
- Vibe is a candidate generator and parallel research track.
- No model/agent output is accepted without equal backtest + paper evidence.

## Fair-comparison rules

Use identical constraints for both tracks:

- Symbols: BTCUSDT, ETHUSDT, SOLUSDT
- Timeframe: 15m
- Date windows: same in-sample and out-of-sample windows
- Costs: same commission + slippage assumptions
- Capital/risk: same initial capital and risk budget
- Promotion metric: same thresholds

## Promotion thresholds (default)

- Minimum unique entries in OOS window: 60
- Profit factor: >= 1.20
- Max drawdown: <= 8%
- Expectancy: > 0
- Walk-forward efficiency: >= 0.55
- Paper confirmation: 30-50 OPEN trades with no operational instability

## Track workflow

1) QuantRex baseline run
- Keep current deterministic pipeline and export results JSON.

2) Vibe candidate generation
- Generate 3-10 candidate strategies.
- Normalize each candidate into a common result JSON schema.

3) Backtest parity check
- Run all candidates under same period/cost assumptions.
- Compare via `scripts/compare_parallel_tracks.py`.

4) Paper trial gate
- Promote top 1-2 candidates to paper only.
- Compare 30-50 paper OPEN trades vs QuantRex control track.

5) Decision
- If Vibe track materially outperforms on robustness + paper behavior, adopt its signal as an input gate.
- If not, keep Vibe as idea factory only.

## Data format for comparison script

Each track JSON should contain at least:

- `track`: string, e.g. `quantrex` or `vibe_candidate_1`
- `window`: object with start/end
- `metrics`: object with:
  - `profit_factor`
  - `max_drawdown`
  - `expectancy`
  - `total_trades`
  - `unique_entries` (preferred; else fallback to total_trades)
  - `win_rate` (optional)
  - `total_pnl` (optional)

## Practical commands

1) Export QuantRex baseline into normalized JSON:

`python scripts/export_quantrex_normalized.py --start-date 2024-01-01 --end-date 2026-04-24 --track quantrex_baseline --output reports/quantrex_baseline.json`

2) Copy and fill Vibe template:

- `reports/vibe_result_template.json` -> e.g. `reports/vibe_candidate_001.json`
- Fill with real Vibe run metrics under identical assumptions.

3) Validate baseline/candidate files before compare:

`python scripts/validate_parallel_track_inputs.py --baseline reports/quantrex_baseline.json --candidates reports/vibe_candidate_001.json`

4) Compare tracks:

`python scripts/compare_parallel_tracks.py --input reports/quantrex_baseline.json reports/vibe_candidate_001.json`

5) Optional stricter gates:

`python scripts/compare_parallel_tracks.py --input reports/quantrex_baseline.json reports/vibe_candidate_001.json --min-entries 80 --min-pf 1.30 --max-dd 7 --min-exp 0.5`

## Why this protocol

- Prevents "tool hype" from bypassing quant discipline.
- Lets you benefit from Vibe ideation without breaking your stable core.
- Creates a repeatable, auditable research loop like a real desk.
