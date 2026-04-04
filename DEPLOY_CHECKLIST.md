# Deploy Checklist (Droplet)

Use this for repeatable deploys and verification on the Ubuntu server.

## 1) Connect and go to project

```bash
ssh trader@157.230.41.163
cd ~/ict-trading-dashboard
```

If repo is not present yet:

```bash
cd ~
git clone https://github.com/talentsolutionsmyanmar-max/gem_trade.git ict-trading-dashboard
cd ~/ict-trading-dashboard
```

## 2) Update code + Python env

```bash
git pull
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Routine droplet update (after `bootstrap` + systemd once):

```bash
bash deploy/vps/sync-and-restart.sh
```

**Move & fly** (sync + restart + health wait; optional full Chief QE after):

```bash
bash deploy/vps/move-and-fly.sh
MOVE_AND_FLY_QE=1 bash deploy/vps/move-and-fly.sh   # long: stability + cost stress logged to chief_qe_fly.log
```

Or first-time from repo root:

```bash
bash deploy/vps/bootstrap.sh
```

Copy secrets: `deploy/vps/.env.example` → `.env` (set `FLASK_SECRET_KEY`, `FLASK_DEBUG=0` on cloud).

## 3) Confirm live-safe defaults

```bash
python - <<'PY'
from config import build_config
c = build_config()
print("SYMBOL:", c.SYMBOL, "TF:", c.TIMEFRAME)
print("REGIME_GATE_ENABLED:", c.REGIME_GATE_ENABLED)
print("MIN_SIGNAL_STRENGTH:", c.MIN_SIGNAL_STRENGTH, "MIN_CONFLUENCE:", c.MIN_CONFLUENCE)
PY
```

Expected: `REGIME_GATE_ENABLED: False` unless intentionally overridden.

## 4) Run full evolution + verify pipeline

```bash
source venv/bin/activate
python scripts/run_evolution_and_verify_v18.py | tee evolution_run.log
```

Capture:
- `RANK1_GENES_START ... RANK1_GENES_END`
- `AGGREGATE_START ... AGGREGATE_END`

## 5) Optional: Karpathy threshold loop (broader multi-pair basket)

```bash
source venv/bin/activate
python scripts/karpathy_autoresearch_loop.py | tee karpathy_loop.log
```

## 6) App restart

**Dashboard service** (recommended unit file in-repo: `deploy/vps/ict-dashboard.service` — adjust `User=` and paths):

```bash
sudo cp deploy/vps/ict-dashboard.service /etc/systemd/system/ict-dashboard.service
# edit paths inside the unit if your repo dir is not /home/trader/autoresearchclaw
sudo systemctl daemon-reload
sudo systemctl enable --now ict-dashboard
sudo systemctl status ict-dashboard --no-pager
```

Ensure `.env` has `FLASK_DEBUG=0` on the VPS (production). `PORT` defaults to `5050`.

Legacy / separate evolution worker (if you still use it):

```bash
sudo systemctl restart ict-evolution.service
sudo systemctl status ict-evolution.service --no-pager
```

If running Flask app manually:

```bash
source venv/bin/activate
export FLASK_DEBUG=0
python app.py
```

## 6a) Quick start + verify (auto-detects repo)

If you just need to get `http://127.0.0.1:5050/api/health` working first (before systemd):

```bash
bash deploy/vps/start-dashboard-and-verify.sh
```

Note: systemd uses `~/ict-trading-dashboard` (see `deploy/vps/ict-dashboard.service`). If your VPS currently only has `~/autoresearchclaw`, clone the correct repo into `~/ict-trading-dashboard` before enabling systemd.

## 6d) Premium production server (Gunicorn + Eventlet)

Werkzeug is a dev server. For production-grade Socket.IO, run Gunicorn with Eventlet workers behind Caddy (TLS on 443).

On the VPS (repo lives at `/home/trader/ict-backend` in our deployed setup):

```bash
cd /home/trader/ict-backend
source venv/bin/activate
pip install -r requirements.txt

sudo cp deploy/vps/ict-backend-gunicorn.service /etc/systemd/system/ict-backend.service
sudo systemctl daemon-reload
sudo systemctl restart ict-backend
sudo systemctl status ict-backend --no-pager | tail -40
curl -sI http://127.0.0.1:5050/api/health | head -10
curl -I https://quantrex.solutions/api/health
```

If websockets misbehave, check logs:

```bash
sudo journalctl -u ict-backend -n 120 --no-pager
sudo journalctl -u caddy -n 120 --no-pager
```

## 6b) Live URL does not load (browser / `curl` from your laptop)

**Symptom A — connection refused / timeout:** nothing listening or firewall blocking.

- On the droplet: `ss -tlnp | grep 5050` (or your `PORT` from `.env`). You should see `python` bound to `0.0.0.0:5050` (not only `127.0.0.1`).
- **DigitalOcean:** Networking → Firewalls / Droplet → **Inbound TCP `5050`** (or use **80/443** + reverse proxy below).
- **UFW:** `sudo ufw allow 5050/tcp && sudo ufw status`

**Symptom B — `curl` connects but “Empty reply from server”:** port is open but the app closes the connection (crash, wrong process, or broken service file).

SSH in and run:

```bash
curl -sv http://127.0.0.1:5050/api/health 2>&1 | tail -25
sudo systemctl status ict-dashboard --no-pager
sudo journalctl -u ict-dashboard -n 80 --no-pager
```

- If **localhost fails too**, fix the app (traceback in logs) or confirm `WorkingDirectory` / `venv` paths in `ict-dashboard.service`.
- If **localhost works** but the public URL fails, fix **cloud firewall** / **UFW** / bind address (must listen on `0.0.0.0`, which `app.py` does by default).

**Recommended for production:** put **Caddy** or **nginx** on **80/443** and reverse-proxy to `http://127.0.0.1:5050` so users hit `https://your-domain/` without exposing a high port.

## 6c) Custom domain (example: `quantrex.solutions`)

### If the domain is on **Squarespace** (Domains → *your domain* → **DNS settings**)

Squarespace “Defaults” point `@` and `www` at **Squarespace hosting**, not your VPS. To use **your droplet + Caddy**:

1. **Remove** (or replace) the default **`A` records for `@`** (the four IPs like `198.49.23.x` / `198.185.159.x`).
2. **Add** **`A` `@`** → your droplet **public IPv4** (one record is enough).
3. **Remove** the **`CNAME` `www`** → `ext-sq.squarespace.com`.
4. **Add** **`A` `www`** → **same droplet IPv4** (simplest for HTTPS on both names).

**Keep email working:** leave **Zoho `MX`** (and any **TXT** for SPF/DKIM) **unchanged**. If you only see website defaults and **no MX**, add the three Zoho MX records again (from Zoho’s setup guide).

**Optional cleanup:** if Squarespace shows an **`HTTPS`** (SVCB) record for `@` tied to Squarespace IPs, **delete** it after you move the `A` records — it can confuse some clients while you’re switching to your own server.

After DNS propagates, use Caddy on the droplet (see below) so **`https://quantrex.solutions`** works.

---

**1) DNS** (wherever `quantrex.solutions` is hosted — Zoho Domains, Zoho Mail → Domains, or Cloudflare):

| Type | Name / Host | Value |
|------|-------------|--------|
| **A** | `@` (apex) | Your droplet **public IPv4** |
| **A** | `www` | Same IPv4 *(or CNAME `www` → `quantrex.solutions` if your DNS allows)* |

Wait for propagation (often minutes; up to 48h). Check: `dig +short quantrex.solutions A` from your laptop.

**2) Firewall on droplet**

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80,443/tcp
sudo ufw enable
sudo ufw status
```

You can **close public `5050`** once Caddy is up (dashboard only via 443).

**3) Caddy (auto HTTPS, WebSockets for Socket.IO)**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Copy the example site block from `deploy/vps/Caddyfile.example` into `/etc/caddy/Caddyfile` (edit domain if needed), then:

```bash
sudo systemctl reload caddy
curl -sI https://quantrex.solutions/api/health
```

Live URL: **`https://quantrex.solutions/`** (and `https://www.quantrex.solutions/` if you added the `www` **A** record).

## 7) API health checks

```bash
curl -s http://127.0.0.1:5050/api/health
curl -s "http://127.0.0.1:5050/api/research/evolution-status?lines=80"
curl -s http://127.0.0.1:5050/api/config/runtime
```

## 7b) Chief QE (stability + cost stress)

After deploy or before promotion:

```bash
source venv/bin/activate
python scripts/chief_qe_sweep.py | tee chief_qe.log
```

Optional env overrides: `START_DATE`, `END_DATE`, `TIMEFRAME`, `N_WINDOWS`, `FRICTION_MULT`, `SYMBOLS` (comma-separated), `GENES_JSON`.

### 7b2) Weekly automation on the VPS (systemd timer)

Runs `scripts/chief_qe_sweep.py` every **Sunday 02:00 UTC** (±15 min jitter), logs under `logs/weekly-qe-*.log`. Default symbols: **SOL/ETH/BTC** (set `SYMBOLS` in `.env` to override).

Adjust paths if your repo is not `/home/trader/ict-backend`:

```bash
cd /home/trader/ict-backend
chmod +x deploy/vps/run-weekly-qe.sh
sudo cp deploy/vps/ict-weekly-qe.service /etc/systemd/system/ict-weekly-qe.service
sudo cp deploy/vps/ict-weekly-qe.timer /etc/systemd/system/ict-weekly-qe.timer
sudo systemctl daemon-reload
sudo systemctl enable --now ict-weekly-qe.timer
sudo systemctl list-timers ict-weekly-qe.timer --no-pager
# Manual test run:
sudo systemctl start ict-weekly-qe.service
sudo journalctl -u ict-weekly-qe.service -n 80 --no-pager
```

Ensure `.env` on the server sets **`TRUST_PROXY=1`** when using Caddy (already in `ict-backend-gunicorn.service` and `.env.example`).

**Live URLs (browser):** main dashboard `https://quantrex.solutions/` — use **Open live watch** or go directly to **`/live`**, **`/live-action`**, or **`/?live=1`** (redirects to `/live`). Start **paper trading** to stream Binance ticks into the Socket.IO feed.

Same checks from the dashboard: **Chief QE · stability sweep** and **Chief QE · cost stress** (Research lab). After **GO + Apply runtime**, the JSON response includes `handoff` with `genes_match_rank1_payload` and API paths to re-verify.

## 8) Promotion rule

Promote only if rank-1 verify beats or matches baseline on:
- `min_sharpe`
- `min_profit_factor`
- `worst_max_drawdown_pct` (not worse)
- `total_trades_all` (no collapse)

**Chief QE:** dashboard **GO** / **GO + Apply** require `cqe_ack` (stability sweep + cost stress completed; checkbox in UI). Stored in SQLite column `cqe_ack`.

Otherwise: hold baseline (`MIN_SIGNAL_STRENGTH=68`, `MIN_CONFLUENCE=2`, regime gate off).
