#!/usr/bin/env bash
# Cenacolo Japan host control: start | stop | restart | recover | status
#
# 放在 pengb_dev 下时（推荐）:
#   cd /root/pengb_dev
#   git clone git@github.com:pengbin1/crawl_vivaticket-.git cenacolo
#   cd cenacolo
#   sudo bash deploy/japan-worker/start.sh
#
# 脚本会自动把 CENACOLO_HOME 设为仓库根目录。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$SCRIPT_DIR/systemd"
DETECTED_HOME="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [[ -z "${CENACOLO_HOME:-}" ]]; then
  if [[ -d "$DETECTED_HOME/services/buy" ]]; then
    CENACOLO_HOME="$DETECTED_HOME"
  else
    CENACOLO_HOME="/opt/cenacolo"
  fi
fi

# 日本机 pengb_dev 习惯用 root；可用 USER_NAME=cenacolo 改回独立用户
REPO_URL="${REPO_URL:-git@github.com:pengbin1/crawl_vivaticket-.git}"
USER_NAME="${USER_NAME:-root}"
BUY_DIR="$CENACOLO_HOME/services/buy"
CFG="$CENACOLO_HOME/etc/buy.local.yaml"
WORKER_UNIT="cenacolo-buy-worker.service"
PAY_WORKER_UNIT="cenacolo-buy-pay-worker.service"
REAPER_TIMER="cenacolo-buy-reaper.timer"

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
    mkdir -p "$(dirname "$CENACOLO_HOME")"
    git clone "$REPO_URL" "$CENACOLO_HOME"
  fi
  [[ -d "$BUY_DIR" ]] || die "缺少 $BUY_DIR，请确认 clone 的是 monorepo 最新代码"
}

ensure_user() {
  if [[ "$USER_NAME" == "root" ]]; then
    return 0
  fi
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

render_unit() {
  local src="$1" dest="$2"
  sed -e "s|__CENACOLO_HOME__|${CENACOLO_HOME}|g" \
      -e "s|__RUN_USER__|${USER_NAME}|g" \
      "$src" >"$dest"
}

install_units() {
  local src="$UNIT_DIR"
  if [[ -d "$CENACOLO_HOME/deploy/japan-worker/systemd" ]]; then
    src="$CENACOLO_HOME/deploy/japan-worker/systemd"
  fi
  [[ -d "$src" ]] || die "缺少 systemd 目录: $src"
  render_unit "$src/cenacolo-buy-worker.service" /etc/systemd/system/cenacolo-buy-worker.service
  render_unit "$src/cenacolo-buy-pay-worker.service" /etc/systemd/system/cenacolo-buy-pay-worker.service
  render_unit "$src/cenacolo-buy-reaper.service" /etc/systemd/system/cenacolo-buy-reaper.service
  cp "$src/cenacolo-buy-reaper.timer" /etc/systemd/system/cenacolo-buy-reaper.timer
  systemctl daemon-reload
  systemctl enable "$WORKER_UNIT" "$PAY_WORKER_UNIT" "$REAPER_TIMER" >/dev/null
}

cmd_start() {
  need_root start
  log "CENACOLO_HOME=$CENACOLO_HOME USER_NAME=$USER_NAME"
  ensure_layout
  ensure_user
  ensure_venv
  ensure_config
  install_units
  if [[ "$USER_NAME" != "root" ]]; then
    chown -R "$USER_NAME:$USER_NAME" "$CENACOLO_HOME" || true
  fi
  log "启动 $WORKER_UNIT + $PAY_WORKER_UNIT + $REAPER_TIMER"
  systemctl start "$WORKER_UNIT"
  systemctl start "$PAY_WORKER_UNIT"
  systemctl start "$REAPER_TIMER"
  systemctl --no-pager --full status "$WORKER_UNIT" || true
  systemctl --no-pager --full status "$PAY_WORKER_UNIT" || true
  log "OK started"
  log "journal lock: journalctl -u $WORKER_UNIT -f"
  log "journal pay:  journalctl -u $PAY_WORKER_UNIT -f"
  log "file:         $CENACOLO_HOME/var/log/buy-worker.log"
}

cmd_stop() {
  need_root stop
  log "停止 lock-worker / pay-worker / reaper"
  systemctl stop "$WORKER_UNIT" || true
  systemctl stop "$PAY_WORKER_UNIT" || true
  systemctl stop "$REAPER_TIMER" || true
  log "OK stopped"
}

cmd_restart() {
  need_root restart
  systemctl restart "$WORKER_UNIT"
  systemctl restart "$PAY_WORKER_UNIT"
  systemctl restart "$REAPER_TIMER" || true
  cmd_status
}

cmd_status() {
  echo "CENACOLO_HOME=$CENACOLO_HOME"
  echo "config: $CFG $([ -f "$CFG" ] && echo OK || echo MISSING)"
  echo "venv:   $BUY_DIR/.venv/bin/python $([ -x "$BUY_DIR/.venv/bin/python" ] && echo OK || echo MISSING)"
  if command -v systemctl >/dev/null 2>&1; then
    echo "lock:   $(systemctl is-active "$WORKER_UNIT" 2>/dev/null || echo unknown)"
    echo "pay:    $(systemctl is-active "$PAY_WORKER_UNIT" 2>/dev/null || echo unknown)"
    echo "reaper: $(systemctl is-active "$REAPER_TIMER" 2>/dev/null || echo unknown)"
    systemctl --no-pager --full status "$WORKER_UNIT" 2>/dev/null || true
    systemctl --no-pager --full status "$PAY_WORKER_UNIT" 2>/dev/null || true
  fi
  if [[ -f "$CENACOLO_HOME/var/log/buy-worker.log" ]]; then
    echo "---- last 20 buy-worker.log ----"
    tail -n 20 "$CENACOLO_HOME/var/log/buy-worker.log" || true
  fi
  if [[ -f "$CENACOLO_HOME/var/log/buy-pay-worker.log" ]]; then
    echo "---- last 20 buy-pay-worker.log ----"
    tail -n 20 "$CENACOLO_HOME/var/log/buy-pay-worker.log" || true
  fi
}

cmd_recover() {
  need_root recover
  log "CENACOLO_HOME=$CENACOLO_HOME"
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
  if [[ "$USER_NAME" != "root" ]]; then
    chown -R "$USER_NAME:$USER_NAME" "$CENACOLO_HOME" || true
  fi
  log "重启服务"
  systemctl restart "$WORKER_UNIT"
  systemctl restart "$PAY_WORKER_UNIT"
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

当前检测到 CENACOLO_HOME=$CENACOLO_HOME

pengb_dev 示例:
  cd /root/pengb_dev
  git clone $REPO_URL cenacolo
  cd cenacolo && sudo bash deploy/japan-worker/start.sh
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
