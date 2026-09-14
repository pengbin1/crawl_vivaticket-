# Cenacolo Buy

无人值守抢票 + 自动支付（Leonardo da Vinci *Last Supper* / Cenacolo Vinciano）。

设计: `docs/superpowers/specs/2026-08-13-cenacolo-buy-design.md`

## 流程

1. Drission 过 Incapsula / Safetynet，导出 cookie  
2. HTTP 登录 → 查库存 → 打码锁座 → 实名 → `bloccaCarrello`  
3. 锁座成功后 **立刻** 支付（约 20 分钟窗口）  
4. 默认 Drission 自动填卡；可选短超时 HTTP 支付尝试  

## 配置

```bash
cp config.example.yaml config.local.yaml
# 填写账号、打码 key、卡信息
pip install -r requirements.txt
python run_once.py
```

`config.local.yaml` 已 gitignore，勿提交卡号/密码。

## 说明

- 不修改旧项目 `crawl_vivaticket` / `cenacolo_vivaticket`  
- 浏览器自动化只用 DrissionPage  
- 正常路径不需要人点击  
