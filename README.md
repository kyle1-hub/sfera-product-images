# Sfera NUEVO Monitor

每天抓取 Sfera 西班牙站女装饰品分类中的 NUEVO 商品，按品类下载产品图、生成压缩包，并通过企业微信机器人发送提醒。

## 功能

- 抓取 5 个品类：PENDIENTES、COLLARES Y CHOKERS、ANILLOS、PULSERAS、BROCHES。
- 本地 SQLite 记录已发送商品，避免重复推送。
- 优先选择白底产品图；如果第一张已经是白底图，就保留第一张。
- 按品类生成 zip，再打入一个总 zip。
- 支持 GitHub Actions 每天自动运行。

## 本地运行

1. 安装依赖：

```bash
pip install -r requirements.txt
```

2. 复制配置：

```bash
cp config.example.json config.json
```

3. 在 `config.json` 中填写企业微信机器人 webhook，或者通过环境变量传入：

```bash
export WECOM_WEBHOOK='https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx'
```

4. 运行：

```bash
python sfera_monitor.py
```

测试时强制把当前 NUEVO 全部当成新品：

```bash
python sfera_monitor.py --force-new
```

## GitHub Actions 定时运行

工作流文件：`.github/workflows/sfera-monitor.yml`

默认定时：

```text
每天 01:00 UTC，也就是北京时间 09:00
```

公开仓库使用普通 Ubuntu runner 通常不消耗私有仓库 Actions 免费分钟数。

### 必须设置 Secret

在 GitHub 仓库页面：

```text
Settings → Secrets and variables → Actions → New repository secret
```

添加：

```text
Name: WECOM_WEBHOOK
Value: 企业微信机器人 webhook 完整地址
```

不要把真实机器人 webhook 提交到公开仓库。

## 状态文件

GitHub Actions 会提交这些状态文件：

- `state/sfera_products.sqlite3`
- `state/snapshot_*.json`

其中 SQLite 文件用于记住已经发送过的商品，避免第二天重复发送同一批商品。

## 注意

GitHub Actions 的定时任务不保证绝对准点，可能会延迟几分钟到几十分钟。
