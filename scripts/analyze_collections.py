# -*- coding: utf-8 -*-
"""
分析 zhihu_collections.db（fetch_zhihu_collections.py 产出的收藏库），
按主题关键词对收藏条目聚类，生成单文件 HTML 报告：
  - 收藏概况（总量/类型分布/时间跨度）
  - 主题分布（10 个预置主题 + 其他）
  - 各主题 Top N（按赞同数）
  - 近期新增（近 N 天收藏）
  - 全库 Top N
  - 移除记录（removed_at 非空，近 N 天）

纯本地运行，不依赖浏览器/网络。

用法：
  python analyze_collections.py --db zhihu_collections.db --out report.html
  python analyze_collections.py --db zhihu_collections.db --out report.html --top 15 --days 14
  python analyze_collections.py --db zhihu_collections.db --export-json theme.json

说明：
  - 主题判定：关键词命中数（标题+问题标题+摘要+正文前300字），取命中最多者，平局按主题优先级。
  - 正文为社区用户原创内容，报告不做真实性背书。
"""
import argparse
import html
import json
import os
import sqlite3
import sys
import time

# (主题名, 关键词列表[小写])
THEMES = [
    ("AI/大模型", ["ai", "gpt", "claude", "gemini", "大模型", "人工智能", "openai", "deepseek",
                  "codex", "agent", "智能体", "神经网络", "机器学习", "llm", "多模态", "豆包",
                  "通义", "文心", "kimi", "glm", "qwen", "sora", "midjourney", "stable diffusion",
                  "开源模型", "token", "算力", "推理", "训练"]),
    ("编程/开发/开源", ["代码", "编程", "开源", "github", "python", "前端", "后端", "数据库",
                     "程序", "bug", "程序员", "开发者", "软件", "部署", "docker", "kubernetes",
                     "react", "vue", "linux", "架构", "微服务", "面试", "leetcode", "git", "api"]),
    ("影视/娱乐", ["电影", "电视剧", "影评", "导演", "演员", "票房", "综艺", "明星", "网飞",
                 "韩剧", "美剧", "剧集", "奥斯卡", "戛纳", "歌手", "演唱会"]),
    ("财经/商业/股市", ["股票", "股价", "市值", "融资", "财报", "营收", "利润", "创业", "公司",
                     "商业", "市场", "投资", "基金", "比特币", "以太坊", "a股", "港股", "美股",
                     "央行", "利率", "房价", "地产", "车企", "新能源车", "经济"]),
    ("科学/学术/技术", ["物理", "数学", "化学", "生物", "论文", "科研", "研究", "科学", "方程",
                     "定理", "量子", "天体", "宇宙", "基因", "细胞", "气候", "环境", "材料",
                     "航天", "火箭", "卫星"]),
    ("健康/医学/心理", ["健康", "疾病", "医院", "医生", "癌症", "疫苗", "营养", "睡眠", "心理",
                     "焦虑", "抑郁", "健身", "减肥", "血压", "血糖", "发烧", "工伤", "医疗"]),
    ("社会/民生/教育", ["社会", "政策", "教育", "考试", "高考", "就业", "失业", "拆迁", "事故",
                     "车祸", "治安", "法律", "维权", "民生", "政府", "学校", "老师", "学生",
                     "家长", "彩礼", "养老"]),
    ("游戏/动漫", ["游戏", "电竞", "steam", "主机", "任天堂", "索尼", "xbox", "原神", "王者荣耀",
                 "米哈游", "动漫", "漫画", "动画", "二次元"]),
    ("职场/生活/情感", ["职场", "工作", "加班", "收入", "工资", "买房", "结婚", "恋爱", "情感",
                     "家庭", "生活", "旅行", "消费", "奢侈品", "相亲", "做饭"]),
]

FALLBACK = "其他"


def classify(title, qtitle, excerpt, text_head):
    hay = " ".join([title or "", qtitle or "", excerpt or "", text_head or ""]).lower()
    best_theme, best_hits = FALLBACK, 0
    for theme, kws in THEMES:
        hits = sum(1 for k in kws if k.lower() in hay)
        if hits > best_hits:
            best_theme, best_hits = theme, hits
    return best_theme, best_hits


def esc(s):
    return html.escape(s or "")


def main():
    ap = argparse.ArgumentParser(description="收藏库主题聚类分析报告")
    ap.add_argument("--db", default="zhihu_collections.db", help="SQLite 库路径")
    ap.add_argument("--out", default="zhihu_collections_report.html", help="HTML 报告输出路径")
    ap.add_argument("--top", type=int, default=10, help="每个主题展示条数（默认10）")
    ap.add_argument("--days", type=int, default=7, help="近期新增窗口天数（默认7）")
    ap.add_argument("--export-json", help="按主题导出的条目 JSON 路径（可选）")
    args = ap.parse_args()

    if not os.path.exists(args.db):
        raise SystemExit("数据库不存在: %s" % args.db)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    cols = [dict(r) for r in conn.execute("SELECT * FROM collections ORDER BY item_count DESC")]
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM items ORDER BY voteup_count DESC")]
    conn.close()
    if not rows:
        raise SystemExit("items 表为空，请先运行 fetch_zhihu_collections.py")

    now = time.time()
    cutoff = now - args.days * 86400

    def _to_ts(s):
        try:
            return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%dT%H:%M:%S"))
        except Exception:
            return 0

    # 主题聚类
    themes = {t: [] for t, _ in THEMES}
    themes[FALLBACK] = []
    for r in rows:
        theme, hits = classify(r.get("title"), r.get("question_title"), r.get("excerpt"),
                               (r.get("content_text") or "")[:300])
        r["_theme"] = theme
        r["_hits"] = hits
        themes[theme].append(r)

    active = [r for r in rows if not r.get("removed_at")]
    removed = [r for r in rows if r.get("removed_at")]
    recent = [r for r in active if r.get("collected_at") and _to_ts(r["collected_at"]) >= cutoff]
    removed_recent = [r for r in removed if r.get("removed_at") and _to_ts(r["removed_at"]) >= cutoff]

    # 导出 JSON（可选）
    if args.export_json:
        payload = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "themes": {t: [{"item_id": r["item_id"], "type": r["item_type"],
                                   "collected": r.get("collected_at"), "title": r.get("title"),
                                   "question_title": r.get("question_title"),
                                   "author": r.get("author_name"), "vote": r.get("voteup_count"),
                                   "theme": r["_theme"], "hits": r["_hits"]} for r in v]
                              for t, v in themes.items()}}
        with open(args.export_json, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        print("已导出主题 -> %s" % args.export_json)

    # ---- HTML 报告 ----
    theme_order = sorted(themes.items(), key=lambda kv: len(kv[1]), reverse=True)
    max_n = max([len(v) for _, v in theme_order] + [1])
    stat_total = len(active)
    n_answer = sum(1 for r in active if r["item_type"] == "answer")
    n_article = sum(1 for r in active if r["item_type"] == "article")
    n_pin = sum(1 for r in active if r["item_type"] == "pin")
    has_thanks = any((r.get("thanks_count") or 0) for r in active)
    total_thanks = sum((r.get("thanks_count") or 0) for r in active)
    span = ""
    if active:
        dates = [r["collected_at"][:10] for r in active if r.get("collected_at")]
        if dates:
            span = "%s ~ %s" % (min(dates), max(dates))

    def item_card(r, rank=None):
        title = r.get("title") or r.get("question_title") or "(无标题)"
        qlink = None
        if r.get("question_title") and r.get("item_type") == "answer":
            qlink = esc(r["question_title"])
        excerpt = esc((r.get("excerpt") or "")[:120])
        collected = (r.get("collected_at") or "")[:10]
        tag = {"answer": "回答", "article": "文章", "pin": "想法"}.get(r.get("item_type"), r.get("item_type"))
        rank_html = '<span style="display:inline-block;min-width:22px;color:#8a8f98;font-size:12px;">#%d</span>' % rank if rank else ""
        researched = ('<span style="font-size:10px;font-weight:400;color:#237804;background:rgba(82,196,26,0.14);'
                      'border-radius:8px;padding:1px 6px;margin-left:6px;">已调研</span>') if r.get("researched_at") else ""
        _th = r.get("thanks_count") or 0
        _thanks = (" · 感谢%d" % _th) if _th else ""
        return (
            '<div style="padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.05);">'
            '<div style="font-size:13px;font-weight:500;color:#1A1B1C;">%s%s'
            '<span style="font-size:10px;font-weight:400;color:#6B7280;background:rgba(0,0,0,0.05);'
            'border-radius:8px;padding:1px 6px;margin-left:6px;">%s</span>%s</div>'
            '%s'
            '<div style="font-size:11px;color:#6B7280;margin-top:2px;">%s · 赞%s%s · 收藏于%s</div>'
            '<div style="font-size:12px;color:#4b5563;margin-top:3px;">%s</div>'
            '</div>') % (rank_html, esc(title[:60]), tag, researched,
                         ('<div style="font-size:12px;color:#8a8f98;">%s</div>' % qlink) if qlink else "",
                         esc(r.get("author_name") or ""), r.get("voteup_count") or 0, _thanks, collected, excerpt)

    parts = []
    parts.append('<html style="margin:0;padding:0;">')
    parts.append('<div style="background-color:transparent;box-sizing:border-box;">')
    parts.append('<div style="font-family:\'Roboto\',\'PingFang SC\',\'Segoe UI\',Arial,sans-serif;color:#1A1B1C;line-height:1.6;max-width:760px;margin:0 auto;box-sizing:border-box;">')

    # 头部 + 统计卡
    col_title = cols[0]["title"] if cols else "我的收藏"
    parts.append('<div style="padding:16px 18px;background:linear-gradient(135deg, rgba(155,187,244,0.18), rgba(155,187,244,0.06));border-radius:14px;margin:16px 0;box-sizing:border-box;">')
    parts.append('<div style="font-size:17px;font-weight:600;">收藏分析报告 · %s</div>' % esc(col_title))
    parts.append('<div style="font-size:12px;color:#6B7280;margin-top:4px;">生成时间 %s · 数据源 zhihu_collections.db（知乎官方接口抓取，社区原创内容）</div>' % time.strftime("%Y-%m-%d %H:%M"))
    cards = [("活跃条目", stat_total), ("回答/文章/想法", "%d/%d/%d" % (n_answer, n_article, n_pin)),
             ("主题数", len([t for t, v in themes.items() if v])), ("收藏跨度", span)]
    if has_thanks:
        cards.append(("累计感谢", total_thanks))
    cards_html = []
    for label, val in cards:
        cards_html.append('<div style="flex:1 1 120px;min-width:0;padding:10px 12px;background:rgba(255,255,255,0.75);border-radius:12px;box-sizing:border-box;">'
                          '<div style="font-size:11px;color:#6B7280;">%s</div><div style="font-size:17px;font-weight:600;margin-top:2px;word-break:break-all;">%s</div></div>' % (label, esc(str(val))))
    parts.append('<div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:12px;box-sizing:border-box;">%s</div>' % "".join(cards_html))
    parts.append('</div>')

    # 主题分布
    parts.append('<div style="margin:18px 0 8px 0;font-size:14px;font-weight:600;">主题分布</div>')
    parts.append('<div style="border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:12px 16px;background:#fff;box-sizing:border-box;">')
    for theme, items in theme_order:
        n = len(items)
        w = int(round(n * 100.0 / max_n))
        parts.append('<div style="margin:6px 0;box-sizing:border-box;">'
                     '<div style="display:flex;justify-content:space-between;font-size:12px;"><span>%s</span><span style="color:#6B7280;">%d 条</span></div>'
                     '<div style="height:8px;background:rgba(0,0,0,0.05);border-radius:4px;margin-top:3px;overflow:hidden;box-sizing:border-box;">'
                     '<div style="width:%d%%;height:100%%;background:#9BBBF4;border-radius:4px;"></div></div></div>' % (esc(theme), n, max(w, 2)))
    parts.append('</div>')

    # 各主题 Top
    for theme, items in theme_order:
        if not items:
            continue
        top = sorted(items, key=lambda r: r.get("voteup_count") or 0, reverse=True)[:args.top]
        parts.append('<div style="margin:18px 0 8px 0;font-size:14px;font-weight:600;">%s <span style="font-size:12px;color:#6B7280;font-weight:400;">共 %d 条 · 展示 Top %d</span></div>' % (esc(theme), len(items), len(top)))
        parts.append('<div style="border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:6px 16px;background:#fff;box-sizing:border-box;">%s</div>' % "".join(item_card(r, i + 1) for i, r in enumerate(top)))

    # 近期新增
    parts.append('<div style="margin:18px 0 8px 0;font-size:14px;font-weight:600;">近 %d 天新增收藏（%d 条）</div>' % (args.days, len(recent)))
    if recent:
        top_recent = sorted(recent, key=lambda r: r.get("collected_at") or "", reverse=True)[:20]
        parts.append('<div style="border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:6px 16px;background:#fff;box-sizing:border-box;">%s</div>' % "".join(item_card(r) for r in top_recent))
    else:
        parts.append('<div style="font-size:12px;color:#6B7280;padding:8px 2px;">近 %d 天无新增收藏</div>' % args.days)

    # 移除记录
    if removed_recent:
        parts.append('<div style="margin:18px 0 8px 0;font-size:14px;font-weight:600;">近 %d 天移除（%d 条）</div>' % (args.days, len(removed_recent)))
        parts.append('<div style="border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:6px 16px;background:#fff;box-sizing:border-box;">%s</div>' % "".join(item_card(r) for r in removed_recent[:20]))

    # 全库 Top
    top_all = sorted(active, key=lambda r: r.get("voteup_count") or 0, reverse=True)[:args.top]
    parts.append('<div style="margin:18px 0 8px 0;font-size:14px;font-weight:600;">全库最高赞同 Top %d</div>' % len(top_all))
    parts.append('<div style="border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:6px 16px;background:#fff;box-sizing:border-box;">%s</div>' % "".join(item_card(r, i + 1) for i, r in enumerate(top_all)))

    if has_thanks:
        top_th = sorted(active, key=lambda r: r.get("thanks_count") or 0, reverse=True)[:args.top]
        parts.append('<div style="margin:18px 0 8px 0;font-size:14px;font-weight:600;">全库最高认可 Top %d <span style="font-size:12px;color:#6B7280;font-weight:400;">按感谢数</span></div>' % len(top_th))
        parts.append('<div style="border:1px solid rgba(0,0,0,0.08);border-radius:14px;padding:6px 16px;background:#fff;box-sizing:border-box;">%s</div>' % "".join(item_card(r, i + 1) for i, r in enumerate(top_th)))

    parts.append('<div style="font-size:11px;color:#8a8f98;margin:20px 2px 8px 2px;">说明：主题由关键词命中数自动判定，仅供归类参考；正文为知乎社区用户原创观点，报告不做真实性背书；接口未返回的已删除/折叠条目不在统计内。</div>')
    parts.append('</div></div></html>')

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))

    # 控制台摘要
    print("收藏分析完成 -> %s" % args.out)
    print("活跃 %d | 回答 %d | 文章 %d | 想法 %d | 主题分布:" % (stat_total, n_answer, n_article, n_pin))
    for theme, items in theme_order:
        if items:
            print("  %-14s %d" % (theme, len(items)))
    print("近 %d 天新增 %d 条 | 近 %d 天移除 %d 条" % (args.days, len(recent), args.days, len(removed_recent)))


if __name__ == "__main__":
    main()
