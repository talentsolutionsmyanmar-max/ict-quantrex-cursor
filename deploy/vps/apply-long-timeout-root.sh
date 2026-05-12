#!/bin/bash
# Run on the VPS once (fixes empty JSON / truncated responses from short proxy + worker timeouts):
#   sudo bash /home/trader/apply-long-timeout-root.sh
set -euo pipefail

CADDY_FILE=/etc/caddy/Caddyfile
UNIT=/etc/systemd/system/ict-backend.service
BACKUP_DIR=/root/quantrex-backup-$(date +%Y%m%d-%H%M%S)
mkdir -p "$BACKUP_DIR"
[[ -f "$CADDY_FILE" ]] && cp -a "$CADDY_FILE" "$BACKUP_DIR/"
[[ -f "$UNIT" ]] && cp -a "$UNIT" "$BACKUP_DIR/"

cat >"$CADDY_FILE" <<'EOF'
quantrex.solutions, www.quantrex.solutions {
	reverse_proxy 127.0.0.1:5050 {
		flush_interval -1
		transport http {
			read_timeout 300s
			write_timeout 300s
			dial_timeout 15s
		}
	}
}
EOF

if [[ -f "$UNIT" ]] && ! grep -q '\-\-timeout 300' "$UNIT"; then
  sed -i 's/--worker-class eventlet -w 1 --bind/--worker-class eventlet -w 1 --timeout 300 --bind/' "$UNIT" || true
fi
if [[ -f "$UNIT" ]] && ! grep -q '\-\-timeout 300' "$UNIT"; then
  echo "WARN: could not inject --timeout 300 into $UNIT — edit ExecStart manually." >&2
fi

command -v caddy >/dev/null && caddy fmt --overwrite "$CADDY_FILE" || true
systemctl daemon-reload
systemctl restart ict-backend
systemctl reload caddy || systemctl restart caddy
echo "Done. Backups under $BACKUP_DIR"
