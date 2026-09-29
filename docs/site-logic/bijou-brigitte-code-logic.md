# Bijou Brigitte 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-09-29  
> 审阅提交：与当前 `sfera_monitor.py` 同步
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`bijou`  
> 对应 workflow：`.github/workflows/bijou-monitor.yml`  
> 专项测试：`tests/test_bijou_delivery.py`

函数名是权威定位依据；行号可能漂移。Bijou 的发送状态与其他网站不同，禁止在不了解本文件的情况下改成通用处理。

## 给新窗口的启动提示词

> 请完整阅读 `docs/site-logic/bijou-brigitte-code-logic.md`，把它作为 Bijou Brigitte 监控的交接上下文。先保持只读，不要运行监控、测试、网站请求、企业微信或 GitHub workflow；不要读取或输出 `config.json`、webhook、Cookie 或 SQLite 业务数据。请特别保留“文字只发一次、图片失败后继续补发”的两阶段交付设计。根据我的需求读取最新源代码，修改前说明拟改文件、状态迁移影响和是否影响其他网站。未经明确授权，不访问网站、不写状态、不发送企业微信、不提交或推送 GitHub。

## 1. 监控目标

- 网站：Bijou Brigitte 德国站。
- 入口：Neu 列表页及其分页。生产默认只跑 `/neu/`。
- `/neu/` 上的商品卡全量与数据库对比，不再依赖固定 `Neu` badge DOM。
- 目标：新品文字提醒尽快发送；图片暂时失败时不阻塞文字，并在后续运行继续补图。

## 2. 安全边界

- 不运行任何监控或发送参数，不触发 `bijou-monitor.yml`。
- 不读取真实 webhook、SQLite 行或历史交付信息。
- 不随意删除或合并 Bijou 专属状态字段。
- 不把 `process_bijou()` 替换为通用 `process_site()` 发送方式，否则会破坏补图重试能力。
- 真实运行会分页访问网站、访问详情页补图、更新交付时间、生成 zip 并可能发消息。

## 3. 配置契约

`config.example.json` 中的脱敏字段：

- `sites.bijou.display_name`
- `sites.bijou.marker`：`Neu`
- `sites.bijou.base_url`
- `sites.bijou.listing_urls`：可配置真实列表入口；生产默认只使用 `/neu/`，并把该入口上的商品卡全量纳入数据库对比，避免因 Neu 标记 DOM 变化漏掉日增量图片提醒。具体品类 `order=neueste` 入口仅适合临时审计补漏，不作为默认生产口径。
- 继承项：`state_dir`、图片下载和空报告设置等。
- `WECOM_WEBHOOK` 仅可作为环境变量名引用，不能记录值。

配置经 `load_config()`、`site_config()` 合并。`--audit-only` 只读审计会抓取网站当前商品并对比数据库，但不写状态、不下载图片、不发送企业微信、不写快照。

## 4. 抓取和解析调用链

通用分发：`run()` → `site_config()` → `scrape_site()` → `scrape_bijou()`，交付阶段进入 `process_bijou()`。

Bijou 专属链：

1. `bijou_headers()`：构建德语站请求头。
2. `bijou_listing_configs()`：读取 Bijou 多入口配置。
3. `bijou_page_url(listing_url, page)`：保留入口查询参数并生成分页 URL。
4. `fetch_text()`：获取 HTML。
5. `extract_bijou_total_pages()`：从分页链接确定总页数。
6. `bijou_has_new_flag()` / `extract_bijou_products()`：解析商品卡；函数仍支持强制 Neu 标记，但生产 `/neu/` 入口默认关闭强制标记，按页面商品卡全量与数据库对比来做日增量提醒。
7. `bijou_big_category()`：根据商品信息确定大类。
8. `scrape_bijou_listing()` / `scrape_bijou()`：遍历配置入口和分页，按 product ID 去重并合并图片候选；默认只跑 `/neu/`。

图片辅助链：

- `bijou_image_candidates_from_html()`：从 listing/detail HTML 的图片属性和 srcset 中收集候选。
- `bijou_detail_image_candidates()`：访问产品详情页寻找补充图片。
- `bijou_preferred_image()`：合并 listing 和 detail 候选，优先白底图。
- `bijou_base_product_number()` / `bijou_image_matches_product()`：允许变体编号与基础图片编号匹配。

## 5. 标准商品字段

- `site`：`bijou`
- `category`
- `name`
- `price`
- `url`
- `image_url`
- `image_candidates`
- `source_id`：商品编号，可能带变体后缀
- `product_id`：`bijou:<商品编号或稳定回退值>`
- `is_new`：解析到 Neu 商品时为真
- `image_path`：运行期补充

`product_image_filename_base()` 对 Bijou 有专门分支：图片文件名保留 `source_id`，防止变体商品名称相同后难以识别。

## 6. 新品与两阶段状态语义

Bijou 除通用商品字段外，Store 还维护：

- `text_sent_at`
- `image_sent_at`
- `last_image_attempt_at`
- `delivery_error`
- `delivery_version`
- `recovery_tag`

关键函数：

- Store 初始化中的 Bijou 状态迁移逻辑。
- `bijou_delivery_pending(field)`：分别查询文字待发或图片待发。
- `mark_delivery_success()`：标记文字或图片发送成功。
- `record_image_result()`：记录图片路径、尝试时间和失败信息。

`process_bijou()` 的设计必须保持：

1. 所有抓到的 Neu 商品先 `mark_seen()`。
2. `text_sent_at` 为空的商品立即发送文字；成功后只标记文字完成。
3. `image_sent_at` 为空的商品独立尝试图片恢复和 zip 发送。
4. 图片失败不会重复发送文字；图片仍保留 pending，后续运行再试。
5. 图片包成功后才标记 `image_sent_at`。

这是 Bijou 与其他网站最重要的区别。

## 7. 图片规则

- listing 页候选与 detail 页候选都会参与选择。
- `unique_urls()` / `upgrade_image_url()` 会把 `/thumbnail/`、`_tb`、`_400x400` 升成 `/media/` 原图；列表缩略图不能当最终下载地址。
- `bijou_preferred_image()` 优先返回检测为白底的图片；若没有白底候选，目前可回退首个候选。
- `process_bijou()` 在 `refine_product_image()` 之后直接下载，不得再把 `image_url` 覆盖成候选第一张，否则白底优选会被缩略图顶掉。
- 商品编号可能为 `基础编号.变体`，图片 URL 只有基础编号；匹配逻辑不能简单改成全字符串相等。
- 下载和 JPEG 转换复用共享 `download_image()`、`save_product_image_as_jpg()`。
- 图片暂时获取不到时，保留 pending，而不是把整个新品通知判为失败。

## 8. 企业微信交付

文字路径：

- `build_bijou_text_message()` 生成产品链接和编号/价格摘要。
- `send_wecom()` 发送 Markdown。
- 成功后更新 `text_sent_at`。

图片路径：

- `prepare_bijou_image_zips()` 为待补图商品生成附件包并控制大小。
- `send_wecom_file()` 上传 zip。
- 成功后更新各商品 `image_sent_at`；失败记录错误并在以后重试。
- 图片包发送成功后会尝试 `archive_sent_zips()`。本机能写共享盘时落到 `Bijou Brigitte/<品类>/`；GitHub 写失败时改写入仓库 `share-inbox/Bijou Brigitte/<品类>/`，由本机 `--sync-share` 再拷进图片库。拷盘失败不阻塞补图状态。

不要改用 `send_wecom_zip_bundle()` 一次性处理全部 Bijou 交付，因为那会失去文字与图片的独立状态。

## 9. Workflow 与测试

- Workflow：`.github/workflows/bijou-monitor.yml`，含手动与定时入口，并处理状态提交。
- 专项测试：`tests/test_bijou_delivery.py`。
- 现有测试保护：
  - 变体编号可匹配基础图片 ID。
  - 图片文件名保留变体商品编号。
  - 文字只发送一次，图片失败后后续继续重试。
  - 历史迁移只恢复指定待补商品，不批量重发旧数据。
- 测试也不得未经授权运行；修改前先静态检查是否需要补 fixture/mocked 用例。

## 10. 改动影响边界

Bijou 局部：`bijou_*` 解析和图片函数、`extract_bijou_products()`、`scrape_bijou()`、`build_bijou_text_message()`、`prepare_bijou_image_zips()`、`process_bijou()`。

状态高风险：Store 表结构、迁移、pending 查询和成功标记。错误修改可能造成漏发或重复发送历史商品。

共享高风险：HTTP、白底检测、图片下载/JPEG 转换、企业微信上传。

## 11. 安全验证阶梯

1. 静态检查函数与状态字段。
2. 用本地 HTML fixture 验证 Neu、分页和编号解析。
3. 用 mocked 企业微信和图片下载验证两阶段状态。
4. 明确授权后才可进行真实网页或交付测试。
5. workflow、提交、推送分别授权，不能从“本地测试”推导授权。

## 12. 维护规则

当 Bijou 抓取、编号、图片恢复、状态迁移、文字/图片交付、测试或 workflow 变化时更新本文件。禁止记录凭证、数据库业务行、Cookie、生成图片或 zip。不要复制生产函数体；以 `sfera_monitor.py` 中的最新符号为准。
