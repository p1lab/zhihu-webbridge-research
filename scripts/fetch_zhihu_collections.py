# -*- coding: utf-8 -*-
"""
通过 Kimi WebBridge 守护进程，在用户真实浏览器的知乎页面内调用知乎官方收藏接口，
抓取收藏夹列表与收藏条目，清洗 HTML，写入本地 SQLite 数据库，支持增量更新与 JSON 导出。

接口（均在已打开的 www.zhihu.com 页面内同源 fetch，自动携带登录态）：
  - 当前用户:      GET /api/v4/me
  - 收藏夹列表:    GET /api/v4/people/{url_token}/collections?limit=&offset=
  - 收藏夹详情:    GET /api/v4/collections/{id}
  - 收藏条目:      GET /api/v4/collections/{id}/items?limit=&offset=
                   data[] 每项 {created: 收藏时间(ISO), content: {type,id,url,title/question,author,
                   voteup_count,comment_count,created_time,updated_time,content(HTML),excerpt,...}}

排序口径（实测确认）：条目接口的排序【稳定但非严格按收藏时间】——
新收藏追加在头部，但中部/尾部存在时间倒挂。因此增量更新采用
「从头部翻页，连续遇到 BOUNDARY 条已入库条目即停止」的边界策略；
新增/重收藏条目只会出现在头部，删除不影响剩余条目相对顺序。

用法：
  python fetch_zhihu_collections.py list [--out collections.json]
  python fetch_zhihu_collections.py fetch --collection YOUR_COLLECTION_ID --db zhihu_collections.db [--full] [--keep-html]
  python fetch_zhihu_collections.py fetch --all --db zhihu_collections.db [--export-json ./export]
  python fetch_zhihu_collections.py fetch --collection YOUR_COLLECTION_ID --export-json ./export --limit 20

前置条件：
  1. Kimi WebBridge 守护进程已启动（127.0.0.1:10086）。
  2. 浏览器中已有一个打开的知乎页面 tab（同一 session），使页内 fetch 同源、带登录态。
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.request

DEFAULT_BASE = "http://127.0.0.1:10086/command"
DEFAULT_SESSION = "zhihu-collections"
DEFAULT_DB = "zhihu_collections.db"
MAX_LIMIT = 20

# ---------------------------------------------------------------- 页内 JS ----------------------------------------------------------------

JS_ME = r"""
(async () => {
  const r = await fetch('https://www.zhihu.com/api/v4/me', {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status});
  const j = await r.json();
  return JSON.stringify({status: 200, name: j.name || '', url_token: j.url_token || '', id: j.id || ''});
})()
"""

JS_COLLECTIONS = r"""
(async () => {
  const TOKEN = '%s';
  const OFFSET = %d;
  const LIMIT = %d;
  const url = 'https://www.zhihu.com/api/v4/people/' + TOKEN + '/collections?limit=' + LIMIT + '&offset=' + OFFSET;
  const r = await fetch(url, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status, isEnd: true, items: []});
  const j = await r.json();
  const out = {status: 200, isEnd: j.paging ? !!j.paging.is_end : true, totals: j.paging ? (j.paging.totals || null) : null, items: []};
  (j.data || []).forEach(c => {
    out.items.push({
      id: c.id, type: c.type, title: c.title || '', description: c.description || '',
      is_public: c.is_public, item_count: c.item_count, answer_count: c.answer_count,
      view_count: c.view_count, follower_count: c.follower_count, like_count: c.like_count,
      comment_count: c.comment_count, created_time: c.created_time, updated_time: c.updated_time,
      creator_name: c.creator ? c.creator.name : '', creator_token: c.creator ? c.creator.url_token : ''
    });
  });
  return JSON.stringify(out);
})()
"""

JS_COLLECTION_DETAIL = r"""
(async () => {
  const CID = '%s';
  const r = await fetch('https://www.zhihu.com/api/v4/collections/' + CID, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status});
  const j = await r.json();
  const c = j.collection || j;
  return JSON.stringify({status: 200, id: c.id, title: c.title || '', description: c.description || '',
    is_public: c.is_public, item_count: c.item_count, answer_count: c.answer_count,
    view_count: c.view_count, follower_count: c.follower_count, like_count: c.like_count,
    comment_count: c.comment_count, created_time: c.created_time, updated_time: c.updated_time,
    creator_name: c.creator ? c.creator.name : '', creator_token: c.creator ? c.creator.url_token : ''});
})()
"""

JS_ITEMS = r"""
(async () => {
  const CID = '%s';
  const OFFSET = %d;
  const LIMIT = %d;
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
  const url = 'https://www.zhihu.com/api/v4/collections/' + CID + '/items?limit=' + LIMIT + '&offset=' + OFFSET;
  const r = await fetch(url, {credentials: 'include'});
  if (r.status !== 200) return JSON.stringify({status: r.status, isEnd: true, items: []});
  const j = await r.json();
  const out = {status: 200, isEnd: j.paging ? !!j.paging.is_end : true, totals: j.paging ? (j.paging.totals || null) : null, items: []};
  (j.data || []).forEach(it => {
    const c = it.content || {};
    const type = c.type || '';
    const q = c.question || {};
    let title = c.title || q.title || '';
    out.items.push({
      item_id: c.id, type: type,
      collected: it.created || '',
      url: c.url || '',
      title: title,
      question_id: q.id || null,
      question_title: q.title || null,
      author: c.author ? c.author.name : '',
      author_token: c.author ? c.author.url_token : '',
      vote: c.voteup_count, comments: c.comment_count, thanks: c.thanks_count,
      created: c.created_time, updated: c.updated_time,
      collapsed: c.is_collapsed, deleted: c.is_deleted,
      excerpt: clean(c.excerpt).slice(0, 500),
      text: clean(c.content),
      html: KEEP_HTML ? (c.content || '') : ''
    });
  });
  return JSON.stringify(out);
})()
"""

# ---------------------------------------------------------------- 守护进程通信 ----------------------------------------------------------------

def run_js(base, session, code, timeout=180):
    body = json.dumps({"action": "evaluate", "args": {"code": code}, "session": session}).encode("utf-8")
    req = urllib.request.Request(base, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    data = json.loads(raw)
    if not data.get("ok"):
        raise RuntimeError("daemon error: %s" % raw[:500])
    return json.loads(data["data"]["value"])


def fetch_me(base, session):
    return run_js(base, session, JS_ME)


# ---------------------------------------------------------------- 数据库 ----------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS collections (
  id INTEGER PRIMARY KEY,
  title TEXT, description TEXT, is_public INTEGER,
  item_count INTEGER, answer_count INTEGER, view_count INTEGER,
  follower_count INTEGER, like_count INTEGER, comment_count INTEGER,
  created_time INTEGER, updated_time INTEGER,
  creator_name TEXT, creator_token TEXT,
  last_scan_at TEXT, last_synced_count INTEGER
);
CREATE TABLE IF NOT EXISTS items (
  collection_id INTEGER NOT NULL,
  item_id TEXT NOT NULL,
  item_type TEXT, collected_at TEXT, content_url TEXT, title TEXT,
  question_id TEXT, question_title TEXT,
  author_name TEXT, author_url_token TEXT,
  voteup_count INTEGER, comment_count INTEGER, thanks_count INTEGER,
  content_text TEXT, content_html TEXT, excerpt TEXT,
  content_created INTEGER, content_updated INTEGER,
  is_collapsed INTEGER, is_deleted INTEGER,
  fingerprint TEXT,
  first_seen_at TEXT, last_seen_at TEXT, removed_at TEXT,
  PRIMARY KEY (collection_id, item_id)
);
CREATE INDEX IF NOT EXISTS idx_items_collection ON items(collection_id);
CREATE INDEX IF NOT EXISTS idx_items_collected ON items(collection_id, collected_at);
CREATE INDEX IF NOT EXISTS idx_items_removed ON items(collection_id, removed_at);
"""


def open_db(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()]
    if "thanks_count" not in cols:
        conn.execute("ALTER TABLE items ADD COLUMN thanks_count INTEGER")
    conn.commit()
    return conn


def upsert_collection(conn, c, scan_at):
    conn.execute(
        """INSERT INTO collections (id, title, description, is_public, item_count, answer_count,
             view_count, follower_count, like_count, comment_count, created_time, updated_time,
             creator_name, creator_token, last_scan_at, last_synced_count)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             title=excluded.title, description=excluded.description, is_public=excluded.is_public,
             item_count=excluded.item_count, answer_count=excluded.answer_count,
             view_count=excluded.view_count, follower_count=excluded.follower_count,
             like_count=excluded.like_count, comment_count=excluded.comment_count,
             created_time=excluded.created_time, updated_time=excluded.updated_time,
             creator_name=excluded.creator_name, creator_token=excluded.creator_token,
             last_scan_at=excluded.last_scan_at, last_synced_count=excluded.last_synced_count""",
        (c["id"], c.get("title"), c.get("description"), int(bool(c.get("is_public"))),
         c.get("item_count"), c.get("answer_count"), c.get("view_count"),
         c.get("follower_count"), c.get("like_count"), c.get("comment_count"),
         c.get("created_time"), c.get("updated_time"),
         c.get("creator_name"), c.get("creator_token"), scan_at, 0),
    )
    conn.commit()


def fingerprint(it):
    h = hashlib.sha1()
    for k in ("item_id", "type", "collected", "vote", "comments", "thanks", "updated", "text"):
        h.update(("%s=%s|" % (k, it.get(k))).encode("utf-8"))
    return h.hexdigest()


def upsert_item(conn, cid, it, fp, scan_at):
    """返回 'new' | 'changed' | 'unchanged'"""
    row = conn.execute(
        "SELECT fingerprint FROM items WHERE collection_id=? AND item_id=?",
        (cid, it["item_id"])).fetchone()
    if row:
        if row[0] == fp:
            conn.execute(
                "UPDATE items SET last_seen_at=? WHERE collection_id=? AND item_id=?",
                (scan_at, cid, it["item_id"]))
            conn.commit()
            return "unchanged"
        conn.execute(
            """UPDATE items SET item_type=?, collected_at=?, content_url=?, title=?, question_id=?,
                 question_title=?, author_name=?, author_url_token=?, voteup_count=?, comment_count=?,
                 thanks_count=?,
                 content_text=?, content_html=?, excerpt=?, content_created=?, content_updated=?,
                 is_collapsed=?, is_deleted=?, fingerprint=?, last_seen_at=?, removed_at=NULL
               WHERE collection_id=? AND item_id=?""",
            (it.get("type"), it.get("collected"), it.get("url"), it.get("title"),
             it.get("question_id"), it.get("question_title"), it.get("author"), it.get("author_token"),
             it.get("vote"), it.get("comments"), it.get("thanks"),
             it.get("text"), it.get("html", ""), it.get("excerpt"),
             it.get("created"), it.get("updated"), int(bool(it.get("collapsed"))),
             int(bool(it.get("deleted"))), fp, scan_at, cid, it["item_id"]))
        conn.commit()
        return "changed"
    conn.execute(
        """INSERT INTO items (collection_id, item_id, item_type, collected_at, content_url, title,
             question_id, question_title, author_name, author_url_token, voteup_count, comment_count,
             thanks_count,
             content_text, content_html, excerpt, content_created, content_updated,
             is_collapsed, is_deleted, fingerprint, first_seen_at, last_seen_at, removed_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)""",
        (cid, it["item_id"], it.get("type"), it.get("collected"), it.get("url"), it.get("title"),
         it.get("question_id"), it.get("question_title"), it.get("author"), it.get("author_token"),
         it.get("vote"), it.get("comments"), it.get("thanks"),
         it.get("text"), it.get("html", ""), it.get("excerpt"),
         it.get("created"), it.get("updated"), int(bool(it.get("collapsed"))),
         int(bool(it.get("deleted"))), fp, scan_at, scan_at))
    conn.commit()
    return "new"


# ---------------------------------------------------------------- 抓取逻辑 ----------------------------------------------------------------

def fetch_collections(base, session, token, limit=20):
    """分页抓取某用户的收藏夹列表。"""
    all_c = []
    offset = 0
    while True:
        code = JS_COLLECTIONS % (token, offset, limit)
        try:
            val = run_js(base, session, code)
        except Exception as e:
            print("offset %d failed: %s, retry once" % (offset, e), file=sys.stderr)
            time.sleep(2)
            val = run_js(base, session, code)
        if val.get("status") != 200:
            raise RuntimeError("collections API status=%s" % val.get("status"))
        items = val.get("items") or []
        all_c.extend(items)
        print("  collections offset %d -> %d, isEnd=%s" % (offset, len(items), val.get("isEnd")))
        if not items or val.get("isEnd"):
            break
        offset += limit
        time.sleep(0.3)
    return all_c


def fetch_collection_detail(base, session, cid):
    return run_js(base, session, JS_COLLECTION_DETAIL % cid)


def fetch_collection_items(base, session, cid, db_path, *, full=False, boundary=3, limit=20,
                           delay=0.4, keep_html=False, export_dir=None):
    """抓取收藏夹全部条目并持久化；增量模式在连续遇到 BOUNDARY 条已知条目后停止。"""
    scan_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    conn = open_db(db_path)
    known = set(r[0] for r in conn.execute("SELECT item_id FROM items WHERE collection_id=?", (cid,)))
    meta = fetch_collection_detail(base, session, cid)
    if meta.get("status") != 200:
        raise RuntimeError("collection detail status=%s" % meta.get("status"))
    upsert_collection(conn, meta, scan_at)
    print("  收藏夹[%s] %s | 接口item_count=%s | 库内已有 %d 条" %
          (cid, meta.get("title"), meta.get("item_count"), len(known)))

    stats = {"new": 0, "changed": 0, "unchanged": 0, "pages": 0, "seen": 0}
    seen_ids = set()
    consecutive_known = 0
    offset = 0
    stop_reason = "is_end"
    while True:
        code = JS_ITEMS % (cid, offset, limit, "true" if keep_html else "false")
        try:
            val = run_js(base, session, code)
        except Exception as e:
            print("  offset %d failed: %s, retry once" % (offset, e), file=sys.stderr)
            time.sleep(2)
            try:
                val = run_js(base, session, code)
            except Exception as e2:
                print("  offset %d retry failed: %s" % (offset, e2), file=sys.stderr)
                stop_reason = "error@%d" % offset
                break
        if val.get("status") != 200:
            print("  offset %d status=%s, stop" % (offset, val.get("status")), file=sys.stderr)
            stop_reason = "status_%s@%d" % (val.get("status"), offset)
            break
        items = val.get("items") or []
        stats["pages"] += 1
        stats["seen"] += len(items)
        print("  offset %d -> %d items (isEnd=%s, totals=%s)" %
              (offset, len(items), val.get("isEnd"), val.get("totals")))
        if not items:
            break
        for it in items:
            iid = str(it.get("item_id"))
            if not iid or iid == "None":
                continue
            seen_ids.add(iid)
            if iid in known:
                consecutive_known += 1
            else:
                consecutive_known = 0
                known.add(iid)
            stats[upsert_item(conn, cid, it, fingerprint(it), scan_at)] += 1
        if not full and consecutive_known >= boundary:
            stop_reason = "boundary_%d" % consecutive_known
            break
        offset += limit
        if val.get("isEnd"):
            stop_reason = "is_end"
            break
        if offset > 50000:
            stop_reason = "cap_50000"
            break
        time.sleep(delay)

    # 全量模式下标记本库中已不在收藏夹的条目为 removed
    if full and seen_ids:
        n_removed = conn.execute(
            "UPDATE items SET removed_at=? WHERE collection_id=? AND removed_at IS NULL AND item_id NOT IN (%s)"
            % ",".join("?" * len(seen_ids)), [scan_at, cid] + sorted(seen_ids)).rowcount
        conn.commit()
        print("  全量扫描：标记 removed %d 条" % n_removed)
    else:
        # 增量模式：仅当接口 item_count 小于库内未删除条目数时提示可能被移除
        active = conn.execute(
            "SELECT COUNT(*) FROM items WHERE collection_id=? AND removed_at IS NULL", (cid,)).fetchone()[0]
        if meta.get("item_count") is not None and meta.get("item_count") < active:
            print("  注意：接口 item_count(%s) < 库内活跃(%d)，可能有条目被移除，建议 --full 校准" %
                  (meta.get("item_count"), active))

    active_count = conn.execute(
        "SELECT COUNT(*) FROM items WHERE collection_id=? AND removed_at IS NULL", (cid,)).fetchone()[0]
    conn.execute("UPDATE collections SET last_synced_count=? WHERE id=?", (active_count, cid))
    conn.commit()

    if export_dir:
        export_collection_items(conn, cid, meta, export_dir, scan_at)

    conn.close()
    return stats, stop_reason, meta


def export_collection_items(conn, cid, meta, export_dir, scan_at):
    os.makedirs(export_dir, exist_ok=True)
    rows = conn.execute(
        """SELECT item_type, collected_at, title, question_title, author_name, voteup_count,
                  comment_count, content_url, excerpt, content_text, content_created, content_updated
           FROM items WHERE collection_id=? AND removed_at IS NULL
           ORDER BY collected_at DESC""", (cid,)).fetchall()
    items = [{"type": r[0], "collected": r[1], "title": r[2], "question_title": r[3],
              "author": r[4], "vote": r[5], "comments": r[6], "url": r[7],
              "excerpt": r[8], "text": r[9], "created": r[10], "updated": r[11]} for r in rows]
    payload = {"collection_id": cid, "collection_title": meta.get("title"),
               "exported_at": scan_at, "item_count": len(items), "items": items}
    path = os.path.join(export_dir, "collection_%s_items.json" % cid)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    print("  已导出 -> %s (%d 条)" % (path, len(items)))


def print_collections_table(cols):
    print("收藏夹列表：")
    print("  %-12s %-20s %-8s %-8s %-8s %-12s" % ("ID", "标题", "公开", "条目数", "浏览", "更新时间"))
    for c in cols:
        print("  %-12s %-20s %-8s %-8s %-8s %-12s" % (
            c["id"], (c.get("title") or "")[:18], "是" if c.get("is_public") else "否",
            c.get("item_count"), c.get("view_count"),
            time.strftime("%Y-%m-%d", time.localtime(c.get("updated_time") or 0))))


# ---------------------------------------------------------------- CLI ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="抓取知乎收藏夹列表与收藏条目并持久化（SQLite + 增量更新）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="抓取用户收藏夹列表（写入 DB，可选 JSON 导出）")
    p_list.add_argument("--token", help="用户 url_token（默认当前登录用户）")
    p_list.add_argument("--out", help="收藏夹列表 JSON 输出路径")
    p_list.add_argument("--db", default=DEFAULT_DB)

    p_fetch = sub.add_parser("fetch", help="抓取收藏夹条目并增量持久化")
    p_fetch.add_argument("--collection", help="收藏夹 ID，或 'all' 抓取该用户全部收藏夹")
    p_fetch.add_argument("--token", help="用户 url_token（--collection all 时使用，默认当前用户）")
    p_fetch.add_argument("--db", default=DEFAULT_DB)
    p_fetch.add_argument("--full", action="store_true", help="全量扫描（默认增量：遇连续已知条目即停）")
    p_fetch.add_argument("--boundary", type=int, default=3, help="增量停止阈值：连续已知条数（默认3）")
    p_fetch.add_argument("--limit", type=int, default=20, help="每页条数（1-20）")
    p_fetch.add_argument("--delay", type=float, default=0.4, help="翻页间隔秒数")
    p_fetch.add_argument("--keep-html", action="store_true", help="同时保存正文原始 HTML")
    p_fetch.add_argument("--export-json", help="导出目录（每收藏夹一个 JSON 文件）")

    for p in (p_list, p_fetch):
        p.add_argument("--session", default=DEFAULT_SESSION)
        p.add_argument("--base", default=DEFAULT_BASE)

    args = ap.parse_args()
    args.limit = min(max(getattr(args, "limit", 20), 1), MAX_LIMIT)

    if args.cmd == "list":
        me = fetch_me(args.base, args.session)
        if me.get("status") != 200:
            raise SystemExit("获取当前用户失败（status=%s），请确认知乎页面已登录" % me.get("status"))
        token = args.token or me.get("url_token")
        print("用户: %s (token=%s)" % (me.get("name"), token))
        cols = fetch_collections(args.base, args.session, token, args.limit)
        print_collections_table(cols)
        conn = open_db(args.db)
        scan_at = time.strftime("%Y-%m-%dT%H:%M:%S")
        for c in cols:
            upsert_collection(conn, c, scan_at)
        conn.close()
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({"token": token, "collections": cols}, f, ensure_ascii=False, indent=1)
            print("已保存 -> %s" % args.out)
        return

    if args.cmd == "fetch":
        me = fetch_me(args.base, args.session)
        if me.get("status") != 200:
            raise SystemExit("获取当前用户失败（status=%s），请确认知乎页面已登录" % me.get("status"))
        token = args.token or me.get("url_token")

        if args.collection and args.collection.lower() != "all":
            cids = [int(args.collection)]
        else:
            cols = fetch_collections(args.base, args.session, token, args.limit)
            if not cols:
                raise SystemExit("该用户没有收藏夹")
            cids = [c["id"] for c in cols]
            print("该用户共 %d 个收藏夹：%s" % (len(cids), ", ".join(str(x) for x in cids)))

        total = {"new": 0, "changed": 0, "unchanged": 0}
        for cid in cids:
            print("==== 抓取收藏夹 %s ====" % cid)
            try:
                stats, stop_reason, meta = fetch_collection_items(
                    args.base, args.session, cid, args.db, full=args.full,
                    boundary=args.boundary, limit=args.limit, delay=args.delay,
                    keep_html=args.keep_html, export_dir=args.export_json)
            except Exception as e:
                print("收藏夹 %s 抓取失败: %s" % (cid, e), file=sys.stderr)
                continue
            for k in total:
                total[k] += stats[k]
            print("  [%s] 新增 %d | 更新 %d | 未变 %d | 翻页 %d | 停止原因: %s" %
                  (cid, stats["new"], stats["changed"], stats["unchanged"],
                   stats["pages"], stop_reason))
        print("==== 汇总: 新增 %d | 更新 %d | 未变 %d ====" %
              (total["new"], total["changed"], total["unchanged"]))
        conn = open_db(args.db)
        n_active = conn.execute("SELECT COUNT(*) FROM items WHERE removed_at IS NULL").fetchone()[0]
        n_all = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        conn.close()
        print("DB 当前状态: 活跃 %d 条 / 总计 %d 条 (%s)" % (n_active, n_all, args.db))


if __name__ == "__main__":
    main()
