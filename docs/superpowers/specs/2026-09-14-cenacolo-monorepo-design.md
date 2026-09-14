# Cenacolo Monorepo + Ops Layout Design

**日期：** 2026-09-14  
**状态：** 已批准（按推荐方案直接落地）

## 1. 目标

把「最后的晚餐」方案 B 收成一个可维护 monorepo：

- `services/buy`：抢票 Worker + 锁座/支付引擎  
- `services/register`：养号（账号池供给）  
- `deploy/`：systemd / 安装约定  
- 统一日志：journald + 滚动文件 + order/stage 字段，禁止敏感信息  

不在本轮：预约前端、VCC、自动支付开关默认开启。

## 2. 仓库目录

```text
cenacolo/   # 远程可仍叫 crawl_vivaticket；README 标明真名
├── README.md
├── .gitignore
├── docs/
│   ├── architecture.md
│   ├── ops-runbook.md
│   └── superpowers/...
├── services/
│   ├── buy/
│   └── register/
├── deploy/
│   ├── japan-worker/
│   └── register-host/
└── configs/          # 仅 example
```

服务器：

```text
/opt/cenacolo/
├── services/buy|register
├── var/log
├── var/artifacts
└── etc/*.local.*     # 密钥，不进 git
```

## 3. 服务边界

| 服务 | 进程 | Mongo |
|------|------|--------|
| buy-worker | `run_worker.py` | orders / runs / 租 accounts |
| buy-reaper | `run_worker.py --reap` | 回收租约 |
| register | `pool_register.py` | email_163 → vivaticket_accounts |

## 4. 日志

- stdout → systemd/journald  
- 文件：`var/log/{service}.log`，RotatingFileHandler  
- 格式：`YYYY-mm-dd HH:MM:SS LEVEL [service.logger] key=value ...`  
- 禁止：卡号、CVV、cookie、打码 token  

## 5. 上线顺序（不变）

0 本 monorepo → 1 Mongo → 2 register 刷号 → 3 日本 worker → 4 seed 锁座 → 5 预约 API → 6 VCC 支付  

## 6. 验收

- clone 后目录一眼能分清 buy / register / deploy  
- worker 启动有文件日志且含 stage  
- `config.local.yaml` / 卡密不进 git  
- 远程 push 成功  
