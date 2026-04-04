"""
Rule-based research suggestions (Atlas-style autoresearch-lite).
No proprietary prompts — heuristics over stored runs + regime. Optional OpenAI if OPENAI_API_KEY is set.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import requests

from config import Config


def config_snapshot(cfg: Config) -> Dict[str, Any]:
    return {
        "SYMBOL": cfg.SYMBOL,
        "WATCHLIST": getattr(cfg, "WATCHLIST", None),
        "MIN_SIGNAL_STRENGTH": cfg.MIN_SIGNAL_STRENGTH,
        "MIN_CONFLUENCE": cfg.MIN_CONFLUENCE,
        "RISK_PER_TRADE": cfg.RISK_PER_TRADE,
        "ATR_MULTIPLIER": cfg.ATR_MULTIPLIER,
        "MAX_CANDLES_HOLD": cfg.MAX_CANDLES_HOLD,
        "TP1_RATIO": cfg.TP1_RATIO,
        "TP2_RATIO": cfg.TP2_RATIO,
        "TP3_RATIO": cfg.TP3_RATIO,
        "TRAIL_AFTER_TP1": cfg.TRAIL_AFTER_TP1,
        "FVG_THRESHOLD": cfg.FVG_THRESHOLD,
        "LIQUIDITY_BUFFER": cfg.LIQUIDITY_BUFFER,
        "ICT_RANGE_HOURS": cfg.ICT_RANGE_HOURS,
    }


def rule_based_suggest(latest: Dict[str, Any], recent: List[Dict[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("## AutoResearchClaw — heuristic review")
    lines.append("")
    lines.append("*Automated suggestions only; verify with another backtest before changing production config.*")
    lines.append("")

    if not latest or latest.get("had_error"):
        m = (latest or {}).get("metrics") or {}
        err = m.get("error", "No valid run")
        lines.append(f"- Latest run: **{err}**. Widen the date range or relax filters if you see no trades.")
        lines.append("- Try lowering **MIN_SIGNAL_STRENGTH** (e.g. 60) or **MIN_CONFLUENCE** (e.g. 1) temporarily to test sensitivity.")
        return "\n".join(lines)

    m = latest["metrics"]
    r = latest.get("regime") or {}
    tag = latest.get("regime_tag", "?")
    cfg = latest.get("config") or {}

    lines.append(f"- **Regime tag:** `{tag}` (vol: {r.get('vol_regime')}, trend: {r.get('trend_regime')})")
    lines.append(
        f"- **Result:** P&L **{m.get('total_pnl', 0)}** ({m.get('total_pnl_pct', 0)}%), "
        f"Sharpe **{m.get('sharpe_ratio', 0)}**, max DD **{m.get('max_drawdown', 0)}%**, "
        f"trades **{m.get('total_trades', 0)}**"
    )
    lines.append("")

    sharpe = float(m.get("sharpe_ratio") or 0)
    dd = float(m.get("max_drawdown") or 0)
    wr = float(m.get("win_rate") or 0)
    pf = float(m.get("profit_factor") or 0)
    if pf > 900:
        pf = 0.0

    suggestions: List[str] = []

    if sharpe < 0 and dd > 15:
        suggestions.append(
            "Sharpe negative with deep drawdown → consider **reducing RISK_PER_TRADE** (e.g. 0.0075) or **tightening MAX_CANDLES_HOLD** to cut tail risk."
        )

    if wr < 40 and m.get("total_trades", 0) >= 15:
        suggestions.append(
            "Low win rate with enough samples → try **raising MIN_SIGNAL_STRENGTH** (e.g. +5) or **MIN_CONFLUENCE** (+1) to filter weaker ICT setups."
        )

    if r.get("vol_regime") == "high_vol" and sharpe < 0.5:
        suggestions.append(
            "**High-vol regime** underperforming → consider **wider ATR_MULTIPLIER** (e.g. 1.75–2.0) or shorter **TIMEFRAME** to avoid whip."
        )

    if r.get("vol_regime") == "low_vol" and m.get("total_trades", 0) < 5:
        suggestions.append(
            "**Low-vol / few trades** → slightly **lower FVG_THRESHOLD** or **LIQUIDITY_BUFFER** may increase signals; re-backtest immediately."
        )

    if r.get("trend_regime") == "range" and pf > 0 and pf < 1.2:
        suggestions.append(
            "Ranging tape with mediocre factor → ICT often needs clear sweeps; consider **ICT_RANGE_HOURS** tweak or higher timeframe for context."
        )

    mss = cfg.get("MIN_SIGNAL_STRENGTH", 65)
    if sharpe > 1.0 and wr > 45:
        suggestions.append(
            f"Solid edge (Sharpe {sharpe:.2f}) → optional experiment: **slightly lower MIN_SIGNAL_STRENGTH** ({mss}→{max(55, mss - 3)}) to capture more trades; confirm on walk-forward."
        )

    if not suggestions:
        suggestions.append("No strong automated flags — compare this run to **prior runs in the same regime_tag** in `/api/runs`.")

    lines.append("### Suggested experiments")
    for s in suggestions:
        lines.append(f"- {s}")
    lines.append("")

    if len(recent) >= 2:
        tags = [x.get("regime_tag") for x in recent[:8]]
        from collections import Counter

        c = Counter(tags)
        lines.append("### Recent regime mix (last few runs)")
        for t, n in c.most_common(5):
            lines.append(f"- `{t}` × {n}")
        lines.append("")

    lines.append("### Config snapshot (this run)")
    for k, v in sorted(cfg.items()):
        lines.append(f"- **{k}:** `{v}`")

    return "\n".join(lines)


def llm_suggest(latest: Dict[str, Any], recent: List[Dict[str, Any]]) -> Optional[str]:
    key = os.getenv("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    summary = rule_based_suggest(latest, recent)
    prompt = (
        "You are a quant research assistant. Given the following backtest summary and heuristics, "
        "reply with 5–8 bullet points: what to test next, risks, and one conservative config tweak. "
        "Do not promise returns. Keep under 250 words.\n\n" + summary
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": os.getenv("OPENAI_RESEARCH_MODEL", "gpt-4o-mini"),
                "messages": [
                    {"role": "system", "content": "Concise trading research assistant. No financial advice disclaimer in every line."},
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 500,
                "temperature": 0.4,
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        return f"(LLM unavailable: {e})\n\n---\n\n" + summary


def build_suggestion(recent: List[Dict[str, Any]], use_llm: bool) -> Dict[str, Any]:
    if not recent:
        return {
            "source": "rules",
            "markdown": "Run at least one backtest to generate suggestions.",
        }
    latest = recent[0]
    if use_llm:
        text = llm_suggest(latest, recent)
        if text and not text.startswith("(LLM unavailable"):
            return {"source": "openai", "markdown": text}
        return {"source": "rules+fallback", "markdown": text or rule_based_suggest(latest, recent)}
    return {"source": "rules", "markdown": rule_based_suggest(latest, recent)}
