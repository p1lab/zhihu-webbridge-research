---
name: zhihu-webbridge-research
description: 使用 Kimi WebBridge 控制用户真实浏览器（含登录态）采集知乎：搜索关键词并提取相关问题/话题、搜索联想词，通过知乎官方 API 分页抓取问题下全部回答（可按创建时间排序）、抓取回答评论、抓取单条回答/文章/想法全文、按作者采集其全部回答/文章/想法/提问、抓取问题的相关问题与关注人数与关联热搜、抓取热榜与首页推荐流、抓取用户收藏夹列表与收藏条目并持久化到本地 SQLite 增量更新；另有可选写操作（对某回答赞同/喜欢/收藏/评论及各自撤销，保存/读取/删除某问题的回答草稿，发布回答与删除回答，关注/取消关注问题，默认 dry-run 且需逐次授权）。适用场景：用户给出关键词、知乎问题或回答链接，要求"搜索/收集相关问题下的回答"、批量抓取某问题的全部回答内容、抓取某作者/某人的全部回答或作品、抓取单条回答/文章全文、抓取热榜/推荐流、看某问题的相关问题与热搜、抓取/备份/增量同步"我的收藏"、"给这条回答点赞/收藏/评论"。触发词：知乎、zhihu、某问题的回答、采集知乎、搜索知乎、收藏夹、收藏、我的收藏、增量更新收藏、热榜、文章全文、回答全文、某作者的回答、按作者采集、推荐流、相关问题、联想词、点赞、赞同、喜欢、评论、取消点赞、移出收藏。
---

# Zhihu Webbridge Research

用 Kimi WebBridge 驱动用户真实浏览器（复用其知乎登录态），从知乎搜索/问题下**采集**回答与内容。**本 skill 以采集为主**（拿到干净的 JSON/SQLite 产物）；如何汇总、排版、出报告由当次任务决定，不内置模板。写操作（赞同/喜欢/收藏/评论/草稿/发布回答）作为可选基础设施存在（工作流 12），**默认 dry-run、须用户授权、一次一条、成对撤销**；对外可见度从低到高是 草稿 < 赞/评/收藏 < 发布回答，验证接口时停在能验证的最低层。

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
python scripts/fetch_zhihu_answers.py --qid <问题ID> --out <输出.json> [--session zhihu-answers] [--sort default|created]
```

- 问题 ID 从 URL 提取：`https://www.zhihu.com/question/{qid}`。
- 脚本按 `limit=10&offset=0,10,...` 分页，直到接口 `paging.is_end`；页内把回答正文 HTML 清洗为纯文本（去图片、保留段落换行），按回答 id 去重，保存 JSON。
- `--sort created`（实测生效）：严格按创建时间倒序返回，适合"过段时间再刷某题的新回答"（配合同收藏夹式的连续已知条目停止策略）。
- 接口口径见 `references/zhihu-api.md`。

### 4. 清洗与合规
- **页面显示的"回答数"可能大于 API 实际返回数**（被折叠/删除的回答接口不返回），如实说明差额，不强行凑数。
- 遇到内容涉及违规话题（如 NSFW 生成教程）的回答，**不收录正文**，在交付物中标注"该条因涉及违规内容未收录"。
- 回答为社区用户原创观点（社区来源，非权威事实），整理时不做真实性背书，并在交付说明中标注。

### 5. 收藏夹（collection）：抓取 + 持久化 + 增量更新
用户要求抓取/备份/增量同步"我的收藏"或某收藏夹时，用 `scripts/fetch_zhihu_collections.py`（持久化到 SQLite）：

```bash
python scripts/fetch_zhihu_collections.py list [--out collections.json]          # 收藏夹列表
python scripts/fetch_zhihu_collections.py fetch --collection 911247604 --db z.db # 单夹抓取（默认增量）
python scripts/fetch_zhihu_collections.py fetch --collection 911247604 --full --keep-html --export-json ./export
python scripts/fetch_zhihu_collections.py fetch --all --db z.db                  # 该用户全部收藏夹
```

- 接口：`/api/v4/me`、`/api/v4/people/{token}/collections`（列表）、`/api/v4/collections/{id}`（详情）、`/api/v4/collections/{id}/items`（条目，`{created: 收藏时间, content: 回答/文章/想法}`）。
- **排序口径（实测）**：条目接口排序稳定但**非严格按收藏时间**（中部/尾部有倒挂）；新收藏追加在头部，删除不改变剩余条目相对顺序。增量策略=从头部翻页，**连续遇到 BOUNDARY（默认 3）条已入库条目即停止**；`--full` 全量扫描并标记 removed。
- 库表：`collections`（收藏夹元数据）、`items`（条目，`(collection_id,item_id)` 主键，指纹去重，`removed_at` 标记删除）。
- 接口 `totals` 可能大于实际返回（已删除/折叠内容不返回），如实说明差额。

### 6. 单条回答 / 文章 / 想法全文
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

### 7. 热榜采集
```bash
python scripts/fetch_zhihu_hotlist.py --out hot.json                  # 全站榜（默认50条）
python scripts/fetch_zhihu_hotlist.py --type digital --limit 30 --out hot_digital.json
python scripts/fetch_zhihu_hotlist.py --all --out-dir ./hot          # 全部分类
```

- 接口 `/api/v3/feed/topstory/hot-lists/{type}?limit=&desktop=true`；type：total/digital/science/sports/finance/film/campus/news/game/music/fashion（均实测 200）。
- 条目含 rank、heat（"xx 万热度"）、trend、qid、answer_count、excerpt。

### 8. 按作者采集全部作品
用户给出某作者（url_token 或 `/people/{token}` 链接），要收集 TA 的全部回答/文章/想法/提问时：

```bash
python scripts/fetch_zhihu_member_works.py --token <url_token> --type answers|articles|pins|questions --out works.json
python scripts/fetch_zhihu_member_works.py --url "https://www.zhihu.com/people/<token>" --type answers --no-content --out list.json
```

- 实测某作者 answers 分页 totals=1514，`include=data[*].content` 可直接带出全文（`--no-content` 只出清单）。
- **members 族接口的 include 用 `data[*].字段` 括号形式**，与坑#11（问题回答接口要求裸字段名）相反，两族口径不同。
- 文章列表接口不含正文，需拿 article id 走 `fetch_zhihu_content.py`；赞同数在 `reaction.statistics.like_count`（列表里无 `voteup_count`）。

### 9. 问题周边上下文（相关问题 / 关注人数 / 关联热搜）
```bash
python scripts/fetch_zhihu_question_context.py --qid <问题ID> --out ctx.json [--aid <回答ID> --with-voters]
```

- 一次并发 gather：`similar-questions`（相关问题）、`questions/{qid}/followers` 的 `paging.totals`（关注人数+首屏昵称）、`search/hot_search?content_type=question`（该题"大家都在搜"，实测 30 条）。
- **合规**：followers/voters 返回真实用户列表，脚本只保留计数与首屏样本，不做批量个人画像采集；勿改写为全量翻页。

### 10. 首页推荐流采集
```bash
python scripts/fetch_zhihu_feed.py --out feed.json [--pages 5] [--limit 20]
```

- `/api/v3/feed/topstory/recommend`（v3），offset 翻页；随登录态个性化，不同账号结果不同属预期。
- 实测每页返回条数由服务端定（limit=10 时返回 6 条），以 `is_end`/空页为停止条件，脚本按 cid 去重。

### 11.（搜索辅助）联想词与垂类
```bash
python scripts/search_zhihu.py --query <关键词> --suggest --out sug.json          # 搜索联想词（做关键词发散）
python scripts/search_zhihu.py --query <关键词> --type people --out users.json   # 垂类：用户/杂志/地点
```

- `--type` 新增 `people|zazhis|place`（均实测 200）；`--suggest` 走 `/api/v4/search/suggest`。

### 12. 写操作（赞同 / 喜欢 / 收藏 / 评论 / 草稿 / 发布回答，需用户逐次授权）
用户要求"给某回答点赞/收藏/评论""保存草稿""写回答"或要搭写侧基础设施时，用 `scripts/zhihu_write_ops.py`：

```bash
python scripts/zhihu_write_ops.py status   --aid <回答ID>                     # 只读复核（赞/评/感谢/收藏夹）
python scripts/zhihu_write_ops.py vote     --aid <aid> [--cancel] --yes       # 赞同 / 取消赞同
python scripts/zhihu_write_ops.py like     --aid <aid> [--cancel] --yes       # 喜欢(感谢) / 取消
python scripts/zhihu_write_ops.py collect  --aid <aid> --yes                  # 收藏到默认夹
python scripts/zhihu_write_ops.py collect  --aid <aid> --collection <cid> --cancel --yes
python scripts/zhihu_write_ops.py comment  --aid <aid> --text "…" --yes        # 发评论（返回评论 id）
python scripts/zhihu_write_ops.py comment  --aid <aid> --delete <comment_id> --yes
python scripts/zhihu_write_ops.py draft    --qid <问题ID> --text "你好" --yes  # 保存草稿（仅本人可见）
python scripts/zhihu_write_ops.py draft    --qid <问题ID> --get               # 读取草稿
python scripts/zhihu_write_ops.py draft    --qid <问题ID> --delete --yes       # 删除草稿
python scripts/zhihu_write_ops.py publish  --qid <问题ID> --text "你好" --yes   # 发布回答（公开！）
python scripts/zhihu_write_ops.py delete-answer --aid <aid> --yes              # 删除回答（软删）
python scripts/zhihu_write_ops.py delete-answer --aid <aid> --cancel --yes      # 恢复（重新公开，已实测）
python scripts/zhihu_write_ops.py follow   --qid <问题ID> [--cancel] --yes      # 关注/取消关注问题
```

- **默认 dry-run**（只打印将要发送的请求），必须 `--yes` 才真正发出——写操作会触发真实通知，不要替用户加 `--yes`。
- **公开度分层**：`draft` 只写本人草稿（对外不可见，最适合验证接口）；`vote/like/collect/comment` 会给作者发通知；`publish`/`delete-answer` 直接改动公开内容。验证需求优先停在草稿层。
- **测试内容要中性**：草稿/回答的正文用"你好"这类无意义文本，**不要写"接口验证""爬虫测试""稍后删除"之类字样**——易被平台判为违规/被举报。
- 端点与响应口径见 `references/zhihu-api.md` 第十节；所有写路径（含 `content/publish`）**都不校验 `x-zse` 签名**，页内 fetch + `x-xsrftoken` 即可。
- 一次只动一条内容；测试/验证优先用**别人提问的冷门问题**（**不要拿用户自己过去的提问当靶子**），做完按原样撤销并用 `status`/`--get` 复核净零。
- `publish` 会**自动关注该问题**，撤销时要 `delete-answer` + `follow --cancel` 两步一起做；发布响应体里**不含回答 ID**，脚本已用"问题回答列表按本人 `url_token` 反查"取回 `aid` 并打印撤销命令。

## 资源

- `scripts/search_zhihu.py`：搜索关键词 → 提取相关问题/话题/文章清单并保存 JSON 的脚本（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_answers.py`：页内调用知乎回答接口分页抓取 + HTML 清洗 + 去重 + 保存 JSON 的脚本（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_collections.py`：抓取收藏夹列表/条目 → 清洗 → SQLite 持久化 + 增量更新 + JSON 导出的脚本（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_content.py`：抓取单条回答/文章/想法全文 + HTML 清洗 + 保存 JSON（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_hotlist.py`：抓取热榜（全站/分类）并保存 JSON（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_comments.py`：抓取某回答评论含楼中楼，分页到 `is_end` 并保存 JSON（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_member_works.py`：按作者分页抓取全部回答/文章/想法/提问 + 清洗 + 去重 + 保存 JSON（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_question_context.py`：一次 gather 抓取问题的相关问题/关注人数/关联热搜（+可选某回答点赞数），保存 JSON（确定性逻辑，直接用）。
- `scripts/fetch_zhihu_feed.py`：抓取首页推荐流信息并保存 JSON（确定性逻辑，直接用）。
- `scripts/zhihu_write_ops.py`：写操作（赞同/喜欢/收藏/评论 + 各自撤销、草稿存取舍、发布回答、删除/恢复回答、关注问题）与只读 `status` 复核；默认 dry-run，`--yes` 才执行（**唯一写侧脚本，使用前须用户授权**）。
- `references/zhihu-api.md`：知乎搜索、回答、收藏、单条内容、热榜、作者、问题上下文、推荐流、评论 v5 与**写接口**的端点、字段口径与坑点。
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

新增坑：
11. **answers include 必须裸字段名**（`content,voteup_count,ip_info,thanks_count,…`）；写成 `data[*].ip_info` 形式会被白名单丢弃、`ip_info/thanks_count/comment_count` 不返回。`favorite_count` 两路实测都拿不到（放弃）。
12. **问题详情 `/api/v4/questions/{qid}` 直取 403**；answers 的 `question` 子对象不含关注/回答数——这些只在 search_v3 结果里。

补充：`fetch_zhihu_comments.py` 也支持 `--concurrency`(页内并发多页,默认5)。

## 接口扩展（v1.1，2026-10-01，实测）

对照真实页面（首页/问题页/个人页/收藏页/通知页网络抓包）+ 约 70 个端点批量探测新增：

- **作者维度**：`/api/v4/members/{token}/answers|articles|pins|questions` 全部 200，answers 带 `include=data[*].content` 直接出全文（实测 totals=1514）；另有 `/members/{token}`（资料）、`/followees`、`/followers`、`/relations/mutuals`。
- **问题上下文**：`/api/v4/questions/{qid}/similar-questions`、`/followers`（totals 可用）、`/api/v4/search/hot_search?content_type=question&content_token={qid}`（实测 30 条热搜）均 200；`/answers/{aid}/voters`（totals 可用）、`/recommendations`、`/labels/v3`（官方认证，多为空）、`/inviters`（常空）。
- **推荐流**：`/api/v3/feed/topstory/recommend` 200；搜索辅助 `search/suggest`、`t=people|zazhis|place` 200。
- `sort_by=created` 对问题回答接口实测生效（严格创建时间倒序）。

新增坑：
13. **members 族 include 与问题回答接口相反**：`/members/{token}/answers` 的 include 用 `data[*].content` 括号形式**有效**（能出全文）；坑#11 的"必须裸字段名"只适用于 `/questions/{qid}/answers`。members 列表单页返回条数可能多于 `limit` 且页间有重叠，按 id 去重+max 截断。
14. **members 列表无 `voteup_count`**：赞同数在 `reaction.statistics.like_count`；评论数列表中无（voters/followers 才有计数）。
15. **合规围栏**：voters/followers 返回真实用户列表，仅取计数/首屏做热度参考，不做批量个人画像采集。
16. **死路勿再试**（均实测）：`topics/{id}` 与 `topics/{id}/feeds` 403（需签名）、话题页 HTML 无 initialData；`notifications`/`pin_feed`/`moments`/`specials`/`collections/hot`/`member-zone`/`member/activities` 404 或回 HTML；`questions/{qid}/top-writers`、`/related-question-feed`、`/draft` 之外未验证的猜测路径 404；`/members/{token}/videos|favours|columns|upvotes|creation` 404。`api/v3/questions/{qid}` 返回 HTML 不是 JSON。

待挖（未固化）：圆桌 `roundtables/{token}/questions` 子路径未验证（列表/详情已 200，全站 2427 个）；专栏文章列表 `zhuanlan/api/columns/{slug}/posts` 需到 zhuanlan 域页面内再测（www 域跨 fetch 被 CORS/403 挡）。

## 写接口基础设施（v1.3，2026-10-01，实测含撤销配对）

`scripts/zhihu_write_ops.py` 覆盖六对写动作，全部**页内 fetch 直接可用、不校验 x-zse 签名**（只带 cookie + `x-xsrftoken`(=cookie 里 `_xsrf`) + `x-requested-with: fetch`）：

| 动作 | 端点 | body |
|---|---|---|
| 赞同 / 取消 | `POST /api/v4/answers/{aid}/voters` | `{"type":"up"}` / `{"type":"neutral"}` |
| 喜欢(感谢) / 取消 | `POST` \| `DELETE /api/v4/answers/{aid}/thankers` | 空 |
| 收藏 / 移出 | `POST /api/v4/collections/contents/answer/{aid}` / `DELETE /api/v4/collections/{cid}/contents/{aid}?content_type=answer` | 空 / 无 |
| 评论 / 删除 | `POST /api/v4/comment_v5/answers/{aid}/comment` / `DELETE /api/v4/comments/{cid}` | `{"content":"…"}` / 无（204） |
| 草稿 存/取/删 | `POST`\|`PUT /api/v4/questions/{qid}/draft` / `GET …/draft?include=question,schedule` / `DELETE …/draft` | `{"content":"<p>…</p>","delta_time":N,"draft_type":"normal","settings":{…}}；删除返回 `{"success":true}`，删后 GET 返回 `{}` |
| 发布回答 | `POST /api/v4/content/publish` | `{"action":"answer","data":{"hybrid":{"html":…,"textLength":N},"extra_info":{"question_id":…,"include":…,"pc_business_params":"<JSON字符串>"},"reprint":{…},"commentsPermission":{…},"appreciate":{…},"publishSwitch":{…},"creationStatement":{…},"commercialReportInfo":{"isReport":0},"contentsTables":{…},"thanksInvitation":{…},"toFollower":{},"draft":{…},"publish":{"traceId":"<ms>,<uuid>"}}}` |
| 删除回答 / 恢复回答 | `DELETE /api/v4/answers/{aid}` → `{"success":true}`（之后 GET 404）；`POST /api/v4/answers/{aid}/actions/restore` → 200 且返回完整回答对象（实测能复原，会重新公开） | 无 |
| 关注 / 取消关注问题 | `POST`\|`DELETE /api/v4/questions/{qid}/followers` → 204；回查用 `GET …/followers?offset=0&limit=5` 的 `paging.totals` | 无 |

草稿计数与历史：`GET /api/v4/answer-drafts/count` → `{count, scheduled_count}`；`GET /api/v4/draft-histories?object_type=question&object_id={qid}`（分页）；源码里还见到 `GET draft-history?versionType&id` + `POST draft-history/revert {version_type,id}`（版本回滚，未实测）。

顺带补全评论**读** v5 命名空间（`…/root_comment?order_by=score|time`、`…/{type}s/{id}/permission`、`collections/contents/{type}/{id}` 查是否已收藏）。老路径 `/answers/{aid}/root_comments` 仍 200，`fetch_zhihu_comments.py` 无需改。

新增坑：
17. **写接口不需要 x-zse 签名**（实测假 ID 得到 422/400/404 业务错误而非签名类 403）；评论创建的 UI body 是密文，只是客户端 `zsEncrypt` 自选加密，**接口接受明文 JSON**（传裸字符串会得到 `invalid character 'p' looking for beginning of value`）。发布回答同样明文可回放（UI 请求带 `x-zse-93/96/zst-81`，去掉仍 200）。
18. **评论删除在 v4 老路由**：`DELETE /api/v4/comments/{cid}`（204）；`/comment_v5/comments/{cid}`、`/comment_v5/answers/{aid}/comment/{cid}`、`…/root_comment/{cid}`、`…/comments/{cid}/delete` 全 404，`…/root_comment` 只支持 GET（POST 405）。
19. **voters 与 thankers 是两套计数**（赞同 vs 喜欢/感谢），但两者响应都返回全套 reaction 状态（`is_up/is_liked/is_thanked/voteup_count/thanks_count`），可省一次状态查询；赞同刚发出时 `heavy_up_result:"reviewing"` 属正常审核态。收藏无 body ⇒ 只能进默认夹，响应里的 `collection.id` 即移出要用的 `{cid}`；重复点"已收藏"会重发 POST 得 403 `code:106 您已经收藏过该内容`。
20. **UI 路径的写操作坑**（若改真实点击）：评论"发布"按钮只在真实键盘输入后出现（`fill` 写 innerText 不点亮，Draft.js state 不同步）→ 用 `evaluate` focus+选区、`send_keys Control+a/Delete` 清空、`key_type` 逐字输入；`svg` 元素没有 `.click()`，`mouse_click` 在后台标签页会因遮挡失败（`isTrusted` 之外还有窗口可见性问题）。
21. **写侧合规**：写动作对真实作者产生可见通知，评论删除前对方可能已收到推送；须逐次授权、一次一条、间隔 ≥1s，做完成对撤销并用 `status` 复核净零（不做批量刷赞/刷收藏）。**测试正文用"你好"这类中性文本**，写"接口验证/稍后删除"会被判违规或被举报。
22. **发布回答的入口不在 `questions/{qid}/answers`**：`POST /api/v4/questions/{qid}/answers` 恒 422 `UnprocessableError`（路由存在但非发布口）；真发布口是 **`POST /api/v4/content/publish`**（`action=answer`，正文在 `data.hybrid.html`，问题设置是 `data.extra_info.pc_business_params` 这段**被再次字符串编码的 JSON**）。发布前 UI 还会 GET `api.zhihu.com/content/publish/control/v2?scene=answer&questionToken=…`（权限/风控探针）。
23. **发布成功响应不含回答 ID**：结构是 `{code,message,toast_message,data}`；`toast_message` 非空即业务失败（例：`已回答过该问题，创建答案失败`，被软删的回答仍占这个名额），HTTP 仍是 200，不能只看状态码。取 `aid` 要回查 `questions/{qid}/answers?include=author` 按本人 `url_token` 过滤。
24. **删除回答后仍留痕**：`DELETE /api/v4/answers/{aid}` 是软删（GET 转 404 `ResourceNotFoundException`），但"已回答过该问题"的判定不解除，同题再发会被 toast 拦下（2026-10-01 实测：软删后该题 permanently 不能再发，换题才能继续测）；`POST /api/v4/answers/{aid}/actions/restore` 可复原并重新公开（实测 200 + 返回回答对象，`GET` 回 200、回答数 +1），复原后可再删。发布还会**自动关注问题**（关注数 +1），净零要补 `DELETE /api/v4/questions/{qid}/followers`（回查 `…/followers?limit=5` 的 `paging.totals`）。
25. **别指望"页内拦截 fetch 再点发布"来安全抓包**：`Page.addScriptToEvaluateOnNewDocument` 装的 patch 能抓到 `read_history/add`、草稿 PUT 等页内请求，但**编辑器的发布请求绕过了 patch**（实测照样真发布），故抓 UI 契约应直接读守护进程 `network` 的 detail（含完整明文 body + 响应），或用 `draft`（对外不可见）验证接口。

