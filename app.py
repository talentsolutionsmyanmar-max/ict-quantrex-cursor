from flask import Flask, render_template, jsonify, request, redirect, url_for, Response
from flask_socketio import SocketIO, emit
from werkzeug.middleware.proxy_fix import ProxyFix
import threading

from backtester import Backtester
from paper_trader import PaperTrader
from paper_alerts import send_telegram_sync
from config import build_config
from regime import detect_regime
from run_store import insert_run, list_runs, get_run
from promotion_store import insert_promotion_decision, list_promotion_decisions
from research_suggest import build_suggestion, config_snapshot
from unusual_whales_client import UnusualWhalesClient
from health_service import get_health_snapshot
from monitoring.supabase_rest_logger import calculate_24h_paper_pnl, fetch_recent_trades, get_live_trades_row_count
from core.live_market_feed import LiveMarketFeed
from session_clock import get_session_state
from strategy.load_spec import public_spec_dict
from risk_engine import RiskEngine
from trade_playbook import list_playbook_events
from mmt_client import fetch_candles, fetch_orderbook, fetch_stats, fetch_vd, mmt_configured
from research_lab import (
    walk_forward_oos,
    stress_crisis_windows,
    run_evolution,
    CRISIS_WINDOWS,
    apply_research_genes,
    runtime_gene_snapshot,
    copy_research_genes,
)
from quant_rigor import build_runner_from_lab, run_cost_stress, run_stability_sweep
from kz_research_store import list_kz_runs
from kz_autoresearch import run_kz_research_once, start_background_poller
import os
import subprocess
from pathlib import Path
import re
import time
import uuid
import json
import sys

ALLOWED_TIMEFRAMES = frozenset(
    {
        "1m",
        "3m",
        "5m",
        "15m",
        "30m",
        "1h",
        "2h",
        "4h",
        "6h",
        "8h",
        "12h",
        "1d",
        "3d",
        "1w",
    }
)

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("FLASK_SECRET_KEY", "autoresearchclaw-dev-secret")
# Behind Caddy/nginx: trust X-Forwarded-Proto/Host so Socket.IO and redirects use https://your-domain
if os.getenv("TRUST_PROXY", "").strip().lower() in ("1", "true", "yes", "on"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
_async_mode = os.getenv("SOCKETIO_ASYNC_MODE", "threading").strip().lower() or "threading"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode=_async_mode)


# Global state
trading_state = {
    "mode": "BACKTEST",
    "is_running": False,
    "current_signal": None,
    "live_metrics": {},
}

config = build_config()
paper_trader = None
risk_engine = RiskEngine(config)
BACKTEST_JOBS: dict[str, dict] = {}
BACKTEST_JOBS_LOCK = threading.Lock()
BACKTEST_JOB_TTL_SEC = 3600
KZ_JOBS: dict[str, dict] = {}
KZ_JOBS_LOCK = threading.Lock()
KZ_JOB_TTL_SEC = 3600
KZ_JOB_DIR = Path(__file__).resolve().parent / "data" / "kz_jobs"
PID_FILE = Path("data/quantrex.pid")


def is_paper_running() -> bool:
    """Check standalone paper trader process health from PID file."""
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        if os.name == "nt":
            probe = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = (probe.stdout or "").strip()
            running = bool(out) and "No tasks are running" not in out
            if not running and PID_FILE.exists():
                PID_FILE.unlink()
            return running
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, FileNotFoundError, ValueError, PermissionError, OSError):
        try:
            if PID_FILE.exists():
                PID_FILE.unlink()
        except Exception:
            pass
        return False


def _clean_backtest_jobs() -> None:
    cutoff = time.time() - BACKTEST_JOB_TTL_SEC
    with BACKTEST_JOBS_LOCK:
        stale = [
            jid
            for jid, job in BACKTEST_JOBS.items()
            if job.get("state") in {"done", "failed"} and float(job.get("updated_at", 0.0)) < cutoff
        ]
        for jid in stale:
            BACKTEST_JOBS.pop(jid, None)


def _build_backtest_response(payload: dict) -> dict:
    # Fresh spec + in-session genes (Apply from Research lab), then request overrides.
    run_config = build_config()
    copy_research_genes(config, run_config)
    if "start_date" in payload:
        run_config.BACKTEST_START_DATE = payload["start_date"]
    if "end_date" in payload:
        run_config.BACKTEST_END_DATE = payload["end_date"]
    if "initial_capital" in payload:
        run_config.INITIAL_CAPITAL = float(payload["initial_capital"])
    if "symbol" in payload and payload["symbol"]:
        run_config.SYMBOL = str(payload["symbol"]).upper().replace("/", "")
    tf = payload.get("timeframe") or payload.get("interval")
    if tf and str(tf) in ALLOWED_TIMEFRAMES:
        run_config.TIMEFRAME = str(tf)
    if payload.get("min_signal_strength") is not None:
        try:
            run_config.MIN_SIGNAL_STRENGTH = float(payload["min_signal_strength"])
        except (TypeError, ValueError):
            pass
    if payload.get("min_confluence") is not None:
        try:
            run_config.MIN_CONFLUENCE = int(payload["min_confluence"])
        except (TypeError, ValueError):
            pass

    # API backtests should stay quiet to avoid log spam slowing requests under Gunicorn.
    results = Backtester(run_config).run(verbose=False)
    # Ensure diagnostics print immediately (debug server + reloader can buffer stdout)
    try:
        subprocess.run(["/bin/sh", "-lc", "true"], check=False)
    except Exception:
        pass

    df = results["df"].copy()
    signals_df = df[["signal", "signal_strength"]].tail(100).copy()

    n = len(df)
    step = max(1, n // 800)
    chart_slice = df.iloc[::step]
    price_bars = []
    for _, row in chart_slice.iterrows():
        ts = row["timestamp"]
        ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
        price_bars.append(
            {
                "t": ts_str,
                "o": float(row["open"]),
                "h": float(row["high"]),
                "l": float(row["low"]),
                "c": float(row["close"]),
            }
        )

    eq = results["equity_curve"]
    equity_sample = eq[:: max(1, len(eq) // 400)] if eq else []

    regime = detect_regime(df)
    try:
        run_id = insert_run(
            symbol=run_config.SYMBOL,
            timeframe=run_config.TIMEFRAME,
            start_date=run_config.BACKTEST_START_DATE,
            end_date=run_config.BACKTEST_END_DATE,
            initial_capital=float(run_config.INITIAL_CAPITAL),
            regime=regime,
            metrics=results["metrics"],
            config_snapshot=config_snapshot(run_config),
        )
    except Exception as persist_err:
        run_id = None
        print(f"Run store warning: {persist_err}")

    return {
        "success": True,
        "run_id": run_id,
        "regime": regime,
        "metrics": results["metrics"],
        "trades": results["trades"][-20:],
        "equity_curve": equity_sample,
        "signals": signals_df.to_dict("records"),
        "price_bars": price_bars,
        "meta": {
            "symbol": run_config.SYMBOL,
            "timeframe": run_config.TIMEFRAME,
        },
    }


def _run_backtest_job(job_id: str, payload: dict) -> None:
    with BACKTEST_JOBS_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if not job:
            return
        job["state"] = "running"
        job["updated_at"] = time.time()
    try:
        result = _build_backtest_response(payload)
        with BACKTEST_JOBS_LOCK:
            job = BACKTEST_JOBS.get(job_id)
            if job:
                job["state"] = "done"
                job["result"] = result
                job["updated_at"] = time.time()
    except Exception as e:
        with BACKTEST_JOBS_LOCK:
            job = BACKTEST_JOBS.get(job_id)
            if job:
                job["state"] = "failed"
                job["error"] = str(e)
                job["updated_at"] = time.time()


def _kz_job_path(job_id: str) -> Path:
    return KZ_JOB_DIR / f"{job_id}.json"


def _write_kz_job(job_id: str, payload: dict) -> None:
    KZ_JOB_DIR.mkdir(parents=True, exist_ok=True)
    _kz_job_path(job_id).write_text(json.dumps(payload), encoding="utf-8")


def _read_kz_job(job_id: str) -> dict | None:
    p = _kz_job_path(job_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _clean_kz_jobs() -> None:
    KZ_JOB_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - KZ_JOB_TTL_SEC
    for p in KZ_JOB_DIR.glob("*.json"):
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            if st.get("state") in {"done", "failed"} and float(st.get("updated_at", 0.0)) < cutoff:
                p.unlink(missing_ok=True)
        except Exception:
            continue


def start_paper_trading_internal():
    """Start the paper loop (used by /api/paper/start and optional VPS autostart)."""
    global paper_trader
    if trading_state["is_running"]:
        return False, "Already running", None
    allow, reasons = risk_engine.allow_new_risk(mode="PAPER", symbol=config.SYMBOL)
    if not allow:
        return False, reasons[0] if reasons else "Risk gate blocked", 403
    paper_trader = PaperTrader(config, socketio)
    trading_state["is_running"] = True
    trading_state["mode"] = "PAPER"
    thread = threading.Thread(target=paper_trader.run, daemon=True)
    thread.start()
    return True, None, None


def start_background_services():
    """Run once per process: KZ autoresearch poller + optional paper autostart (Gunicorn or dev server)."""
    if getattr(start_background_services, "_done", False):
        return
    start_background_services._done = True

    _kz = os.getenv("KZ_AUTO_RESEARCH", "").strip().lower()
    if _kz in ("1", "true", "yes", "on"):
        start_background_poller(lambda: config, interval_sec=float(os.getenv("KZ_POLL_INTERVAL_SEC", "60")))

    _auto = os.getenv("PAPER_TRADING_AUTOSTART", "").strip().lower()
    if _auto in ("1", "true", "yes", "on"):
        ok, err, _status = start_paper_trading_internal()
        if ok:
            print("PAPER_TRADING_AUTOSTART: paper trading loop started")
        else:
            print(f"PAPER_TRADING_AUTOSTART: not started ({err})")


@app.route("/")
def dashboard():
    return render_template("dashboard.html")


@app.route("/live")
def live_watch():
    """Dedicated multi-symbol live view (same Socket.IO feed as dashboard)."""
    return render_template("live_watch.html")


@app.route("/live-action")
def live_action_redirect():
    """Short memorable URL for the live watchlist grid (bookmarks + QR)."""
    return redirect(url_for("live_watch"), code=302)


@app.route("/action")
def live_action_short():
    """Alias for /live-action."""
    return redirect(url_for("live_watch"), code=302)


_STUB_HTML = """<!doctype html><html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>{title}</title>
<style>body{{font-family:system-ui,Segoe UI,sans-serif;background:#0b0f14;color:#e8eef5;
max-width:52rem;margin:2rem auto;padding:0 1.2rem;line-height:1.5}}
a{{color:#5ec8ff}}code{{background:#121922;padding:0.1rem 0.35rem;border-radius:4px}}</style>
</head><body>
<h1>{title}</h1>
<p>{body}</p>
<p><a href="/">Open dashboard</a> · <a href="/live">Live watch</a></p>
</body></html>"""


@app.route("/features")
def stub_features():
    """Avoid bare 404 on this host; marketing site may live elsewhere."""
    html = _STUB_HTML.format(
        title="QuantRex — Features",
        body="This Flask host serves the trading dashboard and APIs. Product marketing pages "
        "may be on your main domain. For order-flow visualization stacks, compare dedicated "
        "tools (e.g. MMT: <a href=\"https://mmt.gg/\">mmt.gg</a>) with what you integrate via "
        "<code>/api/mmt/*</code> on the backend.",
    )
    return Response(html, mimetype="text/html")


@app.route("/pricing")
def stub_pricing():
    html = _STUB_HTML.format(
        title="QuantRex — Pricing",
        body="Pricing is not served from this engine host. Use your public site or billing "
        "provider. This server exposes APIs such as <code>/api/health</code> and "
        "<code>/api/strategy-spec</code> for operators.",
    )
    return Response(html, mimetype="text/html")


@app.route("/api/backtest", methods=["POST"])
def run_backtest():
    """Run backtest and return results"""
    try:
        payload = request.get_json(silent=True) or {}
        return jsonify(_build_backtest_response(payload))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/backtest/start", methods=["POST"])
def api_backtest_start():
    """Queue a backtest job and return immediately for timeout-safe polling."""
    payload = request.get_json(silent=True) or {}
    _clean_backtest_jobs()
    with BACKTEST_JOBS_LOCK:
        running = sum(1 for j in BACKTEST_JOBS.values() if j.get("state") == "running")
        if running >= 1:
            return (
                jsonify(
                    {
                        "success": False,
                        "error": "Another backtest is already running. Wait for it to finish.",
                    }
                ),
                429,
            )
        job_id = uuid.uuid4().hex[:12]
        BACKTEST_JOBS[job_id] = {
            "state": "queued",
            "created_at": time.time(),
            "updated_at": time.time(),
            "result": None,
            "error": None,
        }
    thread = threading.Thread(target=_run_backtest_job, args=(job_id, payload), daemon=True)
    thread.start()
    return jsonify({"success": True, "job_id": job_id, "status_url": f"/api/backtest/status/{job_id}"})


@app.route("/api/backtest/status/<job_id>")
def api_backtest_status(job_id: str):
    with BACKTEST_JOBS_LOCK:
        job = BACKTEST_JOBS.get(job_id)
        if not job:
            return jsonify({"success": False, "error": "Backtest job not found"}), 404
        state = job.get("state")
        out = {
            "success": True,
            "job_id": job_id,
            "state": state,
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
        }
        if state == "done":
            out["result"] = job.get("result")
        elif state == "failed":
            out["error"] = job.get("error") or "Backtest failed"
    return jsonify(out)


@app.route("/api/paper/start", methods=["POST"])
def start_paper_trading():
    """Start paper trading"""
    ok, err, status = start_paper_trading_internal()
    if not ok:
        payload = {"success": False, "error": err}
        if status:
            return jsonify(payload), status
        return jsonify(payload)
    return jsonify({"success": True, "message": "Paper trading started"})


@app.route("/api/paper/stop", methods=["POST"])
def stop_paper_trading():
    """Stop paper trading"""
    global paper_trader

    if paper_trader:
        paper_trader.stop()

    trading_state["is_running"] = False
    trading_state["mode"] = "BACKTEST"

    return jsonify({"success": True, "message": "Paper trading stopped"})


@app.route("/api/paper/status", methods=["GET"])
def paper_trading_status():
    """Paper session snapshot (alias-friendly); same core fields as /api/status plus loop detail."""
    global paper_trader
    out = {
        "success": True,
        "mode": trading_state.get("mode"),
        "is_running": trading_state.get("is_running"),
        "current_signal": trading_state.get("current_signal"),
        "live_metrics": trading_state.get("live_metrics") or {},
    }
    if paper_trader is not None:
        out["paper_loop_running"] = bool(getattr(paper_trader, "running", False))
        out["symbols"] = list(getattr(paper_trader, "symbols", []) or [])
    else:
        out["paper_loop_running"] = False
        out["symbols"] = []
    return jsonify(out)


@app.route("/api/status")
def get_status():
    return jsonify(trading_state)


@app.route("/api/health")
def api_health():
    snap = get_health_snapshot(config.BINANCE_API)
    return jsonify({"success": True, **snap})


@app.route("/live-monitor")
def live_monitor():
    """Lightweight JSON monitor for v2.6 micro deployment checks."""
    running = is_paper_running()
    live_price = None
    try:
        live_price = float(LiveMarketFeed(symbol="BTC/USDT", timeframe=str(config.TIMEFRAME)).fetch_current_price())
    except Exception:
        live_price = None
    recent = fetch_recent_trades(limit=5)
    rec_out = []
    for r in recent:
        rec_out.append(
            {
                "timestamp": r.get("timestamp"),
                "symbol": r.get("symbol"),
                "side": r.get("side", ""),
                "entry": r.get("entry_price"),
                "exit": r.get("exit_price"),
                "r": r.get("r_multiple"),
                "reason": r.get("exit_reason", r.get("exit_type")),
            }
        )
    return jsonify(
        {
            "success": True,
            "live_price": live_price,
            "paper_pnl_24h": calculate_24h_paper_pnl(),
            "active_position": None,
            "recent_trades": rec_out,
            "paper_running": running,
            "mode": "LIVE_PAPER" if running else "PAPER_IDLE",
            "live_trades_row_count": int(get_live_trades_row_count()),
        }
    )


@app.route("/api/alerts/status")
def api_alerts_status():
    """Whether Telegram / webhook env vars are set (no secrets returned)."""
    return jsonify(
        {
            "success": True,
            "telegram_configured": bool(
                os.getenv("TELEGRAM_BOT_TOKEN", "").strip() and os.getenv("TELEGRAM_CHAT_ID", "").strip()
            ),
            "webhook_configured": bool(os.getenv("ALERT_WEBHOOK_URL", "").strip()),
        }
    )


@app.route("/api/alerts/test-telegram", methods=["POST"])
def api_alerts_test_telegram():
    """
    Send one test message via Telegram Bot API.
    Set TELEGRAM_TEST_SECRET in .env, then POST with header X-Telegram-Test-Secret: <secret>
    or JSON {"secret": "<secret>"}.
    """
    expected = os.getenv("TELEGRAM_TEST_SECRET", "").strip()
    if not expected:
        return jsonify(
            {
                "success": False,
                "error": "Set TELEGRAM_TEST_SECRET in .env to enable this endpoint, or run: python3 scripts/test_telegram.py",
            }
        ), 503
    got = request.headers.get("X-Telegram-Test-Secret", "").strip()
    if not got:
        payload = request.get_json(silent=True) or {}
        got = str(payload.get("secret", "")).strip()
    if got != expected:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    ok, err = send_telegram_sync("ICT Quantrex: Telegram test from API — alerts channel OK.")
    if ok:
        return jsonify({"success": True, "message": "Test message sent to Telegram"})
    return jsonify({"success": False, "error": err}), 502


@app.route("/api/session")
def api_session():
    return jsonify({"success": True, "session": get_session_state()})


def _mmt_proxy_authorized() -> bool:
    expected = (os.getenv("MMT_PROXY_SECRET", "") or "").strip()
    if not expected:
        return False
    got = request.headers.get("X-MMT-Proxy-Secret", "").strip()
    if not got:
        payload = request.get_json(silent=True) or {}
        got = str(payload.get("secret", "")).strip()
    return got == expected


@app.route("/api/mmt/status")
def api_mmt_status():
    """Whether MMT integration is configured (no secrets returned)."""
    return jsonify(
        {
            "success": True,
            "mmt_configured": bool(mmt_configured()),
            "docs": "https://docs.mmt.gg/api/quickstart",
            "note": "Set MMT_API_KEY on the server. Use POST /api/mmt/* with X-MMT-Proxy-Secret matching MMT_PROXY_SECRET. "
            "Paper OPEN can attach binance_public_entry (free REST), mmt_stats_entry / mmt_vd_entry when those blocks are enabled in spec.",
        }
    )


@app.route("/api/mmt/candles", methods=["POST"])
def api_mmt_candles():
    if not mmt_configured():
        return jsonify({"success": False, "error": "Set MMT_API_KEY in server .env"}), 503
    if not _mmt_proxy_authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    ex = str(body.get("exchange", "")).strip()
    sym = str(body.get("symbol", "")).strip()
    tf = str(body.get("tf", "")).strip()
    try:
        frm = int(body.get("from"))
        to = int(body.get("to"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "from/to must be unix seconds integers"}), 400
    if tf not in ALLOWED_TIMEFRAMES:
        return jsonify({"success": False, "error": f"Unsupported tf {tf!r}"}), 400
    out = fetch_candles(exchange=ex, symbol=sym, tf=tf, frm=frm, to=to)
    st = 502 if not out.get("success") else 200
    return jsonify(out), st


@app.route("/api/mmt/orderbook", methods=["POST"])
def api_mmt_orderbook():
    if not mmt_configured():
        return jsonify({"success": False, "error": "Set MMT_API_KEY in server .env"}), 503
    if not _mmt_proxy_authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    ex = str(body.get("exchange", "")).strip()
    sym = str(body.get("symbol", "")).strip()
    try:
        levels = int(body.get("levels", 200))
    except (TypeError, ValueError):
        levels = 200
    out = fetch_orderbook(exchange=ex, symbol=sym, levels=levels)
    st = 502 if not out.get("success") else 200
    return jsonify(out), st


@app.route("/api/mmt/stats", methods=["POST"])
def api_mmt_stats():
    if not mmt_configured():
        return jsonify({"success": False, "error": "Set MMT_API_KEY in server .env"}), 503
    if not _mmt_proxy_authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    ex = str(body.get("exchange", "")).strip()
    sym = str(body.get("symbol", "")).strip()
    tf = str(body.get("tf", "")).strip()
    try:
        frm = int(body.get("from"))
        to = int(body.get("to"))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "from/to must be unix seconds integers"}), 400
    if tf not in ALLOWED_TIMEFRAMES:
        return jsonify({"success": False, "error": f"Unsupported tf {tf!r}"}), 400
    try:
        tmo = float(body.get("timeout_sec", 20))
    except (TypeError, ValueError):
        tmo = 20.0
    out = fetch_stats(exchange=ex, symbol=sym, tf=tf, frm=frm, to=to, timeout_sec=tmo)
    st = 502 if not out.get("success") else 200
    return jsonify(out), st


@app.route("/api/mmt/vd", methods=["POST"])
def api_mmt_vd():
    if not mmt_configured():
        return jsonify({"success": False, "error": "Set MMT_API_KEY in server .env"}), 503
    if not _mmt_proxy_authorized():
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    ex = str(body.get("exchange", "")).strip()
    sym = str(body.get("symbol", "")).strip()
    tf = str(body.get("tf", "")).strip()
    try:
        frm = int(body.get("from"))
        to = int(body.get("to"))
        bucket = int(body.get("bucket", 1))
    except (TypeError, ValueError):
        return jsonify({"success": False, "error": "from/to/bucket must be integers"}), 400
    if tf not in ALLOWED_TIMEFRAMES:
        return jsonify({"success": False, "error": f"Unsupported tf {tf!r}"}), 400
    try:
        tmo = float(body.get("timeout_sec", 20))
    except (TypeError, ValueError):
        tmo = 20.0
    out = fetch_vd(exchange=ex, symbol=sym, tf=tf, frm=frm, to=to, bucket=bucket, timeout_sec=tmo)
    st = 502 if not out.get("success") else 200
    return jsonify(out), st


@app.route("/api/strategy-spec")
def api_strategy_spec():
    return jsonify({"success": True, "spec": public_spec_dict()})


@app.route("/api/risk-check")
def api_risk_check():
    mode = request.args.get("mode", "PAPER").upper()
    sym = request.args.get("symbol", config.SYMBOL)
    sym = str(sym).upper().replace("/", "")
    ok, reasons = risk_engine.allow_new_risk(mode=mode, symbol=sym)
    out = {"success": True, "allow": ok, "reasons": reasons, "symbol": sym}
    if request.args.get("market_gates", "").lower() in ("1", "true", "yes"):
        g_ok, g_reasons = risk_engine.check_entry_gates(sym)
        out["market_gates_allow"] = g_ok
        out["market_gates_reasons"] = g_reasons
    return jsonify(out)


@app.route("/api/playbook")
def api_playbook():
    try:
        limit = int(request.args.get("limit", 40))
    except ValueError:
        limit = 40
    sym = request.args.get("symbol")
    sym = sym.strip().upper().replace("/", "") if sym else None
    rows = list_playbook_events(limit=limit, symbol=sym)
    return jsonify({"success": True, "events": rows, "watchlist": getattr(config, "WATCHLIST", None) or []})


@app.route("/api/runs")
def api_list_runs():
    try:
        limit = int(request.args.get("limit", 20))
    except ValueError:
        limit = 20
    return jsonify({"success": True, "runs": list_runs(limit=limit)})


@app.route("/api/runs/<int:run_id>")
def api_get_run(run_id: int):
    row = get_run(run_id)
    if not row:
        return jsonify({"success": False, "error": "Not found"}), 404
    return jsonify({"success": True, "run": row})


@app.route("/api/research/suggest", methods=["POST"])
def api_research_suggest():
    payload = request.get_json(silent=True) or {}
    use_llm = bool(payload.get("use_llm"))
    try:
        lim = int(payload.get("history_limit", 12))
    except ValueError:
        lim = 12
    recent = list_runs(limit=max(5, min(lim, 50)))
    out = build_suggestion(recent, use_llm=use_llm)
    return jsonify({"success": True, **out})


def _symbols_for_rigor(p: dict, symbol: str) -> list:
    syms = p.get("symbols")
    if isinstance(syms, list) and syms:
        return [str(x).upper().replace("/", "") for x in syms if x]
    wl = getattr(config, "WATCHLIST", None) or []
    if wl:
        return [str(x).upper().replace("/", "") for x in wl]
    return [symbol]


def _truthy_cqe_ack(p: dict) -> bool:
    v = p.get("cqe_ack")
    if v is True:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        return int(v) == 1
    except (TypeError, ValueError):
        return False


def _genes_match_rank1(rank1_genes: dict, snap: dict) -> bool:
    if not isinstance(rank1_genes, dict) or not rank1_genes:
        return True
    if not isinstance(snap, dict):
        return False

    def _close(a, b) -> bool:
        try:
            return abs(float(a) - float(b)) < 1e-6
        except (TypeError, ValueError):
            return a == b

    for k, v in rank1_genes.items():
        if k not in snap:
            return False
        if not _close(snap[k], v):
            return False
    return True


def _research_payload():
    """Shared JSON body for lab endpoints (same shape as backtest)."""
    p = request.get_json(silent=True) or {}
    symbol = str(p.get("symbol") or config.SYMBOL).upper().replace("/", "")
    tf = str(p.get("timeframe") or p.get("interval") or config.TIMEFRAME)
    if tf not in ALLOWED_TIMEFRAMES:
        tf = config.TIMEFRAME
    start = str(p.get("start_date") or config.BACKTEST_START_DATE)
    end = str(p.get("end_date") or config.BACKTEST_END_DATE)
    try:
        cap = float(p.get("initial_capital", config.INITIAL_CAPITAL))
    except (TypeError, ValueError):
        cap = float(config.INITIAL_CAPITAL)
    return p, symbol, tf, start, end, cap


@app.route("/api/research/crisis-windows")
def api_research_crisis_windows():
    return jsonify({"success": True, "windows": CRISIS_WINDOWS})


@app.route("/api/research/walk-forward", methods=["POST"])
def api_research_walk_forward():
    """70/30 (configurable) train vs held-out test; composite fitness OOS-weighted."""
    try:
        p, symbol, tf, start, end, cap = _research_payload()
        try:
            train_frac = float(p.get("train_frac", 0.7))
        except (TypeError, ValueError):
            train_frac = 0.7
        train_frac = max(0.5, min(train_frac, 0.9))
        out = walk_forward_oos(
            symbol=symbol,
            timeframe=tf,
            start_date=start,
            end_date=end,
            initial_capital=cap,
            train_frac=train_frac,
            runtime_cfg=config,
        )
        return jsonify({"success": True, **out})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/research/stress", methods=["POST"])
def api_research_stress():
    """Backtest current strategy genes on fixed crisis date slices (crypto stress library)."""
    try:
        _, symbol, tf, _, _, cap = _research_payload()
        out = stress_crisis_windows(symbol=symbol, timeframe=tf, initial_capital=cap, cfg=config)
        return jsonify({"success": True, **out})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/research/stability-sweep", methods=["POST"])
def api_research_stability_sweep():
    """
    Multi-coin run_multi over contiguous date segments — min Sharpe / min PF span gates.
    Optional body.genes overrides rank-1 (else uses in-process runtime genes).
    """
    try:
        p, symbol, tf, start, end, cap = _research_payload()
        try:
            n_windows = int(p.get("n_windows", 3))
        except (TypeError, ValueError):
            n_windows = 3
        genes = p.get("genes") if isinstance(p.get("genes"), dict) else None
        runner = build_runner_from_lab(runtime_cfg=config, genes=genes)
        runner.INITIAL_CAPITAL = cap
        syms = _symbols_for_rigor(p, symbol)
        out = run_stability_sweep(
            runner,
            symbols=syms,
            timeframe=tf,
            start_date=start,
            end_date=end,
            n_windows=n_windows,
            max_workers=1,
        )
        return jsonify({"success": True, **out})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/research/cost-stress", methods=["POST"])
def api_research_cost_stress():
    """
    Baseline vs scaled COMMISSION + SLIPPAGE multi-coin run (friction_mult default 1.5).
    """
    try:
        p, symbol, tf, start, end, cap = _research_payload()
        try:
            friction_mult = float(p.get("friction_mult", 1.5))
        except (TypeError, ValueError):
            friction_mult = 1.5
        genes = p.get("genes") if isinstance(p.get("genes"), dict) else None
        runner = build_runner_from_lab(runtime_cfg=config, genes=genes)
        runner.INITIAL_CAPITAL = cap
        syms = _symbols_for_rigor(p, symbol)
        out = run_cost_stress(
            runner,
            symbols=syms,
            timeframe=tf,
            start_date=start,
            end_date=end,
            friction_mult=friction_mult,
            max_workers=1,
        )
        if not out.get("success"):
            return jsonify({"success": False, **out}), 400
        return jsonify({"success": True, **out})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/research/evolve", methods=["POST"])
def api_research_evolve():
    """
    Small evolutionary search over ICT genes; fast fitness = OOS-weighted composite.
    Top genomes re-scored with crisis windows included (expensive).
    """
    try:
        p, symbol, tf, start, end, cap = _research_payload()
        try:
            population = int(p.get("population", 10))
        except ValueError:
            population = 10
        try:
            generations = int(p.get("generations", 2))
        except ValueError:
            generations = 2
        population = max(4, min(population, 18))
        generations = max(1, min(generations, 4))
        try:
            seed = int(p["seed"]) if p.get("seed") is not None else None
        except (TypeError, ValueError):
            seed = None
        try:
            top_k = int(p.get("verify_top_k", 3))
        except ValueError:
            top_k = 3
        top_k = max(1, min(top_k, 5))
        out = run_evolution(
            symbol=symbol,
            timeframe=tf,
            start_date=start,
            end_date=end,
            initial_capital=cap,
            population=population,
            generations=generations,
            seed=seed,
            verify_top_k_crisis=top_k,
            runtime_cfg=config,
            symbols=p.get("symbols") if isinstance(p.get("symbols"), list) else None,
        )
        return jsonify({"success": True, **out})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/research/evolution-status")
def api_research_evolution_status():
    """
    Background evolution monitor for mobile/browser.

    Reads last lines from `evolution_run.log` if present; otherwise falls back to
    `journalctl -u ict-evolution` so systemd runs are also visible.
    """
    _TAIL_MAX = 40000  # keep JSON small; avoids truncated responses behind proxies
    try:
        try:
            lines = int(request.args.get("lines", 80))
        except ValueError:
            lines = 80
        lines = max(10, min(lines, 300))

        repo_root = Path(__file__).resolve().parent
        file_path = repo_root / "evolution_run.log"

        text = ""
        source = "none"
        if file_path.exists():
            try:
                all_lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                text = "\n".join(all_lines[-lines:])
                source = "file"
            except Exception:
                text = ""

        if not text:
            try:
                cmd = ["journalctl", "-u", "ict-evolution.service", "-n", str(lines), "--no-pager", "-o", "cat"]
                r = subprocess.run(cmd, capture_output=True, text=True, check=False)
                text = r.stdout.strip()
                source = "journalctl"
            except Exception:
                text = ""

        phase = "unknown"
        latest_generation = None
        latest_generation_total = None
        best_fitness = None

        if text:
            if "RANK1_GENES_START" in text or "RANK1_GENES_END" in text:
                phase = "apply_rank1"
            elif "crisis verification" in text.lower() or "crisis verification top_k" in text:
                phase = "crisis"
            elif "AGGREGATE_START" in text or "AGGREGATE_END" in text:
                phase = "verifying"
            else:
                phase = "evolving"

            m = re.findall(r"\[evolution\]\s+generation\s+(\d+)/(\d+)", text)
            if m:
                latest_generation, latest_generation_total = m[-1]

            bf = re.findall(r"best_fitness=([-+]?\d*\.?\d+)", text)
            if bf:
                best_fitness = bf[-1]

        regime_train_snip = None
        regime_gate_enabled_hint = None
        if text:
            rmt = re.findall(r"\[evolution\]\s+regime_train\s+(.+)", text)
            if rmt:
                regime_train_snip = rmt[-1].strip()
            rge = re.findall(r"\[evolution\]\s+regime_gate_enabled=(True|False)", text)
            if rge:
                regime_gate_enabled_hint = rge[-1] == "True"

        tail_truncated = False
        if len(text) > _TAIL_MAX:
            text = text[-_TAIL_MAX:]
            tail_truncated = True

        return jsonify(
            {
                "success": True,
                "source": source,
                "phase": phase,
                "latest_generation": latest_generation,
                "latest_generation_total": latest_generation_total,
                "best_fitness": best_fitness,
                "regime_train_snip": regime_train_snip,
                "regime_gate_enabled_hint": regime_gate_enabled_hint,
                "tail": text,
                "tail_truncated": tail_truncated,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "tail": ""}), 500


@app.route("/api/research/kz-runs")
def api_research_kz_runs():
    """History of kill-zone-triggered autoresearch runs (SQLite). Use detail=1 for full JSON blobs."""
    try:
        lim = int(request.args.get("limit", 30))
    except ValueError:
        lim = 30
    rows = list_kz_runs(limit=lim)
    detail = request.args.get("detail", "").lower() in ("1", "true", "yes")
    if not detail:
        drop = frozenset(
            {"history_json", "top_json", "history_parsed", "top_parsed"}
        )
        rows = [{k: v for k, v in dict(r).items() if k not in drop} for r in rows]
    return jsonify({"success": True, "runs": rows})


@app.route("/api/research/kz-run-now", methods=["POST"])
def api_research_kz_run_now():
    """Legacy alias: enqueue a KZ-style research job and return immediately."""
    body = request.get_json(silent=True) or {}
    _clean_kz_jobs()
    existing = list(KZ_JOB_DIR.glob("*.json")) if KZ_JOB_DIR.exists() else []
    for p in existing:
        try:
            st = json.loads(p.read_text(encoding="utf-8"))
            if st.get("state") in {"queued", "running"}:
                return jsonify({"success": False, "error": "Another KZ research job is already running."}), 429
        except Exception:
            continue
    job_id = uuid.uuid4().hex[:12]
    now = time.time()
    _write_kz_job(
        job_id,
        {"state": "queued", "created_at": now, "updated_at": now, "result": None, "error": None},
    )
    runner_code = """
import json, sys, time
from pathlib import Path
from config import build_config
from kz_autoresearch import run_kz_research_once

jobf = Path(sys.argv[1])
p = json.loads(sys.argv[2])
st = json.loads(jobf.read_text(encoding='utf-8'))
st['state'] = 'running'
st['updated_at'] = time.time()
jobf.write_text(json.dumps(st), encoding='utf-8')
try:
    out = run_kz_research_once(
        build_config(),
        force_tag=str(p.get('tag') or 'manual'),
        zones_exited=p.get('zones_exited') if isinstance(p.get('zones_exited'), list) else None,
    )
    res = {
        'success': True,
        'run_id': out.get('run_id'),
        'trigger_tag': out.get('trigger_tag'),
        'error': out.get('error'),
        'decision_id': out.get('decision_id'),
        'promotion': out.get('promotion'),
    }
    st = json.loads(jobf.read_text(encoding='utf-8'))
    st['state'] = 'done'
    st['result'] = res
    st['updated_at'] = time.time()
    jobf.write_text(json.dumps(st), encoding='utf-8')
except Exception as e:
    st = json.loads(jobf.read_text(encoding='utf-8'))
    st['state'] = 'failed'
    st['error'] = str(e)
    st['updated_at'] = time.time()
    jobf.write_text(json.dumps(st), encoding='utf-8')
"""
    subprocess.Popen(
        [sys.executable, "-c", runner_code, str(_kz_job_path(job_id)), json.dumps(body)],
        cwd=str(Path(__file__).resolve().parent),
        start_new_session=True,
    )
    return jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status_url": f"/api/research/kz-run-status/{job_id}",
        }
    )


@app.route("/api/research/kz-run-start", methods=["POST"])
def api_research_kz_run_start():
    """Start async KZ autoresearch run (Karpathy loop + Gbrain gate)."""
    body = request.get_json(silent=True) or {}
    # same behavior as legacy alias, kept as explicit endpoint for clients
    return api_research_kz_run_now()
    return jsonify(
        {
            "success": True,
            "job_id": job_id,
            "status_url": f"/api/research/kz-run-status/{job_id}",
        }
    )


@app.route("/api/research/kz-run-status/<job_id>")
def api_research_kz_run_status(job_id: str):
    job = _read_kz_job(job_id)
    if not job:
        return jsonify({"success": False, "error": "KZ research job not found"}), 404
    out = {
        "success": True,
        "job_id": job_id,
        "state": job.get("state"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
    if job.get("state") == "done":
        out["result"] = job.get("result")
    elif job.get("state") == "failed":
        out["error"] = job.get("error")
    return jsonify(out)


@app.route("/api/research/promotion-decisions")
def api_research_promotion_decisions():
    try:
        limit = int(request.args.get("limit", 25))
    except ValueError:
        limit = 25
    rows = list_promotion_decisions(limit=limit)
    return jsonify({"success": True, "decisions": rows})


@app.route("/api/research/promotion-decisions", methods=["POST"])
def api_research_promotion_decisions_create():
    try:
        p = request.get_json(silent=True) or {}
        decision = str(p.get("decision") or "").strip().upper()
        if decision == "GO" and not _truthy_cqe_ack(p):
            return jsonify(
                {
                    "success": False,
                    "error": "GO requires Chief QE acknowledgment (stability sweep + cost stress for this window). Send cqe_ack: true after completing both.",
                }
            ), 400
        rid = insert_promotion_decision(
            decision=str(p.get("decision") or ""),
            note=str(p.get("note") or ""),
            rank1_genes=p.get("rank1_genes") if isinstance(p.get("rank1_genes"), dict) else {},
            aggregate=p.get("aggregate") if isinstance(p.get("aggregate"), dict) else {},
            verify_window=p.get("verify_window") if isinstance(p.get("verify_window"), list) else [],
            symbols=p.get("symbols") if isinstance(p.get("symbols"), list) else [],
            source="dashboard",
            cqe_ack=_truthy_cqe_ack(p),
        )
        return jsonify({"success": True, "id": rid})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/research/promote-runtime", methods=["POST"])
def api_research_promote_runtime():
    """
    Atomic premium flow:
    1) save GO/HOLD decision snapshot
    2) if GO, apply rank-1 genes to runtime config in the same request
    """
    global config, risk_engine
    if trading_state.get("is_running"):
        return jsonify({"success": False, "error": "Stop paper trading before promoting runtime genes."}), 409
    try:
        p = request.get_json(silent=True) or {}
        decision = str(p.get("decision") or "").strip().upper()
        if decision not in {"GO", "HOLD"}:
            return jsonify({"success": False, "error": "decision must be GO or HOLD"}), 400

        rank1_genes = p.get("rank1_genes") if isinstance(p.get("rank1_genes"), dict) else {}
        aggregate = p.get("aggregate") if isinstance(p.get("aggregate"), dict) else {}
        verify_window = p.get("verify_window") if isinstance(p.get("verify_window"), list) else []
        symbols = p.get("symbols") if isinstance(p.get("symbols"), list) else []
        note = str(p.get("note") or "")
        if decision == "GO" and not _truthy_cqe_ack(p):
            return jsonify(
                {
                    "success": False,
                    "error": "GO requires Chief QE acknowledgment (stability sweep + cost stress). Send cqe_ack: true.",
                }
            ), 400

        # 1) persist decision first (audit trail always exists)
        rid = insert_promotion_decision(
            decision=decision,
            note=note,
            rank1_genes=rank1_genes,
            aggregate=aggregate,
            verify_window=verify_window,
            symbols=symbols,
            source="dashboard-atomic",
            cqe_ack=_truthy_cqe_ack(p),
        )

        # 2) apply runtime only for GO
        runtime_out = {"applied": False}
        if decision == "GO":
            errs = apply_research_genes(config, rank1_genes)
            if errs:
                return jsonify(
                    {
                        "success": False,
                        "error": "Decision saved, but gene apply failed.",
                        "decision_id": rid,
                        "errors": errs,
                    }
                ), 400
            sym = p.get("symbol")
            if sym and str(sym).strip():
                config.SYMBOL = str(sym).upper().replace("/", "")
            tf = p.get("timeframe") or p.get("interval")
            if tf and str(tf) in ALLOWED_TIMEFRAMES:
                config.TIMEFRAME = str(tf)
            if p.get("initial_capital") is not None:
                try:
                    config.INITIAL_CAPITAL = float(p["initial_capital"])
                except (TypeError, ValueError):
                    pass
            risk_engine = RiskEngine(config)
            runtime_out = {
                "applied": True,
                "runtime": {
                    "genes": runtime_gene_snapshot(config),
                    "symbol": config.SYMBOL,
                    "timeframe": config.TIMEFRAME,
                    "initial_capital": config.INITIAL_CAPITAL,
                },
            }

        handoff = {
            "genes_match_rank1_payload": None,
            "runtime_echo": runtime_out.get("runtime"),
            "verify": {
                "config_runtime_get": "/api/config/runtime",
                "stability_sweep_post": "/api/research/stability-sweep",
                "cost_stress_post": "/api/research/cost-stress",
                "vps_cli": "python scripts/chief_qe_sweep.py",
            },
        }
        if decision == "GO" and runtime_out.get("applied"):
            handoff["genes_match_rank1_payload"] = _genes_match_rank1(rank1_genes, runtime_gene_snapshot(config))

        return jsonify(
            {
                "success": True,
                "decision_id": rid,
                "decision": decision,
                "handoff": handoff,
                **runtime_out,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/config/runtime")
def api_config_runtime():
    """ICT genes + symbol/tf actually used for backtest, paper, and research lab (may differ from spec.yaml on disk)."""
    return jsonify(
        {
            "success": True,
            "genes": runtime_gene_snapshot(config),
            "symbol": config.SYMBOL,
            "timeframe": config.TIMEFRAME,
            "initial_capital": config.INITIAL_CAPITAL,
            "watchlist": getattr(config, "WATCHLIST", None) or [],
            "note": "Runtime state until you Reset or restart Flask. Edit strategy/spec.yaml + restart to persist.",
        }
    )


@app.route("/api/config/apply-genes", methods=["POST"])
def api_config_apply_genes():
    """Apply lab genes to running process (next paper session + research + manual backtest uses build_config per request — see note)."""
    global config, risk_engine
    if trading_state.get("is_running"):
        return jsonify({"success": False, "error": "Stop paper trading before changing genes."}), 409
    body = request.get_json(silent=True) or {}
    if body.get("reset") is True:
        config = build_config()
        risk_engine = RiskEngine(config)
        return jsonify(
            {
                "success": True,
                "applied": {"action": "reset_from_spec_yaml"},
                "runtime": {
                    "genes": runtime_gene_snapshot(config),
                    "symbol": config.SYMBOL,
                    "timeframe": config.TIMEFRAME,
                },
            }
        )

    genes = body.get("genes")
    if not isinstance(genes, dict):
        return jsonify({"success": False, "error": "Missing object: genes"}), 400
    errs = apply_research_genes(config, genes)
    if errs:
        return jsonify({"success": False, "errors": errs}), 400

    sym = body.get("symbol")
    if sym and str(sym).strip():
        config.SYMBOL = str(sym).upper().replace("/", "")
    tf = body.get("timeframe") or body.get("interval")
    if tf and str(tf) in ALLOWED_TIMEFRAMES:
        config.TIMEFRAME = str(tf)
    if body.get("initial_capital") is not None:
        try:
            config.INITIAL_CAPITAL = float(body["initial_capital"])
        except (TypeError, ValueError):
            pass

    risk_engine = RiskEngine(config)
    return jsonify(
        {
            "success": True,
            "applied": {"genes": genes, "symbol": config.SYMBOL, "timeframe": config.TIMEFRAME},
            "runtime": {
                "genes": runtime_gene_snapshot(config),
                "symbol": config.SYMBOL,
                "timeframe": config.TIMEFRAME,
            },
        }
    )


@app.route("/api/uw/status")
def api_uw_status():
    """Unusual Whales API key present (same data family as their MCP server)."""
    c = UnusualWhalesClient(config)
    return jsonify({"success": True, "configured": c.configured})


@app.route("/api/uw/market-tide")
def api_uw_market_tide():
    c = UnusualWhalesClient(config)
    if not c.configured:
        return jsonify({"success": False, "error": "Set UNUSUAL_WHALES_API_KEY in .env"}), 503
    try:
        interval_5m = request.args.get("interval_5m", "false").lower() in ("1", "true", "yes")
        payload = c.market_tide(interval_5m=interval_5m)
        return jsonify({"success": True, "payload": payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/uw/flow-recent/<ticker>")
def api_uw_flow_recent(ticker: str):
    c = UnusualWhalesClient(config)
    if not c.configured:
        return jsonify({"success": False, "error": "Set UNUSUAL_WHALES_API_KEY in .env"}), 503
    try:
        payload = c.flow_recent(ticker)
        return jsonify({"success": True, "payload": payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@app.route("/api/uw/flow-alerts")
def api_uw_flow_alerts():
    c = UnusualWhalesClient(config)
    if not c.configured:
        return jsonify({"success": False, "error": "Set UNUSUAL_WHALES_API_KEY in .env"}), 503
    try:
        limit = int(request.args.get("limit", 15))
        limit = max(1, min(limit, 50))
        ticker = request.args.get("ticker") or request.args.get("ticker_symbol")
        min_prem = request.args.get("min_premium")
        min_premium = int(min_prem) if min_prem and str(min_prem).isdigit() else None
        payload = c.flow_alerts(
            ticker_symbol=ticker,
            limit=limit,
            min_premium=min_premium,
            is_otm=True if request.args.get("is_otm", "").lower() in ("1", "true", "yes") else None,
        )
        return jsonify({"success": True, "payload": payload})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 502


@socketio.on("connect")
def handle_connect():
    print("Client connected")
    emit("connection", {"data": "Connected to AutoResearchClaw"})


@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")


if __name__ == "__main__":
    # Default 5050: macOS often binds 5000 to AirPlay Receiver (System Settings → General → AirDrop & Handoff).
    port = int(os.getenv("PORT", "5050"))
    print(f"AutoResearchClaw: http://127.0.0.1:{port}/  (set PORT= to override)")
    start_background_services()
    _debug = os.getenv("FLASK_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    socketio.run(app, host="0.0.0.0", port=port, debug=_debug, allow_unsafe_werkzeug=True)

