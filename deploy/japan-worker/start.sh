#!/usr/bin/env bash
# 快捷入口：一键启动
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$DIR/ctl.sh" start "$@"
