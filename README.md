# Product Images / New Monitor

每天抓取 Sfera 西班牙站女装饰品 NUEVO 商品、Bijou Brigitte 的 Neu 页面商品、Bershka 西班牙站 Bisutería 页面商品、Lovisa New Arrivals 商品、Stradivarius 五个饰品分类的每日新增商品，以及 Primark Jewelry 页面商品，按品类下载产品图、生成压缩包，并通过企业微信机器人发送提醒。

## 功能

- 监控 Sfera：PENDIENTES、COLLARES Y CHOKERS、ANILLOS、PULSERAS、BROCHES。
- 监控 Bijou Brigitte：`https://www.bijou-brigitte.com/neu/` 页面下的 Neu 商品。
- 监控 Bershka：`https://www.bershka.com/es/mujer/accesorios/bisuteria-n3776.html` 西班牙站 Bisutería 单分类；由于没有上新标识，通过 SQLite 记录商品 ID，按“首次出现”判断新增。
- 监控 Lovisa：`https://www.lovisa.com/collections/new-arrivals?page=1` New Arrivals 商品；按产品名关键词分为 `不锈钢`、`真金`、`CZ`、`fashion`。
- 监控 Stradivarius：`https://www.stradivarius.com/gb/women/accessories/jewellery-n1883` 下的 `EARRINGS`、`NECKLACES`、`RINGS`、`BRACELETS`、`CHOKERS`；由于没有上新标识，通过 SQLite 记录商品 ID，按“首次出现”判断新增。
- 监控 Primark：`https://www.primark.com/en-us/c/women/accessories/jewelry` 页面下的全部商品；按单一分类 `JEWELRY` 处理，优先走后台抓取：通过浏览器完成 challenge 会话引导后，直接拉取 HTML 并解析页面内嵌商品数据；如果分页直拉仍被 challenge，则仅回退到“列表页浏览器抓取”方案，不再逐个打开商品详情页补图。默认本地使用已安装 Chrome，GitHub Actions 会自动改用 Playwright 安装的 Chromium。
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

只运行 Lovisa：

```bash
python sfera_monitor.py --site lovisa
```

只运行 Stradivarius：

```bash
python sfera_monitor.py --site stradivarius
```

只运行 Primark：

```bash
python sfera_monitor.py --site primark
```

Lovisa 分类规则：产品名包含 `waterproof` 归入 `不锈钢`；否则包含 `plated` 归入 `真金`；否则包含 `Cubic Zirconia` 归入 `CZ`；剩余归入 `fashion`。多关键词同时出现时按上述优先级归类。

Stradivarius 监控 `EARRINGS`、`NECKLACES`、`RINGS`、`BRACELETS`、`CHOKERS` 五个分类，按 SQLite 首次出现判断新增。

Bershka 当前切换为西班牙站 Bisutería 单分类，商品状态使用 `bershka-es:` 前缀，与旧英国站 `bershka:` 历史记录隔离。Bershka 工作流目前保持手动验证模式，稳定建立 ES 基线后再恢复定时。

Primark 监控该页面下全部商品，统一归入单一分类 `JEWELRY`；当前优先尝试“浏览器通过 challenge 建立会话 → Python 直拉分页 HTML → 提取 `ld+json` 商品数据”的后台化路径；若分页仍被 challenge，则仅回退到列表页浏览器抓取，不逐个打开详情页补图。GitHub Actions 运行时会安装 Playwright Chromium，并在无本地 Chrome channel 时自动使用 Chromium。

Bershka / Lovisa / Stradivarius / Primark 首次上线建议先建立基线，避免把当前所有商品都推送出去：

```bash
python sfera_monitor.py --site bershka --baseline-only
python sfera_monitor.py --site lovisa --baseline-only
python sfera_monitor.py --site stradivarius --baseline-only
python sfera_monitor.py --site primark --baseline-only
```

测试时强制把当前商品全部当成新品：

```bash
python sfera_monitor.py --site bijou --force-new
python sfera_monitor.py --site bershka --force-new
python sfera_monitor.py --site lovisa --force-new
python sfera_monitor.py --site stradivarius --force-new
python sfera_monitor.py --site primark --force-new
```

## GitHub Actions 定时运行

工作流文件：

- `.github/workflows/sfera-monitor.yml`
- `.github/workflows/bijou-monitor.yml`
- `.github/workflows/bershka-monitor.yml`
- `.github/workflows/lovisa-monitor.yml`
- `.github/workflows/stradivarius-monitor.yml`
- `.github/workflows/primark-monitor.yml`

默认定时：

```text
Sfera：每天 01:07 UTC，也就是北京时间 09:07
Bijou Brigitte：每天 01:17 UTC，也就是北京时间 09:17
Bershka：当前暂停定时，仅保留手动 workflow_dispatch；恢复后建议仍用 01:27 UTC，也就是北京时间 09:27
Lovisa：每天 01:37 UTC，也就是北京时间 09:37
Stradivarius：每天 01:47 UTC，也就是北京时间 09:47
Primark：每天 01:57 UTC，也就是北京时间 09:57
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

其中 SQLite 文件用于记住已经发送过的商品，避免第二天重复发送同一批商品。Bijou Brigitte 商品 ID 会使用 `bijou:` 前缀，Bershka 西班牙站商品 ID 会使用 `bershka-es:` 前缀，旧英国站历史商品保留 `bershka:` 前缀，Lovisa 商品 ID 会使用 `lovisa:` 前缀，Stradivarius 商品 ID 会使用 `stradivarius:` 前缀，避免和 Sfera 商品冲突。

Bershka 和 Stradivarius 没有明确的上新标签，所以第一次正常运行会把当前监控分类中的所有商品都视为新增。Bershka 西班牙站上线前需要先运行 `python sfera_monitor.py --site bershka --baseline-only` 建立 `bershka-es:` 基线并提交状态文件，避免把当前 57 款全部推送出去。Lovisa 监控 New Arrivals 当前页商品，首次上线也建议先建立基线。Primark 当前已支持后台优先的增量监控，首次把现有商品纳入状态库后，后续定时任务只会发送新增商品。

## 注意

GitHub Actions 的定时任务不保证绝对准点，可能会延迟几分钟到几十分钟。
