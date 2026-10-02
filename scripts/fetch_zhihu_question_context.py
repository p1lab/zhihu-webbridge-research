# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在知乎页面内一次性抓取某问题的"周边上下文"：
相关问题(similar-questions)、关注人数(followers 计数+首屏)、关联热搜(hot_search)。

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session）。

用法：
  python fetch_zhihu_question_context.py --qid 443161370 --out ctx.json
  python fetch_zhihu_question_context.py --url "https://www.zhihu.com/question/443161370" --out ctx.json
  python fetch_zhihu_question_context.py --qid ... --out ... --aid <answer_id> --with-voters

合规围栏：
  - followers/voters 返回的是真实用户列表。本脚本默认只保留"关注人数"计数与首屏昵称
    （供问题热度参考），**不做批量个人画像采集**；请勿改写为全量翻页拉取。
"""
import argparse
import json
import re
import sys
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-answers"

JS_TEMPLATE = r"""
(async () => {
  const qid = '%s';
  const aid = '%s';
  const clean = (s) => (s || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  const gj = async (u) => { try { const r = await fetch(u, {credentials: 'include'}); return await r.json(); } catch (e) { return {_err: e.message}; } };
  const tasks = [
    gj('https://www.zhihu.com/api/v4/questions/' + qid + '/similar-questions?include=' + encodeURIComponent('data[*].answer_count,data[*].follower_count') + '&limit=20&offset=0'),
    gj('https://www.zhihu.com/api/v4/questions/' + qid + '/followers?limit=20&offset=0'),
    gj('https://www.zhihu.com/api/v4/search/hot_search?content_type=question&content_token=' + qid)
  ];
  if (aid) tasks.push(gj('https://www.zhihu.com/api/v4/answers/' + aid + '/voters?limit=1&offset=0'));
  const [sim, fol, hot, vot] = await Promise.all(tasks);
  const out = {qid: qid,
    similar_questions: (sim.data || []).map(d => ({qid: String(d.id), title: clean(d.title), answer_count: d.answer_count, follower_count: d.follower_count})),
    follower_count: fol.paging ? (fol.paging.totals || fol.paging.total || null) : null,
    follower_sample: (fol.data || []).map(d => d.name),
    hot_search: (hot.hot_search_queries || []).map(h => ({query: h.query, hot: h.hot, hot_show: h.hot_show, label: h.label}))};
  if (aid) { out.voter_count_of_aid = vot && vot.paging ? (vot.paging.totals || vot.paging.total || null) : null; out.aid = aid; }
  if (sim._err || fol._err || hot._err) out.errors = [sim._err, fol._err, hot._err].filter(Boolean);
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


def extract_qid(url_or_qid):
    m = re.search(r"/question/(\d+)", url_or_qid)
    if m:
        return m.group(1)
    if url_or_qid.isdigit():
        return url_or_qid
    raise SystemExit("无法从输入解析问题ID: %s" % url_or_qid)


def main():
    ap = argparse.ArgumentParser(description="抓取知乎问题的上下文（相关问题/关注数/关联热搜）")
    ap.add_argument("--qid", help="问题ID 或 /question/{id} 链接")
    ap.add_argument("--url", help="问题链接（二选一）")
    ap.add_argument("--aid", help="可选：某回答ID，配合 --with-voters 取点赞数")
    ap.add_argument("--with-voters", action="store_true", help="附带某条回答的点赞人数计数")
    ap.add_argument("--out", required=True)
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    qid = extract_qid(args.url or args.qid)
    aid = args.aid if (args.with_voters and args.aid) else ""
    if args.with_voters and not args.aid:
        raise SystemExit("--with-voters 需要同时提供 --aid")

    ctx = run_js(args.base, args.session, JS_TEMPLATE % (qid, aid))
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(ctx, f, ensure_ascii=False, indent=1)

    print("qid=%s 相关问题 %d | 关注数 %s | 关联热搜 %d%s" % (
        qid, len(ctx.get("similar_questions", [])), ctx.get("follower_count"),
        len(ctx.get("hot_search", [])),
        (" | 回答%s点赞数 %s" % (aid, ctx.get("voter_count_of_aid"))) if aid else ""))
    print("saved -> %s" % args.out)


if __name__ == "__main__":
    main()
