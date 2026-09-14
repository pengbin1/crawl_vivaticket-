# Cenacolo Monorepo

Vivaticket「最后的晚餐」（Cenacolo Vinciano）无人值守方案 B：

**养号 → 订单驱动锁座 → 支付队列唤醒 →（可选）VCC 开卡支付**

远程仓库：[`pengbin1/crawl_vivaticket-`](https://github.com/pengbin1/crawl_vivaticket-)（名称带尾缀 `-`）。

---

## 整体流程（一眼看懂）

```text
┌─────────────┐     ┌──────────────────┐     ┌─────────────────────┐
│  register   │     │   预约 API / seed  │     │   YesCaptcha / Chrome │
│  养号刷 ready│     │  写 cenacolo_orders│     │   Dings VCC（可选）  │
└──────┬──────┘     └─────────┬────────┘     └──────────▲──────────┘
       │ ready                │ queued                  │
       ▼                      ▼                         │
┌──────────────────────────────────────────────────────┴──────────┐
│                     Mongo（dingstest）                            │
│  vivaticket_accounts          cenacolo_orders                     │
└───────────────────────────────┬──────────────────────────────────┘
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
 ┌─────────────────────┐                   ┌─────────────────────┐
 │   lock-worker       │                   │   pay-worker（常驻）  │
 │   run_worker.py     │                   │   run_pay_worker.py │
 │                     │                   │                     │
 │ 领单 → 租号 → 锁座   │                   │ 听 /wake + 消费队列  │
 │ 写 payment_url      │──入队+唤醒────────▶│ 或扫 Mongo locked    │
 │ 释放账号继续抢      │                   │                     │
 │ （约 20 分钟占座）   │                   │ VCC开卡→填卡→OTP    │
 │                     │                   │ →确认成功→上报→paid │
 └─────────────────────┘                   └─────────────────────┘
          │                                           │
          ▼                                           ▼
   status=locked                              status=paid
   + 队列 pending                             + 队列 done（出队）
```

### 成功路径（最短）

1. 订单进入 `queued`  
2. **锁座 Worker** 抢到座 → `locked` + `payment_url`（约 20 分钟窗口）  
3. 同时：**入本地支付队列** + `POST /wake` 通知支付  
4. **支付 Worker** 领取 →（VCC 开启时）开卡填卡 → 成功页确认 → 上报 → `paid` 并出队  

锁座与支付**拆成两个进程**：抢票不被开卡/3DS 卡住；没有支付链接不会开卡。

### 订单状态

| status | 含义 |
|--------|------|
| `queued` / `waiting_inventory` / `retry_wait` | 待锁座或重试 |
| `processing` | 锁座 Worker 正在干 |
| `locked` | 已有 `payment_url`，等支付（已入队或靠扫库兜底） |
| `paying` | 支付 Worker 已原子领取 |
| `paid` | 支付成功（VCC 开启时已上报） |
| `manual_review` | 硬失败或占座窗口过期，需人工 |

### 支付双保险

| 机制 | 作用 |
|------|------|
| 本地队列 `var/pay_queue/` | 锁座成功立刻入队，成功出队 |
| HTTP wake `127.0.0.1:18765/wake` | 少空转查库，立刻叫醒支付 |
| Mongo 扫 `locked` | wake/队列丢了也能兜底（约每 15s） |

失败可重试时：**回队 + 复用同一 VCC 幂等键**，不会盲目再开一张卡。

---

## 目录

```text
services/buy/          锁座 + 支付（进程拆分）
  run_worker.py        锁座 Worker
  run_pay_worker.py    支付 Worker（队列 / wake / 扫库）
  payer/vcc/           VCC 开卡、OTP、上报
services/register/     养号 → vivaticket_accounts
deploy/japan-worker/   systemd 一键启停（lock + pay + reaper）
docs/                  架构 / VCC / 运维
var/log|artifacts|pay_queue   运行时（gitignore）
```

---

## 快速开始（开发机）

### 1. Buy

```bash
cd services/buy
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.local.yaml   # 填 captcha；账号来自 Mongo
export CENACOLO_HOME="$(cd ../.. && pwd)"

# 播种测试单
python run_worker.py --seed-order --any --passengers Peng:Bin

# 终端 A：锁座（可多开进程提高吞吐）
python run_worker.py

# 终端 B：支付常驻（默认不开 VCC，只练通队列/唤醒）
python run_pay_worker.py
```

关键配置（`config.local.yaml`）：

- `payment.vcc.enabled`：**默认 false**，确认额度/`pnr_source` 后再开  
- `payment.pay_queue.wake_url`：默认 `http://127.0.0.1:18765/wake`  

单机调试：

```bash
python run_once.py --lock-only          # 只锁座
python pay_only.py '<payment_url>'      # 对已有链接支付
```

### 2. Register（养号）

```bash
cd services/register
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python pool_register.py --available
python pool_register.py --count 5
```

---

## 生产（日本机）

```bash
cd /root/pengb_dev
git clone git@github.com:pengbin1/crawl_vivaticket-.git cenacolo
cd cenacolo
sudo bash deploy/japan-worker/start.sh    # 同时装 lock + pay + reaper
bash deploy/japan-worker/status.sh
sudo bash deploy/japan-worker/recover.sh  # 以后拉代码更新
```

日志：

- `$CENACOLO_HOME/var/log/buy-worker.log`  
- `$CENACOLO_HOME/var/log/buy-pay-worker.log`  
- `journalctl -u cenacolo-buy-worker -f`  
- `journalctl -u cenacolo-buy-pay-worker -f`  

未就绪 VCC 时：可 `systemctl stop cenacolo-buy-pay-worker`，或保持 `vcc.enabled=false`。

---

## 外部依赖

| 依赖 | 用途 |
|------|------|
| `https://dingstest.133.cn/mongo_api` | 订单 / 账号 |
| YesCaptcha | 锁座打码 |
| Chrome/Chromium | WAF / 支付页 |
| Dings VCC Ticket | 开卡 / OTP / 采购上报（可选） |

---

## 文档

| 文档 | 内容 |
|------|------|
| [架构](docs/architecture.md) | 锁座/支付拆分细节 |
| [VCC 支付](docs/vcc-payment.md) | 开卡顺序与安全约定 |
| [运维手册](docs/ops-runbook.md) | 启停、排障 |
| [账号池设计](docs/2026-08-17-reservation-worker-account-pool-design.md) | 订单/租约设计原文 |

---

## 上线顺序建议

1. Mongo API 可读可写  
2. register 刷出 `ready` 账号  
3. 只跑 **lock-worker**，seed 验证能锁到 `payment_url`  
4. 再跑 **pay-worker**（可先 `vcc.enabled=false` 验证入队/唤醒）  
5. 与支付团队确认 `card_amount` / `pnr_source` / MCC 后，打开 `payment.vcc.enabled`  
