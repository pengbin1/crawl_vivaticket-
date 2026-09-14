#!/bin/bash
# 启动入口：固定使用项目下 pengb_dev 虚拟环境
#
#   cd /root/pengb_dev/cenacolo_vivaticket
#   ./scripts/start.sh --list
#   ./scripts/start.sh --count 1
#   ./scripts/start.sh --count 5
#   ./scripts/start.sh --mark-used you@163.com

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON="$ROOT/pengb_dev/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "虚拟环境不存在: $ROOT/pengb_dev"
  echo "请先执行: bash scripts/install_on_server.sh"
  exit 1
fi

exec "$PYTHON" pool_register.py "$@"
