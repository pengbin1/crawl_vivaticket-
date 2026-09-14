#!/bin/bash
# 本机 → 服务器同步（rsync，比纯 scp 更合适；也可用同目录 scp 脚本）
#
#   export POKEMON_HOST=root@你的服务器
#   ./scripts/sync_to_server.sh

set -euo pipefail

HOST="${POKEMON_HOST:-root@52.68.67.78}"
REMOTE_DIR="${CENACOLO_REMOTE_DIR:-/root/pengb_dev/cenacolo_vivaticket}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "sync $ROOT -> ${HOST}:${REMOTE_DIR}"

ssh "$HOST" "mkdir -p ${REMOTE_DIR}"

rsync -avz \
  --exclude '.venv' \
  --exclude 'pengb_dev' \
  --exclude '__pycache__' \
  --exclude 'logs' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  "$ROOT/" "${HOST}:${REMOTE_DIR}/"

echo "done."
echo "登录服务器执行:"
echo "  ssh ${HOST}"
echo "  cd ${REMOTE_DIR} && bash scripts/install_on_server.sh"
echo "  ./scripts/start.sh --list"
echo "  ./scripts/start.sh --count 1"
