# Architecture — Cenacolo 方案 B（锁座 / 支付拆分）

## 组件

| 组件 | 入口 | 职责 |
|------|------|------|
| lock-worker | `run_worker.py` | 领单、租账号、锁座、写 `payment_url`，立刻释放账号抢下一单 |
| pay-worker | `run_pay_worker.py` | 领取 `locked` → `paying` → 开卡/填卡/上报 → `paid` |
| buy-reaper | `--reap` / timer | 回收过期租约；`paying` 宕机且窗口未过 → 退回 `locked` |
| register | `services/register` | 养号写入账号池 |
| VCC | `payment.vcc` | 仅支付进程在确认有支付链接后开卡 |

## 数据流

```text
queued → lock-worker → locked + payment_url
              ↓
         ① 写入本地支付队列 var/pay_queue/pending
         ② POST http://127.0.0.1:18765/wake 唤醒支付
              ↓
pay-worker：优先出队 → claim locked→paying → 开卡/支付
              ↓ 成功
         出队(done) + status=paid
              ↓ 可重试失败
         回队 + status=locked（复用同一 VCC 幂等键，不换卡）
              ↓
         Mongo 兜底：即使 wake/队列丢了，仍定期扫 locked
```

## 为什么拆

- 抢票吞吐：锁座不等支付
- 不误开卡：没有 payment_url 不会进支付
- 队列 + wake：少空转查库；Mongo 双保险
- 失败复用卡：同一 `client_request_id` / 订单上已写的 `vcc_order_id`，禁止盲目再开一张

## 状态要点

| status | 含义 |
|--------|------|
| `locked` | 已有支付链接，在队列或等扫库 |
| `paying` | 支付 Worker 已领取 |
| `paid` | 成功并出队 |
| `manual_review` | 硬失败 / 窗口过期 |

## 本地

```bash
python3 run_worker.py --once          # 锁座 + 入队 + wake
python3 run_pay_worker.py             # 常驻：听 /wake + 消费队列 + 扫库
```

队列目录：`$CENACOLO_HOME/var/pay_queue/{pending,processing,done,dead}`  
Wake：`POST http://127.0.0.1:18765/wake`  
日本机：`deploy/japan-worker` 同时装 lock + pay。
