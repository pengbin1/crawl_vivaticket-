# Ops Runbook — Cenacolo

## 日常检查

```bash
# ready 账号水位（Mongo raw-query 或自建脚本）
# Worker 是否活着
systemctl status cenacolo-buy-worker
journalctl -u cenacolo-buy-worker -n 100 --no-pager

# 文件日志
tail -f /opt/cenacolo/var/log/buy-worker.log
```

关注：`NO_ACCOUNT`、连续 captcha 失败、lease 堆积、`payment_url` 长时间未付。

## 启停（日本机）

```bash
sudo systemctl start|stop|restart cenacolo-buy-worker
sudo systemctl start cenacolo-buy-reaper.timer
```

## 发版

```bash
cd /opt/cenacolo
git pull
cd services/buy && .venv/bin/pip install -r requirements.txt
sudo systemctl restart cenacolo-buy-worker
```

## 测试订单

```bash
cd /opt/cenacolo/services/buy
export CENACOLO_HOME=/opt/cenacolo
.venv/bin/python run_worker.py --seed-order --any --passengers Peng:Bin
.venv/bin/python run_worker.py --once
```

## 养号

```bash
cd /opt/cenacolo/services/register   # 或养号专用机同路径
.venv/bin/python pool_register.py --count 20
.venv/bin/python pool_register.py --list
```

## 排障

| 现象 | 排查 |
|------|------|
| NO_ACCOUNT | register 是否在刷号；账号 status 是否 ready |
| captcha 余额不足 | YesCaptcha getBalance |
| Incapsula / 短 HTML | Chrome 是否可用；代理；WAF cookie |
| Mongo error | 日本机访问 dingstest；代理配置 |
| 锁座成功无支付 | 一期预期；用 payment_url + pay_only 或人工 |

## 敏感信息

日志、飞书、artifacts 文件名可用 order_no；**不要**把 `etc/*.local.yaml` 提交或 scp 到公开位置。
