# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在用户真实浏览器的知乎页面内调用知乎官方回答接口，
分页抓取某问题的全部回答正文并保存为 JSON。

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session），使页内 fetch 同源、带登录态。

用法：
  python fetch_zhihu_answers.py --qid 2063557784394785882 --out answers.json
  python fetch_zhihu_answers.py --url "https://www.zhihu.com/question/2063557784394785882" --out answers.json
  python fetch_zhihu_answers.py --qid ... --out ... --session zhihu-answers --limit 10

说明：
  - 通过 evaluate 在页内执行 fetch('https://www.zhihu.com/api/v4/questions/{qid}/answers?...')，
    按 offset 分页直到 paging.is_end，页内完成 HTML->纯文本清洗。
  - 页面显示的回答数与 API 返回数可能不一致（被折叠/删除的回答接口不返回），属正常现象。
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
  const qid = '%s';
  const START = %d;
  const W = %d;
  const LIMIT = %d;
  const include = encodeURIComponent('content,voteup_count,comment_count,created_time,updated_time,is_collapsed,content_need_truncated,author,question,ip_info,thanks_count,is_copyable');
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
  const mapA = (a) => ({id: a.id, author: a.author ? a.author.name : '', vote: a.voteup_count, comments: a.comment_count, created: a.created_time, updated: a.updated_time, truncated: a.content_need_truncated, collapsed: a.is_collapsed, text: strip(a.content),
    thanks: a.thanks_count, favorite: a.favorite_count, ip: a.ip_info, copyable: a.is_copyable,
    author_headline: a.author ? a.author.headline : '', author_gender: a.author ? a.author.gender : '', author_url_token: a.author ? a.author.url_token : '', author_id: a.author ? a.author.id : ''});
  const url = (o) => 'https://www.zhihu.com/api/v4/questions/' + qid + '/answers?include=' + include + '&limit=' + LIMIT + '&offset=' + o + '&platform=desktop&sort_by=default';
  const offs = []; for (let i = 0; i < W; i++) offs.push(START + i * LIMIT);
  const pages = await Promise.all(offs.map(o => fetch(url(o), {credentials: 'include'}).then(r => r.json())));
  return JSON.stringify(pages.map(j => ({isEnd: j.paging ? j.paging.is_end : null, answers: (j.data || []).map(mapA)})));
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
    ap = argparse.ArgumentParser(description="抓取知乎问题全部回答")
    ap.add_argument("--qid", help="知乎问题ID 或 /question/{id} 链接")
    ap.add_argument("--url", help="知乎问题链接（二选一）")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--limit", type=int, default=10, help="每页条数（默认10）")
    ap.add_argument("--concurrency", type=int, default=5, help="一次evaluate内并发拉的页数(1-10)，1=串行")
    args = ap.parse_args()

    qid = extract_qid(args.url or args.qid)
    limit = min(max(args.limit, 1), 20)
    W = min(max(args.concurrency, 1), 10)
    all_answers = []
    start = 0
    while True:
        code = JS_TEMPLATE % (qid, start, W, limit)
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
        got = 0
        for pg in pages:
            ans = pg.get("answers") or []
            got += len(ans)
            all_answers.extend(ans)
            if pg.get("isEnd") is True or not ans:
                stop = True
                break
        print("batch@%d -> 累计 %d 条 (本批页=%d 收=%d 截断=%s)" % (start, len(all_answers), len(pages), got, stop))
        if stop:
            break
        start += W * limit
        if start > 2000:
            break
        time.sleep(0.2)

    # 按 id 去重
    seen = set()
    uniq = []
    for a in all_answers:
        if a["id"] not in seen:
            seen.add(a["id"])
            uniq.append(a)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(uniq, f, ensure_ascii=False, indent=1)

    total_votes = sum(a.get("vote", 0) for a in uniq)
    empty = sum(1 for a in uniq if not (a.get("text") or "").strip())
    print("TOTAL answers: %d | 空正文: %d | 累计赞同: %d" % (len(uniq), empty, total_votes))
    print("saved -> %s" % args.out)


if __name__ == "__main__":
    main()
