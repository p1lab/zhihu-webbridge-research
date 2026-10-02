# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在知乎页面内调用 v3 推荐流接口，
抓取首页"推荐"信息流条目并保存为 JSON。

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session）。

用法：
  python fetch_zhihu_feed.py --out feed.json [--pages 5] [--limit 20]

说明：
  - 端点 /api/v3/feed/topstory/recommend（注意是 v3），按 offset 翻页。
  - 用途：观察"知乎今天给我推荐了什么"、监控某话题是否进入推荐流。
  - 推荐流随登录态个性化，不同账号结果不同，属预期。
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
  const OFFSET = %d;
  const LIMIT = %d;
  const clean = (s) => (s || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  const r = await fetch('https://www.zhihu.com/api/v3/feed/topstory/recommend?limit=' + LIMIT + '&offset=' + OFFSET + '&desktop=true', {credentials: 'include'});
  const j = await r.json();
  const items = (j.data || []).map(d => {
    const t = d.target || {};
    return {type: d.type, verb: d.verb, feed_id: String(d.id || ''), created: d.created_time,
      content_type: t.type || '', cid: String(t.id || ''), title: clean(t.title || (t.question && t.question.title) || ''),
      author: t.author ? t.author.name : '', vote: t.voteup_count,
      qid: t.question ? String(t.question.id) : '', brief: clean((d.brief || {}).content)};
  });
  return JSON.stringify({isEnd: j.paging ? !!j.paging.is_end : null, items});
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


def main():
    ap = argparse.ArgumentParser(description="抓取知乎推荐流")
    ap.add_argument("--out", required=True)
    ap.add_argument("--pages", type=int, default=5)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    items_all = []
    is_end = None
    for page in range(args.pages):
        offset = page * args.limit
        try:
            val = run_js(args.base, args.session, JS_TEMPLATE % (offset, args.limit))
        except Exception as e:
            print("offset %d failed: %s" % (offset, e), file=sys.stderr)
            break
        is_end = val.get("isEnd")
        got = val.get("items") or []
        print("offset %d -> %d 条, isEnd=%s" % (offset, len(got), is_end))
        if not got:
            break
        items_all.extend(got)
        if is_end:
            break
        time.sleep(0.4)

    seen = set()
    uniq = []
    for it in items_all:
        k = it.get("cid") or it.get("feed_id")
        if k and k not in seen:
            seen.add(k)
            uniq.append(it)

    result = {"stats": {"collected": len(uniq), "is_end": is_end}, "feed": uniq}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print("TOTAL feed: %d | saved -> %s" % (len(uniq), args.out))


if __name__ == "__main__":
    main()
