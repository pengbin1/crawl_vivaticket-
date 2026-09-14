#!/bin/bash
# 在服务器上执行：创建 pengb_dev 虚拟环境并安装依赖
#
#   cd /root/pengb_dev/cenacolo_vivaticket
#   bash scripts/install_on_server.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV_DIR="$ROOT/pengb_dev"
PYTHON="$VENV_DIR/bin/python"
PIP="$VENV_DIR/bin/pip"

if [ ! -x "$PYTHON" ]; then
  echo "creating venv: $VENV_DIR"
  python3 -m venv "$VENV_DIR"
fi

"$PIP" install -U pip
"$PIP" install -r requirements.txt

# Amazon Linux 等无 apt-get，不能用 --with-deps
echo "installing Playwright Chromium (no OS deps auto-install)..."
"$PYTHON" -m playwright install chromium

chmod +x scripts/*.sh 2>/dev/null || true

echo "---- mongo ping ----"
"$PYTHON" - <<'PY'
from mongo_api import MONGO_HTTP_BASE, MONGO_HTTP_PROXY, mongo_find, VIVATICKET_ACCOUNTS_COLLECTION
print("MONGO_HTTP_BASE=", MONGO_HTTP_BASE)
print("MONGO_HTTP_PROXY=", MONGO_HTTP_PROXY or "(empty)")
rows = mongo_find(VIVATICKET_ACCOUNTS_COLLECTION, {}, limit=3)
print("vivaticket_accounts sample count=", len(rows))
for r in rows:
    print(" ", r.get("status"), r.get("email"))
PY

echo
echo "安装完成。启动方式:"
echo "  ./scripts/start.sh --list"
echo "  ./scripts/start.sh --count 1"
echo "  ./scripts/start.sh --count 5"
echo "  ./scripts/start.sh --mark-used email@163.com"
