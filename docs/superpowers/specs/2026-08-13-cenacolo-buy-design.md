# Cenacolo Vinciano 自动抢票支付设计

**日期:** 2026-08-13  
**项目:** `cenacolo_buy/`（全新项目，不修改 `crawl_vivaticket` / `cenacolo_vivaticket`）  
**目标站点:** https://cenacolovinciano.vivaticket.it  
**活动:** Cenacolo Vinciano / Last Supper — event `151991`, `idt=2547`

## 1. 目标与成功标准

端到端 **无人值守** 完成购票并支付：

1. 过 WAF / Safetynet  
2. 账号登录  
3. 发现有票场次并锁座  
4. 填写实名  
5. 确认购物车拿到支付链接  
6. 自动完成信用卡支付  

**成功:** 支付网关返回成功（或站点订单确认），并发出成功通知。  
**保底成功（半成功）:** 锁座成功且拿到有效 `payment_url`；若支付自动失败，保留链接并紧急告警（正常路径不依赖人工点击）。

**明确不做:**

- 不修改旧仓库代码  
- 不拆「等人手工点支付」的外部服务  
- 正常流程不要求人点击浏览器  

## 2. 关键约束

| 约束 | 说明 |
|------|------|
| 支付时限 | 锁座成功后约 **20 分钟** 必须完成支付，否则座位释放 |
| 风控 | 裸 HTTP 会命中 Incapsula；需浏览器拿到 `incap_ses_*` / `reese84` 等 cookie 后再用 HTTP |
| 验证码 | 价格页 / 下单有 reCAPTCHA，需打码服务 |
| 支付链 | Vivaticket Secure → Sella → Axerve Orchestra；可能出现 3DS |
| 浏览器技术 | 凡需要浏览器自动化，**统一使用 DrissionPage** |

## 3. 总体架构

单项目、单进程流水线；代码按模块分层，运行时串行执行。

```
run_once.py
    │
    ├─ locker.bootstrap_waf()     # Drission：打开活动页，导出 cookie + UA
    ├─ locker.login()             # HTTP：keylogin
    ├─ locker.clear_cart()        # HTTP
    ├─ locker.find_inventory()    # HTTP：日历 + eventoWidgetTlite
    ├─ locker.lock_and_checkout() # HTTP：prices → captcha → checkAnagrafica
    │                             #        → personalDetails → bloccaCarrello
    │                             #        → 记录 locked_at / deadline_at
    └─ payer.pay(job)             # 立刻执行（20 分钟窗口）
           ├─ try http_pay()      # 短超时试 Axerve HTTP
           └─ fallback browser_pay()  # Drission 自动选卡/填卡/提交
```

人只接收通知，不参与操作。

## 4. 模块边界

### 4.1 `locker/`

职责：从冷启动到产出 `PaymentJob`。

步骤：

1. **Drission** 打开  
   `https://cenacolovinciano.vivaticket.it/en/event/cenacolo-vinciano/151991?idt=2547`  
   （可带当前有效的 `qubs*` Safetynet 参数；若在队列页则处理 rejoin）  
2. 导出 cookie + User-Agent → `requests`/`httpx` Session  
3. GET 会员/首页提取 `keylogin`，POST 登录  
4. `cmd=cancellaCarrello&empty=1` 清空购物车  
5. GET 活动页，解析 `eventi[...].push(new Array(...))` 得到日期与余量  
6. 对候选日期 POST `eventoWidgetTlite.php` 取时段余量  
7. GET `cmd=prices` → 提取 `data-sitekey` → 打码 → POST `checkAnagrafica`  
8. 提交实名（`ana_firstname` / `ana_lastname`）  
9. POST `bloccaCarrello` → 解析跳转得到  
   `https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK...&shop=CV0`  
10. 构造 `PaymentJob`，**立即**交给 payer（同进程调用，不入队等待）

### 4.2 `payer/`

职责：在 `deadline_at` 前完成支付，全程无人点击。

**路径 A — HTTP（优先，短超时）**

1. GET `formtr.php`  
2. POST `doauth.php`（`payment_mode=300` 信用卡）  
3. 跟随 Sella → Axerve `flowId` / `authToken`  
4. 调用 Axerve `fulfillFlow`（或等价 API）提交卡数据  

若：超时、非预期 HTML/JSON、明确 3DS 挑战、会话字段缺失 → 立刻转路径 B。

**路径 B — Drission（默认可靠路径）**

1. 用已有代理/环境打开 `payment_url`（或路径 A 已到达的 Axerve URL）  
2. 选择信用卡支付  
3. 自动填入卡号 / 有效期 / CVV / 持卡人（如需要）  
4. 点击支付  
5. 若出现 3DS：在自动化能力内尽量完成；无法自动完成则标记失败并告警（仍不设计「等人点」的主流程）  
6. 根据成功/失败页或订单号判定结果  

卡数据只在 payer 配置中读取，日志脱敏（仅卡号后四位）。

### 4.3 `shared/`

- 配置加载、日志、飞书通知  
- `PaymentJob` 数据类  
- Session / cookie 工具  
- 截止时间工具：`seconds_remaining(deadline_at)`

## 5. PaymentJob 契约

```json
{
  "job_id": "uuid",
  "payment_url": "https://secure.vivaticket.com/paycmd/formtr.php?custref=VIVATK...&shop=CV0",
  "custref": "VIVATK...",
  "shop": "CV0",
  "account_email": "user@example.com",
  "event": {
    "event_id": "151991",
    "date": "2026-09-01",
    "time": "10:15",
    "tcode": "vt0005655",
    "pcode": "13792799"
  },
  "ticket_count": 1,
  "amount_cents": 1500,
  "passengers": [{"first_name": "...", "last_name": "..."}],
  "user_agent": "...",
  "cookies": {},
  "locked_at": "2026-08-13T15:30:00+08:00",
  "deadline_at": "2026-08-13T15:50:00+08:00"
}
```

说明：

- `deadline_at = locked_at + 20 minutes`（可配置，默认 1200 秒）  
- `cookies` 可选：支付域若需带站内会话则传入；支付页本身通常以 `custref` 为主  

## 6. 20 分钟窗口规则（硬约束）

1. **锁到即付:** `bloccaCarrello` 成功解析出 URL 后，同步调用 `payer.pay(job)`，中间禁止：
   - sleep 等待下次放票  
   - 再扫其他日期  
   - 重新打码 / 重新过 WAF  
   - 长时间调试 dump（仅写短日志）  
2. **截止时间:** 每个支付子步骤检查剩余时间；HTTP 单路径建议总预算 ≤ 60 秒。  
3. **紧急刹车:** 剩余 &lt; 120 秒仍未成功 → 发紧急告警，停止无意义重试。  
4. **失败保留:** 自动支付失败时仍保存 `payment_url` 到本地结果文件 + 告警，便于极端兜底（非主流程）。

## 7. 配置

使用本地配置（`config.yaml` 或环境变量），**不提交真实密钥/卡号**。提供 `config.example.yaml`：

```yaml
account:
  email: "i67oe5e7f6@163.com"
  password: "SET_VIA_ENV_OR_LOCAL"
  # 可选：imap_code / phone_number 仅排查用，购票主路径不需要

event:
  base: "https://cenacolovinciano.vivaticket.it"
  path: "/en/event/cenacolo-vinciano/151991"
  idt: "2547"
  target_dates: []          # 空 = 任意有票日期
  use_any_available: true
  ticket_count: 1

passengers:
  - first_name: "peng"
    last_name: "bin"

captcha:
  provider: "yescaptcha"
  client_key: "SET_VIA_ENV"

payment:
  lock_ttl_seconds: 1200    # 20 分钟
  http_attempt_timeout_seconds: 60
  urgent_remaining_seconds: 120
  card:
    number: "SET_VIA_ENV"
    exp_month: "SET_VIA_ENV"
    exp_year: "SET_VIA_ENV"
    cvv: "SET_VIA_ENV"
    holder_first_name: "SET_VIA_ENV"
    holder_last_name: "SET_VIA_ENV"

browser:
  headless: true
  proxy: ""                 # 可选，与 HTTP 同出口更稳

notify:
  feishu_webhook: ""
  feishu_secret: ""
```

账号示例来自用户提供的 ready 账号（密码等敏感项只放本地/环境变量）。

## 8. 目录结构

```
cenacolo_buy/
  README.md
  requirements.txt
  config.example.yaml
  run_once.py
  locker/
    __init__.py
    waf.py          # Drission bootstrap
    auth.py         # login / logout detect
    inventory.py    # calendar + widget
    checkout.py     # prices / captcha / lock / personal / blocca
  payer/
    __init__.py
    http_pay.py     # formtr → doauth → Axerve HTTP
    browser_pay.py  # Drission card automation
    orchestrator.py # HTTP then Drission, respect deadline
  shared/
    __init__.py
    config.py
    models.py
    session.py
    notify.py
    log.py
  docs/superpowers/specs/
    2026-08-13-cenacolo-buy-design.md
```

## 9. 错误处理与通知

| 阶段 | 行为 |
|------|------|
| WAF 失败 | 重试有限次 → 失败退出 + 告警 |
| 登录失败 | 退出 + 告警 |
| 无票 | 可配置：退出 / 间隔轮询（轮询仅在未锁座前） |
| 打码失败 | 换时段或有限重试；**锁座后不再打码** |
| 锁座 `seat_not_assignable` | 换下一时段（仍在锁座前） |
| 锁座成功但支付失败 | **P0 告警** + 保存 job/URL |
| 支付成功 | 成功告警（日期、时段、金额、custref、后四位卡） |

## 10. 实现顺序（供后续 plan）

1. 脚手架 + 配置 + PaymentJob  
2. Drission WAF bootstrap → Session  
3. 登录 + 清车 + 库存解析  
4. 打码 + 锁座 + 实名 + blocca → PaymentJob  
5. payer HTTP 骨架  
6. payer Drission 自动填卡（优先保证 20 分钟内可付）  
7. `run_once` 串联 + 通知  
8. （可选）未锁座前的余量轮询  

建议实现时 **先打通「锁座 + Drission 支付」**，再优化 Axerve 纯 HTTP；这样在 20 分钟窗口内先有可靠闭环。

## 11. 非目标 / 延后

- 多账号并发抢同一场（可后续加）  
- 与 `cenacolo_vivaticket` 账号池自动对接（首版本地配置即可）  
- 完整逆向所有 Axerve 3DS 分支的纯 HTTP 实现（有则加，无则 Drission）  

## 12. 决策记录

- 采用「单进程流水线 + 模块分层」，不用等人点击的拆服务方案  
- 浏览器统一 DrissionPage  
- 支付：HTTP 短试 + Drission 保底自动完成  
- 锁座后 20 分钟硬窗口：锁到即付  
- 旧项目只作协议参考，零改动  
