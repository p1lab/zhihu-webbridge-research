# -*- coding: utf-8 -*-
"""
调研成果归档与数据库标记工具。

解决两个问题：
  1. 调研过某个知乎问题后，把成果（报告/抓取数据/中间产物）集中归档到一个文件夹；
  2. 在 zhihu_collections.db 中标记相关收藏条目"已调研"，避免重复调研、便于追踪。

归档目录规范（默认 <db所在目录>/research/）：
  research/{question_id}_{题目前缀}/
    ├── manifest.json   调研元数据（qid、题目、时间、关联条目、文件清单、备注）
    ├── report.*        调研报告（--report 复制而来）
    ├── answers.json    抓取数据（--data 复制而来）
    └── ...             其他成果（--extra 可多次）

数据库变更（幂等迁移，不破坏既有数据）：
  - 新建 research 表：research_id(=question_id), question_id, question_title,
    research_dir, created_at, notes
  - items 表新增列：researched_at TEXT, research_dir TEXT
    （标记该收藏条目已被调研；--link-items 或自动按 question_id 关联）

用法：
  python research_register.py register --question 2079574865397532387 \
      --report NS方程AI解决_知乎调研报告.html --data question_2079574865397532387_answers.json \
      --notes "含高赞Top与主题整理"
  python research_register.py register --question <qid> --title "自定义题目" --link-items id1,id2
  python research_register.py status --question <qid>
  python research_register.py list

说明：
  - register 是纯本地文件/DB 操作，不依赖浏览器；重复对同一 qid register 视为更新（幂等）。
  - 自动关联：库中 items.question_id 等于该 qid 的条目会被标记；还可 --link-items 手动补标。
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time

DEFAULT_DB = "zhihu_collections.db"
LINK_COLUMNS = ["researched_at", "research_dir"]


# ---------------------------------------------------------------- DB 迁移 ----------------------------------------------------------------

def ensure_schema(conn):
    """幂等迁移：建 research 表；items 表补列。"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research (
          research_id TEXT PRIMARY KEY,
          question_id TEXT,
          question_title TEXT,
          research_dir TEXT,
          created_at TEXT,
          notes TEXT
        )""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(items)")}
    for c in LINK_COLUMNS:
        if c not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN %s TEXT" % c)
    conn.commit()


# ---------------------------------------------------------------- 工具 ----------------------------------------------------------------

def clean_dir_name(qid, title):
    """research 目录名：{qid}_{题目前缀}，去非法字符，限长。"""
    t = re.sub(r'[\\/:*?"<>|\s]+', "_", title or "").strip("_")
    t = t[:20].rstrip("._")
    return "%s_%s" % (qid, t) if t else qid


def q_title_from_db(conn, qid):
    r = conn.execute("SELECT question_title FROM items WHERE question_id=? AND question_title IS NOT NULL LIMIT 1",
                     (str(qid),)).fetchone()
    return r[0] if r else None


def q_items_from_db(conn, qid):
    rows = conn.execute("SELECT item_id, collected_at FROM items WHERE question_id=? ORDER BY collected_at",
                        (str(qid),)).fetchall()
    return [{"item_id": r[0], "collected_at": r[1]} for r in rows]


def copy_into(src, dst_dir, kind, manifest_files):
    """复制成果文件进目录并登记到 manifest.files；返回是否成功。"""
    if not src:
        return None
    if not os.path.exists(src):
        print("  警告: 文件不存在，跳过: %s" % src, file=sys.stderr)
        return None
    name = os.path.basename(src)
    dst = os.path.join(dst_dir, name)
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copy2(src, dst)
    if not any(f["name"] == name for f in manifest_files):
        manifest_files.append({"name": name, "type": kind, "path": dst})
    return dst


# ---------------------------------------------------------------- 子命令 ----------------------------------------------------------------

def cmd_register(args):
    conn = sqlite3.connect(args.db)
    ensure_schema(conn)

    qid = str(args.question)
    title = args.title or q_title_from_db(conn, qid) or qid
    root = os.path.abspath(args.root)
    os.makedirs(root, exist_ok=True)
    d = os.path.join(root, clean_dir_name(qid, title))
    os.makedirs(d, exist_ok=True)

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    manifest_path = os.path.join(d, "manifest.json")
    old = {}
    if os.path.exists(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            old = json.load(f)

    files = list(old.get("files", []))
    copy_into(args.report, d, "report", files)
    copy_into(args.data, d, "data", files)
    for src in args.extra or []:
        copy_into(src, d, "extra", files)

    linked = q_items_from_db(conn, qid)
    if args.link_items:
        for iid in args.link_items.split(","):
            iid = iid.strip()
            if iid and not any(x["item_id"] == iid for x in linked):
                linked.append({"item_id": iid, "collected_at": None})

    manifest = {
        "research_id": qid,
        "question_id": qid,
        "question_title": title,
        "created_at": old.get("created_at", now),
        "updated_at": now,
        "notes": args.notes or old.get("notes", ""),
        "linked_items": linked,
        "files": files,
        "research_dir": d,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    # 更新 research 表（upsert）
    conn.execute(
        """INSERT INTO research (research_id, question_id, question_title, research_dir, created_at, notes)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(research_id) DO UPDATE SET
             question_title=excluded.question_title, research_dir=excluded.research_dir,
             notes=excluded.notes""",
        (qid, qid, title, d, now, manifest["notes"]))

    # 标记关联条目（幂等）
    n_marked = 0
    for it in linked:
        before = conn.execute("SELECT researched_at FROM items WHERE item_id=?", (it["item_id"],)).fetchone()
        if not (before and before[0]):
            n_marked += 1
        conn.execute("UPDATE items SET researched_at=?, research_dir=? WHERE item_id=?",
                     (now, d, it["item_id"]))
    conn.commit()

    print("已登记调研成果 -> %s" % d)
    print("  问题: %s | %s" % (qid, title))
    print("  关联条目 %d 条（本次标记 %d 条，含已有标记）| 文件 %d 个" %
          (len(linked), n_marked, len(files)))
    print("  manifest -> %s" % manifest_path)
    conn.close()


def cmd_status(args):
    conn = sqlite3.connect(args.db)
    ensure_schema(conn)
    qid = str(args.question)
    r = conn.execute("SELECT * FROM research WHERE question_id=?", (qid,)).fetchone()
    if not r:
        print("未调研过该问题: %s" % qid)
        conn.close()
        return 1
    n = conn.execute("SELECT COUNT(*) FROM items WHERE research_dir=? AND researched_at IS NOT NULL",
                     (r[3],)).fetchone()[0]
    print("已调研 ✅")
    print("  问题: %s | %s" % (r[1], r[2]))
    print("  登记时间: %s" % r[4])
    print("  备注: %s" % (r[5] or "(无)"))
    print("  成果目录: %s" % r[3])
    print("  库内关联标记条目: %d 条" % n)
    mp = os.path.join(r[3], "manifest.json")
    if os.path.exists(mp):
        m = json.load(open(mp, encoding="utf-8"))
        print("  文件清单:")
        for f in m.get("files", []):
            print("    [%s] %s" % (f.get("type"), f.get("name")))
    conn.close()
    return 0


def cmd_list(args):
    conn = sqlite3.connect(args.db)
    ensure_schema(conn)
    rows = conn.execute("SELECT question_id, question_title, research_dir, created_at FROM research ORDER BY created_at DESC").fetchall()
    if not rows:
        print("暂无调研记录")
        conn.close()
        return
    print("共 %d 次调研记录：" % len(rows))
    for qid, title, d, created in rows:
        n = conn.execute("SELECT COUNT(*) FROM items WHERE research_dir=?", (d,)).fetchone()[0]
        print("  %s | %s | %s | 关联标记 %d 条" % (created[:10], qid, (title or "")[:40], n))
    conn.close()


# ---------------------------------------------------------------- CLI ----------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="知乎调研成果归档与收藏库标记")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_reg = sub.add_parser("register", help="登记一次调研：归档成果 + 标记 DB")
    p_reg.add_argument("--question", required=True, help="问题 ID")
    p_reg.add_argument("--title", help="问题标题（缺省自动从库中查）")
    p_reg.add_argument("--report", help="调研报告文件路径（复制进归档目录）")
    p_reg.add_argument("--data", help="抓取数据文件路径（复制进归档目录）")
    p_reg.add_argument("--extra", action="append", help="其他成果文件（可多次指定）")
    p_reg.add_argument("--notes", help="备注")
    p_reg.add_argument("--link-items", help="手动补标条目 id（逗号分隔，额外于自动关联）")

    p_st = sub.add_parser("status", help="查询某问题是否已调研")
    p_st.add_argument("--question", required=True, help="问题 ID")

    p_li = sub.add_parser("list", help="列出全部调研记录")
    p_li.add_argument("--db", default=DEFAULT_DB)
    p_li.add_argument("--root", default="research", help="调研成果根目录（默认 <db 目录>/research）")

    for p in (p_reg, p_st):
        p.add_argument("--db", default=DEFAULT_DB)
        p.add_argument("--root", default="research", help="调研成果根目录（默认 <db 目录>/research）")

    args = ap.parse_args()
    if not getattr(args, "db", None):
        args.db = DEFAULT_DB
    if not os.path.exists(args.db):
        raise SystemExit("数据库不存在: %s" % args.db)
    if getattr(args, "root", "research") == "research":
        args.root = os.path.join(os.path.dirname(os.path.abspath(args.db)), "research")

    if args.cmd == "register":
        cmd_register(args)
    elif args.cmd == "status":
        sys.exit(cmd_status(args))
    elif args.cmd == "list":
        cmd_list(args)


if __name__ == "__main__":
    main()
