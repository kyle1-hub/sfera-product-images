# Product Images / New Monitor

每天抓取 Sfera 西班牙站女装饰品 NUEVO 商品、Bijou Brigitte 的 Neu 页面商品，以及 Bershka 两个饰品分类的每日新增商品，按品类下载产品图、生成压缩包，并通过企业微信机器人发送提醒。

## 功能

- 监控 Sfera：PENDIENTES、COLLARES Y CHOKERS、ANILLOS、PULSERAS、BROCHES。
- 监控 Bijou Brigitte：`https://www.bijou-brigitte.com/neu/` 页面下的 Neu 商品。
- 监控 Bershka：Accessories 和 Jewellery 两个分类；由于没有上新标识，通过 SQLite 记录商品 ID，按“首次出现”判断新增。
- 本地 SQLite 记录已发送商品，避免重复推送。
- 优先选择白底产品图；如果第一张已经是白底图，就保留第一张，否则继续找后面的白底图。
- 按品类生成 zip，再打入一个总 zip。
- 支持 GitHub Actions 每天自动运行。
- 即使没有新增商品，也会按网站发送当天无新增提醒。

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

4. 运行全部网站：

```bash
python sfera_monitor.py --site all
```

只运行 Sfera：

```bash
python sfera_monitor.py --site sfera
```

只运行 Bijou Brigitte：

```bash
python sfera_monitor.py --site bijou
```

只运行 Bershka：

```bash
python sfera_monitor.py --site bershka
```

Bershka 首次上线建议先建立基线，避免把当前所有商品都推送出去：

```bash
python sfera_monitor.py --site bershka --baseline-only
```

测试时强制把当前商品全部当成新品：

```bash
python sfera_monitor.py --site bijou --force-new
python sfera_monitor.py --site bershka --force-new
```

## GitHub Actions 定时运行

工作流文件：

- `.github/workflows/sfera-monitor.yml`
- `.github/workflows/bijou-monitor.yml`
- `.github/workflows/bershka-monitor.yml`

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

GitHub Actions 会提交这个状态文件：

- `state/sfera_products.sqlite3`

其中 SQLite 文件用于记住已经发送过的商品，避免第二天重复发送同一批商品。Bijou Brigitte 商品 ID 会使用 `bijou:` 前缀，Bershka 商品 ID 会使用 `bershka:` 前缀，避免和 Sfera 商品冲突。

Bershka 没有明确的上新标签，所以第一次正常运行会把当前两个分类中的所有商品都视为新增。生产上线前如需只建立基线，请先运行 `python sfera_monitor.py --site bershka --baseline-only` 并提交状态文件。

## 注意

GitHub Actions 的定时任务不保证绝对准点，可能会延迟几分钟到几十分钟。
