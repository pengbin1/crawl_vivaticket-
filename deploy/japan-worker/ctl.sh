#!/usr/bin/env bash
# Cenacolo Japan host control: start | stop | restart | recover | status
#
#   sudo bash deploy/japan-worker/ctl.sh start
#   sudo bash deploy/japan-worker/ctl.sh recover
#   bash deploy/japan-worker/ctl.sh status

set -euo pipefail

CENACOLO_HOME="${CENACOLO_HOME:-/opt/cenacolo}"
REPO_URL="${REPO_URL:-http://47.93.55.234:8888/pengbin/crawl_vivaticket.git}"
USER_NAME="${USER_NAME:-cenacolo}"
BUY_DIR="$CENACOLO_HOME/services/buy"
CFG="$CENACOLO_HOME/etc/buy.local.yaml"
WORKER_UNIT="cenacolo-buy-worker.service"
REAPER_TIMER="cenacolo-buy-reaper.timer"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$SCRIPT_DIR/systemd"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
die() { log "ERROR: $*"; exit 1; }

need_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "请用 root 或 sudo 执行: sudo bash $0 $1"
  fi
}

ensure_layout() {
  mkdir -p "$CENACOLO_HOME"/{etc,var/log,var/artifacts}
  if [[ ! -d "$CENACOLO_HOME/.git" ]]; then
    log "clone $REPO_URL → $CENACOLO_HOME"
    git clone "$REPO_URL" "$CENACOLO_HOME"
  fi
  [[ -d "$BUY_DIR" ]] || die "缺少 $BUY_DIR，请先拉 monorepo 最新代码"
}

ensure_user() {
  if ! id -u "$USER_NAME" &>/dev/null; then
    log "创建系统用户 $USER_NAME"
    useradd --system --create-home --home-dir "$CENACOLO_HOME" --shell /usr/sbin/nologin "$USER_NAME" || true
  fi
}

ensure_venv() {
  cd "$BUY_DIR"
  if [[ ! -x "$BUY_DIR/.venv/bin/python" ]]; then
    log "创建 venv"
    python3 -m venv .venv
  fi
  log "安装/更新 Python 依赖"
  .venv/bin/pip install -U pip -q
  .venv/bin/pip install -r requirements.txt -q
}

ensure_config() {
  if [[ ! -f "$CFG" ]]; then
    cp "$BUY_DIR/config.example.yaml" "$CFG"
    chmod 600 "$CFG"
    log "已生成 $CFG —— 请填 captcha.client_key / browser / notify 后再正式抢票"
  else
    chmod 600 "$CFG" || true
  fi
}

install_units() {
  [[ -d "$UNIT_DIR" ]] || die "缺少 systemd 目录: $UNIT_DIR"
  # Prefer units from the checked-out repo when present
  local src="$UNIT_DIR"
  if [[ -d "$CENACOLO_HOME/deploy/japan-worker/systemd" ]]; then
    src="$CENACOLO_HOME/deploy/japan-worker/systemd"
  fi
  cp "$src/cenacolo-buy-worker.service" /etc/systemd/system/
  cp "$src/cenacolo-buy-reaper.service" /etc/systemd/system/
  cp "$src/cenacolo-buy-reaper.timer" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable "$WORKER_UNIT" "$REAPER_TIMER" >/dev/null
}

cmd_start() {
  need_root start
  ensure_layout
  ensure_user
  ensure_venv
  ensure_config
  install_units
  chown -R "$USER_NAME:$USER_NAME" "$CENACOLO_HOME" || true
  log "启动 $WORKER_UNIT + $REAPER_TIMER"
  systemctl start "$WORKER_UNIT"
  systemctl start "$REAPER_TIMER"
  systemctl --no-pager --full status "$WORKER_UNIT" || true
  log "OK started"
  log "journal: journalctl -u $WORKER_UNIT -f"
  log "file:    $CENACOLO_HOME/var/log/buy-worker.log"
}

cmd_stop() {
  need_root stop
  log "停止 worker / reaper"
  systemctl stop "$WORKER_UNIT" || true
  systemctl stop "$REAPER_TIMER" || true
  log "OK stopped"
}

cmd_restart() {
  need_root restart
  systemctl restart "$WORKER_UNIT"
  systemctl restart "$REAPER_TIMER" || true
  cmd_status
}

cmd_status() {
  echo "CENACOLO_HOME=$CENACOLO_HOME"
  echo "config: $CFG $([ -f "$CFG" ] && echo OK || echo MISSING)"
  echo "venv:   $BUY_DIR/.venv/bin/python $([ -x "$BUY_DIR/.venv/bin/python" ] && echo OK || echo MISSING)"
  if command -v systemctl >/dev/null 2>&1; then
    echo "worker: $(systemctl is-active "$WORKER_UNIT" 2>/dev/null || echo unknown)"
    echo "reaper: $(systemctl is-active "$REAPER_TIMER" 2>/dev/null || echo unknown)"
    systemctl --no-pager --full status "$WORKER_UNIT" 2>/dev/null || true
  fi
  if [[ -f "$CENACOLO_HOME/var/log/buy-worker.log" ]]; then
    echo "---- last 30 log lines ----"
    tail -n 30 "$CENACOLO_HOME/var/log/buy-worker.log" || true
  fi
}

cmd_recover() {
  need_root recover
  ensure_layout
  ensure_user
  log "git fetch + hard reset origin/main"
  cd "$CENACOLO_HOME"
  git remote set-url origin "$REPO_URL" 2>/dev/null || true
  git fetch --all --prune
  git checkout main
  git reset --hard origin/main
  ensure_venv
  ensure_config
  install_units
  chown -R "$USER_NAME:$USER_NAME" "$CENACOLO_HOME" || true
  log "重启服务"
  systemctl restart "$WORKER_UNIT"
  systemctl restart "$REAPER_TIMER" || systemctl start "$REAPER_TIMER"
  sleep 1
  cmd_status
  log "OK recovered + restarted"
}

usage() {
  cat <<EOF
用法: sudo bash $0 <start|stop|restart|recover|status>

  start    一键：目录 / venv / 配置 / systemd 并启动
  stop     停止 worker + reaper
  restart  仅重启（不拉代码）
  recover  一键恢复：拉最新代码 + 依赖 + unit + 重启
  status   状态与最近日志（可不 sudo）

环境变量:
  CENACOLO_HOME  默认 /opt/cenacolo
  REPO_URL       默认 $REPO_URL
  USER_NAME      默认 cenacolo
EOF
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  recover) cmd_recover ;;
  status) cmd_status ;;
  -h|--help|help) usage ;;
  "") usage; exit 1 ;;
  *) die "未知命令: $1（见 --help）" ;;
esac
