# VCC 支付接入（必须符合文档流程）

对照 `crawler-vcc-payment-api-docs-20260914/CRAWLER_INTEGRATION_GUIDE.md`。

## 顺序

1. 持久化 `client_request_id`（`CENACOLO-CARD-{custref}-1`）到 `var/artifacts/vcc/`
2. `POST /api/v1/vcc-card-applications`（同 key 原样重放；`UNKNOWN/PENDING` 只 refresh）
3. `POST .../sensitive-details`（PAN/CVV 仅内存）
4. 浏览器在 Axerve 填**服务端返回的**持卡人姓名/邮箱 + 卡信息；提交前 `POST /api/v1/otp-payments`
5. 若 3DS：同 `payment_id=PAY-{custref}` 有界 wait，验证码不落盘
6. **仅当供应商成功页确认后** → `POST /api/v2/procurement-payment-events`（首次 payload 落盘后原样重试）

## 配置

`services/buy/config.local.yaml` → `payment.vcc`：

| 字段 | 说明 |
|---|---|
| `enabled` | `true` 走 VCC；`false` 才用静态 `payment.card`（仅调试） |
| `base_url` | 默认 `https://dings.133.cn/online/dings_vcc_ticket` |
| `expected_env` | 与 `/health` 的 `env` 一致，否则停写 |
| `card_amount` / `card_currency` | 必须等于部署 `VCC_CARD_*` |
| `pnr_source` | 须支付团队登记 profile（勿抄 `small_train_official`） |
| `allowed_merchant_categories` | 空=省略；有值须支付团队确认 MCC |

环境变量：`CENACOLO_VCC_ENABLED`、`CENACOLO_VCC_BASE_URL`、`CENACOLO_VCC_CARD_AMOUNT`、`CENACOLO_VCC_CARD_CURRENCY`、`CENACOLO_VCC_PNR_SOURCE`。

## 本地试支付

```bash
# 先确认 health env
curl -sS "$BASE_URL/health"

# 锁座后支付，或对已有 formtr URL：
python pay_only.py 'https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK...&shop=CV0'
```

未确认成功页时**不会**上报；日志只有 `****last4` / `vcc_order_id` / `purchase_id`。
