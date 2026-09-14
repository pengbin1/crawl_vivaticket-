# Cenacolo Monorepo

Vivaticket「最后的晚餐」方案 B：**养号 + 锁座 Worker + 支付 Worker（拆分）**。

远程仓库名可能仍是 `crawl_vivaticket`，本仓内容以本文为准。

## 目录

```text
services/buy/        锁座引擎 + 支付引擎（进程拆分）
services/register/   养号（刷 vivaticket_accounts ready）
deploy/              systemd / 安装脚本
docs/                架构与运维手册
configs/             仅示例配置
var/log|artifacts    运行日志与调试 HTML（gitignore）
```

## 服务依赖

```text
register      →  Mongo vivaticket_accounts (ready)
预约 API      →  Mongo cenacolo_orders (queued)
lock-worker   → 领单 + 租号 + 锁座 → status=locked + payment_url
pay-worker    → 领取 locked → VCC/浏览器支付 → paid
```

外部：`https://dingstest.133.cn/mongo_api`、YesCaptcha、Chrome/Chromium、Dings VCC。

## 快速开始（开发机）

### Buy

```bash
cd services/buy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.local.yaml
export CENACOLO_HOME="$(cd ../.. && pwd)"
python run_worker.py --seed-order --any --passengers Peng:Bin
# 终端 A：只锁座
python run_worker.py --once
# 终端 B：只支付（另开；VCC 默认关，确认后再 enabled）
python run_pay_worker.py --once
```

单机调试：

```bash
python run_once.py --lock-only
python pay_only.py '<payment_url>'
```

### Register

```bash
cd services/register
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python pool_register.py --available
python pool_register.py --count 5
```

## 生产

```bash
cd /root/pengb_dev
git clone git@github.com:pengbin1/crawl_vivaticket-.git cenacolo
cd cenacolo
sudo bash deploy/japan-worker/start.sh   # lock + pay + reaper
bash deploy/japan-worker/status.sh
```

## 文档

- [架构](docs/architecture.md)
- [VCC 支付](docs/vcc-payment.md)
- [运维手册](docs/ops-runbook.md)

## 上线顺序

1. Mongo API 可读可写  
2. register 刷 ready 账号  
3. 先跑 lock-worker（可先停 pay 或保持 `vcc.enabled=false`）  
4. seed 验证锁座  
5. 确认 VCC 参数后启用 pay-worker  
