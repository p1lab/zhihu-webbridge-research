# 知乎接口说明

在已打开的 `www.zhihu.com` 页面上用页内 `fetch()` 调用（同源、自动携带用户登录 cookie）。

## 一、搜索接口（提取相关问题）

```
GET https://www.zhihu.com/api/v4/search_v3
```

参数（URL 编码）：
- `t`：搜索类型。`general`（混合：回答/文章/问题/话题）、`question`（只搜问题）、`topic`（只搜话题）、`answer`、`article`
- `q`：关键词
- `correction=1`、`offset`、`limit`（建议 10）
- `paging.is_end`：是否最后一页（`paging.total` 不一定返回）

结果实体的关键字段（`data[*].object`）：
- `type=answer`：搜索到"某问题下的一个高赞回答"。**问题标题在 `object.title`，问题 ID 在 `object.question.id`**（注意 `object.question.title` 常为空）；还带 `voteup_count`、`answer_count`（所属问题的回答数）、`author.name`、`excerpt`
- `type=question`：直接命中的问题，`id`/`title`/`answer_count`/`follower_count`
- `type=article`：专栏文章，`id`/`title`/`author.name`
- `type=topic`：话题，`id`/`name`/`followers_count`
- 另有 `hot_timing`、`relevant_query` 等辅助条目，无提取价值

建议直接用 `scripts/search_zhihu.py`，它按 offset 翻页、按问题 qid 去重、聚合出 `{stats, questions[], topics[], articles[], other[]}`。

---

## 二、回答接口（抓取某问题全部回答）

### 端点

```
GET https://www.zhihu.com/api/v4/questions/{qid}/answers
```

参数（URL 编码）：
- `include`：要返回的字段，见下
- `limit`：每页条数（1–20，建议 10）
- `offset`：分页偏移（0, limit, 2*limit, …）
- `platform=desktop`
- `sort_by=default`（默认/综合排序；知乎还有 `created` 等排序可尝试）

## include 字段串

```
data[*].content,data[*].voteup_count,data[*].comment_count,data[*].created_time,data[*].updated_time,data[*].is_collapsed,data[*].content_need_truncated,data[*].author
```

返回项：
- `content`：回答正文 HTML（含 `<figure><img>` 图片）
- `voteup_count` 赞同数、`comment_count` 评论数、`created_time`/`updated_time` Unix 时间戳
- `is_collapsed` 是否被折叠、`content_need_truncated` 是否被截断
- `author.name` 作者名
- `paging.is_end`：是否最后一页（`paging.total` 通常不存在）

## HTML → 纯文本清洗

页内用临时 `div.innerHTML = content` 后处理：
- 删除 `img, figure`（图片不进正文，或仅统计张数）
- `br` → `\n`；块级元素 `p,div,li,h1..h4,blockquote,pre` 后追加 `\n`
- 再 `innerText`，用正则剔除残留标签，合并多余空行

## 口径与坑点

1. **页面显示的回答数可能 > API 返回数**。实测某问题页面显示 111 条，API 仅返回 102 条（被折叠/删除/屏蔽的回答接口不返回）。向用户如实说明差额，不强行凑数。
2. **`sort_by=default` 的排序与网页首屏可能不完全一致**（网页有推荐加权），但不影响"收集全部"的目标。
3. **登录态**：接口依赖页面 cookie；未登录会返回 401 或风控。必须先确认 tab 已打开在知乎域名、且用户已登录。
4. **`content_need_truncated` 为 true 的个别长答**接口仍会给出完整 `content`，一般无需二次翻页。
5. **合规**：个别回答可能涉及违规内容（如 NSFW 生成教程），整理时剔除其正文，并在交付物中标注原因。

## 请求示例（evaluate 内的 fetch）

```js
const url = 'https://www.zhihu.com/api/v4/questions/' + qid +
  '/answers?include=' + encodeURIComponent(INCLUDE) +
  '&limit=10&offset=' + offset + '&platform=desktop&sort_by=default';
const r = await fetch(url, {credentials: 'include'});
const j = await r.json();
```

建议直接用 `scripts/fetch_zhihu_answers.py`，勿手写分页循环。

---

## 三、收藏接口（收藏夹列表 / 详情 / 条目）

### 端点

```
GET https://www.zhihu.com/api/v4/me                                     # 当前登录用户（name, url_token, id）
GET https://www.zhihu.com/api/v4/people/{url_token}/collections?limit=&offset=   # 某用户的收藏夹列表
GET https://www.zhihu.com/api/v4/collections/{id}                       # 收藏夹详情（返回 {collection:{...}}）
GET https://www.zhihu.com/api/v4/collections/{id}/items?limit=&offset=  # 收藏夹条目
```

### 字段口径

- 收藏夹对象：`id, title, description, is_public, item_count, answer_count, view_count, follower_count, like_count, comment_count, created_time, updated_time, creator{name,url_token}`。
- 条目数组每项：`{created: 收藏时间(ISO 带时区，如 "2026-09-09T02:56:21+08:00"), content: <内容对象>}`。
- `content` 按 `type` 区分：`answer`（含 `question{id,title}`、`content` HTML、`voteup_count`、`comment_count`、`created_time/updated_time`、`author{name,url_token}`）、`article`（含 `title`）、`pin`（想法，通常无 `title`）。字段 `is_collapsed/is_deleted` 标记折叠/删除。
- `paging.totals` / `paging.is_end` 分页；`limit` 上限 20。

### 排序与增量（实测结论，重要）

1. **条目接口排序稳定但非严格按收藏时间**：同一时刻两次 `offset=0` 返回完全相同的 id 序列；但中部/尾部存在时间倒挂（如 2018/2023 年的条目混在 2026 年区间内），不能按时间戳截断。
2. **新收藏追加在头部**，删除条目不影响剩余条目的相对顺序 → 增量更新用「从头部翻页，连续遇到 N 条已入库条目即停」的边界策略；只有 `--full` 全量扫描才能可靠标记 removed。
3. **`totals` 可能大于 API 实际返回数**：实测 1335 的收藏夹只返回 1296 条（已删除/被折叠/私密内容接口不返回），如实说明差额。
4. `/collection/`（带斜杠）302 到 `/collection/hot`（热门收藏夹公共页）；**用户自己的收藏夹列表必须走 `/api/v4/people/{token}/collections`**；`/api/v4/me/collections` 不存在（404）。
5. HTML→纯文本清洗与回答接口相同（删 `img,figure`、`br`→`\n`、块级元素追加 `\n`、`innerText` 去残留标签、合并空行）。

建议直接用 `scripts/fetch_zhihu_collections.py`（list / fetch 两个子命令，SQLite 持久化 + 增量 + JSON 导出），勿手写分页循环。

---

## 四、单条内容接口（回答 / 文章 / 想法）

### 回答（单条）

```
GET https://www.zhihu.com/api/v4/answers/{aid}?include=content,author,voteup_count,comment_count,question,created_time,updated_time,excerpt
```

- **不带 `include=content` 时响应里没有 content 字段**（只有标题/作者/计数等）。
- `include` 值需 URL 编码（encodeURIComponent 整段）。
- 返回：`content`（HTML）、`question{id,title}`、`author{name,url_token}`、`voteup_count`、`comment_count`、`created_time/updated_time`。

### 文章

```
GET https://zhuanlan.zhihu.com/api/articles/{arid}
```

- **`www.zhihu.com/api/v4/articles/{id}` 一律 403**（`{"code":10003,"message":"请求参数异常，请升级客户端后重试。"}`），任何 include 变体都不行。
- 必须走 `zhuanlan.zhihu.com` 域（从 www.zhihu.com 页面跨域 fetch 带登录态实测 200）。
- 返回：`title`、`content`（HTML）、`author`、`voteup_count`、`comment_count`、`created_time/updated_time`、`url`。

### 想法（pin）

```
GET https://www.zhihu.com/api/v4/pins/{pid}?include=content,content_html,author,like_count,comment_count,created,updated,excerpt_title
```

- 正文在 **`content_html`**；`content` 是对象数组（`[{title,content},...]`，含图片等富文本节点），`content_html` 为空时可自行拼接数组项的 `content/text`。
- 点赞字段是 `like_count`（不是 voteup_count）；标题在 `excerpt_title`（含 HTML，需清洗）。

建议直接用 `scripts/fetch_zhihu_content.py`（链接自动识别类型）。

---

## 五、热榜接口

```
GET https://www.zhihu.com/api/v3/feed/topstory/hot-lists/{type}?limit=N&desktop=true
```

- 注意是 **v3** 路径；`limit` 低于 30 会被忽略（固定返回 30 条），实测 30/50 可用。
- `type` 实测可用：`total`(全站) `digital` `science` `sports` `finance` `film` `campus` `news` `game` `music` `fashion`。
- 条目 `data[*]`（`type=hot_list_feed`）：`detail_text`（热度文本，如 "1065 万热度"）、`trend`（up/down/same，部分为空）、`target{id,title,url,answer_count,follower_count,excerpt}`。

建议直接用 `scripts/fetch_zhihu_hotlist.py`（--type / --all）。
