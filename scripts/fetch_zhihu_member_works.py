# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在知乎页面内调用 members 族接口，
分页抓取某个作者的全部回答/文章/想法/提问，保存为 JSON。

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session），使页内 fetch 同源、带登录态。

用法：
  python fetch_zhihu_member_works.py --token <url_token> --type answers --out answers.json
  python fetch_zhihu_member_works.py --url "https://www.zhihu.com/people/<url_token>" --type articles --out art.json
  python fetch_zhihu_member_works.py --token <tok> --type answers --no-content --out list.json

说明：
  - members 族接口的 include 必须用 `data[*].字段` 括号形式（与 questions/{qid}/answers 的
    裸字段名要求相反），实测 `data[*].content` 可直接返回回答全文。
  - answers 按 sort_by=created 抓取（严格创建时间倒序）；--no-content 只出清单（更快、更省流量）。
  - 文章正文不在列表接口里，需拿 article id 走 fetch_zhihu_content.py（zhuanlan 域）。
  - 想法正文在 content 对象数组，本脚本拼接其文本字段。
"""
import argparse
import json
import re
import sys
import time
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-answers"

JS_TEMPLATE = r"""
(async () => {
  const tok = '%s';
  const TYPE = '%s';
  const WITH_CONTENT = %s;
  const START = %d;
  const W = %d;
  const LIMIT = %d;
  const strip = (html) => {
    if (!html) return '';
    const d = document.createElement('div');
    d.innerHTML = html;
    d.querySelectorAll('img,figure').forEach(el => el.remove());
    d.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
    d.querySelectorAll('p,div,li,h1,h2,h3,h4,blockquote,pre').forEach(el => el.append('\n'));
    let t = d.innerText.replace(/<[^>]+>/g, '');
    return t.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+\n/g, '\n').trim();
  };
  const clean = (s) => (s || '').replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  const pinText = (c) => {
    if (!c) return '';
    if (typeof c === 'string') return clean(c);
    if (Array.isArray(c)) return c.map(x => (x && (x.content || x.text || x.title)) || (typeof x === 'string' ? x : '')).map(clean).filter(Boolean).join('\n');
    return '';
  };
  const mapByType = (d) => {
    if (TYPE === 'answers') return {id: d.id, question_id: d.question ? String(d.question.id) : '', question_title: d.question ? clean(d.question.title) : '', author: d.author ? d.author.name : '', author_url_token: d.author ? d.author.url_token : '', vote: d.voteup_count !== undefined ? d.voteup_count : (d.reaction && d.reaction.statistics ? d.reaction.statistics.like_count : null), comments: d.comment_count !== undefined ? d.comment_count : (d.reaction && d.reaction.statistics ? d.reaction.statistics.comment_count : null), created: d.created_time, updated: d.updated_time, collapsed: d.is_collapsed, text: WITH_CONTENT ? strip(d.content) : ''};
    if (TYPE === 'articles') return {id: d.id, title: clean(d.title), excerpt: clean(d.excerpt), author: d.author ? d.author.name : '', author_url_token: d.author ? d.author.url_token : '', created: d.created, url: d.url, image: d.image_url};
    if (TYPE === 'pins') return {id: d.id, title: clean(d.excerpt_title), like: d.like_count, comments: d.comment_count, created: d.created, author: d.author ? d.author.name : '', text: WITH_CONTENT ? pinText(d.content) : ''};
    return {id: d.id, title: clean(d.title), created: d.created, updated: d.updated_time};
  };
  const incMap = {
    answers: WITH_CONTENT ? 'data[*].content,data[*].voteup_count,data[*].comment_count,data[*].question' : 'data[*].voteup_count,data[*].comment_count,data[*].question',
    articles: 'data[*].excerpt,data[*].author',
    pins: 'data[*].content,data[*].like_count,data[*].comment_count',
    questions: ''
  };
  const url = (o) => {
    let u = 'https://www.zhihu.com/api/v4/members/' + encodeURIComponent(tok) + '/' + TYPE + '?limit=' + LIMIT + '&offset=' + o;
    if (incMap[TYPE]) u += '&include=' + encodeURIComponent(incMap[TYPE]);
    if (TYPE === 'answers') u += '&sort_by=created';
    return u;
  };
  const offs = []; for (let i = 0; i < W; i++) offs.push(START + i * LIMIT);
  const pages = await Promise.all(offs.map(o => fetch(url(o), {credentials: 'include'}).then(r => r.json())));
  return JSON.stringify(pages.map(j => ({isEnd: j.paging ? j.paging.is_end : null, total: j.paging ? (j.paging.totals || j.paging.total || null) : null, items: (j.data || []).map(mapByType)})));
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


def extract_token(url_or_token):
    m = re.search(r"/people/([^/?#]+)", url_or_token)
    if m:
        return m.group(1)
    if re.match(r"^[\w\-]+$", url_or_token):
        return url_or_token
    raise SystemExit("无法解析 url_token: %s" % url_or_token)


def main():
    ap = argparse.ArgumentParser(description="抓取知乎某作者的全部作品")
    ap.add_argument("--token", help="作者 url_token 或 /people/{token} 链接")
    ap.add_argument("--url", help="个人主页链接（二选一）")
    ap.add_argument("--type", default="answers", choices=["answers", "articles", "pins", "questions"])
    ap.add_argument("--out", required=True)
    ap.add_argument("--no-content", action="store_true", help="只出清单不抓正文（answers/pins）")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--max", type=int, default=500, help="安全上限（条）")
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    tok = extract_token(args.url or args.token)
    limit = min(max(args.limit, 1), 20)
    W = min(max(args.concurrency, 1), 10)
    with_content = "false" if args.no_content else "true"

    items_all = []
    total = None
    start = 0
    while True:
        code = JS_TEMPLATE % (tok, args.type, with_content, start, W, limit)
        try:
            pages = run_js(args.base, args.session, code)
        except Exception as e:
            print("batch@%d failed: %s, retry once" % (start, e), file=sys.stderr)
            time.sleep(2)
            try:
                pages = run_js(args.base, args.session, code)
            except Exception as e2:
                print("batch@%d retry failed: %s" % (start, e2), file=sys.stderr)
                break
        stop = False
        for pg in pages:
            items = pg.get("items") or []
            if total is None and pg.get("total") is not None:
                total = pg["total"]
            items_all.extend(items)
            if pg.get("isEnd") is True or not items:
                stop = True
                break
        print("batch@%d -> 累计 %d 条 (接口总数=%s)" % (start, len(items_all), total))
        if stop or len(items_all) >= args.max:
            break
        start += W * limit
        if start >= args.max:
            break
        time.sleep(0.3)

    seen = set()
    uniq = []
    for it in items_all:
        if it["id"] not in seen:
            seen.add(it["id"])
            uniq.append(it)
    uniq = uniq[: args.max]

    result = {"stats": {"url_token": tok, "type": args.type, "with_content": not args.no_content,
                        "collected": len(uniq), "api_total": total}}
    result[args.type] = uniq
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    print("TOTAL %s: %d | 接口总数: %s" % (args.type, len(uniq), total))
    print("saved -> %s" % args.out)


if __name__ == "__main__":
    main()
