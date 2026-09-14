# Cenacolo Buy — lock + pay + worker

详见仓库根 `README.md` 与 `docs/ops-runbook.md`。

```bash
export CENACOLO_HOME="$(cd ../.. && pwd)"
cp config.example.yaml config.local.yaml
python run_worker.py --once
python run_once.py --lock-only
```

日志：`$CENACOLO_HOME/var/log/buy-worker.log`  
调试 HTML：`$CENACOLO_HOME/var/artifacts/`
