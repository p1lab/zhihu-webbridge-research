# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在用户真实浏览器的知乎页面内调用知乎官方**写接口**，
对单条回答执行 赞同/取消赞同、喜欢(感谢)/取消、收藏/移出收藏、评论/删评论。

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session），使页内 fetch 同源、带登录态。

用法（默认 dry-run，只打印将要发送的请求；加 --yes 才真正执行）：
  python zhihu_write_ops.py vote   --aid 2055686718242740080 --yes
  python zhihu_write_ops.py vote   --aid ... --cancel --yes
  python zhihu_write_ops.py like   --aid ... --yes
  python zhihu_write_ops.py like   --aid ... --cancel --yes
  python zhihu_write_ops.py collect --aid ... --yes
  python zhihu_write_ops.py collect --aid ... --collection 911247604 --cancel --yes
  python zhihu_write_ops.py comment --aid ... --text "感谢分享，学到了。" --yes
  python zhihu_write_ops.py comment --aid ... --delete 11587879515 --yes
  python zhihu_write_ops.py status  --aid 2055686718242740080

  # 草稿（保存/读取/删除，最安全：草稿只有本人可见，不对外发布）
  python zhihu_write_ops.py draft --qid 20213920 --text "你好" --yes
  python zhihu_write_ops.py draft --qid 20213920 --get
  python zhihu_write_ops.py draft --qid 20213920 --delete --yes

  # 写回答（真正把内容发布到问题下，务必三思；publish 会自动反查 aid 便于撤销）
  python zhihu_write_ops.py publish --qid 20213920 --text "你好" --yes
  python zhihu_write_ops.py delete-answer --aid 2089115230492176517 --yes      # 软删除
  python zhihu_write_ops.py delete-answer --aid ... --cancel --yes             # 撤销删除(restore)
  python zhihu_write_ops.py follow --qid 20213920 --yes
  python zhihu_write_ops.py follow --qid 20213920 --cancel --yes               # 取消关注问题

端点（实测：页内 fetch 带 cookie+x-xsrftoken 即可，**不校验 x-zse 签名**）：
  赞同/取消   POST /api/v4/answers/{aid}/voters      body {"type":"up"} / {"type":"neutral"}
  喜欢/取消   POST|DELETE /api/v4/answers/{aid}/thankers
  收藏/移出   POST /api/v4/collections/contents/answer/{aid}   （空 body，收藏到默认收藏夹）
             DELETE /api/v4/collections/{cid}/contents/{aid}?content_type=answer
  评论/删除   POST /api/v4/comment_v5/answers/{aid}/comment  body {"content":"..."}（明文即可）
             DELETE /api/v4/comments/{cid}   → 204
  草稿        POST|PUT /api/v4/questions/{qid}/draft  body {"content":"<p>..</p>","delta_time":N,"draft_type":"normal","settings":{...}}
             GET /api/v4/questions/{qid}/draft?include=question,schedule   /   DELETE 同路径
             GET /api/v4/answer-drafts/count → {"count":N,"scheduled_count":N}
  发布回答    POST /api/v4/content/publish  body {"action":"answer","data":{"hybrid":{"html":...,"textLength":N},"extra_info":{"question_id":...,"pc_business_params":"<JSON字符串>"},"reprint":{...},"commentsPermission":{...}, ...}}
             响应 {code,message,toast_message,data}；**toast_message 非空即失败**（如"已回答过该问题"）；
             成功时响应里不含回答 ID，需用 questions/{qid}/answers 按本人 url_token 反查。
  删除/恢复   DELETE /api/v4/answers/{aid} → {"success":true}（软删，GET 变 404）
             POST /api/v4/answers/{aid}/actions/restore → 200 并返回完整回答对象（实测已复原：GET 回 200、回答数 +1；会重新公开）
  关注问题    POST|DELETE /api/v4/questions/{qid}/followers → 204（发布回答会自动关注，撤销时要一起取消）
             回查关注状态：GET /api/v4/questions/{qid}/followers?offset=0&limit=5 → paging.totals（取消后回 0）

完整闭环（2026-10-01 在冷门第三方问题 498864288 上，全部经本脚本逐步实测通过、结束净零）：
  publish --text 你好 → 反查 aid → delete-answer → restore → delete-answer → follow → follow --cancel
  每步的可观测状态：totals 1→2→1→2→1，answer GET 200→404→200→404，followers totals 0→0。
"""
import argparse
import json
import sys
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-write"

JS = r"""
(async () => {
  const METHOD = '%s';
  const PATH = '%s';
  const BODY = %s;
  const xsrf = (document.cookie.match(/_xsrf=([^;]+)/) || [])[1] || '';
  const opt = {method: METHOD, credentials: 'include', headers: {'x-requested-with': 'fetch', 'x-xsrftoken': xsrf}};
  if (BODY !== null) { opt.headers['content-type'] = 'application/json'; opt.body = JSON.stringify(BODY); }
  const r = await fetch('https://www.zhihu.com' + PATH, opt);
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) {}
  return JSON.stringify({status: r.status, json: j, text: j ? null : t.slice(0, 300)});
})()
"""

STATUS_JS = r"""
(async () => {
  const AID = '%s';
  const out = {};
  const r1 = await fetch('https://www.zhihu.com/api/v4/answers/' + AID + '?include=voteup_count,comment_count,thanks_count,author,question', {credentials: 'include'});
  const a = await r1.json();
  out.answer = {vote: a.voteup_count, comments: a.comment_count, thanks: a.thanks_count, author: a.author ? a.author.name : '', qid: a.question ? a.question.id : ''};
  const r2 = await fetch('https://www.zhihu.com/api/v4/collections/contents/answer/' + AID + '?offset=0&limit=5', {credentials: 'include'});
  const c = await r2.json();
  out.favorited = (c.data || []).filter(d => d.is_favorited).map(d => ({collection_id: d.id, title: d.title}));
  const r3 = await fetch('https://www.zhihu.com/api/v4/comment_v5/answers/' + AID + '/root_comment?order_by=score&limit=20&offset=', {credentials: 'include'});
  const cm = await r3.json();
  out.comment_totals = cm.paging ? cm.paging.totals : null;
  out.my_comments = (cm.data || []).filter(d => d.is_author).map(d => ({id: d.id, content: d.content}));
  return JSON.stringify(out);
})()
"""

# 发布回答：POST /api/v4/content/publish 后，用问题回答列表按本人 url_token 反查新回答 ID。
PUBLISH_JS = r"""
(async () => {
  const QID = '%s';
  const HTML = %s;
  const xsrf = (document.cookie.match(/_xsrf=([^;]+)/) || [])[1] || '';
  const settings = {reshipment_settings: 'allowed', comment_permission: 'all', columns: null,
    reward_setting: {can_reward: false, tagline: ''}, disclaimer_status: 'close', disclaimer_type: 'none',
    commercial_report_info: {is_report: false}, commercial_zhitask_bind_info: null, is_report: false,
    push_activity: true, table_of_contents_enabled: false, thank_inviter_status: 'close', thank_inviter: ''};
  const body = {action: 'answer', data: {
    publish: {traceId: Date.now() + ',' + 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
      const r = Math.random() * 16 | 0; return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16); })},
    hybridInfo: {}, draft: {isPublished: false, disabled: 1},
    extra_info: {question_id: QID, publisher: 'pc',
      include: 'content,editable_content,excerpt,created_time,updated_time,question,author,answer_type',
      pc_business_params: JSON.stringify(settings)},
    hybrid: {html: HTML, textLength: HTML.length},
    reprint: {reshipment_settings: settings.reshipment_settings},
    commentsPermission: {comment_permission: settings.comment_permission},
    appreciate: settings.reward_setting, publishSwitch: {draft_type: 'normal'},
    creationStatement: {disclaimer_status: settings.disclaimer_status, disclaimer_type: settings.disclaimer_type},
    commercialReportInfo: {isReport: 0}, toFollower: {},
    contentsTables: {table_of_contents_enabled: false},
    thanksInvitation: {thank_inviter_status: settings.thank_inviter_status, thank_inviter: settings.thank_inviter}
  }};
  const r = await fetch('https://www.zhihu.com/api/v4/content/publish', {
    method: 'POST', credentials: 'include',
    headers: {'content-type': 'application/json', 'x-requested-with': 'fetch', 'x-xsrftoken': xsrf},
    body: JSON.stringify(body)});
  const t = await r.text();
  let j = null; try { j = JSON.parse(t); } catch (e) {}
  const out = {status: r.status, toast: j ? j.toast_message : t.slice(0, 200), error: j ? j.error : null};
  const me = await (await fetch('https://www.zhihu.com/api/v4/me?include=url_token', {credentials: 'include', headers: {'x-requested-with': 'fetch'}})).json().catch(() => null);
  const token = me && me.url_token;
  const list = await (await fetch('https://www.zhihu.com/api/v4/questions/' + QID + '/answers?limit=20&offset=0&include=author,content', {credentials: 'include', headers: {'x-requested-with': 'fetch'}})).json().catch(() => null);
  out.aid = ((list && list.data) || []).filter(a => a.author && a.author.url_token === token)
    .map(a => a.id).sort()[0] || null;
  return JSON.stringify(out);
})()
"""

DRAFT_SETTINGS = {
    "reshipment_settings": "allowed", "columns": None, "comment_permission": "all",
    "can_reward": False, "tagline": "", "disclaimer_status": "close", "disclaimer_type": "none",
    "commercial_report_info": {"is_report": False}, "table_of_contents_enabled": False,
    "thank_inviter_status": "close", "thank_inviter": "",
}


def text_to_html(text):
    esc = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return "".join("<p>%s</p>" % ln for ln in esc.split("\n") if ln.strip()) or "<p></p>"


def run_js(base, session, code, timeout=60):
    body = json.dumps({"action": "evaluate", "args": {"code": code}, "session": session}).encode("utf-8")
    req = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError("daemon error: %s" % raw[:400])
    v = data["data"]["value"]
    return json.loads(v) if isinstance(v, str) and v[:1] in "[{" else v


def build(op, a):
    """返回 (method, path, body, 人类可读说明)。"""
    aid = getattr(a, "aid", None)
    if op == "vote":
        return ("POST", "/api/v4/answers/%s/voters" % aid, {"type": "neutral" if a.cancel else "up"},
                "取消赞同" if a.cancel else "赞同")
    if op == "like":
        return ("DELETE" if a.cancel else "POST", "/api/v4/answers/%s/thankers" % aid, None,
                "取消喜欢" if a.cancel else "喜欢(感谢)")
    if op == "collect":
        if a.cancel:
            if not a.collection:
                raise SystemExit("移出收藏需要 --collection <收藏夹ID>（可先 status 查看 favorited）")
            return ("DELETE", "/api/v4/collections/%s/contents/%s?content_type=%s" % (a.collection, aid, a.ctype), None,
                    "移出收藏")
        return ("POST", "/api/v4/collections/contents/%s/%s" % (a.ctype, aid), None, "收藏到默认收藏夹")
    if op == "comment":
        if a.delete:
            return ("DELETE", "/api/v4/comments/%s" % a.delete, None, "删除评论 %s" % a.delete)
        if not a.text:
            raise SystemExit("发表评论需要 --text <内容>")
        return ("POST", "/api/v4/comment_v5/answers/%s/comment" % aid, {"content": a.text}, "发表评论")
    if op == "draft":
        qid = a.qid
        if a.delete:
            return ("DELETE", "/api/v4/questions/%s/draft" % qid, None, "删除草稿")
        if a.get:
            return ("GET", "/api/v4/questions/%s/draft?include=question,schedule" % qid, None, "读取草稿")
        html = a.html or (text_to_html(a.text) if a.text else None)
        if html is None:
            raise SystemExit("保存草稿需要 --text <纯文本> 或 --html <HTML>")
        return ("POST", "/api/v4/questions/%s/draft" % qid,
                {"content": html, "delta_time": 1, "draft_type": "normal", "attachment": None, "settings": DRAFT_SETTINGS},
                "保存草稿")
    if op == "delete-answer":
        if a.cancel:
            return ("POST", "/api/v4/answers/%s/actions/restore" % aid, None, "恢复已删除回答（会重新公开）")
        return ("DELETE", "/api/v4/answers/%s" % aid, None, "删除回答（软删除，可 restore 撤销）")
    if op == "follow":
        return ("DELETE" if a.cancel else "POST", "/api/v4/questions/%s/followers" % a.qid, None,
                "取消关注问题" if a.cancel else "关注问题")
    raise SystemExit("未知操作: %s" % op)


def main():
    ap = argparse.ArgumentParser(description="知乎写操作（赞同/喜欢/收藏/评论）")
    sub = ap.add_subparsers(dest="op", required=True)

    def make(name, help_text, **extra):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--session", default=DEFAULT_SESSION)
        p.add_argument("--base", default=DEFAULT_BASE)
        p.add_argument("--yes", action="store_true", help="确认执行写操作（默认 dry-run）")
        for k, v in extra.items():
            p.add_argument("--" + k, **v)
        return p

    make("vote", "赞同/取消赞同", aid={"required": True, "help": "回答ID"}, cancel={"action": "store_true", "help": "执行撤销动作"})
    make("like", "喜欢(感谢)/取消", aid={"required": True, "help": "回答ID"}, cancel={"action": "store_true", "help": "执行撤销动作"})
    make("collect", "收藏/移出收藏", aid={"required": True, "help": "回答ID"}, cancel={"action": "store_true", "help": "执行撤销动作"},
         collection={"help": "收藏夹ID（collect --cancel 必填）"},
         ctype={"default": "answer", "choices": ["answer", "article", "pin"], "help": "内容类型"})
    make("comment", "评论/删评论", aid={"required": True, "help": "回答ID"},
         text={"help": "评论内容"}, delete={"help": "要删除的评论ID"})
    make("status", "只读：查当前回答的赞/评/收藏状态", aid={"required": True, "help": "回答ID"})
    make("draft", "草稿：保存(--text/--html)/读取(--get)/删除(--delete)", qid={"required": True, "help": "问题ID"},
         text={"help": "纯文本内容，自动转成 <p> 段落"}, html={"help": "直接给 HTML 内容"},
         get={"action": "store_true", "help": "只读取当前草稿"},
         delete={"action": "store_true", "help": "删除该问题的草稿"},
         cancel={"action": "store_true", "help": "执行撤销动作"})
    make("publish", "发布回答（真正对外公开，务必确认内容后再 --yes）", qid={"required": True, "help": "问题ID"},
         text={"help": "纯文本内容"}, html={"help": "直接给 HTML 内容"})
    make("delete-answer", "删除回答（软删除）/ --cancel 恢复", aid={"required": True, "help": "回答ID"},
         cancel={"action": "store_true", "help": "执行撤销动作（恢复回答）"})
    make("follow", "关注问题 / --cancel 取消关注", qid={"required": True, "help": "问题ID"},
         cancel={"action": "store_true", "help": "执行撤销动作"})
    a = ap.parse_args()

    if a.op == "status":
        print(json.dumps(run_js(a.base, a.session, STATUS_JS % a.aid), ensure_ascii=False, indent=1))
        return

    if a.op == "publish":
        html = a.html or (text_to_html(a.text) if a.text else None)
        if html is None:
            raise SystemExit("publish 需要 --text <纯文本> 或 --html <HTML>")
        if not a.yes:
            print("DRY-RUN（未执行，加 --yes 才发送）")
            print("  POST https://www.zhihu.com/api/v4/content/publish  (action=answer, question=%s)" % a.qid)
            print("  html: %s" % html)
            print("  动作: 发布回答 —— 这会**公开出现在该问题下**；撤销用 delete-answer --aid <ID> --yes")
            return
        res = run_js(a.base, a.session, PUBLISH_JS % (a.qid, json.dumps(html, ensure_ascii=False)))
        print(json.dumps(res, ensure_ascii=False, indent=1))
        ok = res.get("status") == 200 and not res.get("toast") and not res.get("error") and res.get("aid")
        if not ok:
            print("失败或未取得回答ID（toast 非空即为业务失败，如“已回答过该问题”）", file=sys.stderr)
        else:
            print("已发布 aid=%s；撤销：delete-answer --aid %s --yes（并 follow --qid %s --cancel --yes）" % (res["aid"], res["aid"], a.qid))
        sys.exit(0 if ok else 1)

    method, path, body, desc = build(a.op, a)
    code = JS % (method, path, json.dumps(body, ensure_ascii=False) if body is not None else "null")
    if not a.yes:
        print("DRY-RUN（未执行，加 --yes 才发送）")
        print("  %s https://www.zhihu.com%s" % (method, path))
        if body is not None:
            print("  body: %s" % json.dumps(body, ensure_ascii=False))
        print("  动作: %s" % desc)
        return

    res = run_js(a.base, a.session, code)
    print("%s -> HTTP %s" % (desc, res.get("status")))
    print(json.dumps(res.get("json") if res.get("json") is not None else res.get("text"), ensure_ascii=False, indent=1)[:1500])
    ok = res.get("status") in (200, 204)
    j = res.get("json") or {}
    if isinstance(j, dict) and j.get("error"):
        ok = False
        print("失败: %s" % j["error"].get("message"), file=sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
