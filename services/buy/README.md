# Cenacolo Buy

锁座与支付进程拆分。总览与流程图见仓库根目录 [README.md](../../README.md)。

```bash
export CENACOLO_HOME="$(cd ../.. && pwd)"
cp config.example.yaml config.local.yaml

# 锁座：成功后入队 + POST /wake，账号释放继续抢
python run_worker.py

# 支付常驻：听 wake、消费队列、Mongo locked 兜底
python run_pay_worker.py
```

| 路径 | 说明 |
|------|------|
| `var/pay_queue/` | 支付队列 pending → processing → done/dead |
| `http://127.0.0.1:18765/wake` | 锁座成功后的唤醒接口 |
| `payment.vcc.enabled` | 默认 false；开启后才开卡 |

更多：`docs/architecture.md`、`docs/vcc-payment.md`。
