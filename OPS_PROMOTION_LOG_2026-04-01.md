# OPS Promotion Log — 2026-04-01

## Decision

- Promote status: **GO**
- Action: **HOLD baseline genes** (no gene changes to apply)
- Live regime gate: **OFF** (`strategy/spec.yaml` already has `regime.enabled: false`)

## Verified rank-1 genes (from pipeline)

```json
{
  "ATR_MULTIPLIER": 1.8,
  "FVG_THRESHOLD": 0.001,
  "ICT_RANGE_HOURS": 5,
  "LIQUIDITY_BUFFER": 0.006,
  "MIN_CONFLUENCE": 2,
  "MIN_SIGNAL_STRENGTH": 68,
  "TRAIL_ATR_MULTIPLIER": 1.0
}
```

These match current baseline defaults, so no promotion delta is required.

## Full-window verification (multi-coin)

- Window: `2024-01-01` → `2026-03-30`
- Symbols: `SOLUSDT`, `ETHUSDT`, `BTCUSDT`
- Timeframe: `1h`
- Initial capital each: `10000`

Aggregate:

- `mean_sharpe`: `1.073`
- `median_sharpe`: `1.07`
- `min_sharpe`: `0.91`
- `min_profit_factor`: `1.23`
- `worst_max_drawdown_pct`: `-6.75`
- `robustness_score`: `0.8186`
- `total_trades_all`: `2515`
- `warnings`: `[]`

Per-coin snapshot:

- `SOLUSDT`: sharpe `1.07`, PF `1.63`, max DD `-5.89`, trades `850`
- `ETHUSDT`: sharpe `1.24`, PF `2.24`, max DD `-4.23`, trades `879`
- `BTCUSDT`: sharpe `0.91`, PF `1.23`, max DD `-6.75`, trades `786`

## Regime-gate policy

- Production policy remains **gate OFF**.
- Research/evolution now includes an auto-rollback guard that penalizes gate-ON configs if they degrade versus gate-OFF baseline on:
  - `min_sharpe` drop > `0.05`
  - `min_profit_factor` drop > `0.10`

## Operational notes

- Keep current baseline locked in `strategy/spec.yaml`.
- Re-run verification weekly or after any model/feature change.
- Do not promote a gate-ON profile unless it beats baseline on `min_sharpe`, `min_profit_factor`, and robustness without trade collapse.

## Dashboard promotion row (SQLite audit) — 2026-04-01 15:39:24 UTC

Logged via **Promotion decision center** with metrics snapshot + note.

| | |
| --- | --- |
| **Decision** | GO |
| **Note (excerpt)** | Rank #1 equals locked baseline. Full multi-coin verification passed on 2024-01-01..2026-03-30 (min_sharpe 0.91, min_profit_factor 1.23, worst_dd -6.75%, total_trades 2515, warnings none). Promote to runtime with regime gate kept OFF for live. |

**Evolve OOS test-window snapshot (table columns in UI)** — bound to rank #1 at save time; shorter window than full verify:

- `min_sharpe`: `0.92`
- `min_profit_factor`: `2.01`
- `worst_max_drawdown_pct`: `-7.24`
- `total_trades_all`: `287`

**Full-window verification (same session / desk narrative in note)** — see section *Full-window verification* above: `0.91` / `1.23` / `-6.75%` / `2515` trades.

Both figures can be correct at once: the **table** stores the evolution **out-of-sample test leg** aggregate; the **note** cites the **long-range multi-coin run** used for sign-off. Do not overwrite historical rows to force them to match.
