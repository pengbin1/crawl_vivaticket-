# Cenacolo / Vivaticket 支付接口（实站抓取结论）

来源：`crawl_vivaticket/支付链路总结.md`、`pay_.py`、支付页 HTML 样本。  
**不是臆造。**

## 已确认 HTTP 链

1. 站内锁座成功后跳转  
   `https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK...&shop=CV0`

2. `GET formtr.php`  
   - hidden: `shop`, `custref`, `domain=prod-pay-it`  
   - UI: `#pmcreditcard`

3. `POST https://secure.vivaticket.com/paycmd/doauth.php`  
   - `payment_mode=300`（信用卡）  
   - `shop`, `custref`, `domain`, `paymentMethod=on`

4. `GET https://ecomm.sella.it/pagam/pagam.aspx?a=...&b=...`

5. `GET .../Ax_Acceptance_Create.aspx` → 302  
   `https://web.axerve.com/orchestra/checkout/a/{flowId}/b/{authToken}`

6. Axerve 辅助 API（已见）  
   - `POST .../api/flows/{flowId}/trace`  
   - `PUT  .../api/flows/{flowId}/dcc`（输入卡号 BIN）

## 未完整抓到（因此默认 Drission）

点击 Pay 后的真正提交（JS `gatewayService.fulfillFlow`）：

- 预期 `POST .../orchestra/checkout/api/flows/{flowId}/...`
- body 含 `cardData.pan/cvv/expirationMonth/expirationYear`

旧笔记明确写：**这一包还没抓全，不能当稳定纯 HTTP 支付实现。**

## 本项目实现

| 模块 | 行为 |
|------|------|
| `payer/http_pay.py` | 实现 1–5，拿到 Axerve URL |
| `payer/browser_pay.py` | Drission 打开 formtr 或 Axerve，选卡/填卡/点付 |
| `payer/orchestrator.py` | `prefer_browser=true` 直接自动化；`false` 先 HTTP 到 Axerve 再自动化填卡 |

有票锁座或提供有效 `custref` 支付链后，可用 MCP 补抓 fulfill 包，再升级纯 HTTP。
