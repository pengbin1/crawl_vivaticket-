# Cenacolo Buy — 锁座 / 支付拆分

```bash
export CENACOLO_HOME="$(cd ../.. && pwd)"
cp config.example.yaml config.local.yaml

# 锁座：成功后入队 + POST /wake
python run_worker.py

# 支付常驻：听 wake、消费队列、Mongo locked 兜底
python run_pay_worker.py
```

队列：`$CENACOLO_HOME/var/pay_queue/`  
Wake：`http://127.0.0.1:18765/wake`  
VCC：`docs/vcc-payment.md`（默认不开卡）
