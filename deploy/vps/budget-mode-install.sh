#!/usr/bin/env bash
# Install low-cost VPS automation profile:
# - Keep dashboard/paper loop running all day
# - Run lighter research sweep nightly off-peak
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
REPO_PATH="${ROOT}"

echo "[budget-mode] root=${ROOT}"
echo "[budget-mode] user=${USER_NAME}"

if [[ ! -f "${ROOT}/app.py" ]]; then
  echo "No app.py found in ${ROOT}" >&2
  exit 1
fi

chmod +x "${ROOT}/deploy/vps/run-nightly-research-lite.sh"
perl -pi -e 's/\r$//' "${ROOT}/deploy/vps/run-nightly-research-lite.sh" 2>/dev/null || true

install_cron_fallback() {
  echo "[budget-mode] no passwordless sudo — installing user crontab (no root required)"
  mkdir -p "${ROOT}/logs"
  local line="40 2 * * * ${ROOT}/deploy/vps/run-nightly-research-lite.sh >> ${ROOT}/logs/cron-nightly.log 2>&1"
  (crontab -l 2>/dev/null | grep -v 'run-nightly-research-lite.sh' || true; echo "${line}") | crontab -
  echo "[budget-mode] crontab entry:"
  crontab -l | grep run-nightly-research-lite || true
  echo ""
  echo "[budget-mode] cron log: tail -f ${ROOT}/logs/cron-nightly.log"
}

if sudo -n true 2>/dev/null; then
  TMP_SERVICE="/tmp/ict-nightly-research-lite.service"
  sed \
    -e "s|^User=.*|User=${USER_NAME}|g" \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${REPO_PATH}|g" \
    -e "s|^Environment=PATH=.*|Environment=PATH=${REPO_PATH}/venv/bin:/usr/bin:/bin|g" \
    -e "s|^EnvironmentFile=.*|EnvironmentFile=-${REPO_PATH}/.env|g" \
    -e "s|^ExecStart=.*|ExecStart=${REPO_PATH}/deploy/vps/run-nightly-research-lite.sh|g" \
    "${ROOT}/deploy/vps/ict-nightly-research-lite.service" > "${TMP_SERVICE}"

  sudo cp "${TMP_SERVICE}" /etc/systemd/system/ict-nightly-research-lite.service
  sudo cp "${ROOT}/deploy/vps/ict-nightly-research-lite.timer" /etc/systemd/system/ict-nightly-research-lite.timer
  sudo systemctl daemon-reload
  sudo systemctl enable --now ict-nightly-research-lite.timer

  echo "[budget-mode] installed nightly lite systemd timer"
  echo "[budget-mode] next triggers:"
  sudo systemctl list-timers ict-nightly-research-lite.timer --no-pager || true
else
  install_cron_fallback
fi

echo ""
echo "[budget-mode] dashboard checks:"
echo "  curl -s http://127.0.0.1:5050/api/health"
echo "  curl -s http://127.0.0.1:5050/api/paper/status"
echo ""
echo "[budget-mode] logs:"
echo "  tail -n 120 ${ROOT}/logs/nightly-research-lite-latest.log"
