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
- `sort_by=default`（默认/综合排序）；**`sort_by=created` 实测生效**，严格按创建时间倒序，适合增量刷新某题新回答

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

---

## 六、作者维度接口（按人采集全部作品，2026-10 实测）

```
GET https://www.zhihu.com/api/v4/members/{url_token}/answers?limit=&offset=&sort_by=created
GET https://www.zhihu.com/api/v4/members/{url_token}/articles?limit=&offset=
GET https://www.zhihu.com/api/v4/members/{url_token}/pins?limit=&offset=
GET https://www.zhihu.com/api/v4/members/{url_token}/questions?limit=&offset=
GET https://www.zhihu.com/api/v4/members/{url_token}                        # 资料（include 控制）
GET https://www.zhihu.com/api/v4/members/{url_token}/followers|followees|relations/mutuals
```

- **include 口径与第二节相反**：members 族用 `data[*].content` 括号形式**有效**（answers 实测直接返回全文，totals=1514 场景验证）；裸字段名形式在此族无效。
- 列表项**无 `voteup_count`/`comment_count`**：赞同数在 `reaction.statistics.like_count`；评论数不在列表（问题侧计数走 `/api/v4/answers/{aid}/voters` 的 totals）。
- 单页返回条数可能多于 `limit`，且相邻 offset 有重叠 → 必须按 id 去重。
- articles 列表项含 `title/excerpt/created/url/image_url/author`，**无正文**；正文拿 article id 走 `zhuanlan.zhihu.com/api/articles/{id}`（见第四节）。
- pins 列表项含 `like_count/comment_count/created/excerpt_title`；正文在 `content`（对象数组，拼接各项 content/text/title）。
- questions 列表项含 `id/title/created/updated_time`。
- 404 的猜测路径（勿再试）：`/members/{token}/videos|favours|columns|upvotes|creation`。

建议直接用 `scripts/fetch_zhihu_member_works.py`（--type answers|articles|pins|questions，--no-content 只出清单）。

---

## 七、问题上下文接口（问题页真实在调用，2026-10 实测）

```
GET https://www.zhihu.com/api/v4/questions/{qid}/similar-questions?include=data[*].answer_count,data[*].follower_count&limit=20
GET https://www.zhihu.com/api/v4/questions/{qid}/followers?limit=&offset=     # 关注该问题的人；paging.totals 可用
GET https://www.zhihu.com/api/v4/search/hot_search?content_type=question&content_token={qid}   # 该题"大家都在搜"
GET https://www.zhihu.com/api/v4/answers/{aid}/voters?limit=&offset=          # 点赞该回答的人；totals=点赞人数
GET https://www.zhihu.com/api/v4/answers/{aid}/recommendations?limit=         # 该答下的"相关阅读"推荐
GET https://www.zhihu.com/api/v4/answers/{aid}/labels/v3                      # 官方认证 endorsement（多为空数组）
GET https://www.zhihu.com/api/v4/questions/{qid}/inviters                     # 邀请人（常为空）
```

- 404 的错误猜测（勿再试）：`top-writers`、`related-question-feed`、`similar_questions`（下划线版）；`/api/v3/questions/{qid}` 返回 HTML 非 JSON。
- **合规**：followers/voters 是真实用户列表——只做计数/首屏样本，不做批量个人画像采集。

建议直接用 `scripts/fetch_zhihu_question_context.py`（一次并发 gather 全部）。

---

## 八、推荐流与搜索辅助（2026-10 实测）

```
GET https://www.zhihu.com/api/v3/feed/topstory/recommend?limit=&offset=&desktop=true   # 首页推荐（个性化，随账号而异）
GET https://www.zhihu.com/api/v4/search/suggest?q={关键词}                              # 搜索联想词，suggest[*].query
GET https://www.zhihu.com/api/v4/search_v3?t=people|zazhis|place&q=...                 # 垂类搜索（t=video 通但常空）
GET https://www.zhihu.com/api/v4/roundtables?limit=&offset=                             # 全站圆桌清单（实测 totals=2427）；单圆桌详情 /roundtables/{url_token} 200；其下问题列表路径未验证
```

- recommend 条目字段：`id/type/verb/created_time/target/brief`；每页实际返回条数由服务端定（可少于 limit），以 `paging.is_end`/空页为停止。
- 建议用 `scripts/fetch_zhihu_feed.py`；suggest 用 `search_zhihu.py --suggest`。

---

## 九、评论读接口 v5 命名空间（2026-10 实测）

```
GET https://www.zhihu.com/api/v4/comment_v5/{type}s/{id}/root_comment?order_by=score|time&limit=20&offset=   # 一级评论
GET https://www.zhihu.com/api/v4/comment_v5/comment/{root_cid}/child_comment                                 # 楼中楼（源码，未实测）
GET https://www.zhihu.com/api/v4/comment_v5/{type}s/{id}/permission                                          # 该内容谁能评论（all/…）
GET https://www.zhihu.com/api/v4/comment_v5/{type}s/permission                                               # 该类型能否评论
GET https://www.zhihu.com/api/v4/collections/contents/{type}/{id}?offset=0&limit=5                           # 该内容被收藏进哪些夹（is_favorited）
```

- `{type}` ∈ answers/questions/articles（均实测 200）。老路径 `/api/v4/answers/{aid}/root_comments?order=by_vote` **仍可用**（返回 `featured_counts/common_counts/collapsed_counts/reviewing_counts`）；v5 版分页参数名是 `order_by`（不是 `order`），额外给 `counts`、`edit_status`、`ad_plugin_infos`。
- 评论**删除**端点在 v4 老路由 `DELETE /api/v4/comments/{cid}`；`/comment_v5/comments/{cid}`、`/comment_v5/answers/{aid}/comment/{cid}`、`…/root_comment/{cid}`、`…/comments/{cid}/delete` 全部 404，`…/root_comment` 只支持 GET（POST 得 405）。

---

## 十、写接口（赞同/喜欢/收藏/评论，2026-10 实测，含撤销配对）

**均为页内 fetch 直接可用，只带 cookie + `x-xsrftoken`（= cookie 里的 `_xsrf`）+ `x-requested-with: fetch`，不校验 `x-zse-93/96` 签名。**

| 动作 | 请求 | body | 成功响应 |
|---|---|---|---|
| 赞同 | `POST /api/v4/answers/{aid}/voters` | `{"type":"up"}` | `reaction_count:+1, reaction_value:"up", is_upped:true` |
| 取消赞同 | 同上 | `{"type":"neutral"}` | `reaction_count:0, reaction_state:false` |
| 喜欢(感谢) | `POST /api/v4/answers/{aid}/thankers` | 空 | `is_thanked:true, thanks_count:1, red_heart_count:1` |
| 取消喜欢 | `DELETE /api/v4/answers/{aid}/thankers` | 无 | `is_thanked:false, thanks_count:0` |
| 收藏（默认夹） | `POST /api/v4/collections/contents/{type}/{id}` | 空 | `{collection:{id,title:"我的收藏",is_default:true}, favlists_count:1, success:true}` |
| 移出收藏 | `DELETE /api/v4/collections/{cid}/contents/{id}?content_type={type}` | 无 | `{success:true}` |
| 发评论 | `POST /api/v4/comment_v5/answers/{aid}/comment` | `{"content":"文本"}`（明文即可） | 完整评论对象：`id/content/url/can_delete/is_author` |
| 删评论 | `DELETE /api/v4/comments/{cid}` | 无 | **204 空体** |
| 评论点赞 | `POST` / `DELETE /api/v4/comments/{cid}/like` | — | 源码确认存在，未真实写入验证 |
| 存草稿 | `POST` \| `PUT /api/v4/questions/{qid}/draft` | `{"content":"<p>…</p>","delta_time":N,"draft_type":"normal","attachment":null,"settings":{…}}` | 完整 draft 对象（`created_time/content/excerpt/editable_content/settings/answer_type`） |
| 读草稿 | `GET /api/v4/questions/{qid}/draft?include=question,schedule` | — | draft 对象；**无草稿时返回 `{}`（仍 200）** |
| 删草稿 | `DELETE /api/v4/questions/{qid}/draft` | 无 | `{success:true}` |
| 草稿计数 | `GET /api/v4/answer-drafts/count` | — | `{count:N, scheduled_count:N}` |
| 草稿历史 | `GET /api/v4/draft-histories?object_type=question&object_id={qid}` | — | 分页列表；源码另有 `GET draft-history?versionType&id`、`POST draft-history/revert {version_type,id}`（未实测） |
| **发布回答** | `POST /api/v4/content/publish` | `{"action":"answer","data":{…}}`（见下） | `{code,message,toast_message,data}` |
| 删除回答 | `DELETE /api/v4/answers/{aid}` | 无 | `{success:true}`（软删；随后 `GET /api/v4/answers/{aid}` → 404 `code:4041`） |
| 恢复回答 | `POST /api/v4/answers/{aid}/actions/restore` | 无 | 200 + 完整回答对象（**实测可复原**：`GET` 回 200、该题回答数 +1；会重新公开） |
| 关注/取消关注问题 | `POST` \| `DELETE /api/v4/questions/{qid}/followers` | 无 | 204 空体（发布回答会自动关注）；回查 `GET …/followers?offset=0&limit=5` → `paging.totals`（取消后回 0） |

### 发布回答 body 结构（`POST /api/v4/content/publish`，2026-10-01 实测）

```json
{"action": "answer",
 "data": {
   "publish": {"traceId": "<毫秒时间戳>,<uuid>"},
   "hybridInfo": {}, "draft": {"isPublished": false, "disabled": 1},
   "extra_info": {"question_id": "<qid>", "publisher": "pc",
                  "include": "content,editable_content,…",
                  "pc_business_params": "<下面 settings 再 JSON.stringify 一次的字符串>"},
   "hybrid": {"html": "<p>正文</p>", "textLength": 2},
   "reprint": {"reshipment_settings": "allowed"},
   "commentsPermission": {"comment_permission": "all"},
   "appreciate": {"can_reward": false, "tagline": ""},
   "publishSwitch": {"draft_type": "normal"},
   "creationStatement": {"disclaimer_status": "close", "disclaimer_type": "none"},
   "commercialReportInfo": {"isReport": 0},
   "toFollower": {}, "contentsTables": {"table_of_contents_enabled": false},
   "thanksInvitation": {"thank_inviter_status": "close", "thank_inviter": ""}}}
```

`pc_business_params` 内容：`{reshipment_settings, comment_permission, columns, reward_setting:{can_reward,tagline}, disclaimer_status, disclaimer_type, commercial_report_info:{is_report}, commercial_zhitask_bind_info, is_report, push_activity, table_of_contents_enabled, thank_inviter_status, thank_inviter}`。


要点：

1. **voters 与 thankers 是两套独立计数**（赞同 vs 喜欢/感谢），但两者的响应都会返回**全套 reaction 状态**（`is_up/is_liked/is_thanked/voting/voteup_count/up_count/heavy_up_result`），可直接当"我的反应"查询接口用，省一次请求。
2. **评论 body 明文可重放**：网页 UI 发的 body 是密文（源码 `fetchOptions:{zsEncrypt:!0}`），但接口本身接受 `{"content":"..."}`。用裸字符串当 body 会得到 `400 invalid character 'p' looking for beginning of value`（说明它按 JSON 解析）。
3. **赞同刚发出时 `heavy_up_result` 会是 `"reviewing"`**（进审核态），`reaction_count` 已 +1；随后自行落定。
4. 错误口径：内容不存在 → `422 {code:4000,"参数异常，无法获取内容"}`（voters/thankers）、`400 {评论区已关闭}`（comment）、`404 {code:4041,"资源不存在"}`（comments/{cid}）；重复收藏 → `403 {code:106,"您已经收藏过该内容"}`。**没有出现签名类 403/风控**。
5. `POST /api/v4/collections/contents/{type}/{id}` 无 body ⇒ 只能进**默认收藏夹**；返回体里的 `collection.id` 就是后续移出要用的 `{cid}`。指定收藏夹的形态未探明（需要第二个收藏夹才能测）。
6. **UI 侧的对应坑**（若要改用真实点击而非 API）：`已收藏` 按钮再点只重发 POST 得 403，真正的移入口在"添加收藏"弹窗的行内按钮；评论编辑器的"发布"按钮 `.Comments-container button.Button--primary` 只在**真实键盘输入**后出现，用 `fill` 直接写 innerText 不会点亮（Draft.js 内部 state 不同步）→ 正确顺序是 `evaluate` focus + 选区 → `send_keys Control+a/Delete` 清空 → `key_type` 逐字输入 → 点发布。

7. **发布口不在 `questions/{qid}/answers`**：`POST /api/v4/questions/{qid}/answers` 恒 `422 UnprocessableError {code:4000,"请求错误"}`（路由存在但非发布入口）；真入口是 `POST /api/v4/content/publish`（`action` 区分 `answer`/其他内容类型）。UI 在发布前会先 GET `api.zhihu.com/content/publish/control/v2?scene=answer&questionToken={qid}` 与 `…/publish/content_source/config`（风控/权限探针，纯接口发布不需要）。
8. **发布成功与否看 `toast_message`，不能只看 HTTP**：三种情况都返回 200 —— 成功（`toast_message:""`）、业务失败（如 `"已回答过该问题，创建答案失败"`）。且成功响应里**不含回答 ID**，要用 `GET /api/v4/questions/{qid}/answers?include=author` 按本人 `url_token` 反查（`zhihu_write_ops.py publish` 已内置这步）。
9. **软删除不解除"已回答过"判定**：`DELETE /api/v4/answers/{aid}` 后回答对外 404、列表不再返回，但同题再发仍被 toast 拦下；恢复用 `POST /api/v4/answers/{aid}/actions/restore`（会重新公开，2026-10-01 已实测可用）。发布自动关注问题（关注数 +1），做净零需补 `DELETE /api/v4/questions/{qid}/followers`。净零复核这三条读数最可靠：`GET /api/v4/answers/{aid}`→404、`GET /api/v4/questions/{qid}/answers?include=author`→本人 aid 不在列表且 `paging.totals` 回到基线、`GET …/followers?limit=5`→`paging.totals` 回到基线；注意 `GET /api/v4/questions/{qid}` 本身对部分问题会 403 `code:10003`，别拿它当复核依据。
10. **草稿层是最安全的验证面**：`draft` 三态（存/取/删）纯接口全通、对外不可见，验证写链路应停在草稿层。UI 的自动保存与手动保存同址（`POST` 新建 / `PUT` 覆盖），body 与 UI 抓包一致即可；`GET` 无草稿时返回 `{}` 而非 404，判空要看 key。
11. **别用"页内 patch fetch 后再点发布"来安全抓包**：`Page.addScriptToEvaluateOnNewDocument` 注入的 patch 能拦到 `read_history/add`、草稿 `PUT` 等页内请求，但编辑器的发布请求绕过了它（实测仍真发布）。抓 UI 契约的可靠办法是读守护进程 `network` 的 `detail`（含完整明文 request body + 响应，且不受签名影响），发布请求的 header 里带 `x-zse-93/x-zse-96/x-zst-81` 但**去掉后纯接口回放照样 200**。

### 合规与风险（写接口必读）

- 写操作会**给真实作者产生可见通知与计数变化**；评论删除前对方可能已收到推送。**每个写动作都要用户明确授权，一次只动一条**，不做批量刷赞/刷收藏。
- 首选**测试目标**：别人提问的冷门（赞 0 评 0）回答/问题；**不要拿用户自己过去的提问当靶子**。操作后按原样撤销，并用 `status` / `draft --get` / 回答列表复核净零（`vote/comments/thanks=0`、`favorited=[]`、回答 `totals` 与关注数回到操作前）。
- **测试正文用"你好"这类中性无意义文本**；写"接口验证""爬虫测试""稍后删除"等字样容易被平台判违规或被他人举报。
- 对外可见度分层：`draft`（仅本人可见）< `vote/like/collect/comment`（作者收到通知）< `publish/delete-answer`（直接改公开内容）。能在低层验证清楚的，不要升到高层。
- 高频写最易触发账号风控，串行执行、操作间隔 ≥1s。

建议直接用 `scripts/zhihu_write_ops.py`（默认 dry-run，`--yes` 才发送；`status` 子命令做只读复核）。
