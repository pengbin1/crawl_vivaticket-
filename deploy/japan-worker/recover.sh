#!/usr/bin/env bash
# 快捷入口：一键恢复（拉代码 + 依赖 + 重启）
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$DIR/ctl.sh" recover "$@"
