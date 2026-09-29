# Sfera 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-07-23  
> 审阅提交：`b9ca0d0`  
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`sfera`  
> 对应 workflow：`.github/workflows/sfera-monitor.yml`

函数名是权威定位依据；行号会随代码修改而变化。处理 Sfera 任务时，应先读本文件，再按函数名读取最新源代码。

## 给新窗口的启动提示词

> 请完整阅读 `docs/site-logic/sfera-code-logic.md`，把它作为 Sfera 监控的交接上下文。先保持只读，不要运行监控、Python 测试、浏览器、网站请求、企业微信测试或 GitHub workflow；不要读取或输出 `config.json`、webhook、Cookie、SQLite 业务数据。根据我的具体需求，只读取本文件所引用的最新源代码。修改前先说明拟改文件、验证方式，以及是否会影响其他网站共享逻辑。未经我明确授权，不访问网站、不写状态、不发送企业微信、不提交或推送 GitHub。

## 1. 监控目标

- 网站：Sfera 西班牙站。
- 监控范围：女装饰品下的 5 个配置分类：`PENDIENTES`、`COLLARES Y CHOKERS`、`ANILLOS`、`PULSERAS`、`BROCHES`。
- 网站新品信号：商品 API 中的 `badges.new`。
- 交付目标：把首次需要通知的 NUEVO 商品图片按分类打包，并通过企业微信机器人发送。

## 2. 安全边界

- 不运行 `sfera_monitor.py`，尤其不要使用发送、强制新品或机器人测试参数。
- 不触发 `.github/workflows/sfera-monitor.yml`。
- 不读取真实 `config.json`、环境变量值、webhook、SQLite 内容或生成快照。
- 不以“只是测试”为由访问网站；真实抓取会访问 API、写状态、下载图片，并可能发送通知。
- 修改共享函数前必须检查对 Bijou、Bershka、Lovisa、Stradivarius、Primark 的影响。

## 3. 配置契约

脱敏示例在 `config.example.json`：

- `sites.sfera.display_name`：企业微信消息中的站点名。
- `sites.sfera.marker`：当前为 `NUEVO`。
- `sites.sfera.base_url`：Sfera 饰品入口。
- `sites.sfera.categories`：每项包含展示名称 `name` 和 API slug `slug`。
- 继承的全局项：`state_dir`、`download_images`、`send_empty_report`、超时等。
- `share_library_root`：客户网站图片库根目录；路径含中文和双空格，必须原样保留 `06  客户网站图片`。
- `share_site_folders`：站点到库内一级文件夹的映射，当前只配 `sfera → E03-SFERA`。未映射站点跳过拷盘。一级是网站文件夹，二级是品类文件夹（如 `PENDIENTES`），分类 zip 直接丢进对应品类，不再每天新建日期目录。
- `WECOM_WEBHOOK` 只作为环境变量名存在；任何文档或日志都不得记录其值。
- 共享盘写入用 Python `shutil.copy2` 写 UNC；拷盘失败不影响企业微信已发送状态。GitHub 云端和关机电脑都写不了这台内网盘。不要写到 ERP 附件上传目录。

配置合并由 `load_config()` 和 `site_config()` 完成。站点配置覆盖同名全局配置。

## 4. 核心调用链

### 分发与通用处理

`run()` → `enabled_sites()` → `site_config()` → `scrape_site()` → `scrape_sfera()` → `process_site()`。

### Sfera 抓取链

1. `api_headers(category_slug)`：构建 API 请求头。
2. `api_url(category_slug, page)`：构建分类和分页 URL。
3. `fetch_json()`：请求 JSON，底层复用带重试的 HTTP 工具。
4. `extract_products_from_payload()`：取商品数组和总页数。
5. `map_api_product()`：映射为统一商品结构。
6. `scrape_sfera()`：逐分类、逐页抓取，只保留 API 标记为 NUEVO 的商品。

### 映射辅助函数

- `product_url()`：优先使用 API 给出的产品路径，否则根据 code/name 构造链接。
- `product_price()`：从商品或颜色变体价格中生成欧元格式。
- `product_image_entries()`：汇总颜色和变体下的预览/完整图片。
- `preferred_plain_image()`：优先选择 API 标记为 plain 且非 look 的图片，并结合白底检测。
- `image_url()`：执行候选图优先级并提供最后兜底。
- `map_api_product()`：设置 `site="sfera"`、分类、名称、价格、URL、图片、product ID 和 `is_new`。

## 5. 标准商品字段

统一商品对象主要包含：

- `site`：`sfera`
- `category`：配置中的分类名
- `name`：标准化后的商品名
- `price`
- `url`
- `image_url`
- `image_candidates`：部分站点使用；Sfera 主要在映射时直接选图
- `source_id`：若源数据提供
- `product_id`：优先 `code_a` 或 API ID，否则使用稳定回退 ID
- `is_new`：来自 `badges.new`
- `image_path`：运行期下载后补充

## 6. 新品和状态语义

Sfera 有两层判定：

1. `scrape_sfera()` 只返回网站当前带 `badges.new` 的商品。
2. `process_site()` 再调用 `Store.mark_seen()`；只有 SQLite 中第一次出现的商品才进入本次发送列表。

因此，NUEVO 标签持续多天不会导致每日重复推送。`--force-new` 会绕过第二层保护，可能把所有当前 NUEVO 再发一次，未经明确授权禁止使用。`--baseline-only` 会记录商品但不下载、不打包、不发送，仍会改变状态库。

## 7. 图片规则

- 首选 API 标注 `is_plain=true` 且 `is_look=false` 的商品图。
- `has_white_background()` 会下载缩略图并检查边缘像素的白色比例。
- Sfera 的主要选图发生在 `image_url()` / `map_api_product()`，不是 `refine_product_image()` 的站点分支。
- `upgrade_image_url()` 对 `dam.elcorteingles.es` 的 `impolicy=Resize` 把宽度提到 1600，避免列表接口留下的 967 宽图。
- 下载由共享 `download_image()` 完成；打包前由 `save_product_image_as_jpg()` 统一转为白底 RGB JPEG，并按产品名命名。
- 修改白底算法或通用下载器会影响多个网站，不属于 Sfera 局部改动。

## 8. 状态与企业微信交付

通用流程：

1. `Store.mark_seen()` 写入或更新商品状态。
2. 对本次新增商品执行图片处理和可选下载。
3. 写入运行快照。
4. `send_wecom_zip_bundle()` 调用 `build_product_zip_bundle()`：按分类生成 zip，再生成站点总包。
5. 若总包超过企业微信限制，`split_zip_bundle_by_size()` 按实际压缩体积拆包。
6. `build_zip_bundle_message()` 生成 Markdown 汇总。
7. `send_wecom()` 发送文字，`send_wecom_file()` 上传附件。
8. 企业微信成功后，`copy_zips_to_share()` 把分类 zip 拷到共享盘 `E03-SFERA/<品类>/`，例如 `E03-SFERA/PENDIENTES/`。同品类后续压缩包继续放进同一文件夹；文件名带日期以免覆盖。GitHub 云端或电脑关机写不了内网盘时只记失败，不改已发送状态。
9. 全部成功后清理临时打包目录；失败则保留用于排查。

附件名现在由站点名、分类、数量和 marker 组合。修改打包或企业微信函数会影响所有通用交付站点。

## 9. Workflow 与测试

- Workflow：`.github/workflows/sfera-monitor.yml`。
- 它包含手动入口和定时/路径触发逻辑，并会在运行后处理状态文件提交。
- 不要为了验证文档或解析改动而触发 workflow。
- 当前仓库没有 Sfera 专项测试文件。涉及 Sfera 修改时，优先新增脱网 fixture/mocked 单元测试，不能把真实抓取当单元测试。

## 10. 改动影响边界

通常仅影响 Sfera：

- `api_headers()`、`api_url()`、`extract_products_from_payload()`、`product_url()`、`product_price()`、`map_api_product()`、`scrape_sfera()`。

通常影响多个网站：

- `Store`、`fetch_json()`、`has_white_background()`、`download_image()`、`process_site()`、zip/企业微信函数、配置合并和 CLI 分发。

## 11. 安全验证阶梯

按授权逐级执行，不可跳级：

1. 静态读取最新函数与配置示例。
2. 使用本地固定 fixture 验证解析和映射。
3. 运行完全 mocked 的单元测试。
4. 经明确授权后才可进行 baseline-only 或真实网站验证。
5. 企业微信测试、workflow、提交和推送必须分别获得明确授权。

## 12. 维护规则

当 Sfera 的配置、API、映射、图片规则、状态语义、交付流程、测试或 workflow 变化时，同一改动中更新本文件。不要粘贴大段生产代码；始终以函数名指向 `sfera_monitor.py`。严禁加入凭证、Cookie、SQLite 行、快照、图片或 zip 产物。
