# Cenacolo Register — Vivaticket account farm

从 `email_163` 领取邮箱 → 注册激活 → 写入 `vivaticket_accounts`（status=ready）。

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
export CENACOLO_HOME="$(cd ../.. && pwd)"
python pool_register.py --available
python pool_register.py --count 10
python pool_register.py --list
```

日志：`$CENACOLO_HOME/var/log/register.log`

Mongo：与 buy 相同 `https://dingstest.133.cn/mongo_api`（见 `mongo_api.py`）。
