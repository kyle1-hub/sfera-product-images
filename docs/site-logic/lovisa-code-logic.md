# Lovisa 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-07-23  
> 审阅提交：`b9ca0d0`  
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`lovisa`  
> 对应 workflow：`.github/workflows/lovisa-monitor.yml`

函数名是权威定位依据；处理 Lovisa 任务时，先读本文件，再按函数名核对最新实现。

## 给新窗口的启动提示词

> 请完整阅读 `docs/site-logic/lovisa-code-logic.md`，把它作为 Lovisa 监控交接。先保持只读，不要运行监控、测试、网站请求、企业微信或 GitHub workflow；不要读取或输出 `config.json`、webhook、Cookie 或 SQLite 业务数据。根据我的需求，只读取本文件引用的最新代码。修改前说明拟改文件、分类/增量语义，以及是否触及其他网站共享逻辑。未经明确授权，不访问网站、不写状态、不发送企业微信、不提交或推送 GitHub。

## 1. 监控目标

- 网站：Lovisa。
- 页面：New Arrivals 集合。
- 数据源：集合对应的 Shopify 风格 `products.json`。
- 网站没有独立新品布尔字段；集合当前商品经 SQLite 首次出现判定是否推送。
- 商品按名称关键词映射为业务分类。

## 2. 安全边界

- 不运行 Lovisa 抓取或发送，不触发 `lovisa-monitor.yml`。
- 不读取真实配置、webhook、SQLite 状态或自动提交信息。
- baseline-only 仍会写状态和快照，必须授权。
- 不因看似普通 JSON API 而默认可以访问；任何网站请求都属于真实运行。
- 修改共享白底图、下载、zip 或企业微信逻辑前检查其他 5 站。

## 3. 配置契约

`config.example.json` 中的 Lovisa 字段：

- `sites.lovisa.display_name`
- `sites.lovisa.marker`：`New`
- `sites.lovisa.base_url`：New Arrivals 集合页面，查询参数中的 page 会参与 JSON URL 构造
- 继承：`state_dir`、`download_images`、`send_empty_report` 等
- `WECOM_WEBHOOK` 只能出现变量名，不得记录真实值

## 4. 抓取调用链

通用：`run()` → `site_config()` → `scrape_site()` → `scrape_lovisa()` → `process_site()`。

Lovisa 专属：

1. `lovisa_products_json_url()`：把集合页面或 products API 转换为显式页码的 `products.json` 地址，使用每页 250 的批量上限。
2. `lovisa_headers()`：构建 JSON 请求头。
3. `fetch_json()`：按第 1 页开始顺序获取产品集合 JSON，直到明确空页。
4. `lovisa_category_from_name()`：根据商品名分类。
5. `lovisa_price()`：读取变体价格并取最低价。
6. `lovisa_image_candidates()`：汇总商品图片，并优先匹配变体 `image_id` 的图片。
7. `map_lovisa_product()`：映射统一字段；图片允许为空。
8. `scrape_lovisa()`：校验每页结构、映射并按父 `product_id` 跨页去重。首屏空、结构异常、连续整页重复、最终无有效商品或达到安全页数上限均视为失败，而不是成功的零新品。短页不是结束条件。

## 5. 分类规则

名称标准化后按以下优先级匹配：

1. 包含 `waterproof` → `不锈钢`
2. 包含 `plated` → `真金`
3. 包含 `cubic zirconia` → `CZ`
4. 其他 → `fashion`

顺序是业务语义的一部分。例如同时命中多个关键词时，靠前规则优先。修改关键词必须补脱网单元测试，避免历史分类漂移。

## 6. 标准商品字段

- `site`：`lovisa`
- `category`：上述业务分类之一
- `name`
- `price`：最低变体价格，美元格式
- `url`：优先 handle 对应产品页面
- `image_url`：初始首候选；刚上架无图时允许为空，不因此丢弃商品
- `image_candidates`：产品图和变体匹配图；允许为空并进入后续补图
- `source_id`
- `product_id`：`lovisa:<产品ID、handle或稳定回退值>`
- `is_new=True`：表示进入首次出现判定，不是网站原生新品标志
- `image_path`：运行期下载后补充

## 7. 新品与状态语义

- `products.json` 返回的集合商品均为当前候选。
- `Store.mark_seen()` 用 `lovisa:<父产品 id>` 判断第一次出现；新增颜色或 SKU 不单独算新品。
- `seen` 只表示发现过，不表示企业微信已经交付成功。
- Lovisa 使用 `text_sent_at` 和 `image_sent_at` 两阶段状态：文字失败继续待发；图片失败只待补图，不重复文字。
- 第一次接入或状态丢失时，全量集合都会被认作新增；应先建立 baseline，但写 baseline 必须明确授权。
- `--baseline-only` 会将本次完整集合登记为文字和图片均已完成，不产生后续 pending。
- 旧版 Lovisa 状态行会保守迁移为历史已交付，避免升级后全量重发；修复后当前集合中从未入库的历史遗漏仍会被识别为新增。
- 商品离开后再回来，只要同一父 product ID 仍在 SQLite，就不会重新作为新增。
- `--force-new` 会破坏去重保护，禁止未经授权使用。

## 8. 图片规则

- `lovisa_image_candidates()` 收集 `products.json` 的商品图片和变体 `image_id` 对应图片并去重，但不再把变体图强行排到最前，避免优先选中佩戴/模特图。
- `lovisa_absolute_image_url()` 会去掉 Shopify `_100x` / `_large` 以及 `width=` 查询，下载原图而不是列表缩略图。
- 图片交付前会调用 `lovisa_fetch_detail_candidates_for_pending()` 做候选图补全：先检查集合 `products.json` 已有候选；已有白底候选时不再访问详情，缺少白底或完全无图时才并发访问产品详情 `.js`，防止集合接口未带全图。
- 详情补抓只针对图片 pending 商品，不会对全量已完成商品重复抓；默认并发数由 `LOVISA_DETAIL_WORKERS` / `detail_workers` 控制，当前默认 8。
- `refine_product_image()` 对 Lovisa 调用 `lovisa_preferred_image()`：白底优先，检测不到白底时回退到完整候选中的第一张图，避免漏图。
- 当前策略：先尽量发送白底图；如果白底检测漏判或确实没有白底图，则发送候选中的第一张图。
- `has_white_background()`、下载和 JPEG 转换是共享函数，修改会影响其他网站。

## 9. 企业微信交付

Lovisa 从 `process_site()` 进入专属 `process_lovisa()` 两阶段交付：

1. mark seen 登记完整集合，新行进入文字和图片 pending。
2. 先发送全部文字 pending；无图商品也包含名称、链接、source ID、分类和价格。
3. Markdown 返回 `errcode == 0` 后才写 `text_sent_at`；非零结果抛错并保留待发状态。
4. 独立处理图片 pending，先用列表候选找白底；缺少白底或无候选时并发补抓产品详情 `.js` 图片候选，再白底优先选择图片、下载和 JPEG 转换。
5. 没有候选图、下载或转换失败只记录尝试与错误，其他商品继续处理；后续定时只补图片。
6. 图片按实际压缩体积拆包，每个包保留精确商品列表；每包企业微信成功后才写对应 `image_sent_at`。
7. 附件返回非零错误码时抛错，已成功包保持成功，失败包留待下次重试。
8. 发送成功后尝试 `archive_sent_zips()`。本机能写共享盘时落到 `Lovisa/<YYYYMMDD>/<品类>/`；GitHub 写失败时改写入仓库 `share-inbox/Lovisa/<YYYYMMDD>/<品类>/`，由本机 `--sync-share` 再拷进图片库。拷盘失败不影响图片已发送状态。品类用商品真实分类（不锈钢 / 真金 / CZ / fashion），不要再用「待补图片」。
9. 全部准备好的附件成功后清理临时目录；失败时保留。

Lovisa 不再依赖通用一次性 `send_wecom_zip_bundle()` 判断交付成功。workflow 即使监控步骤失败也保存 SQLite 的 pending/部分成功进度，但不吞掉失败退出码。

## 10. Workflow 与测试

- Workflow：`.github/workflows/lovisa-monitor.yml`，仅手动 `workflow_dispatch`。现网每日跑本机隐藏任务。
- 不要从代码修改任务推导出运行 workflow 的授权。
- 专项测试：`tests/test_lovisa_delivery.py`。覆盖显式分页、短页继续、空页/异常结构/重复页/页数上限失败、跨页去重、无图保留、分类/最低价/候选图/父 product ID、旧状态迁移、baseline、文字与附件失败及补发。所有网站、图片和企业微信调用均 mock。

## 11. 改动影响边界

Lovisa 局部：`lovisa_headers()`、`lovisa_products_json_url()`、分类/价格/图片函数、`map_lovisa_product()`、`scrape_lovisa()`。

共享高风险：`fetch_json()`、Store、白底检测、`refine_product_image()`、下载、`process_site()`、zip 和企业微信。

## 12. 安全验证阶梯

1. 静态核对 JSON 结构和函数。
2. 本地脱敏 fixture 验证分类、价格、图片和 ID。
3. mocked 网络/企业微信的单元测试。
4. 明确授权后才访问 Lovisa 或写 baseline。
5. 发送、workflow、commit/push 均需独立授权。

## 13. 维护规则

Lovisa API、集合、分类关键词、价格、图片、状态、交付、测试或 workflow 变化时更新本文件。不得加入凭证、Cookie、数据库行、快照或附件。不要复制生产函数体；以 `sfera_monitor.py` 中的最新函数为准。
