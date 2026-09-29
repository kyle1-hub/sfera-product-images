# Primark 监控代码逻辑交接

> 文档性质：不可执行的代码逻辑说明，不是生产代码副本。  
> 审阅日期：2026-07-23  
> 审阅提交：`b9ca0d0`  
> 权威源文件：`sfera_monitor.py`  
> CLI site key：`primark`  
> 对应 workflow：`.github/workflows/primark-monitor.yml`

函数名是权威定位依据。Primark 会使用持久化浏览器 profile，即使“只抓取”也可能访问网站并写本地状态，因此安全边界比其他网站更严格。

## 给新窗口的启动提示词

> 请完整阅读 `docs/site-logic/primark-code-logic.md`，把它作为 Primark 监控交接。默认严格只读，不要启动浏览器、Playwright、监控、测试、网站请求、企业微信或 GitHub workflow；不要读取或输出 `config.json`、webhook、Cookie、浏览器 profile 或 SQLite 业务数据。当前主流程是 backend-first，并且不逐个打开商品详情页补图。根据我的需求读取最新源代码；修改前说明拟改文件、browser/profile 写入风险、fallback 行为及共享影响。未经明确授权，不访问网站、不写 profile/状态、不发送企业微信、不提交或推送 GitHub。

## 1. 监控目标

- 网站：Primark 美国站 Jewelry 分类。
- 优先使用 backend-oriented 路径：通过持久浏览器上下文建立会话，再复用 Cookie 做直接 HTML 请求和结构化数据解析。
- 后端路径失败且配置允许时，回退到浏览器列表页抓取。
- 当前主链不逐个打开产品详情页补图。
- SQLite 首次出现判断新增。

## 2. 严格安全边界

- 不启动 Playwright 或任何浏览器。
- 不读取、复制或删除 persistent profile；其中可能包含 Cookie 和挑战状态。
- 不运行 Primark monitor、tests 或 workflow。
- 不把 detail-page enrichment 接回主流程，除非用户明确要求并理解访问量与锁定风险。
- baseline-only 也会启动抓取链并写 SQLite/快照，不是只读。
- 不读取真实配置、webhook 或 state 内容。

## 3. 配置契约

`config.example.json` 的 Primark 配置：

- `display_name`
- `marker`：`Newly appeared`
- `base_url`：美国站 Jewelry
- `prefer_backend`：默认 true
- `fallback_to_browser`：默认 true
- `backend_headless`
- `browser_headless`
- `browser_channel`
- `headless` / `slow_mo_ms`
- `max_scroll_rounds`
- 继承 `state_dir`、图片下载、空报告等

浏览器 profile 路径由代码根据 state 目录生成。它是运行数据，不能写进文档或提交。

## 4. 抓取调用链

通用：`run()` → `site_config()` → `scrape_site()` → `scrape_primark()` → `process_site()`。

### Backend-first 路径

1. `primark_profile_dir()`：确定持久化浏览器 profile 目录。
2. `primark_browser_channel()`：按环境选择浏览器 channel。
3. `primark_launch_persistent_context()`：启动 persistent Playwright context。
4. `primark_backend_session()`：建立挑战/Cookie 会话并返回后端请求所需上下文。
5. `primark_fetch_html()`：复用会话直接获取页面 HTML。
6. `primark_ldjson_products()`：解析 schema.org JSON-LD 商品。
7. `primark_products_from_html()`：汇总页面中的商品结构。
8. `map_primark_product()`：映射统一商品对象。
9. `scrape_primark_via_backend()`：完成后端路径。

### 浏览器 fallback

- `scrape_primark_via_browser()`：直接浏览列表页、滚动并收集商品。
- `primark_listing_products()`：从卡片解析产品名、URL 和候选图。
- 仅在 backend 失败且 `fallback_to_browser` 开启时使用。

### 当前不在主路径的详情图 helper

`primark_detail_image_candidates()` / `primark_enrich_detail_images()` 存在，但当前 `scrape_primark()` 不逐个调用。不要因发现这些函数就默认启用。

## 5. 标准商品字段

- `site`：`primark`
- `category`：`JEWELRY`
- `name`
- `price`
- `url`
- `image_url`：初始首候选
- `image_candidates`
- `source_id`：通常从产品 URL 后缀识别
- `product_id`：`primark:<source_id或稳定回退ID>`
- `is_new=True`：交给 SQLite 首次出现判断
- `image_path`：运行期补充

## 6. 新品与状态语义

- 当前分类列表中的商品都是候选。
- `Store.mark_seen()` 以 `primark:` product ID 决定首次出现。
- 首次部署或状态丢失会把全量认作新增，应建立 baseline，但必须明确授权。
- 后台路径与浏览器 fallback 必须生成一致 product ID，否则会重复推送。
- `--force-new` 会绕过状态保护，禁止未经授权使用。
- `process_site()` 中对 Primark 有说明：当前只走后台抓取，不再打开详情页逐个补图。

## 7. 图片规则

- backend 路径主要从 JSON-LD/HTML 结构中获取候选；`image` 可以是字符串、对象或数组，由 `primark_iter_image_values()` 展平。
- `upgrade_image_url()` 把 Amplience `/i/primark/` 地址升到 `articleimages-largedesktop` / `productimages-largedesktop`，不要直接下 JSON-LD 里的 `w=200` 缩略图。
- browser fallback 从列表卡片获取图片，同样走升级。
- `refine_product_image()` 对 Primark 使用共享白底优先逻辑；找不到白底时当前可回退首候选。
- 详情页 enrich helper 不在主链。
- 下载、白底检测和 JPEG 转换复用共享函数。
- 如果要提高图片质量，应先增加 fixture 或 mocked 图片候选测试，而不是直接恢复逐详情页访问。

## 8. 企业微信交付

Primark 使用通用 `process_site()` 和 `send_wecom_zip_bundle()`：

- 首次出现 → 图片处理/下载 → snapshot → 分类/总 zip → 企业微信文字和附件。
- 商品量大时，`split_zip_bundle_by_size()` 会按实际压缩体积分包。
- 成功后清理临时打包目录，失败保留。
- 修改打包、附件名、上传或清理属于多站共享变更。

## 9. Workflow 与依赖

- Workflow：`.github/workflows/primark-monitor.yml`，仅手动 `workflow_dispatch`。现网每日跑本机隐藏任务。
- 除 Python 依赖外，GitHub 手动跑还会安装 Playwright Chromium。
- 本机 Primark 默认无头，且 `allow_headed_fallback=false`、`fallback_to_browser=false`，过不了验证就失败，不弹 Chrome。
- 未经授权不得运行、dispatch 或以 push 触发。

## 10. 测试现状

当前没有 Primark 专项测试。优先应补纯本地测试：

- JSON-LD 和 HTML fixture 解析。
- backend 与 fallback 映射得到同一 product ID。
- backend 失败时是否按配置 fallback。
- 列表滚动去重和终止条件。
- 图片候选及不调用详情页的主路径保证。
- 浏览器和网络全部 mock，不启动 Playwright。

## 11. 改动影响边界

Primark 局部：`primark_*`、`map_primark_product()`、`scrape_primark_via_backend()`、`scrape_primark_via_browser()`、`scrape_primark()`。

共享高风险：Store、白底检测、下载、`process_site()`、zip 和企业微信。

浏览器高风险：profile 路径、channel、persistent context、challenge/session；错误变更可能破坏后续抓取或泄露会话数据。

## 12. 安全验证阶梯

1. 静态阅读函数和 workflow。
2. HTML/JSON-LD 固定 fixture 测试。
3. 完全 mocked 的 browser/context/session 测试。
4. 只有明确授权才启动浏览器或访问 Primark。
5. baseline、企业微信、workflow、commit/push 分别授权。

## 13. 维护规则

Primark backend、browser fallback、profile、映射、图片、状态、交付、依赖或 workflow 变化时更新本文件。禁止加入凭证、Cookie、profile 路径内容、SQLite 行、快照或附件。以 `sfera_monitor.py` 最新函数为唯一生产事实。
