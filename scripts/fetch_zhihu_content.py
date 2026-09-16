# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在用户真实浏览器的知乎页面内调用知乎官方接口，
抓取【单条】回答 / 文章 / 想法（pin）的完整正文并保存为 JSON。

接口（页内同源/跨域 fetch，自动携带登录态）：
  - 回答: GET https://www.zhihu.com/api/v4/answers/{aid}?include=content,...
  - 文章: GET https://zhuanlan.zhihu.com/api/articles/{arid}   （跨域可访问）
  - 想法: GET https://www.zhihu.com/api/v4/pins/{pid}          （正文在 content_html）

用法：
  python fetch_zhihu_content.py --url "https://www.zhihu.com/question/2079574865397532387/answer/2080852019556921373" --out a.json
  python fetch_zhihu_content.py --url "https://zhuanlan.zhihu.com/p/2074519498015691830" --out art.json
  python fetch_zhihu_content.py --url "https://www.zhihu.com/pin/2073154622433379690" --out pin.json
  python fetch_zhihu_content.py --aid 2080852019556921373 --out a.json
  python fetch_zhihu_content.py --arid 2074519498015691830 --keep-html --out art.json
  python fetch_zhihu_content.py --pid 2073154622433379690 --out pin.json

说明：
  - 输入可以是链接或 id；链接自动识别类型（/question/x/answer/y、zhuanlan.zhihu.com/p/z、/pin/w）。
  - 输出 {type,id,title,question_id,author,vote,comments,created,updated,url,excerpt,text[,html]}。
"""
import argparse
import json
import re
import sys
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-content"

JS_ANSWER = r"""
(async () => {
  const ID = '%s';
  const KEEP_HTML = %s;
  const clean = (html) => {
    if (!html) return '';
    const d = document.createElement('div');
    d.innerHTML = html;
    d.querySelectorAll('img,figure').forEach(el => el.remove());
    d.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
    d.querySelectorAll('p,div,li,h1,h2,h3,h4,blockquote,pre').forEach(el => el.append('\n'));
    let t = d.innerText.replace(/<[^>]+>/g, '');
    return t.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+\n/g, '\n').trim();
  };
  const url = 'https://www.zhihu.com/api/v4/answers/' + ID + '?include=' + encodeURIComponent('content,author,voteup_count,comment_count,question,created_time,updated_time,excerpt,ip_info,thanks_count');
  const r = await fetch(url, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status});
  const j = await r.json();
  const q = j.question || {};
  return JSON.stringify({status: 200, type: 'answer', id: j.id,
    title: q.title || '', question_id: q.id || null,
    author: j.author ? j.author.name : '', author_token: j.author ? j.author.url_token : '',
    vote: j.voteup_count, comments: j.comment_count,
    thanks: j.thanks_count, ip: j.ip_info,
    author_headline: j.author ? j.author.headline : '', author_gender: j.author ? j.author.gender : '',
    created: j.created_time, updated: j.updated_time,
    url: 'https://www.zhihu.com/question/' + (q.id || '') + '/answer/' + j.id,
    excerpt: clean(j.excerpt).slice(0, 500), text: clean(j.content),
    html: KEEP_HTML ? (j.content || '') : ''});
})()
"""

JS_ARTICLE = r"""
(async () => {
  const ID = '%s';
  const KEEP_HTML = %s;
  const clean = (html) => {
    if (!html) return '';
    const d = document.createElement('div');
    d.innerHTML = html;
    d.querySelectorAll('img,figure').forEach(el => el.remove());
    d.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
    d.querySelectorAll('p,div,li,h1,h2,h3,h4,blockquote,pre').forEach(el => el.append('\n'));
    let t = d.innerText.replace(/<[^>]+>/g, '');
    return t.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+\n/g, '\n').trim();
  };
  const url = 'https://zhuanlan.zhihu.com/api/articles/' + ID;
  const r = await fetch(url, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status});
  const j = await r.json();
  return JSON.stringify({status: 200, type: 'article', id: j.id,
    title: j.title || '', question_id: null,
    author: j.author ? j.author.name : '', author_token: j.author ? j.author.url_token : '',
    vote: j.voteup_count, comments: j.comment_count,
    created: j.created_time, updated: j.updated_time,
    url: j.url || ('https://zhuanlan.zhihu.com/p/' + j.id),
    excerpt: clean(j.excerpt).slice(0, 500), text: clean(j.content),
    html: KEEP_HTML ? (j.content || '') : ''});
})()
"""

JS_PIN = r"""
(async () => {
  const ID = '%s';
  const KEEP_HTML = %s;
  const clean = (html) => {
    if (!html) return '';
    const d = document.createElement('div');
    d.innerHTML = html;
    d.querySelectorAll('img,figure').forEach(el => el.remove());
    d.querySelectorAll('br').forEach(b => b.replaceWith('\n'));
    d.querySelectorAll('p,div,li,h1,h2,h3,h4,blockquote,pre').forEach(el => el.append('\n'));
    let t = d.innerText.replace(/<[^>]+>/g, '');
    return t.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+\n/g, '\n').trim();
  };
  const url = 'https://www.zhihu.com/api/v4/pins/' + ID + '?include=' + encodeURIComponent('content,content_html,author,like_count,comment_count,created,updated,excerpt_title');
  const r = await fetch(url, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status});
  const j = await r.json();
  let html = j.content_html || '';
  if (!html && Array.isArray(j.content)) {
    html = j.content.map(x => x.content || x.text || '').join('\n');
  } else if (!html) {
    html = j.content || '';
  }
  return JSON.stringify({status: 200, type: 'pin', id: j.id,
    title: clean(j.excerpt_title || ''), question_id: null,
    author: j.author ? j.author.name : '', author_token: j.author ? j.author.url_token : '',
    vote: j.like_count, comments: j.comment_count,
    created: j.created, updated: j.updated,
    url: j.url || ('https://www.zhihu.com/pin/' + j.id),
    excerpt: clean(html).slice(0, 500), text: clean(html),
    html: KEEP_HTML ? html : ''});
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


def detect(url_or_id):
    """识别输入是回答/文章/想法链接还是裸 id（裸 id 需带类型参数）。"""
    if re.search(r"/question/\d+/answer/(\d+)", url_or_id):
        m = re.search(r"/question/\d+/answer/(\d+)", url_or_id)
        return "answer", m.group(1)
    if re.search(r"zhuanlan\.zhihu\.com/p/(\d+)", url_or_id):
        m = re.search(r"zhuanlan\.zhihu\.com/p/(\d+)", url_or_id)
        return "article", m.group(1)
    if re.search(r"/pin/(\d+)", url_or_id):
        m = re.search(r"/pin/(\d+)", url_or_id)
        return "pin", m.group(1)
    return None, None


def main():
    ap = argparse.ArgumentParser(description="抓取单条知乎回答/文章/想法全文")
    ap.add_argument("--url", help="知乎链接（回答/文章/想法，自动识别类型）")
    ap.add_argument("--aid", help="回答 ID")
    ap.add_argument("--arid", help="文章 ID")
    ap.add_argument("--pid", help="想法 ID")
    ap.add_argument("--out", required=True, help="输出 JSON 路径")
    ap.add_argument("--keep-html", action="store_true", help="同时保存原始 HTML")
    ap.add_argument("--session", default=DEFAULT_SESSION)
    ap.add_argument("--base", default=DEFAULT_BASE)
    args = ap.parse_args()

    kind, cid = None, None
    if args.url:
        kind, cid = detect(args.url)
        if not kind:
            raise SystemExit("无法从链接识别类型: %s （支持 /question/x/answer/y、zhuanlan.zhihu.com/p/z、/pin/w）" % args.url)
    elif args.aid:
        kind, cid = "answer", args.aid
    elif args.arid:
        kind, cid = "article", args.arid
    elif args.pid:
        kind, cid = "pin", args.pid
    else:
        raise SystemExit("必须提供 --url 或 --aid/--arid/--pid 之一")

    tpl = {"answer": JS_ANSWER, "article": JS_ARTICLE, "pin": JS_PIN}[kind]
    code = tpl % (cid, "true" if args.keep_html else "false")
    try:
        val = run_js(args.base, args.session, code)
    except Exception as e:
        print("抓取失败: %s" % e, file=sys.stderr)
        sys.exit(1)
    if val.get("status") != 200:
        print("接口返回 status=%s（可能内容已删除或需登录）" % val.get("status"), file=sys.stderr)
        sys.exit(1)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(val, f, ensure_ascii=False, indent=1)

    text = (val.get("text") or "").strip()
    print("%s[%s] 标题: %s" % (kind, cid, (val.get("title") or "")[:50]))
    print("  作者: %s | 赞 %s | 评论 %s | 正文 %d 字" % (val.get("author"), val.get("vote"), val.get("comments"), len(text)))
    print("  saved -> %s" % args.out)


if __name__ == "__main__":
    main()
