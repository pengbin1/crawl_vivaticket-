# Ops Runbook — Cenacolo

## 日常检查

```bash
systemctl status cenacolo-buy-worker cenacolo-buy-pay-worker
journalctl -u cenacolo-buy-worker -n 80 --no-pager
journalctl -u cenacolo-buy-pay-worker -n 80 --no-pager

tail -f $CENACOLO_HOME/var/log/buy-worker.log
tail -f $CENACOLO_HOME/var/log/buy-pay-worker.log
```

关注：`NO_ACCOUNT`、captcha 失败、lease 堆积、`locked` 超时未付、`manual_review`。

## 启停（日本机）

```bash
cd /root/pengb_dev/cenacolo
sudo bash deploy/japan-worker/start.sh      # lock + pay + reaper
sudo bash deploy/japan-worker/recover.sh
sudo bash deploy/japan-worker/stop.sh
bash deploy/japan-worker/status.sh
```

```bash
sudo systemctl start|stop|restart cenacolo-buy-worker
sudo systemctl start|stop|restart cenacolo-buy-pay-worker
sudo systemctl start cenacolo-buy-reaper.timer
```

未就绪 VCC 时：可先 `systemctl stop cenacolo-buy-pay-worker`，或保持 `payment.vcc.enabled=false`。

## 发版

```bash
cd $CENACOLO_HOME
git pull
cd services/buy && .venv/bin/pip install -r requirements.txt
sudo systemctl restart cenacolo-buy-worker cenacolo-buy-pay-worker
```

## 测试订单

```bash
cd $CENACOLO_HOME/services/buy
export CENACOLO_HOME=...
.venv/bin/python run_worker.py --seed-order --any --passengers Peng:Bin
.venv/bin/python run_worker.py --once          # 锁座 → locked
.venv/bin/python run_pay_worker.py --once      # 支付 → paid（需 VCC 配置）
```

## 养号

```bash
cd $CENACOLO_HOME/services/register
.venv/bin/python pool_register.py --count 20
.venv/bin/python pool_register.py --list
```

## 排障

| 现象 | 排查 |
|------|------|
| NO_ACCOUNT | register 水位；账号是否 ready |
| captcha 余额不足 | YesCaptcha getBalance |
| Incapsula / 短 HTML | Chrome / 代理 / WAF |
| locked 一直不付 | pay-worker 是否在跑；deadline 是否过期 |
| 支付失败 manual_review | 日志 `vcc_order_id` / `purchase_id`；勿盲目重开卡 |
| 误开卡 | 确认只有 pay-worker 且 `vcc.enabled=true` |

## 敏感信息

日志可用 order_no / custref / vcc_order_id；禁止 PAN、CVV、OTP、完整 cookie。
