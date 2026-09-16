# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在用户真实浏览器的知乎页面内调用知乎官方搜索接口，
搜索关键词并提取相关问题清单，保存为 JSON。

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session），使页内 fetch 同源、带登录态。

用法：
  python search_zhihu.py --query "codex" --out questions.json
  python search_zhihu.py --query "AI编程" --type question --out q.json --pages 3
  python search_zhihu.py --query "codex" --out q.json --session zhihu-answers

说明：
  - 页内 fetch 调用 /api/v4/search_v3。
  - t=general 混合结果（回答/文章/问题）；t=question 只返回问题；t=topic 返回话题。
  - 从结果中抽取问题（直接的问题实体 + 回答所属问题），按 qid 去重。
  - 顺带提取结果中出现的话题（topic）与文章（article）。
  - 保存 {stats, questions[], topics[], articles[], other[]}。
"""
import argparse
import json
import sys
import time
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-answers"

JS_TEMPLATE = r"""
(async () => {
  const kw = '%s';
  const T = '%s';
  const OFFSET = %d;
  const LIMIT = %d;
  const url = 'https://www.zhihu.com/api/v4/search_v3?t=' + T + '&q=' + encodeURIComponent(kw) + '&correction=1&offset=' + OFFSET + '&limit=' + LIMIT;
  const r = await fetch(url, {credentials: 'include'});
  const j = await r.json();
  const clean = (s) => (s || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  const out = {isEnd: j.paging ? !!j.paging.is_end : null, total: j.paging ? (j.paging.total || null) : null, items: []};
  (j.data || []).forEach(d => {
    const o = d.object || d;
    if (!o || !o.type) return;
    if (o.type === 'question') {
      out.items.push({kind: 'question', qid: o.id, title: clean(o.title), answer_count: o.answer_count, follower_count: o.follower_count, excerpt: clean(o.excerpt || o.detail)});
    } else if (o.type === 'answer') {
      const q = o.question || {};
      out.items.push({kind: 'answer', qid: q.id, title: clean(q.title || o.title), answer_id: o.id, vote: o.voteup_count, answer_count: o.answer_count, author: o.author ? o.author.name : '', excerpt: clean(o.excerpt || o.content).slice(0, 160)});
    } else if (o.type === 'article') {
      out.items.push({kind: 'article', title: clean(o.title || o.name), article_id: o.id, author: o.author ? o.author.name : '', excerpt: clean(o.excerpt).slice(0, 160)});
    } else if (o.type === 'topic') {
      out.items.push({kind: 'topic', topic_id: o.id, title: clean(o.name), follower_count: o.followers_count, excerpt: clean(o.excerpt).slice(0, 160)});
    } else {
      out.items.push({kind: o.type, title: clean(o.name || o.title)});
    }
  });
  return JSON.stringify(out);
})()
"""


def run_js(base, session, code, timeout=120):
    body = json.dumps({"action": "evaluate", "args": {"code": code}, "session": session}).encode("utf-8")
    req = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError("daemon error: %s" % raw[:500])
    return json.loads(data["data"]["value"])


def js_escape(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def main():
    ap = argparse.ArgumentParser(description="知乎搜索并提取相关问题清单")
    ap.add_argument("--query", required=True, help="搜索关键词")
    ap.add_argument("--type", default="general", choices=["general", "question", "topic", "answer", "article"], help="搜索类型（默认 general 混合）")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--pages", type=int, default=3, help="最多翻几页（默认3）")
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--limit", type=int, default=10, help="每页条数（默认10）")
    args = ap.parse_args()

    limit = min(max(args.limit, 1), 20)
    items_all = []
    is_end = None
    for page in range(args.pages):
        offset = page * limit
        code = JS_TEMPLATE % (js_escape(args.query), args.type, offset, limit)
        try:
            val = run_js(args.base, args.session, code)
        except Exception as e:
            print("offset %d failed: %s, retry once" % (offset, e), file=sys.stderr)
            time.sleep(2)
            try:
                val = run_js(args.base, args.session, code)
            except Exception as e2:
                print("offset %d retry failed: %s" % (offset, e2), file=sys.stderr)
                break
        items = val.get("items") or []
        is_end = val.get("isEnd")
        print("offset %d -> %d items, isEnd=%s" % (offset, len(items), is_end))
        if not items:
            break
        items_all.extend(items)
        if is_end:
            break
        time.sleep(0.4)

    # 聚合问题清单：直接问题实体 + 回答所属问题，按 qid 去重
    qmap = {}
    articles = []
    topics = []
    other = []
    for it in items_all:
        if it["kind"] in ("question", "answer"):
            qid = it.get("qid")
            if not qid:
                continue
            q = qmap.setdefault(str(qid), {"qid": str(qid), "title": "", "search_hits": 0,
                                           "answer_count": None, "follower_count": None,
                                           "best_vote": 0, "sample_author": "", "sample_excerpt": ""})
            q["search_hits"] += 1
            if not q["title"] and it.get("title"):
                q["title"] = it["title"]
            if it.get("answer_count") is not None:
                q["answer_count"] = it["answer_count"]
            if it.get("follower_count") is not None:
                q["follower_count"] = it["follower_count"]
            if it["kind"] == "answer":
                v = it.get("vote") or 0
                if v > q["best_vote"]:
                    q["best_vote"] = v
                    q["sample_author"] = it.get("author", "")
                    q["sample_excerpt"] = it.get("excerpt", "")
        elif it["kind"] == "article":
            articles.append({k: it.get(k, "") for k in ("title", "article_id", "author", "excerpt")})
        elif it["kind"] == "topic":
            topics.append({"topic_id": it.get("topic_id"), "name": it.get("title", ""),
                           "follower_count": it.get("follower_count"), "excerpt": it.get("excerpt", "")})
        else:
            other.append(it)

    questions = sorted(qmap.values(), key=lambda q: q["best_vote"], reverse=True)

    stats = {
        "query": args.query,
        "type": args.type,
        "search_items": len(items_all),
        "questions": len(questions),
        "topics": len(topics),
        "articles": len(articles),
        "other": len(other),
        "is_end": is_end,
    }
    result = {"stats": stats, "questions": questions, "topics": topics, "articles": articles, "other": other}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print("STATS: 搜索条目 %d | 去重问题 %d | 话题 %d | 文章 %d | 其他 %d" % (len(items_all), len(questions), len(topics), len(articles), len(other)))
    print("Top 问题：")
    for q in questions[:10]:
        print("  [%s] 赞%d 命中%d  %s" % (q["qid"], q["best_vote"], q["search_hits"], q["title"][:40]))
    print("saved -> %s" % args.out)


if __name__ == "__main__":
    main()
