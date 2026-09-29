# Stradivarius 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-07-23  
> 审阅提交：`b9ca0d0`  
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`stradivarius`  
> 对应 workflow：`.github/workflows/stradivarius-monitor.yml`

函数名是权威定位依据。Stradivarius 与 Bershka 共用部分 Inditex 数据解析 helper，但站点配置、URL 和状态命名空间不可混用。

## 给新窗口的启动提示词

> 请完整阅读 `docs/site-logic/stradivarius-code-logic.md`，把它作为 Stradivarius 监控交接。先保持只读，不要运行监控、测试、网站请求、企业微信或 GitHub workflow；不要读取或输出 `config.json`、webhook、Cookie 或 SQLite 业务数据。根据我的需求读取最新源代码；修改前说明拟改文件、5 个分类的影响、与 Bershka 共用 helper 的影响，以及验证计划。未经明确授权，不访问网站、不写状态、不发送企业微信、不提交或推送 GitHub。

## 1. 监控目标

- 网站：Stradivarius 英国站。
- 范围：5 个首饰分类：EARRINGS、NECKLACES、RINGS、BRACELETS、CHOKERS。
- 抓取当前分类商品，SQLite 首次出现决定新品推送。
- 使用 Inditex API 上下文和 productsArray 补全商品详情。

## 2. 安全边界

- 不运行真实抓取、发送或 workflow。
- 不把 Bershka ES 的 country/store/catalog/language/category 复制到 Stradivarius。
- 不读取真实配置、webhook、状态数据或 Cookie。
- baseline-only 会改变 SQLite，必须授权。
- 修改复用的 Bershka 解析 helper 时，同时核对 Bershka ES。

## 3. 配置契约

`config.example.json` 中包含：

- `sites.stradivarius.display_name`
- `marker`：`Newly appeared`
- 英国站 `base_url`
- `country`、`store_id`、`catalog_id`、`language_id`
- 5 个分类，每项有 `name`、页面 `url`、`category_id`
- 继承：`state_dir`、图片下载、空报告等

配置由 `load_config()` / `site_config()` 合并。任何 API ID 调整都应局限在 Stradivarius 配置，除非有证据表明共享协议发生变化。

## 4. 抓取调用链

通用：`run()` → `site_config()` → `scrape_site()` → `scrape_stradivarius()` → `process_site()`。

专属链：

1. `stradivarius_headers()`：站点请求头。
2. `fetch_stradivarius_page()`：建立页面会话、处理挑战页。
3. `extract_stradivarius_category_id()`：从配置或 URL 取分类 ID。
4. `stradivarius_api_urls()`：构造 category 和 productsArray 候选。
5. `discover_stradivarius_api_context()`：从页面发现 store/catalog/language。
6. `fetch_stradivarius_category_products()`：依次尝试 API；直接解析商品或先取 commercial IDs。
7. `fetch_stradivarius_products_array()`：根据 IDs 获取商品详情。
8. `map_stradivarius_product()`：映射统一对象。
9. `scrape_stradivarius()`：逐分类抓取；记录失败分类，汇总成功分类，并按 product ID 去重。

复用关系：

- `extract_stradivarius_commercial_ids()` 复用 Bershka commercial ID 解析。
- `extract_stradivarius_products_from_payload()` 复用 Bershka 商品对象识别。
- `stradivarius_price()` 复用 Bershka 嵌套价格解析。
- URL/媒体解析也复用同类 helper。

## 5. 标准商品字段

- `site`：`stradivarius`
- `category`：5 个配置分类之一
- `name`
- `price`
- `url`：英国站商品 URL
- `image_url`：初始首候选
- `image_candidates`
- `source_id`
- `product_id`：`stradivarius:<source_id或稳定回退ID>`
- `is_new=True`：表示交给 SQLite 首次出现判断
- `image_path`：运行期补充

## 6. 新品、失败与状态语义

- 网站没有在此链路使用明确新品标记，当前目录商品均为候选。
- `Store.mark_seen()` 以 `stradivarius:` product ID 判定首次出现。
- 初次接入应建立 baseline，但它是写状态操作。
- `scrape_stradivarius()` 允许单个分类失败后继续其他分类，并记录错误。
- 成功分类商品会继续返回并交付；修改时要明确是否保持“部分成功”语义。
- 如果所有分类失败/为空，不能把它误判为“今天零新品”；应保留明确失败。
- `--force-new` 可能把当前全量重发，未经授权禁止。

## 7. 图片规则

- `stradivarius_image_candidates()` 从嵌套媒体对象广泛收集候选并去重。
- 候选经过共享 `upgrade_image_url()` / `unique_site_urls()`；Stradivarius 静态图通常无需改写。
- `refine_product_image()` 对 Stradivarius 使用共享白底优先选择。
- 当前策略是优先白底；找不到时回退第一个候选，区别于 Bershka ES 严格留空。
- 图片下载、白底检测、JPEG 转换和产品名文件名均为共享代码。
- 修改共用媒体解析需同时验证 Bershka。

## 8. 企业微信交付

使用通用流程：

- `process_site()` 负责首次出现、图片处理、snapshot 和是否发送。
- `send_wecom_zip_bundle()` 负责 Markdown、分类包、总包和文件上传。
- `build_product_zip_bundle()` 的分类附件名含站点前缀。
- 大包由 `split_zip_bundle_by_size()` 按实际压缩体积拆分。
- 全部发送成功后删除临时目录，失败保留。

修改通用发送或 zip 命名会影响其他网站。

## 9. Workflow 与测试

- Workflow：`.github/workflows/stradivarius-monitor.yml`，包含手动/定时或路径触发，并处理状态提交。
- 未经授权不要运行 workflow，也不要借测试触发真实网站。
- 当前没有 Stradivarius 专项测试。建议补齐：5 分类 URL/API 构造、部分分类失败、全分类失败、productsArray、价格、图片、namespace 和跨分类去重的 fixture/mocked 测试。

## 10. 改动影响边界

Stradivarius 局部：`stradivarius_*`、`map_stradivarius_product()`、`fetch_stradivarius_category_products()`、`scrape_stradivarius()`。

跨站影响：复用的 `extract_bershka_*`、价格、绝对 URL 和媒体 helper；修改这些必须同时检查 Bershka。

全站共享：Store、HTTP、白底、下载、`process_site()`、zip 和企业微信。

## 11. 安全验证阶梯

1. 静态检查 5 分类配置和函数链。
2. 使用脱敏 JSON fixture 验证 direct products 和 IDs/productsArray 两条路径。
3. mocked 部分失败和全失败测试。
4. 明确授权后才访问站点或建立 baseline。
5. 企业微信、workflow、commit/push 逐项授权。

## 12. 维护规则

分类、API 上下文、解析复用、失败语义、状态、图片、交付、测试或 workflow 变化时更新本文件。禁止加入凭证、Cookie、SQLite 数据、快照或 zip。以 `sfera_monitor.py` 最新函数为唯一源代码真相。
