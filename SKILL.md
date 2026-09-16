---
name: zhihu-webbridge-research
description: 使用 Kimi WebBridge 控制用户真实浏览器（含登录态）调研知乎：搜索关键词并提取相关问题/话题，再通过知乎官方 API 分页抓取问题下全部回答、抓取单条回答/文章/想法全文、抓取热榜、抓取用户收藏夹列表与收藏条目并持久化到本地 SQLite 增量更新，以及对本地产物做主题聚类分析并生成 HTML 报告。适用场景：用户给出关键词、知乎问题或回答链接，要求"搜索/收集/整理/总结相关问题下的回答"、"看看大家怎么说"、批量抓取某问题的全部回答内容、抓取单条回答/文章全文、抓取热榜、抓取/备份/增量同步"我的收藏"、对收藏做主题分析报告。触发词：知乎、zhihu、某问题的回答、整理回答、采集知乎、搜索知乎、收藏夹、收藏、我的收藏、增量更新收藏、热榜、文章全文、回答全文、分析报告。
---

# Zhihu Webbridge Research

用 Kimi WebBridge 驱动用户真实浏览器（复用其知乎登录态），从知乎搜索/问题下收集并整理回答。

## 核心结论（先读）

**不要靠页面滚动加载来收集回答** —— 知乎问题页默认只渲染前几个回答，`window.scrollTo` 等程序化滚动无法可靠触发懒加载（实测多轮振荡滚动也仅能到 ~45/111 条）。**可靠路径是：在已打开的知乎页面上，用页内 `fetch()` 调用知乎官方回答接口**（同源、自动带登录 cookie），按 `offset` 分页拉全。

## 前置：WebBridge 操作要点

- 守护进程未运行则先启动：Windows `& "$env:USERPROFILE\.kimi-webbridge\bin\kimi-webbridge.exe" start`（macOS/Linux 用 `~/.kimi-webbridge/bin/kimi-webbridge start`）。
- **Windows 下禁止用 shell 内联 JSON 发送请求**（PowerShell 会破坏中文与引号）。必须用文件工具把请求体写入唯一临时文件，再用 `curl.exe -s -X POST http://127.0.0.1:10086/command -H "Content-Type: application/json" --data-binary "@<file>"`，用后删除。
- 每个任务固定一个 `session` 名，首次 `navigate` 设置用户语言的 `group_title`，任务结束仅当用户要求时才 `close_session`。
- 定位 tab：先 `list_tabs`（新会话通常为空）；再 `find_tab` 传 `active:true` 借取用户当前正看的 tab；仍找不到则 `navigate`（`newTab:true`）打开目标 URL。
- 详细操作见 `references/webbridge-operations.md`。

## 工作流

### 1. 定位/打开知乎页面
- 输入是回答链接 `/question/{qid}/answer/{aid}`：先按上述方式尝试复用用户 tab；不可用则新开标签页导航到该 URL，用于确认问题存在与页面标题。
- **注意**：回答链接打开的是"单答聚焦视图"，只能看到少量回答。收集全部回答仍走下面的 API 路径。

### 2.（可选）搜索关键词，提取相关问题
没有现成问题、只有调研主题时，先搜索并提取问题清单：

```bash
python scripts/search_zhihu.py --query <关键词> --out questions.json [--type general|question|topic] [--pages 3]
```

- 默认 `t=general` 混合搜索，从结果聚合出问题清单（按 qid 去重），含每题标题、回答数、搜索命中数、最高赞回答作者与摘要；顺带提取话题与文章。
- `--type question` 只返回问题实体；`--type topic` 只返回话题。
- 拿到 `questions.json` 里的 `qid` 后，逐个进入下一步抓取回答。
- 字段口径与坑点见 `references/zhihu-api.md`。

### 3. 用脚本拉取全部回答
页面已在知乎域名打开后，运行打包脚本（它会通过守护进程在页内执行 fetch）：

```bash
python scripts/fetch_zhihu_answers.py --qid <问题ID> --out <输出.json> [--session zhihu-answers]
```

- 问题 ID 从 URL 提取：`https://www.zhihu.com/question/{qid}`。
- 脚本按 `limit=10&offset=0,10,...` 分页，直到接口 `paging.is_end`；页内把回答正文 HTML 清洗为纯文本（去图片、保留段落换行），按回答 id 去重，保存 JSON。
- 接口口径见 `references/zhihu-api.md`。

### 4. 清洗与合规
- **页面显示的"回答数"可能大于 API 实际返回数**（被折叠/删除的回答接口不返回），如实说明差额，不强行凑数。
- 遇到内容涉及违规话题（如 NSFW 生成教程）的回答，**不收录正文**，在交付物中标注"该条因涉及违规内容未收录"。
- 回答为社区用户原创观点（社区来源，非权威事实），整理时不做真实性背书，并在交付说明中标注。

### 5. 整理与交付
- 用统一模板生成调研报告（确定性逻辑，可直接用）：

```bash
python scripts/gen_answers_report.py --answers answers.json --out report.html \
    --qid <问题ID> --title "问题标题" [--top 5] [--notes "备注"]
```

- 报告结构：① 标题头（问题/qid/抓取说明）② 概况卡（回答数/累计赞同/时间跨度/最高赞）③ **核心观点速览**（高赞 Top N 观点摘要 + 各主题代表观点，摘要为回答开头原句确定性截取）④ 主题导航 + 按主题分组的全部回答（作者/赞同/评论/日期/全文）。
- 按主题归类回答（多模型分工/省Token、提示词与工程化、/goal 自主运行、生态与外部工具、非编程应用、额度与账号、官方动态、个人体验、合规风险等），主题关键词在 `gen_answers_report.py` 的 `THEMES` 中可增删。
- 交付物用 `present_files` 交付；同时可附原始 JSON 数据文件。

### 6. 收藏夹（collection）：抓取 + 持久化 + 增量更新
用户要求抓取/备份/增量同步"我的收藏"或某收藏夹时，用 `scripts/fetch_zhihu_collections.py`（持久化到 SQLite）：

```bash
python scripts/fetch_zhihu_collections.py list [--out collections.json]          # 收藏夹列表
python scripts/fetch_zhihu_collections.py fetch --collection YOUR_COLLECTION_ID --db z.db # 单夹抓取（默认增量）
python scripts/fetch_zhihu_collections.py fetch --collection YOUR_COLLECTION_ID --full --keep-html --export-json ./export
python scripts/fetch_zhihu_collections.py fetch --all --db z.db                  # 该用户全部收藏夹
```

- 接口：`/api/v4/me`、`/api/v4/people/{token}/collections`（列表）、`/api/v4/collections/{id}`（详情）、`/api/v4/collections/{id}/items`（条目，`{created: 收藏时间, content: 回答/文章/想法}`）。
- **排序口径（实测）**：条目接口排序稳定但**非严格按收藏时间**（中部/尾部有倒挂）；新收藏追加在头部，删除不改变剩余条目相对顺序。增量策略=从头部翻页，**连续遇到 BOUNDARY（默认 3）条已入库条目即停止**；`--full` 全量扫描并标记 removed。
- 库表：`collections`（收藏夹元数据）、`items`（条目，`(collection_id,item_id)` 主键，指纹去重，`removed_at` 标记删除）。
- 接口 `totals` 可能大于实际返回（已删除/折叠内容不返回），如实说明差额。

### 7. 单条回答 / 文章 / 想法全文
有链接或 id、只需抓单条全文时，用 `scripts/fetch_zhihu_content.py`：

```bash
python scripts/fetch_zhihu_content.py --url "https://www.zhihu.com/question/{qid}/answer/{aid}" --out a.json
python scripts/fetch_zhihu_content.py --url "https://zhuanlan.zhihu.com/p/{arid}" --out art.json
python scripts/fetch_zhihu_content.py --url "https://www.zhihu.com/pin/{pid}" --out pin.json
python scripts/fetch_zhihu_content.py --aid <aid> --keep-html --out a.json
```

- 回答走 `/api/v4/answers/{aid}?include=content,...`（不带 include 时无 content 字段）。
- **文章必须走 `zhuanlan.zhihu.com/api/articles/{id}`**（`www.zhihu.com/api/v4/articles/...` 全形态 403 code 10003）。
- 想法正文在 `content_html`（`content` 是对象数组）。

### 8. 热榜采集
```bash
python scripts/fetch_zhihu_hotlist.py --out hot.json                  # 全站榜（默认50条）
python scripts/fetch_zhihu_hotlist.py --type digital --limit 30 --out hot_digital.json
python scripts/fetch_zhihu_hotlist.py --all --out-dir ./hot          # 全部分类
```

- 接口 `/api/v3/feed/topstory/hot-lists/{type}?limit=&desktop=true`；type：total/digital/science/sports/finance/film/campus/news/game/music/fashion（均实测 200）。
- 条目含 rank、heat（"xx 万热度"）、trend、qid、answer_count、excerpt。

### 9. 收藏分析报告（主题聚类）
```bash
python scripts/analyze_collections.py --db zhihu_collections.db --out report.html
python scripts/analyze_collections.py --db zhihu_collections.db --top 15 --days 14 --export-json theme.json
```

- 纯本地运行（不依赖浏览器）；按预置 10 主题关键词对条目聚类，输出单文件 HTML：概况卡片、主题分布、各主题 Top、近 N 天新增/移除、全库 Top。
- 主题判定为关键词命中（标题+问题标题+摘要+正文前 300 字），命中最多者胜，平局按主题优先级；仅供归类参考。

### 10. 调研成果归档与收藏库标记
调研完一个问题后，用 `scripts/research_register.py` 归档成果并标记"已调研"（纯本地，不依赖浏览器）：

```bash
python scripts/research_register.py register --question <qid> \
    --report 报告.html --data answers.json --extra 生成脚本.py --notes "备注"
python scripts/research_register.py status --question <qid>   # 是否已调研
python scripts/research_register.py list                      # 全部调研记录
```

- **归档目录**：`<db 所在目录>/research/{qid}_{题目前缀}/`，含 `manifest.json`（qid、题目、时间、关联条目、文件清单、备注）与复制的成果文件。
- **DB 标记**（幂等迁移）：新建 `research` 表（问题维度记录）；`items` 表新增 `researched_at` / `research_dir` 列标记条目。库内 `question_id` 匹配的条目自动关联，`--link-items` 可手动补标。
- 同一 qid 重复 register 视为更新（复用目录、manifest 文件清单按名去重）。
- `analyze_collections.py` 报告会自动显示「已调研」徽标。

## 资源

- `scripts/search_zhihu.py`：搜索关键词 → 提取相关问题/话题/文章清单并保存 JSON 的脚本（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_answers.py`：页内调用知乎回答接口分页抓取 + HTML 清洗 + 去重 + 保存 JSON 的脚本（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_collections.py`：抓取收藏夹列表/条目 → 清洗 → SQLite 持久化 + 增量更新 + JSON 导出的脚本（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_content.py`：抓取单条回答/文章/想法全文 + HTML 清洗 + 保存 JSON（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_hotlist.py`：抓取热榜（全站/分类）并保存 JSON（确定性逻辑，直接用）。
- `scripts/analyze_collections.py`：收藏库主题聚类 → 单文件 HTML 报告 + 可选主题 JSON 导出（纯本地，确定性逻辑，直接用）。
- `scripts/research_register.py`：调研成果归档（research/{qid}_{题目}/ + manifest）+ 收藏库"已调研"标记（research 表 + items.researched_at/research_dir），纯本地，确定性逻辑，直接用。
- `scripts/gen_answers_report.py`：统一调研报告模板（概况卡 + 核心观点速览 + 主题分组全文），确定性逻辑，直接用。
- `references/zhihu-api.md`：知乎搜索、回答、收藏、单条内容与热榜接口的端点、字段口径与坑点。
- `references/webbridge-operations.md`：Kimi WebBridge 的启动、tab 定位、Windows 文件体请求、已知限制等操作细则。

## 已知坑（务必遵守）

1. **滚动加载不可靠**：不要用 `window.scrollTo`/振荡滚动来"刷"出更多回答，直接走 API。
2. **接口域名用 `www.zhihu.com/api/v4/...`**（与页面同源），不要用 `api.zhihu.com` 跨域。
3. **evaluate 里 `const` 跨调用重复声明会报错**，包装 `(() => {...})()`。
4. **CDP `Input.dispatchMouseEvent` 可能挂起**，优先用 evaluate 的 fetch 路径。
5. 长文档/大响应会被持久化到 tool-results 文件，需要时用脚本二次读取，不要用命令行内联解析。
6. **搜索结果的 answer 实体**：问题标题在 `object.title`、问题 ID 在 `object.question.id`（`object.question.title` 常为空），别读错字段。
7. **收藏条目接口**：`/collection/` 会 302 到 `/collection/hot`（热门收藏夹页）；用户自己的收藏夹列表走 `/api/v4/people/{token}/collections`。`/api/v4/me/collections` 无效（404/HTML）。
8. **收藏条目排序非严格按时间**：增量停止逻辑基于"连续已知条目"而非时间戳；`--full` 才做 removed 校准。条目 `created` 是收藏时间（ISO 带时区），`content.created_time/updated_time` 是内容本身的时间戳。
9. **文章接口坑**：`/api/v4/articles/{id}` 一律 403（code 10003），必须用 `zhuanlan.zhihu.com/api/articles/{id}`（跨域 fetch 带登录态可用）。回答单条接口不带 `include=content` 时响应里没有 content 字段。想法正文取 `content_html`（`content` 为对象数组）。
10. **热榜接口**：`/api/v3/feed/topstory/hot-lists/{type}?limit=&desktop=true`（v3 非 v4）；`trend` 字段部分条目为空属正常。

## 采集增强（v1.0，2026-09-16，实测）

- **`fetch_zhihu_comments.py`（新）**：抓某回答评论含楼中楼，端点 `/api/v4/answers/{aid}/root_comments?order=by_vote|by_time`，分页到 `paging.is_end`（`totals` 不可靠勿用）。用法 `--aid/--url --out comments.json [--order] [--limit]`。
- **富字段**：`fetch_zhihu_answers/content` 的回答记录新增 `thanks`(感谢)/`ip`(IP属地)/`copyable`/`author_headline`/`author_gender`/`author_url_token`；`collections` items 新增 `thanks_count`（DB 已加列并对旧库自动 `ALTER` 迁移）。均向后兼容（旧 JSON 缺键不报错）。
- **`--concurrency`（默认5）**：answers 抓取在**单次 evaluate 内 `Promise.all` 并发多页**（daemon 命令单通道串行，只有页内并发才提速）。同题实测 8.8s→3.9s、结果 id 集合一致。
- **报告**：`gen_answers_report.py` 概况卡加"累计感谢/最多地区"，每条回答补"· 感谢 N · 属地"。

新增坑：
11. **answers include 必须裸字段名**（`content,voteup_count,ip_info,thanks_count,…`）；写成 `data[*].ip_info` 形式会被白名单丢弃、`ip_info/thanks_count/comment_count` 不返回。`favorite_count` 两路实测都拿不到（放弃）。
12. **问题详情 `/api/v4/questions/{qid}` 直取 403**；answers 的 `question` 子对象不含关注/回答数——这些只在 search_v3 结果里。

补充：`fetch_zhihu_comments.py` 也支持 `--concurrency`(页内并发多页,默认5)；`analyze_collections.py` 概况含"累计感谢"并有"全库最高认可(按感谢)"榜（旧库无 thanks_count 列自动降级）。
