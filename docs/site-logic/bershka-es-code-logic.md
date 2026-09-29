# Bershka ES 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-07-23  
> 审阅提交：`b9ca0d0`  
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`bershka`  
> 状态命名空间：`bershka-es`  
> 对应 workflow：`.github/workflows/bershka-monitor.yml`（当前仅手动，定时已暂停）  
> 专项测试：`tests/test_bershka_monitor.py`

函数名是权威定位依据。当前目标是西班牙站 Bisutería，不得退回旧英国站配置或旧 `bershka:` 状态。

## 给新窗口的启动提示词

> 请完整阅读 `docs/site-logic/bershka-es-code-logic.md`，把它作为 Bershka ES 监控交接。先保持只读，不要运行监控、测试、网站请求、企业微信或 GitHub workflow；不要读取或输出 `config.json`、webhook、Cookie 或 SQLite 业务数据。当前 workflow 定时已暂停，不能顺手恢复。图片必须优先且只取白底商品图，不能把人物、穿戴或场景首图作为回退。根据我的需求读取本文件引用的最新源代码；修改前说明拟改文件、状态命名空间和共享影响。未经明确授权，不访问网站、不写状态、不发送企业微信、不提交或推送 GitHub。

## 1. 监控目标

- 网站：Bershka 西班牙站。
- 范围：女装 Accessories → Bisutería 单一分类。
- 当前目录商品没有可靠的新品标志；是否新增由 SQLite 首次出现判断。
- 商品图片必须使用白底商品图。

## 2. 安全边界

- 不运行任何真实抓取或发送参数，不触发 workflow。
- `.github/workflows/bershka-monitor.yml` 当前只保留手动入口；不要恢复 schedule。
- 不把 ES 配置替换为历史 GB store/catalog/language/country。
- 不混用 `bershka-es:` 与旧 `bershka:` product ID。
- 不读取真实 webhook、状态数据库或浏览器 Cookie。
- 即使 baseline-only 也会写 SQLite 和快照，必须明确授权。

## 3. 配置契约

`config.example.json` 的 Bershka 脱敏配置包括：

- `display_name`：`Bershka ES`
- `marker`：`Newly appeared`
- `base_url`：西班牙 Bisutería 页面
- `country`：`es`
- `store_id`
- `catalog_id`
- `language_id`
- `accept_language`
- `currency_symbol`：欧元
- `state_namespace`：`bershka-es`
- `categories`：必须且只能配置一个 Bisutería 分类，包含页面 URL、页面 category ID 和 API category ID

`validate_bershka_config()` 强制校验：单分类、`/es/` URL、store/catalog/language/country 完整、country 为 ES、分类 ID 存在。

## 4. API 与抓取调用链

通用分发：`run()` → `site_config()` → `scrape_site()` → `scrape_bershka()` → 通用 `process_site()`。

Bershka 专属链：

1. `validate_bershka_config()`：先阻止错误国家或分类配置。
2. `bershka_headers()`：生成西班牙站请求头。
3. `fetch_bershka_page()`：建立页面会话并处理站点验证页。
4. `bershka_api_urls()`：基于 store/catalog/language/country/category 构造 API 候选。
5. `discover_bershka_api_context()`：配置缺失时从页面发现 API 上下文。
6. `bershka_fetch_json()`：带重试获取 API JSON。
7. `extract_bershka_products_from_payload()`：直接解析商品对象。
8. 若返回商品 ID：`extract_bershka_commercial_ids()` → `fetch_bershka_products_array()` → `fetch_bershka_products_array_chunk()`；10 个一批，失败时拆分重试。
9. `map_bershka_product()`：映射为统一商品结构。
10. `scrape_bershka()`：要求分类返回非空商品，最后按 `product_id` 去重。

## 5. 商品映射

关键函数：

- `bershka_price()`：从嵌套价格字段读取数值或字符串，按分转为欧元格式。
- `bershka_base_url()` / `bershka_absolute_url()`：保持西班牙 locale。
- `bershka_product_url()`：选择 SEO/detail URL，排除图片 URL。
- `bershka_image_from_media()`：从媒体对象及 `extraInfo` 读取地址。
- `bershka_image_candidates()`：遍历嵌套媒体，收集所有候选图并去重。
- `map_bershka_product()`：生成：
  - `site="bershka-es"`
  - `product_id="bershka-es:<source_id或稳定回退ID>"`
  - 西班牙商品 URL
  - 欧元价格
  - `image_url` 初始首候选和完整 `image_candidates`
  - `is_new=True`（这里只代表进入首次出现判定，不代表网站给了新品标签）

## 6. 新品与状态语义

- 网站当前目录里的全部商品都是候选。
- `Store.mark_seen()` 对 `bershka-es:<id>` 做首次出现判断。
- 第一次建立 ES 基线时，应使用 baseline-only 记录全量，但这仍是有状态写入操作。
- 历史英国站行使用 `bershka:<id>`，不会阻止同 ID 的 ES 商品首次出现。
- `--force-new` 会把所有当前商品当新增，可能推送全量，禁止未经授权使用。

## 7. 严格白底图片规则

关键路径：`bershka_image_candidates()` → `refine_product_image()` → `first_white_background_image(..., fallback_to_first=False)`。

现行规则：

- 对 `site` 为 `bershka` 或 `bershka-es` 的商品逐一检测候选。
- `has_white_background()` 检测缩略图四周边缘白色像素比例。
- 返回第一个通过白底检测的候选。
- 如果所有候选都不是白底，`image_url` 留空。
- **禁止回退到第一张人物、穿戴或场景图。**
- 真实样本中 `-s`、`-a2d`、`-a4o` 常为白底，`-r`、`-p` 常非白底；这只是观察，最终仍以像素检测为准，不能仅靠后缀硬编码。
- 候选收集后仍走共享 `upgrade_image_url()`；Bershka 静态图域名通常无需改写，但不能因此跳过白底检测。
- 打包前由 `save_product_image_as_jpg()` 转为标准 JPEG。

## 8. 企业微信交付和附件命名

Bershka 使用通用 `process_site()` 与 `send_wecom_zip_bundle()`：

1. 新增商品图片筛选、下载。
2. 按分类建立 zip 和站点总包。
3. 大于限制时按实际压缩体积拆分。
4. 发送 Markdown 汇总和附件。
5. 全部成功后清理临时目录。

附件命名已优化为带站点名前缀，例如逻辑格式为：`Bershka_ES_<分类>_<数量>款_<marker>.zip`，避免企业微信只显示 `Bisutería` 而看不出品牌。该命名由共享 `build_product_zip_bundle()` 生成，因此修改会影响其他网站附件名。

## 9. Workflow 与测试

- Workflow：`.github/workflows/bershka-monitor.yml`。
- 当前是手动入口，文件明确说明定时监控暂停。
- 不要因修改代码或文档而恢复 schedule，也不要手动 dispatch。
- `tests/test_bershka_monitor.py` 保护：
  - ES category/API URL、store/catalog/language/country。
  - 不回退 GB 配置。
  - ES URL、欧元价格和 `bershka-es:` 命名空间。
  - ES 与旧 GB 状态隔离。
  - 空分类失败。
  - baseline-only 不发送企业微信。
  - `bershka-es` 能从非白底首图切到白底候选。
  - 全部非白底时不保留首图。
  - 分类 zip 文件名带 `Bershka_ES` 前缀。

## 10. 改动影响边界

通常仅影响 Bershka：`bershka_*`、`map_bershka_product()`、`validate_bershka_config()`、`scrape_bershka()`。

共享高风险：`first_white_background_image()` 也用于其他站点；`has_white_background()`、`process_site()`、Store、下载、zip 和企业微信均为共享逻辑。对共享 zip 命名的修改要检查所有站点。

## 11. 安全验证阶梯

1. 静态读取配置校验、API、映射和图片函数。
2. 用脱敏固定 JSON fixture 验证解析、价格、URL、namespace 和候选顺序。
3. mocked 白底检测与发送函数的单元测试。
4. 明确授权后才可 baseline-only 或真实 API 验证。
5. 完整商品包、企业微信、workflow、commit/push 必须逐项授权。

## 12. 维护规则

任何 ES 目标、API ID、映射、状态命名空间、白底规则、附件命名、测试或 workflow 状态变化，都要更新本文件。不得加入 webhook、Cookie、SQLite 行、商品快照、图片或 zip。源代码始终以 `sfera_monitor.py` 最新函数为准。
