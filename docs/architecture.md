# Architecture — Cenacolo 方案 B

## 组件

| 组件 | 路径 | 职责 |
|------|------|------|
| buy-worker | `services/buy` | 轮询领单、租账号、锁座、回写 `payment_url` |
| buy-reaper | 同上 `--reap` | 回收过期订单/账号租约 |
| register | `services/register` | 163 邮箱注册激活 Vivaticket，写入账号池 |
| Mongo HTTP | dingstest | 订单/账号真相源 |
| 预约 API | 外部 | 写 `cenacolo_orders` |
| VCC | 外部 | 二期自动支付记账 |

## 数据流

```text
用户预约 → cenacolo_orders(queued)
                ↓
         buy-worker claim
                ↓
    reserve vivaticket_accounts(ready)
                ↓
         WAF → login → inventory → captcha → lock
                ↓
         order.status=locked + payment_url
                ↓
         (phase2) pay → paid / used
```

## 一期边界

- Worker **默认不自动支付**（`auto_pay=False`）。  
- 支付引擎代码在 `services/buy/payer/`，供 `pay_only.py` / 二期使用。  
- 养号与抢票进程隔离，避免浏览器配置互相干扰。

## 日志

- 环境变量 `CENACOLO_HOME`（默认 monorepo 根）  
- 文件：`$CENACOLO_HOME/var/log/{buy-worker,buy-once,register}.log`  
- stdout 交给 systemd/journald  
- 禁止记录卡号、CVV、完整 cookie、打码 token  
