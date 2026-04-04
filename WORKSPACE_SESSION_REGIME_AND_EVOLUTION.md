# Workspace session notes — regime metrics & evolution wiring

Saved from the Cursor conversation (April 2026). Use this as a handoff for what changed and how to validate it.

**Environment setup:** run `./scripts/setup.sh` from the project root (see `README.md`).

## Goals addressed

1. **Backtest regime / gate statistics** — Make A/B comparisons easier (raw ICT bars vs gate-filtered, entry mix by `regime_state`).
2. **Research / evolution visibility** — Same regime context in logs, evolution-status API, dashboard monitor, and evolve JSON.

## Code changes (by area)

### ICT engine (`ict_engine.py`)

- `signal_pre_regime_gate` — Direction (+1/−1/0) before the v1.9 regime gate.
- `regime_gate_removed` — Per bar: raw signal in ranging was zeroed by the gate.

### Backtester (`backtester.py`)

- Each trade includes `entry_regime_state`, `entry_regime_gate_allowed`.
- `metrics["regime_summary"]` on every run (including “no trades”), with fields such as:
  - `regime_gate_enabled`, `bars_total`, `bars_with_raw_ict_signal`, `bars_regime_gate_removed`, `pct_raw_signals_removed_by_gate`
  - `bar_regime_mix_pct`
  - If trades exist: `unique_entries_by_regime_state`, `unique_entries_total`, `pct_unique_entries_in_ranging` (deduped by `entry_time` + `side`).
- `run_multi` copies all `REGIME_*` config fields; per-coin summary adds `bars_gate_removed`, `pct_entries_ranging`; aggregate adds `bars_regime_gate_removed_all_symbols`.
- Verbose / error diagnostics print a short regime line when applicable.

### Dashboard (`templates/dashboard.html`)

- Strip under metric cards: regime gate on/off, raw bars, removed count/%, entry mix (from `metrics.regime_summary`).
- Background evolution monitor header: `gate_cfg` from log + `train …` from `regime_train_snip`.

### Research lab (`research_lab.py`)

- `aggregate_regime_from_multi_result`, `format_evolution_regime_log`, `regime_snapshot_from_detail`.
- `evaluate_config_oos_multi` detail: `regime_train_aggregate`, `regime_test_aggregate`.
- `evaluate_config_oos` detail: `regime_summary_train`, `regime_summary_test`.
- `run_evolution` logs:
  - `[evolution] regime_gate_enabled=… symbols=…`
  - `[evolution] regime_train …` after each generation’s best fitness.
- Return payload: `best_regime_train`; each `history[]` entry: `regime_train_log`, `regime_train`.

### App (`app.py`)

- `/api/research/evolution-status` parses log tail for:
  - `regime_train_snip` (last `[evolution] regime_train …` line)
  - `regime_gate_enabled_hint` (from `[evolution] regime_gate_enabled=…`)

## Spec / config reminder

- Regime gate is driven by `strategy/spec.yaml` → `load_spec.py` → `Config` (`REGIME_GATE_ENABLED`, etc.).
- **`clone_config_genes` copies all `REGIME_*` fields from the source config** (aligned with `Backtester.run_multi`), so evolution / walk-forward / stress inherit `runtime_cfg` regime settings.

## Verification performed in session

- AST/syntax checks on modified Python files.
- Small in-process checks: mock `run_multi` payload → aggregated regime; `format_evolution_regime_log`; regex matching sample evolution log lines.
- Full `import research_lab` failed with exit 139 in one sandbox run (likely environment/native stack); succeeded with full permissions on the same machine.

## Suggested checks on your machine

1. Run a dashboard backtest; confirm metrics strip and saved run JSON include `regime_summary`.
2. Run `/api/research/evolve` (small population/generations); confirm response has `best_regime_train` and `history[].regime_train`.
3. With background evolution logging to `evolution_run.log` or journald, hit `/api/research/evolution-status` and confirm `regime_train_snip` / `regime_gate_enabled_hint`.

## Related files

- `regime.py` — `annotate_regime`, `regime_state` / ADX+ATR%+EMA persistence.
- `strategy/spec.yaml` — `regime:` block.
- `strategy/load_spec.py` — env mapping for regime keys.

## Final production decision (2026-04-01)

- Promotion outcome: **GO**
- Promotion action: **HOLD baseline genes** (rank-1 matched current defaults).
- Verified aggregate (2024-01-01 → 2026-03-30, SOL/ETH/BTC, 1h):
  - `mean_sharpe`: `1.073`
  - `min_sharpe`: `0.91`
  - `min_profit_factor`: `1.23`
  - `worst_max_drawdown_pct`: `-6.75`
  - `robustness_score`: `0.8186`
  - `total_trades_all`: `2515`
- Live setting: keep `regime.enabled: false`.
- Full record: `OPS_PROMOTION_LOG_2026-04-01.md`.
