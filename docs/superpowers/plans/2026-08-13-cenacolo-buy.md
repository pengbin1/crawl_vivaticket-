# Cenacolo Buy Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox syntax.

**Goal:** Build `cenacolo_buy/` — unattended Cenacolo Vinciano lock-seat + auto-pay pipeline (Drission for browser steps, HTTP for shop APIs).

**Architecture:** Single-process `run_once.py`: Drission WAF → requests login/inventory/lock/checkout → PaymentJob → payer (HTTP short try → Drission card fill). 20-minute lock window: pay immediately after lock.

**Tech Stack:** Python 3.10+, requests, lxml, PyYAML, DrissionPage, YesCaptcha HTTP API

## Global Constraints

- Do not modify `crawl_vivaticket` or `cenacolo_vivaticket`
- Browser automation: DrissionPage only
- No human click in the happy path
- `deadline_at = locked_at + lock_ttl_seconds` (default 1200)
- Secrets only in `config.local.yaml` / env (gitignored)

---

### Task 1: Scaffold + shared config/models

**Files:**
- Create: `requirements.txt`, `config.example.yaml`, `README.md`
- Create: `shared/__init__.py`, `shared/log.py`, `shared/models.py`, `shared/config.py`, `shared/notify.py`, `shared/captcha.py`, `shared/deadline.py`

- [ ] Implement models (`Passenger`, `Slot`, `PaymentJob`, `PayResult`)
- [ ] Implement YAML config loader with env override
- [ ] Implement Feishu notify (optional webhook)
- [ ] Implement YesCaptcha client

### Task 2: Locker — WAF + auth + inventory

**Files:**
- Create: `locker/__init__.py`, `locker/waf.py`, `locker/auth.py`, `locker/inventory.py`

- [ ] Drission open event page, queue rejoin, export cookies to requests.Session
- [ ] keylogin login + clear cart
- [ ] Parse `eventi[]` + `eventoWidgetTlite.php` slots

### Task 3: Locker — checkout → PaymentJob

**Files:**
- Create: `locker/checkout.py`, `locker/pipeline.py`

- [ ] prices → captcha → checkAnagrafica → personal → bloccaCarrello
- [ ] Extract payment URL, set locked_at/deadline_at, return PaymentJob

### Task 4: Payer — Drission + HTTP stub + orchestrator

**Files:**
- Create: `payer/__init__.py`, `payer/http_pay.py`, `payer/browser_pay.py`, `payer/orchestrator.py`

- [ ] browser_pay: formtr → credit card → fill → submit
- [ ] http_pay: attempt doauth chain with short timeout; return needs_browser on failure
- [ ] orchestrator: respect deadline; HTTP then Drission (or prefer_browser)

### Task 5: run_once entrypoint

**Files:**
- Create: `run_once.py`

- [ ] Wire pipeline; save job artifact on lock; pay immediately; notify

---

**Execution:** Inline in this session (user authorized architect decisions).
