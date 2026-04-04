"""
Walk-forward OOS, crisis stress windows, and lightweight evolutionary search over ICT spec genes.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from backtester import Backtester
from config import Config, build_config
from research_fitness import composite_fitness, parsimony_penalty_vs_baseline

# Crypto-relevant stress slices (BTCUSDT on Binance has data from ~2017; adjust if needed)
CRISIS_WINDOWS: List[Dict[str, str]] = [
    {"id": "covid_crash", "label": "COVID crash", "start": "2020-03-01", "end": "2020-04-15"},
    {"id": "2021_chop", "label": "2021 Q2 chop", "start": "2021-05-01", "end": "2021-07-31"},
    {"id": "2022_bear", "label": "2022 bear / rates", "start": "2022-01-01", "end": "2022-12-31"},
    {"id": "ftx_window", "label": "FTX collapse", "start": "2022-11-01", "end": "2022-11-30"},
    {"id": "2023_mar", "label": "USDC / bank stress", "start": "2023-03-01", "end": "2023-03-31"},
]

# Mutable genes for evolution (attr name → allowed values)
# Conservative lab note:
# - RISK_PER_TRADE is intentionally excluded (kept fixed at the baseline 1.7 value).
# - Ranges are designed for robustness-first search, not maximum in-sample profit.
GENE_SPACE: List[Tuple[str, Tuple[Any, ...]]] = [
    ("ICT_RANGE_HOURS", (3, 4, 5, 6)),
    ("LIQUIDITY_BUFFER", (0.004, 0.005, 0.006, 0.007, 0.008)),
    ("FVG_THRESHOLD", (0.001, 0.0015, 0.002, 0.0025, 0.003)),
    ("ATR_MULTIPLIER", (1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8)),
    ("TRAIL_ATR_MULTIPLIER", (0.8, 1.0, 1.2, 1.4)),
    ("MIN_CONFLUENCE", (2, 3)),
    ("MIN_SIGNAL_STRENGTH", (65, 68, 70, 72, 75, 78, 80)),
]

ALLOWED_GENE_VALUES: Dict[str, Tuple[Any, ...]] = {a: vals for a, vals in GENE_SPACE}
GENE_KEYS = frozenset(ALLOWED_GENE_VALUES.keys())

# Regime gate + detector tunables (spec/YAML) — not evolved; must follow runtime_cfg like Backtester.run_multi.
_REGIME_CFG_ATTRS: Tuple[str, ...] = (
    "REGIME_GATE_ENABLED",
    "REGIME_ADX_PERIOD",
    "REGIME_ADX_MIN",
    "REGIME_ATR_PERIOD",
    "REGIME_ATR_PCT_MIN",
    "REGIME_EMA_FAST",
    "REGIME_EMA_SLOW",
    "REGIME_PERSIST_BARS",
    "REGIME_RANGE_MIN_SIGNAL_STRENGTH",
    "REGIME_RANGE_MIN_CONFLUENCE",
)

# Auto-rollback guard for regime gate in research/evolution:
# if gate ON degrades too much vs same genes with gate OFF, apply a strong penalty.
_REGIME_GUARD_MAX_DROP_MIN_SHARPE = 0.05
_REGIME_GUARD_MAX_DROP_MIN_PF = 0.10


def apply_research_genes(target: Config, genes: Dict[str, Any]) -> List[str]:
    """Set validated genes on config instance. Returns error strings (empty if OK)."""
    errors: List[str] = []
    if not isinstance(genes, dict):
        return ["genes must be a JSON object"]
    for k, v in genes.items():
        if k not in ALLOWED_GENE_VALUES:
            errors.append(f"unknown gene: {k}")
            continue
        if v not in ALLOWED_GENE_VALUES[k]:
            errors.append(f"{k}={v!r} is not an allowed value for this lab")
            continue
        setattr(target, k, v)
    return errors


def runtime_gene_snapshot(cfg: Config) -> Dict[str, Any]:
    return {attr: getattr(cfg, attr) for attr, _ in GENE_SPACE}


def copy_research_genes(source: Config, target: Config) -> None:
    """Copy evolved ICT genes from source onto target (after build_config() load)."""
    for attr, _ in GENE_SPACE:
        setattr(target, attr, getattr(source, attr))


def run_backtest_silent(cfg: Config) -> Dict[str, Any]:
    return Backtester(cfg, record_playbook=False).run(verbose=False)


def _run_multi_silent(
    *,
    cfg: Config,
    symbols: List[str],
    start_date: str,
    end_date: str,
    max_workers: int = 1,
) -> Dict[str, Any]:
    """
    Multi-coin evaluation wrapper for robustness-first evolution.
    Uses Backtester.run_multi() and returns the full structure with aggregate + per_symbol.
    """
    return Backtester.run_multi(
        base_config=cfg,
        symbols=symbols,
        timeframe=str(cfg.TIMEFRAME),
        start_date=start_date,
        end_date=end_date,
        initial_capital=float(cfg.INITIAL_CAPITAL),
        max_workers=max_workers,
        verbose=False,
    )


def aggregate_regime_from_multi_result(res: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Sum raw-signal / gate-removed bar counts across symbols for evolution logs + API."""
    if not isinstance(res, dict) or not res.get("success"):
        return None
    per = res.get("per_symbol") or {}
    rows: List[Tuple[str, Dict[str, Any]]] = []
    for sym, payload in per.items():
        if not isinstance(payload, dict) or payload.get("error"):
            continue
        m = payload.get("metrics")
        if not isinstance(m, dict):
            continue
        rs = m.get("regime_summary")
        if not isinstance(rs, dict):
            continue
        rows.append((str(sym), rs))
    if not rows:
        return None
    gate_on = any(r.get("regime_gate_enabled") for _, r in rows)
    n_raw = sum(int(r.get("bars_with_raw_ict_signal") or 0) for _, r in rows)
    n_rm = sum(int(r.get("bars_regime_gate_removed") or 0) for _, r in rows)
    pct = round(100.0 * n_rm / n_raw, 2) if n_raw > 0 else 0.0
    return {
        "evaluation_mode": "multi",
        "regime_gate_enabled": gate_on,
        "bars_with_raw_ict_signal": n_raw,
        "bars_regime_gate_removed": n_rm,
        "pct_raw_signals_removed_by_gate": pct,
        "symbols": [s for s, _ in rows],
        "per_symbol_regime": [
            {
                "symbol": s,
                "raw": int(r.get("bars_with_raw_ict_signal") or 0),
                "removed": int(r.get("bars_regime_gate_removed") or 0),
                "pct_entries_ranging": r.get("pct_unique_entries_in_ranging"),
            }
            for s, r in rows
        ],
    }


def format_evolution_regime_log(detail: Dict[str, Any]) -> str:
    """One-line train-window regime summary for evolution_run.log / journalctl."""
    agg = detail.get("regime_train_aggregate")
    if isinstance(agg, dict) and agg.get("bars_with_raw_ict_signal") is not None:
        g = "ON" if agg.get("regime_gate_enabled") else "OFF"
        raw = agg.get("bars_with_raw_ict_signal")
        rm = agg.get("bars_regime_gate_removed")
        pct = agg.get("pct_raw_signals_removed_by_gate")
        syms = agg.get("symbols") or []
        sym_s = ",".join(syms) if syms else "?"
        return f"gate={g} raw={raw} removed={rm} ({pct}%) syms={sym_s}"
    rs = detail.get("regime_summary_train")
    if not isinstance(rs, dict) and isinstance(detail.get("train_metrics"), dict):
        rs = detail["train_metrics"].get("regime_summary")
    if isinstance(rs, dict):
        g = "ON" if rs.get("regime_gate_enabled") else "OFF"
        raw = rs.get("bars_with_raw_ict_signal", "—")
        rm = rs.get("bars_regime_gate_removed", "—")
        pct = rs.get("pct_raw_signals_removed_by_gate", "—")
        return f"gate={g} raw={raw} removed={rm} ({pct}%)"
    return "n/a"


def regime_snapshot_from_detail(detail: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Structured regime snapshot for JSON API responses (best genome, train window)."""
    agg = detail.get("regime_train_aggregate")
    if isinstance(agg, dict) and agg.get("bars_with_raw_ict_signal") is not None:
        return dict(agg)
    rs = detail.get("regime_summary_train")
    if not isinstance(rs, dict) and isinstance(detail.get("train_metrics"), dict):
        rs = detail["train_metrics"].get("regime_summary")
    if isinstance(rs, dict):
        out = dict(rs)
        out.setdefault("evaluation_mode", "single")
        return out
    return None


def _cap_profit_factor_for_fitness(pf: float, *, cap: float = 8.0) -> float:
    """Cap raw profit factor so evolution cannot chase huge PF from tiny loss sums."""
    try:
        x = float(pf)
    except Exception:
        return 0.0
    if x < 0.0:
        return 0.0
    return float(min(x, cap))


def _multi_coin_fitness_core(agg: Dict[str, Any]) -> Tuple[float, Dict[str, Any]]:
    """
    Evolution score component: worst-coin Sharpe dominates; mean Sharpe secondary;
    profit factor enters only through a capped min-PF (reduces sparse-trade artifacts).
    """
    if not isinstance(agg, dict):
        return -10.0, {"error": "missing_aggregate"}
    pcs = agg.get("per_coin_summary")
    if not isinstance(pcs, list):
        return -10.0, {"error": "missing_per_coin_summary"}

    sharpes: List[float] = []
    capped_pfs: List[float] = []
    per_trades: List[int] = []
    for row in pcs:
        if not isinstance(row, dict) or row.get("error"):
            continue
        try:
            sharpes.append(float(row.get("sharpe", 0.0)))
        except Exception:
            sharpes.append(0.0)
        try:
            raw_pf = float(row.get("profit_factor", 0.0))
        except Exception:
            raw_pf = 0.0
        capped_pfs.append(_cap_profit_factor_for_fitness(raw_pf))
        try:
            per_trades.append(int(row.get("trades", 0) or 0))
        except Exception:
            per_trades.append(0)

    if len(sharpes) < 1:
        return -10.0, {"error": "no_valid_symbols"}

    min_s = float(min(sharpes))
    mean_s = float(sum(sharpes) / max(1, len(sharpes)))
    min_pf_c = float(min(capped_pfs)) if capped_pfs else 0.0
    try:
        worst_dd_pct = float(agg.get("worst_max_drawdown_pct", 0.0))
    except Exception:
        worst_dd_pct = 0.0

    core = (
        0.48 * min_s
        + 0.22 * mean_s
        + 0.20 * min_pf_c
        + 0.10 * (-worst_dd_pct / 100.0)
    )
    diag = {
        "fitness_core": round(float(core), 4),
        "min_sharpe": round(min_s, 4),
        "mean_sharpe": round(mean_s, 4),
        "min_profit_factor_capped": round(min_pf_c, 4),
        "per_coin_trades": per_trades,
        "total_trades_all": int(agg.get("total_trades_all", 0) or 0),
    }
    return float(core), diag


def _fitness_penalties_from_multi(res: Dict[str, Any], *, dd_cap_pct: float = 9.0) -> Tuple[float, Dict[str, Any]]:
    """
    Hard robustness penalties to avoid overfitting and regime fragility.
    - Penalize if any coin Sharpe < 0.7
    - Penalize if any coin PF < 1.0
    - Penalize if any coin max DD worse than dd_cap_pct (e.g. -9%)
    - Penalize weak minimum Sharpe (cross-coin floor) and overly sparse activity
    Returns: (penalty, diagnostics)
    """
    agg = (res or {}).get("aggregate") if isinstance(res, dict) else None
    pcs = (agg or {}).get("per_coin_summary") if isinstance(agg, dict) else None
    if not isinstance(pcs, list):
        return 5.0, {"error": "missing per_coin_summary"}

    penalty = 0.0
    flags: Dict[str, Any] = {
        "sharpe_breaches": [],
        "pf_breaches": [],
        "dd_breaches": [],
        "min_sharpe_floor": None,
        "sparse_trades": [],
    }
    sharpes: List[float] = []
    for row in pcs:
        if not isinstance(row, dict):
            continue
        sym = str(row.get("symbol") or "?")
        try:
            sharpe = float(row.get("sharpe", 0.0))
        except Exception:
            sharpe = 0.0
        try:
            pf = float(row.get("profit_factor", 0.0))
        except Exception:
            pf = 0.0
        try:
            dd = float(row.get("max_dd_pct", 0.0))  # negative pct
        except Exception:
            dd = 0.0

        if not row.get("error"):
            sharpes.append(sharpe)

        if sharpe < 0.7:
            penalty += 2.0
            flags["sharpe_breaches"].append({"symbol": sym, "sharpe": sharpe})
        if pf < 1.0:
            penalty += 3.0
            flags["pf_breaches"].append({"symbol": sym, "pf": pf})
        if dd < -abs(dd_cap_pct):
            # scale with severity (each extra 1% beyond cap adds +1.0)
            penalty += 1.0 + (abs(dd) - abs(dd_cap_pct))
            flags["dd_breaches"].append({"symbol": sym, "max_dd_pct": dd})

        if not row.get("error"):
            try:
                nt = int(row.get("trades", 0) or 0)
            except Exception:
                nt = 0
            if nt < 35:
                penalty += 1.2
                flags["sparse_trades"].append({"symbol": sym, "trades": nt, "threshold": 35})

    if sharpes:
        min_sh = float(min(sharpes))
        flags["min_sharpe_floor"] = min_sh
        if min_sh < 0.85:
            penalty += 5.0 * float(0.85 - min_sh)

    try:
        total_tr = int((agg or {}).get("total_trades_all", 0) or 0)
    except Exception:
        total_tr = 0
    n_ok = len(sharpes)
    if n_ok >= 1 and total_tr < max(90, 30 * n_ok):
        penalty += 2.5
        flags["sparse_trades"].append({"symbol": "ALL", "trades": total_tr, "threshold": max(90, 30 * n_ok)})

    return float(penalty), flags


def _agg_metric(agg: Dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float((agg or {}).get(key, default))
    except Exception:
        return float(default)


def _regime_guard_penalty_multi(
    *,
    on_train_agg: Dict[str, Any],
    on_test_agg: Dict[str, Any],
    off_train_agg: Dict[str, Any],
    off_test_agg: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    """
    Penalize gate-ON configs that underperform the same genes with gate OFF.
    Primary checks use OOS test deltas (train is logged for transparency).
    """
    on_te_ms = _agg_metric(on_test_agg, "min_sharpe")
    off_te_ms = _agg_metric(off_test_agg, "min_sharpe")
    on_te_pf = _agg_metric(on_test_agg, "min_profit_factor")
    off_te_pf = _agg_metric(off_test_agg, "min_profit_factor")
    d_te_ms = on_te_ms - off_te_ms
    d_te_pf = on_te_pf - off_te_pf

    on_tr_ms = _agg_metric(on_train_agg, "min_sharpe")
    off_tr_ms = _agg_metric(off_train_agg, "min_sharpe")
    on_tr_pf = _agg_metric(on_train_agg, "min_profit_factor")
    off_tr_pf = _agg_metric(off_train_agg, "min_profit_factor")
    d_tr_ms = on_tr_ms - off_tr_ms
    d_tr_pf = on_tr_pf - off_tr_pf

    penalty = 0.0
    breaches: List[str] = []
    if d_te_ms < -float(_REGIME_GUARD_MAX_DROP_MIN_SHARPE):
        # Strong base penalty + severity scaling to demote harmful gate configs.
        penalty += 6.0 + (abs(d_te_ms) - float(_REGIME_GUARD_MAX_DROP_MIN_SHARPE)) * 30.0
        breaches.append("min_sharpe")
    if d_te_pf < -float(_REGIME_GUARD_MAX_DROP_MIN_PF):
        penalty += 6.0 + (abs(d_te_pf) - float(_REGIME_GUARD_MAX_DROP_MIN_PF)) * 10.0
        breaches.append("min_profit_factor")

    detail = {
        "enabled": True,
        "thresholds": {
            "max_drop_min_sharpe": float(_REGIME_GUARD_MAX_DROP_MIN_SHARPE),
            "max_drop_min_profit_factor": float(_REGIME_GUARD_MAX_DROP_MIN_PF),
        },
        "test_delta_vs_gate_off": {
            "min_sharpe": round(float(d_te_ms), 4),
            "min_profit_factor": round(float(d_te_pf), 4),
        },
        "train_delta_vs_gate_off": {
            "min_sharpe": round(float(d_tr_ms), 4),
            "min_profit_factor": round(float(d_tr_pf), 4),
        },
        "breaches": breaches,
        "penalty": round(float(penalty), 4),
    }
    return float(penalty), detail


def _promotion_test_metrics_snapshot(detail: Dict[str, Any]) -> Dict[str, Any]:
    """
    OOS metrics shape used by the promotion log table (multi-coin aggregate or single-coin metrics).
    """
    src: Any = None
    tm = detail.get("test_metrics")
    if isinstance(tm, dict) and tm:
        src = tm
    elif isinstance(detail.get("test"), dict):
        agg = (detail["test"] or {}).get("aggregate")
        if isinstance(agg, dict) and agg:
            src = agg
    if not isinstance(src, dict):
        return {}
    multi_keys = ("min_sharpe", "min_profit_factor", "worst_max_drawdown_pct", "total_trades_all")
    if any(k in src for k in multi_keys):
        return {k: src[k] for k in multi_keys if k in src}
    out: Dict[str, Any] = {}
    if "sharpe_ratio" in src:
        out["min_sharpe"] = src["sharpe_ratio"]
    if "profit_factor" in src:
        out["min_profit_factor"] = src["profit_factor"]
    if "max_drawdown" in src:
        out["worst_max_drawdown_pct"] = src["max_drawdown"]
    if "total_trades" in src:
        out["total_trades_all"] = src["total_trades"]
    return out


def evaluate_config_oos_multi(
    cfg: Config,
    *,
    symbols: List[str],
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    baseline_genes: Dict[str, Any],
    dd_cap_pct: float = 9.0,
) -> Tuple[float, Dict[str, Any]]:
    """
    Robustness-first multi-coin fitness.
    Uses a capped-PF, min-Sharpe-heavy core score from run_multi aggregates plus hard penalties.
    """
    c_tr = clone_config_genes(cfg)
    c_tr.BACKTEST_START_DATE = train_start
    c_tr.BACKTEST_END_DATE = train_end
    r_tr = _run_multi_silent(cfg=c_tr, symbols=symbols, start_date=train_start, end_date=train_end, max_workers=1)

    c_te = clone_config_genes(cfg)
    c_te.BACKTEST_START_DATE = test_start
    c_te.BACKTEST_END_DATE = test_end
    r_te = _run_multi_silent(cfg=c_te, symbols=symbols, start_date=test_start, end_date=test_end, max_workers=1)

    tr_agg = (r_tr or {}).get("aggregate") if isinstance(r_tr, dict) else {}
    te_agg = (r_te or {}).get("aggregate") if isinstance(r_te, dict) else {}
    tr_core, tr_core_diag = _multi_coin_fitness_core(tr_agg if isinstance(tr_agg, dict) else {})
    te_core, te_core_diag = _multi_coin_fitness_core(te_agg if isinstance(te_agg, dict) else {})

    # Hard robustness penalties (avoid overfitting).
    p_tr, p_tr_diag = _fitness_penalties_from_multi(r_tr, dd_cap_pct=dd_cap_pct)
    p_te, p_te_diag = _fitness_penalties_from_multi(r_te, dd_cap_pct=dd_cap_pct)

    # Parsimony vs baseline (favor baseline unless a change clearly helps).
    genes = {a: getattr(cfg, a) for a, _ in GENE_SPACE}
    ppen = parsimony_penalty_vs_baseline(genes, baseline_genes)

    # Conservative weighting: mostly OOS test core, some train core.
    fit = (0.65 * te_core) + (0.25 * tr_core) - (0.10 * (p_tr + p_te)) - float(ppen)
    regime_guard_detail: Dict[str, Any] = {"enabled": False, "penalty": 0.0}
    if bool(getattr(cfg, "REGIME_GATE_ENABLED", False)):
        c_tr_off = clone_config_genes(cfg)
        c_tr_off.REGIME_GATE_ENABLED = False
        c_tr_off.BACKTEST_START_DATE = train_start
        c_tr_off.BACKTEST_END_DATE = train_end
        r_tr_off = _run_multi_silent(cfg=c_tr_off, symbols=symbols, start_date=train_start, end_date=train_end, max_workers=1)

        c_te_off = clone_config_genes(cfg)
        c_te_off.REGIME_GATE_ENABLED = False
        c_te_off.BACKTEST_START_DATE = test_start
        c_te_off.BACKTEST_END_DATE = test_end
        r_te_off = _run_multi_silent(cfg=c_te_off, symbols=symbols, start_date=test_start, end_date=test_end, max_workers=1)

        tr_off_agg = (r_tr_off or {}).get("aggregate") if isinstance(r_tr_off, dict) else {}
        te_off_agg = (r_te_off or {}).get("aggregate") if isinstance(r_te_off, dict) else {}
        rg_pen, regime_guard_detail = _regime_guard_penalty_multi(
            on_train_agg=tr_agg if isinstance(tr_agg, dict) else {},
            on_test_agg=te_agg if isinstance(te_agg, dict) else {},
            off_train_agg=tr_off_agg if isinstance(tr_off_agg, dict) else {},
            off_test_agg=te_off_agg if isinstance(te_off_agg, dict) else {},
        )
        fit = float(fit - rg_pen)

    detail = {
        "fitness": float(fit),
        "train": {
            "aggregate": tr_agg,
            "penalty": p_tr,
            "penalty_detail": p_tr_diag,
            "fitness_core": tr_core,
            "fitness_core_detail": tr_core_diag,
            "robustness_score_display": tr_agg.get("robustness_score") if isinstance(tr_agg, dict) else None,
        },
        "test": {
            "aggregate": te_agg,
            "penalty": p_te,
            "penalty_detail": p_te_diag,
            "fitness_core": te_core,
            "fitness_core_detail": te_core_diag,
            "robustness_score_display": te_agg.get("robustness_score") if isinstance(te_agg, dict) else None,
        },
        "genes": genes,
        "parsimony_penalty": float(ppen),
        "regime_train_aggregate": aggregate_regime_from_multi_result(r_tr),
        "regime_test_aggregate": aggregate_regime_from_multi_result(r_te),
        "regime_guard": regime_guard_detail,
    }
    return float(fit), detail


def split_train_test(start_date: str, end_date: str, train_frac: float = 0.7) -> Tuple[str, str, str, str]:
    s = datetime.strptime(start_date, "%Y-%m-%d")
    e = datetime.strptime(end_date, "%Y-%m-%d")
    days = max(1, (e - s).days)
    split_days = max(30, int(days * train_frac))
    train_end = min(s + timedelta(days=split_days), e - timedelta(days=14))
    test_start = train_end + timedelta(days=1)
    if test_start >= e:
        test_start = e - timedelta(days=30)
        train_end = test_start - timedelta(days=1)
    return (
        start_date,
        train_end.strftime("%Y-%m-%d"),
        test_start.strftime("%Y-%m-%d"),
        end_date,
    )


def _baseline_gene_dict(cfg: Config) -> Dict[str, Any]:
    return {attr: getattr(cfg, attr) for attr, _ in GENE_SPACE}


def clone_config_genes(source: Config) -> Config:
    """Fresh spec load + copy traded genes and run identifiers from source."""
    c = build_config()
    c.SYMBOL = source.SYMBOL
    c.TIMEFRAME = source.TIMEFRAME
    c.INITIAL_CAPITAL = source.INITIAL_CAPITAL
    c.BINANCE_API = source.BINANCE_API
    c.COMMISSION = source.COMMISSION
    c.SLIPPAGE = source.SLIPPAGE
    wl = getattr(source, "WATCHLIST", None)
    if wl is not None:
        c.WATCHLIST = wl
    for attr, _ in GENE_SPACE:
        setattr(c, attr, getattr(source, attr))
    for attr in _REGIME_CFG_ATTRS:
        if hasattr(source, attr) and hasattr(c, attr):
            setattr(c, attr, getattr(source, attr))
    return c


def random_genome_config(rng: random.Random, baseline: Config) -> Tuple[Config, Dict[str, Any]]:
    """Randomly set each gene from its grid (full exploration around YAML defaults)."""
    c = clone_config_genes(baseline)
    g: Dict[str, Any] = {}
    for attr, choices in GENE_SPACE:
        pick = rng.choice(choices)
        setattr(c, attr, pick)
        g[attr] = pick
    return c, g


def mutate_genome_config(rng: random.Random, parent: Config, rate: float = 0.35) -> Tuple[Config, Dict[str, Any]]:
    c = clone_config_genes(parent)
    g: Dict[str, Any] = {}
    for attr, choices in GENE_SPACE:
        cur = getattr(c, attr)
        if rng.random() < rate:
            pool = [x for x in choices if x != cur]
            pick = rng.choice(pool) if pool else cur
            setattr(c, attr, pick)
        g[attr] = getattr(c, attr)
    return c, g


def evaluate_config_oos(
    cfg: Config,
    train_start: str,
    train_end: str,
    test_start: str,
    test_end: str,
    baseline_genes: Dict[str, Any],
    crisis_metrics: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[float, Dict[str, Any]]:
    c_tr = clone_config_genes(cfg)
    c_tr.BACKTEST_START_DATE = train_start
    c_tr.BACKTEST_END_DATE = train_end
    try:
        r_tr = run_backtest_silent(c_tr)
        m_tr = r_tr["metrics"]
    except Exception as e:
        m_tr = {"error": str(e)}

    c_te = clone_config_genes(cfg)
    c_te.BACKTEST_START_DATE = test_start
    c_te.BACKTEST_END_DATE = test_end
    try:
        r_te = run_backtest_silent(c_te)
        m_te = r_te["metrics"]
    except Exception as e:
        m_te = {"error": str(e)}

    genes = {a: getattr(cfg, a) for a, _ in GENE_SPACE}
    ppen = parsimony_penalty_vs_baseline(genes, baseline_genes)
    fit = composite_fitness(m_tr, m_te, crisis_metrics, parsimony_penalty=ppen)
    detail = {
        "fitness": fit,
        "train_metrics": m_tr,
        "test_metrics": m_te,
        "genes": genes,
        "parsimony_penalty": ppen,
        "regime_summary_train": m_tr.get("regime_summary") if isinstance(m_tr, dict) else None,
        "regime_summary_test": m_te.get("regime_summary") if isinstance(m_te, dict) else None,
    }
    return fit, detail


def walk_forward_oos(
    *,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: float,
    train_frac: float = 0.7,
    runtime_cfg: Optional[Config] = None,
) -> Dict[str, Any]:
    spec_baseline = build_config()
    base = clone_config_genes(runtime_cfg) if runtime_cfg is not None else build_config()
    base.SYMBOL = symbol.upper().replace("/", "")
    base.TIMEFRAME = timeframe
    base.BACKTEST_START_DATE = start_date
    base.BACKTEST_END_DATE = end_date
    base.INITIAL_CAPITAL = float(initial_capital)

    tr_s, tr_e, te_s, te_e = split_train_test(start_date, end_date, train_frac)
    baseline_genes = _baseline_gene_dict(spec_baseline)
    fit, detail = evaluate_config_oos(base, tr_s, tr_e, te_s, te_e, baseline_genes, crisis_metrics=None)
    detail["windows"] = {"train": [tr_s, tr_e], "test": [te_s, te_e]}
    detail["composite_fitness"] = fit
    return detail


def stress_crisis_windows(
    *,
    symbol: str,
    timeframe: str,
    initial_capital: float,
    cfg: Optional[Config] = None,
) -> Dict[str, Any]:
    ref = cfg if cfg is not None else build_config()
    base = clone_config_genes(ref)
    base.SYMBOL = symbol.upper().replace("/", "")
    base.TIMEFRAME = timeframe
    base.INITIAL_CAPITAL = float(initial_capital)

    rows: List[Dict[str, Any]] = []
    for w in CRISIS_WINDOWS:
        c = clone_config_genes(base)
        c.BACKTEST_START_DATE = w["start"]
        c.BACKTEST_END_DATE = w["end"]
        try:
            r = run_backtest_silent(c)
            m = r["metrics"]
        except Exception as e:
            m = {"error": str(e)}
        rows.append(
            {
                "id": w["id"],
                "label": w["label"],
                "start": w["start"],
                "end": w["end"],
                "metrics": m,
            }
        )
    crisis_metric_dicts = [x["metrics"] for x in rows]
    return {"windows": rows, "crisis_metric_dicts": crisis_metric_dicts}


def run_evolution(
    *,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
    initial_capital: float,
    population: int = 10,
    generations: int = 2,
    seed: Optional[int] = None,
    train_frac: float = 0.7,
    verify_top_k_crisis: int = 3,
    runtime_cfg: Optional[Config] = None,
    symbols: Optional[List[str]] = None,
) -> Dict[str, Any]:
    rng = random.Random(seed)
    spec_baseline = build_config()
    base = clone_config_genes(runtime_cfg) if runtime_cfg is not None else build_config()
    base.SYMBOL = symbol.upper().replace("/", "")
    base.TIMEFRAME = timeframe
    base.INITIAL_CAPITAL = float(initial_capital)
    base.BACKTEST_START_DATE = start_date
    base.BACKTEST_END_DATE = end_date

    tr_s, tr_e, te_s, te_e = split_train_test(start_date, end_date, train_frac)
    baseline_genes = _baseline_gene_dict(spec_baseline)
    multi_syms = [str(x).upper().replace("/", "") for x in (symbols or []) if x]
    if not multi_syms:
        multi_syms = [base.SYMBOL]

    # Initial population: baseline + random mutants
    population_cfgs: List[Config] = [clone_config_genes(base)]
    while len(population_cfgs) < population:
        c, _ = random_genome_config(rng, base)
        c.SYMBOL = base.SYMBOL
        c.TIMEFRAME = base.TIMEFRAME
        c.INITIAL_CAPITAL = base.INITIAL_CAPITAL
        population_cfgs.append(c)

    history: List[Dict[str, Any]] = []
    last_scored: List[Tuple[float, Config, Dict[str, Any]]] = []

    for gen in range(generations):
        print(
            f"[evolution] generation {gen + 1}/{generations} | population={len(population_cfgs)} | symbols={multi_syms}",
            flush=True,
        )
        scored: List[Tuple[float, Config, Dict[str, Any]]] = []
        for c in population_cfgs:
            if len(multi_syms) > 1:
                fit, detail = evaluate_config_oos_multi(
                    c,
                    symbols=multi_syms,
                    train_start=tr_s,
                    train_end=tr_e,
                    test_start=te_s,
                    test_end=te_e,
                    baseline_genes=baseline_genes,
                    dd_cap_pct=9.0,
                )
            else:
                fit, detail = evaluate_config_oos(c, tr_s, tr_e, te_s, te_e, baseline_genes, crisis_metrics=None)
            scored.append((fit, c, detail))
        scored.sort(key=lambda x: x[0], reverse=True)
        last_scored = scored
        print(
            f"[evolution] gen {gen + 1} best_fitness={scored[0][0]:.4f}",
            flush=True,
        )
        print(
            f"[evolution] regime_train {format_evolution_regime_log(scored[0][2])}",
            flush=True,
        )
        history.append(
            {
                "generation": gen,
                "best_fitness": scored[0][0],
                "best_genes": {a: getattr(scored[0][1], a) for a, _ in GENE_SPACE},
                "regime_train_log": format_evolution_regime_log(scored[0][2]),
                "regime_train": regime_snapshot_from_detail(scored[0][2]),
            }
        )

        if gen == generations - 1:
            break

        # Selection: top half + mutated children
        keep_n = max(2, population // 2)
        survivors = [x[1] for x in scored[:keep_n]]
        next_pop: List[Config] = survivors[:]
        while len(next_pop) < population:
            parent = rng.choice(survivors)
            child, _ = mutate_genome_config(rng, parent, rate=0.4)
            child.SYMBOL = base.SYMBOL
            child.TIMEFRAME = base.TIMEFRAME
            child.INITIAL_CAPITAL = base.INITIAL_CAPITAL
            next_pop.append(child)
        population_cfgs = next_pop

    # Optional crisis verification on top-k (expensive)
    top_reports: List[Dict[str, Any]] = []
    k = min(verify_top_k_crisis, len(last_scored))
    print(f"[evolution] crisis verification top_k={k} (this can take a while)", flush=True)
    for i in range(k):
        fit, c, detail = last_scored[i]
        if len(multi_syms) > 1:
            # Conservative crisis check: use robustness penalties on each crisis slice.
            crisis_rows: List[Dict[str, Any]] = []
            crisis_penalties: List[float] = []
            for w in CRISIS_WINDOWS:
                cc = clone_config_genes(c)
                r = _run_multi_silent(cfg=cc, symbols=multi_syms, start_date=w["start"], end_date=w["end"], max_workers=1)
                pen, _ = _fitness_penalties_from_multi(r, dd_cap_pct=9.0)
                crisis_penalties.append(float(pen))
                crisis_rows.append({"id": w["id"], "label": w["label"], "start": w["start"], "end": w["end"], "aggregate": r.get("aggregate")})

            # Re-score: base OOS fitness minus average crisis penalty (conservative).
            fit2, detail2 = evaluate_config_oos_multi(
                c,
                symbols=multi_syms,
                train_start=tr_s,
                train_end=tr_e,
                test_start=te_s,
                test_end=te_e,
                baseline_genes=baseline_genes,
                dd_cap_pct=9.0,
            )
            fit2 = float(fit2 - (sum(crisis_penalties) / max(1, len(crisis_penalties))) * 0.10)
            detail2["crisis_penalties_avg"] = float(sum(crisis_penalties) / max(1, len(crisis_penalties)))
            detail2["crisis_windows"] = crisis_rows
        else:
            stress = stress_crisis_windows(symbol=symbol, timeframe=timeframe, initial_capital=initial_capital, cfg=c)
            crisis_dicts = stress["crisis_metric_dicts"]
            fit2, detail2 = evaluate_config_oos(c, tr_s, tr_e, te_s, te_e, baseline_genes, crisis_metrics=crisis_dicts)
        top_reports.append(
            {
                "rank": i + 1,
                "composite_fitness_fast": fit,
                "composite_fitness_with_crisis": fit2,
                "genes": detail2["genes"],
                "train_metrics": detail2.get("train_metrics") or detail2.get("train", {}).get("aggregate"),
                "test_metrics": detail2.get("test_metrics") or detail2.get("test", {}).get("aggregate"),
                "promotion_aggregate": _promotion_test_metrics_snapshot(detail2),
                "crisis_windows": detail2.get("crisis_windows") or [],
            }
        )

    best_regime_train = regime_snapshot_from_detail(last_scored[0][2]) if last_scored else None

    return {
        "train_window": [tr_s, tr_e],
        "test_window": [te_s, te_e],
        "baseline_genes": baseline_genes,
        "generations_run": generations,
        "population": population,
        "history": history,
        "top": top_reports,
        "symbols": multi_syms,
        "best_regime_train": best_regime_train,
    }
