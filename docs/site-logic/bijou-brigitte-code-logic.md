# Bijou Brigitte 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-08-04  
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`bijou`  
> 对应 workflow：`.github/workflows/bijou-monitor.yml`  
> 专项测试：`tests/test_bijou_delivery.py`

Bijou 的发送状态与其他网站不同，禁止把它改成通用发送流程。

## 核心口径

- 生产默认只抓 `https://www.bijou-brigitte.com/neu/` 及分页。
- `/neu/` 页面上的商品卡全量参与数据库对比，不再依赖固定 `Neu` badge DOM，避免网站标记结构变化导致漏掉每日增量图片提醒。
- 具体品类 `order=neueste` 入口只适合临时审计补漏，不作为默认生产口径。
- 新品判定使用：本次抓到的 `product_id` 是否已存在于 SQLite。

## 两阶段交付

1. 所有抓到商品先 `mark_seen()`。
2. `text_sent_at` 为空的商品发送文字提醒，成功后只标记文字完成。
3. `image_sent_at` 为空的商品独立补图、打 zip、上传企业微信文件。
4. 图片失败不会重复发送文字；图片仍保留 pending，后续运行再试。
5. 图片包成功后才标记 `image_sent_at`。

## 关键函数

- `bijou_listing_configs()`：读取 `/neu/` 入口配置；默认 `require_new_flag=false`。
- `bijou_page_url(listing_url, page)`：保留 query 并生成分页 URL。
- `extract_bijou_products(html, require_new_flag=True)`：支持强制 Neu 标记，也支持 `/neu/` 全量商品卡解析。
- `bijou_sku_from_url()`：当 `data-number` 缺失时从 URL 提取稳定 SKU。
- `scrape_bijou_listing()` / `scrape_bijou()`：分页抓取、按 `product_id` 去重并合并图片候选。
- `build_bijou_audit_report()` / `print_bijou_audit_report()`：`--audit-only` 只读对比数据库与网站，不写状态、不发企业微信。
- `process_bijou()`：保留文字/图片两阶段交付，不得替换成通用 `process_site()` 图片包逻辑。

## 安全边界

- 不提交 `state/sfera_products.sqlite3` 测试状态。
- 不记录 webhook、Cookie、SQLite 业务行。
- 修改后先跑 `tests/test_bijou_delivery.py`。
- GitHub workflow、真实网站运行、企业微信发送、提交/推送分别授权。
