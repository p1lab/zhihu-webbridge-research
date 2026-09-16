# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在用户真实浏览器的知乎页面内调用知乎官方热榜接口，
抓取热榜（全站/分类）并保存为 JSON。

接口（页内 fetch，自动携带登录态）：
  GET https://www.zhihu.com/api/v3/feed/topstory/hot-lists/{type}?limit=N&desktop=true

type 可选（已实测 200）：total(全站) digital(数码) science(科学) sports(体育) finance(财经)
  film(影视) campus(校园) news(时事) game(游戏) music(音乐) fashion(时尚)

条目字段：
  - rank: 名次（按返回顺序）
  - detail_text: 热度文本（如 "1065 万热度"）
  - trend: 热度趋势（up/down/same）
  - target: {id(问题ID), title, answer_count, follower_count, excerpt}
  - url: 问题链接

用法：
  python fetch_zhihu_hotlist.py --out hot.json
  python fetch_zhihu_hotlist.py --type digital --limit 30 --out hot_digital.json
  python fetch_zhihu_hotlist.py --all --out-dir ./hot
"""
import argparse
import json
import os
import sys
import time
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-hotlist"

HOT_TYPES = ["total", "digital", "science", "sports", "finance", "film",
             "campus", "news", "game", "music", "fashion"]

JS_HOTLIST = r"""
(async () => {
  const TYPE = '%s';
  const LIMIT = %d;
  const url = 'https://www.zhihu.com/api/v3/feed/topstory/hot-lists/' + TYPE + '?limit=' + LIMIT + '&desktop=true';
  const r = await fetch(url, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status, items: []});
  const j = await r.json();
  const clean = (s) => (s || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  const out = {status: 200, items: []};
  let rank = 0;
  (j.data || []).forEach(it => {
    if (!it || it.type !== 'hot_list_feed') return;
    const t = it.target || {};
    rank += 1;
    out.items.push({rank: rank, heat: it.detail_text || '', trend: it.trend || '',
      qid: t.id || null, title: clean(t.title), url: t.url || '',
      answer_count: t.answer_count, follower_count: t.follower_count,
      excerpt: clean(t.excerpt).slice(0, 160)});
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


def fetch_one(base, session, htype, limit):
    code = JS_HOTLIST % (htype, limit)
    try:
        return run_js(base, session, code)
    except Exception as e:
        print("%s 抓取失败: %s, retry once" % (htype, e), file=sys.stderr)
        time.sleep(2)
        return run_js(base, session, code)


def main():
    ap = argparse.ArgumentParser(description="抓取知乎热榜（全站/分类）")
    ap.add_argument("--type", default="total", help="热榜类型（默认 total；见脚本头注释）")
    ap.add_argument("--limit", type=int, default=50, help="条数上限（默认50）")
    ap.add_argument("--out", help="输出 JSON 路径（单类型时）")
    ap.add_argument("--all", action="store_true", help="抓取全部类型，按类型分别存到 --out-dir")
    ap.add_argument("--out-dir", default=".", help="--all 模式输出目录")
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    limit = min(max(args.limit, 1), 100)

    if args.all:
        os.makedirs(args.out_dir, exist_ok=True)
        for htype in HOT_TYPES:
            val = fetch_one(args.base, args.session, htype, limit)
            path = os.path.join(args.out_dir, "hot_%s.json" % htype)
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"type": htype, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                           "count": len(val.get("items") or []), "items": val.get("items") or []},
                          f, ensure_ascii=False, indent=1)
            print("[%s] %d 条 -> %s" % (htype, len(val.get("items") or []), path))
            time.sleep(0.3)
        return

    if not args.out:
        raise SystemExit("单类型模式需要 --out 指定输出路径")
    val = fetch_one(args.base, args.session, args.type, limit)
    if val.get("status") != 200:
        raise SystemExit("热榜接口 status=%s（类型 %s 可能无效）" % (val.get("status"), args.type))
    items = val.get("items") or []
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"type": args.type, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "count": len(items), "items": items}, f, ensure_ascii=False, indent=1)
    print("热榜[%s] %d 条 -> %s" % (args.type, len(items), args.out))
    print("Top 10:")
    for it in items[:10]:
        print("  #%d %s [%s/%s] %s" % (it["rank"], it["heat"], it["trend"], it["answer_count"], (it["title"] or "")[:40]))


if __name__ == "__main__":
    main()
