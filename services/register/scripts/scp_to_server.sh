#!/bin/bash
# 纯 scp 打包上传（不依赖 rsync）
#
#   export POKEMON_HOST=root@你的服务器
#   ./scripts/scp_to_server.sh

set -euo pipefail

HOST="${POKEMON_HOST:-root@52.68.67.78}"
REMOTE_DIR="${CENACOLO_REMOTE_DIR:-/root/pengb_dev/cenacolo_vivaticket}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TMP_TGZ="/tmp/cenacolo_vivaticket_$$.tgz"

echo "pack $ROOT"
tar -C "$(dirname "$ROOT")" \
  --exclude='cenacolo_vivaticket/.venv' \
  --exclude='cenacolo_vivaticket/pengb_dev' \
  --exclude='cenacolo_vivaticket/__pycache__' \
  --exclude='cenacolo_vivaticket/logs' \
  --exclude='*.pyc' \
  -czf "$TMP_TGZ" "$(basename "$ROOT")"

echo "scp -> ${HOST}:${REMOTE_DIR}"
ssh "$HOST" "mkdir -p ${REMOTE_DIR}"
scp "$TMP_TGZ" "${HOST}:/tmp/cenacolo_vivaticket.tgz"
ssh "$HOST" "mkdir -p ${REMOTE_DIR} && tar -xzf /tmp/cenacolo_vivaticket.tgz -C $(dirname "$REMOTE_DIR") && rm -f /tmp/cenacolo_vivaticket.tgz"
rm -f "$TMP_TGZ"

echo "done. 登录服务器:"
echo "  ssh ${HOST}"
echo "  cd ${REMOTE_DIR} && bash scripts/install_on_server.sh"
echo "  ./scripts/start.sh --list"
echo "  ./scripts/start.sh --count 1"
