#!/usr/bin/env bash
# Install buy worker on Japan host (run as root).
set -euo pipefail

CENACOLO_HOME="${CENACOLO_HOME:-/opt/cenacolo}"
REPO_URL="${REPO_URL:-http://47.93.55.234:8888/pengbin/crawl_vivaticket.git}"
USER_NAME="${USER_NAME:-cenacolo}"

id -u "$USER_NAME" &>/dev/null || useradd --system --create-home --home-dir "$CENACOLO_HOME" --shell /usr/sbin/nologin "$USER_NAME"

mkdir -p "$CENACOLO_HOME"/{etc,var/log,var/artifacts}
if [[ ! -d "$CENACOLO_HOME/.git" ]]; then
  git clone "$REPO_URL" "$CENACOLO_HOME"
fi

cd "$CENACOLO_HOME/services/buy"
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt

if [[ ! -f "$CENACOLO_HOME/etc/buy.local.yaml" ]]; then
  cp config.example.yaml "$CENACOLO_HOME/etc/buy.local.yaml"
  echo "Edit $CENACOLO_HOME/etc/buy.local.yaml (captcha key, browser, notify)"
fi
chmod 600 "$CENACOLO_HOME/etc/buy.local.yaml"
chown -R "$USER_NAME:$USER_NAME" "$CENACOLO_HOME"

UNIT_DIR="$(cd "$(dirname "$0")" && pwd)/systemd"
cp "$UNIT_DIR/cenacolo-buy-worker.service" /etc/systemd/system/
cp "$UNIT_DIR/cenacolo-buy-reaper.service" /etc/systemd/system/
cp "$UNIT_DIR/cenacolo-buy-reaper.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cenacolo-buy-worker.service
systemctl enable --now cenacolo-buy-reaper.timer
echo "OK: worker + reaper timer enabled. CENACOLO_HOME=$CENACOLO_HOME"
