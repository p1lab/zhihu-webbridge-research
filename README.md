# zhihu-webbridge-research

知乎调研采集 —— 用 **Kimi WebBridge 驱动真实浏览器（复用登录态）**，在页面内**同源 `fetch` 调知乎官方 JSON API**（无签名），采集：搜索相关问题、问题下全部回答、回答评论（含楼中楼）、收藏夹（SQLite 持久化 + 增量）、单条回答/文章/想法、热榜；并本地做主题聚类报告。

> 面向个人研究/学习。非官方，依赖接口结构，改版可能失效。

## 前置依赖
- **[Kimi WebBridge](https://www.kimi.com/products/kimi-webbridge)** 守护进程（`127.0.0.1:10086`）+ 浏览器扩展（本工具不自带）。
- 一个**已登录知乎的真实浏览器**标签页（同源 fetch 依赖登录 cookie）。
- Python 3（标准库）。

## 设计要点
- **不要靠滚动加载**（程序化滚动刷不出全部回答）；走 `www.zhihu.com/api/v4/...` 同源接口分页（`api.zhihu.com` 跨域不用）。
- **并发在一次 evaluate 内 `Promise.all` 多页**（daemon 命令单通道串行，页内并发才提速）。
- 文章必须走 `zhuanlan.zhihu.com/api/articles/{id}`（`api/v4/articles` 403）；想法正文在 `content_html`。

## 能力（scripts，各自可独立跑）
| 脚本 | 作用 |
|---|---|
| `search_zhihu.py` | 关键词搜索→问题/话题/文章清单（含 answer_count） |
| `fetch_zhihu_answers.py` | 某问题全部回答（分页到 is_end，含富字段 IP/感谢/作者身份，`--concurrency` 页内并发） |
| `fetch_zhihu_comments.py` | 某回答评论含楼中楼（`root_comments`，`--order`、`--concurrency`） |
| `fetch_zhihu_content.py` | 单条 回答/文章/想法全文 |
| `fetch_zhihu_collections.py` | 收藏夹列表/条目 → SQLite 持久化 + 增量（含 thanks_count） |
| `fetch_zhihu_hotlist.py` | 热榜（全站/分类） |
| `analyze_collections.py` / `gen_answers_report.py` / `research_register.py` | 主题聚类报告 / 调研报告生成 / 成果归档 |

## 目录
```
SKILL.md  references/{zhihu-api.md, webbridge-operations.md}  scripts/*.py
```

## 合规
仅个人研究/学习；遵守知乎 ToS 与 robots；内容为社区用户原创观点、不背书真实性；不得用于商业/批量抓取；收藏夹为**你本人**数据。抓取产物（`*.db`、`*.json`、`*.html`）被 `.gitignore` 排除、不入库。

## 许可
MIT（见 LICENSE）。
