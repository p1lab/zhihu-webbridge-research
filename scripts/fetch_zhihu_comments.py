# -*- coding: utf-8 -*-
"""抓取某知乎回答的评论（root_comments）+ 楼中楼，分页到 is_end，清洗 HTML→纯文本，存 JSON。
并发：一次 evaluate 内 Promise.all 拉多 offset（daemon 单命令通道串行，页内并发才提速）。
前置：Kimi WebBridge 守护进程运行、浏览器有已登录知乎 tab（同源 fetch）。

用法：
  python fetch_zhihu_comments.py --aid 2080852019556921373 --out comments.json
  python fetch_zhihu_comments.py --url "https://www.zhihu.com/question/{qid}/answer/{aid}" --out c.json
  python fetch_zhihu_comments.py --aid ... --order by_time --limit 20 --concurrency 5 --session zhihu-comments
"""
import argparse
import json
import re
import sys
import time
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-comments"

JS_TEMPLATE = r"""
(async () => {
  const aid = '%s';
  const ORDER = '%s';
  const START = %d;
  const W = %d;
  const LIMIT = %d;
  const strip = (h) => {
    if (!h) return '';
    const d = document.createElement('div');
    d.innerHTML = h;
    d.querySelectorAll('img,figure').forEach(el => el.remove());
    let t = d.innerText.replace(/<[^>]+>/g, ' ');
    return t.replace(/\s+/g, ' ').trim();
  };
  const mapC = (c) => {
    const m = (c.author && c.author.member) ? c.author.member : (c.author || {});
    return {id: c.id, text: strip(c.content), author: m.name || '', author_token: m.url_token || '',
      created: c.created_time, vote: c.vote_count, is_author: !!c.is_author,
      collapsed: !!c.collapsed, ip: c.address_text || '',
      reply_to: c.reply_to_author ? (c.reply_to_author.name || c.reply_to_author.full_name || '') : '',
      children: (c.child_comments || []).map(mapC)};
  };
  const url = (o) => 'https://www.zhihu.com/api/v4/answers/' + aid + '/root_comments?limit=' + LIMIT + '&offset=' + o + '&order=' + ORDER;
  const offs = []; for (let i = 0; i < W; i++) offs.push(START + i * LIMIT);
  const pages = await Promise.all(offs.map(o => fetch(url(o), {credentials: 'include'}).then(r => r.json()).catch(() => null)));
  return JSON.stringify(pages.map(j => j ? ({status: 200, isEnd: j.paging ? !!j.paging.is_end : null,
    total: j.paging ? j.paging.totals : null, comments: (j.data || []).map(mapC)}) : {status: 0}));
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


def extract_aid(url_or_id):
    m = re.search(r"/answer/(\d+)", str(url_or_id))
    if m:
        return m.group(1)
    if str(url_or_id).isdigit():
        return str(url_or_id)
    raise SystemExit("无法解析回答ID(需 /question/x/answer/{aid} 或 --aid)：%s" % url_or_id)


def main():
    ap = argparse.ArgumentParser(description="抓取知乎回答评论(含楼中楼, 页内并发分页)")
    ap.add_argument("--aid", help="回答ID")
    ap.add_argument("--url", help="回答链接（含 /answer/{aid}）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    ap.add_argument("--limit", type=int, default=20, help="每页(1-20)")
    ap.add_argument("--order", default="by_vote", choices=["by_vote", "by_time"], help="排序")
    ap.add_argument("--concurrency", type=int, default=5, help="一次evaluate内并发页数(1-10)，1=串行")
    args = ap.parse_args()

    aid = extract_aid(args.url or args.aid)
    limit = min(max(args.limit, 1), 20)
    W = min(max(args.concurrency, 1), 10)
    all_c, start, total = [], 0, None
    while True:
        code = JS_TEMPLATE % (aid, args.order, start, W, limit)
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
            if pg.get("status") != 200:
                stop = True
                break
            if pg.get("total") is not None:
                total = pg.get("total")
            cs = pg.get("comments") or []
            got += len(cs)
            all_c.extend(cs)
            if pg.get("isEnd") is True or not cs:
                stop = True
                break
        print("batch@%d -> 累计 %d 条 (本批收=%d 截断=%s)" % (start, len(all_c), got, stop))
        if stop:
            break
        start += W * limit
        if start > 3000:
            break
        time.sleep(0.2)

    seen, uniq = set(), []
    for c in all_c:
        if c["id"] not in seen:
            seen.add(c["id"])
            uniq.append(c)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"aid": aid, "order": args.order, "total": total, "comments": uniq}, f, ensure_ascii=False, indent=1)

    flat = sum(1 + len(c.get("children") or []) for c in uniq)
    print("TOTAL 主评论 %d (接口total=%s) | 含楼中楼 %d 条 | saved -> %s" % (len(uniq), total, flat, args.out))


if __name__ == "__main__":
    main()
