#!/usr/bin/env bash
# Deploy agent lên VPS Hostinger: rsync code + secrets, cài deps, restart service.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VPS="root@187.77.135.158"
KEY="$HOME/.ssh/jenny_vps"
SSH="ssh -i $KEY -o BatchMode=yes"

echo "→ Rsync code + secrets"
rsync -az -e "$SSH" --delete \
  --exclude '__pycache__' --exclude '*.pyc' \
  "$REPO_DIR/agent/" "$VPS:/opt/jenny/app/"
rsync -az -e "$SSH" "$REPO_DIR/config/secrets/" "$VPS:/opt/jenny/config/secrets/"

echo "→ Cài dependencies + service"
$SSH "$VPS" bash -s <<'REMOTE'
set -euo pipefail
mkdir -p /opt/jenny/workdir /opt/jenny/logs
[ -d /opt/jenny/venv ] || python3 -m venv /opt/jenny/venv
/opt/jenny/venv/bin/pip install -q -r /opt/jenny/app/requirements.txt
cp /opt/jenny/app/jenny.service /etc/systemd/system/jenny.service
cp /opt/jenny/app/jenny-lark.service /etc/systemd/system/jenny-lark.service
cp /opt/jenny/app/jenny-web.service /etc/systemd/system/jenny-web.service
cp /opt/jenny/app/jenny-cron.service /etc/systemd/system/jenny-cron.service
cp /opt/jenny/app/jenny-events.service /etc/systemd/system/jenny-events.service
systemctl daemon-reload

systemctl enable jenny-cron >/dev/null 2>&1 || true
systemctl restart jenny-cron
systemctl is-active jenny-cron && echo "✓ jenny-cron (scheduler) running"

systemctl enable jenny-events >/dev/null 2>&1 || true
systemctl restart jenny-events
sleep 2
systemctl is-active jenny-events && echo "✓ jenny-events (bot events) running"

if grep -q '^WEBHOOK_SECRET=..*' /opt/jenny/config/secrets/agent.env 2>/dev/null; then
  systemctl enable jenny-web >/dev/null 2>&1 || true
  systemctl restart jenny-web
  sleep 2
  systemctl is-active jenny-web && echo "✓ jenny-web (webhooks) running"
fi

if grep -q '^TELEGRAM_BOT_TOKEN=..*' /opt/jenny/config/secrets/agent.env 2>/dev/null; then
  systemctl enable jenny >/dev/null 2>&1 || true
  systemctl restart jenny
  sleep 2
  systemctl is-active jenny && echo "✓ jenny (Telegram) running"
else
  echo "⚠ Chưa có TELEGRAM_BOT_TOKEN — service jenny chưa start."
fi

if grep -q '^LARK_APP_ID=..*' /opt/jenny/config/secrets/agent.env 2>/dev/null; then
  systemctl enable jenny-lark >/dev/null 2>&1 || true
  systemctl restart jenny-lark
  sleep 2
  systemctl is-active jenny-lark && echo "✓ jenny-lark (Lark) running"
else
  echo "⚠ Chưa có LARK_APP_ID — service jenny-lark chưa start."
fi
REMOTE

echo "✓ Deploy xong"
