# Cenacolo Monorepo

Vivaticket「最后的晚餐」方案 B：**养号 + 订单驱动锁座（一期不自动扣款）**。

远程仓库名可能仍是 `crawl_vivaticket`，本仓内容以本文为准。

## 目录

```text
services/buy/        抢票引擎 + Worker（锁座 → payment_url）
services/register/   养号（刷 vivaticket_accounts ready）
deploy/              systemd / 安装脚本
docs/                架构与运维手册
configs/             仅示例配置
var/log|artifacts    运行日志与调试 HTML（gitignore）
```

## 服务依赖

```text
register  →  Mongo vivaticket_accounts (ready)
预约 API  →  Mongo cenacolo_orders (queued)     # 外部，暂不在本仓
buy-worker → 领单 + 租号 + 锁座 → payment_url
```

外部：`https://dingstest.133.cn/mongo_api`、YesCaptcha、Chrome/Chromium。

## 快速开始（开发机）

### Buy

```bash
cd services/buy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.local.yaml   # 填 captcha / browser；worker 账号来自 Mongo
export CENACOLO_HOME="$(cd ../.. && pwd)"  # 日志写到仓库 var/log
python run_worker.py --seed-order --any --passengers Peng:Bin
python run_worker.py --once
```

单机调试（配置里写死账号）：

```bash
python run_once.py --lock-only
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

## 生产目录约定

```text
/opt/cenacolo
├── services/buy|register   # 本仓对应目录
├── etc/buy.local.yaml      # 密钥，chmod 600
├── var/log/
└── var/artifacts/
```

`export CENACOLO_HOME=/opt/cenacolo`  

一键脚本（日本机）：

```bash
sudo bash deploy/japan-worker/start.sh      # 一键启动
sudo bash deploy/japan-worker/recover.sh    # 一键恢复（拉代码+重启）
bash deploy/japan-worker/status.sh
```

更多见 `deploy/japan-worker/`、`docs/ops-runbook.md`。

## 文档

- [架构](docs/architecture.md)
- [运维手册](docs/ops-runbook.md)
- [Monorepo 设计](docs/superpowers/specs/2026-09-14-cenacolo-monorepo-design.md)
- [Worker/账号池设计](docs/2026-08-17-reservation-worker-account-pool-design.md)

## 上线顺序

1. Mongo API + 集合可读可写  
2. register 刷出 ready 账号  
3. 日本机 buy-worker systemd  
4. seed 订单验证锁座  
5. 预约 API 写真实单  
6. VCC 就绪后再开自动支付  
