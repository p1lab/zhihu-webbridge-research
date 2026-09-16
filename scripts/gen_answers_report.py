# -*- coding: utf-8 -*-
"""
知乎问题调研报告通用生成器（确定性逻辑，纯本地）。

输入：fetch_zhihu_answers.py 产出的回答 JSON（每条含 author/vote/comments/created/text）。
输出：单文件 HTML 调研报告，结构：
  1. 标题头（问题、qid、抓取说明）
  2. 概况卡（回答数 / 累计赞同 / 时间跨度 / 最高赞）
  3. 核心观点速览（★新增）：高赞 Top N 观点 + 各主题代表观点
  4. 主题导航 + 按主题分组的全部回答（作者/赞同/评论/日期/全文）

用法：
  python gen_answers_report.py --answers answers.json --out report.html \
      --qid 2075318872438265091 --title "问题标题" [--top 5] [--notes "备注"]

说明：
  - 主题归类基于关键词命中数（见 THEMES），可自行增删。
  - "观点摘要"为确定性抽取：取正文中第一个长度>=15 的句子（跳过"谢邀"等短句），截断至 90 字。
  - 回答为社区用户原创观点，报告不背书真实性。
"""
import argparse
import datetime
import html
import json
import re
import sys
from collections import Counter

THEMES = [
    ("影响评估", ["影响", "没什么影响", "没有影响", "不至于", "无所谓", "涨价", "优惠取消",
               "还能", "能用", "跑路", "羊毛", "难受", "缺点", "问题不大", "砍了", "后果"]),
    ("替代方案与 Plan 推荐", ["plan", "套餐", "推荐", "替代", "平替", "token", "lite", "pro",
                           "gemini", "智谱", "qwen", "千问", "deepseek", "claude", "gpt",
                           "glm", "kimi", "豆包", "基元", "squilla", "免费", "白嫖"]),
    ("白嫖与省钱技巧", ["白嫖", "免费", "积分", "薅", "省", "拼", "共享", "记忆", "对齐", "白嫖党"]),
    ("官方动态", ["发布", "更新", "版本", "公告", "官方", "上线", "新增", "v2", "v3", "beta", "灰度"]),
    ("平台吐槽与质量问题", ["降智", "克扣", "福利", "换账号", "麻烦", "辣鸡", "垃圾", "坑",
                        "bug", "不稳", "识别", "阉割", "缩水", "烂"]),
    ("个人体验", ["我用", "我的", "体验", "实测", "用了", "一直用", "之前用", "我现在"]),
]


def theme_of(text):
    best, best_hits = "其他", 0
    for name, kws in THEMES:
        hits = sum(1 for k in kws if k in text)
        if hits > best_hits:
            best, best_hits = name, hits
    return best


def esc(s):
    return html.escape(s or "")


def fmt_ts(ts):
    if not ts:
        return ""
    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return ""


def summary_sentence(text, maxlen=90):
    """确定性观点摘要：取第一个长度>=15 的句子，否则取正文开头。"""
    text = (text or "").strip()
    if not text:
        return ""
    sentences = [s.strip() for s in re.split(r"[。！？!?\n]+", text) if s.strip()]
    for s in sentences:
        if len(s) >= 15:
            return s[:maxlen] + ("…" if len(s) > maxlen else "")
    return text[:maxlen] + ("…" if len(text) > maxlen else "")


def build(data, qid, qtitle, notes, top_n):
    data = sorted(data, key=lambda a: a.get("vote", 0), reverse=True)
    groups = {}
    for a in data:
        groups.setdefault(theme_of((a.get("text") or "") + (a.get("author") or "")), []).append(a)

    total_votes = sum(a.get("vote", 0) for a in data)
    dates = [fmt_ts(a.get("created")) for a in data if a.get("created")]
    date_span = "%s ~ %s" % (min(dates), max(dates)) if dates else ""
    total_thanks = sum((a.get("thanks") or 0) for a in data)
    regions = Counter()
    for a in data:
        _ip = (a.get("ip") or "").replace("IP 属地", "").strip()
        if _ip:
            regions[_ip] += 1
    top_region, top_region_n = (regions.most_common(1)[0] if regions else ("", 0))

    # 核心观点速览：高赞 Top N + 各主题代表
    top_answers = data[:top_n]
    theme_reps = []
    for name in sorted(groups.keys()):
        items = sorted(groups[name], key=lambda a: a.get("vote", 0), reverse=True)
        rep = items[0]
        theme_reps.append((name, len(items), rep))

    P = []
    P.append('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">')
    P.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    P.append('<title>%s</title>' % esc(qtitle))
    P.append('<style>')
    P.append('body{font-family:"Roboto","PingFang SC","Segoe UI",Arial,sans-serif;color:#1A1B1C;background:#F4F3EE;margin:0;line-height:1.7}')
    P.append('.wrap{max-width:860px;margin:0 auto;padding:24px 16px 60px}')
    P.append('.head{background:linear-gradient(135deg,#0f1c2e,#1f3a5f);color:#fff;border-radius:16px;padding:28px 26px;margin-bottom:20px}')
    P.append('.head h1{font-size:21px;margin:0 0 8px;line-height:1.5}')
    P.append('.head .meta{font-size:12.5px;opacity:.85}')
    P.append('.cards{display:flex;gap:12px;flex-wrap:wrap;margin:18px 0}')
    P.append('.card{flex:1 1 140px;min-width:0;background:#fff;border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:12px 14px}')
    P.append('.card .num{font-size:22px;font-weight:600;color:#1f3a5f}')
    P.append('.card .lab{font-size:12px;color:#6B7280}')
    P.append('.vwrap{background:#fff;border:1px solid rgba(0,0,0,.08);border-radius:14px;padding:18px 20px;margin:16px 0 8px}')
    P.append('.vwrap h2{font-size:15px;margin:0 0 12px;color:#1f3a5f}')
    P.append('.vitem{display:flex;gap:10px;padding:8px 0;border-bottom:1px dashed rgba(0,0,0,.06)}')
    P.append('.vitem:last-child{border-bottom:none}')
    P.append('.vrank{flex:0 0 26px;font-size:13px;font-weight:600;color:#fff;background:#1f3a5f;border-radius:8px;height:24px;line-height:24px;text-align:center;margin-top:1px}')
    P.append('.vbody{flex:1;min-width:0}')
    P.append('.vbody .who{font-size:12px;color:#6B7280;margin-bottom:2px}')
    P.append('.vbody .who b{color:#1A1B1C}')
    P.append('.vbody .sum{font-size:13.5px;color:#1A1B1C}')
    P.append('.trow{display:flex;gap:10px;padding:7px 0;border-bottom:1px dashed rgba(0,0,0,.06);align-items:flex-start}')
    P.append('.trow:last-child{border-bottom:none}')
    P.append('.tname{flex:0 0 118px;font-size:12.5px;font-weight:600;color:#1f3a5f}')
    P.append('.trow .sum{flex:1;min-width:0;font-size:13px;color:#4b5563}')
    P.append('.nav{display:flex;gap:8px;flex-wrap:wrap;margin:14px 0 8px}')
    P.append('.nav a{font-size:12.5px;color:#1f3a5f;background:#fff;border:1px solid rgba(0,0,0,.1);border-radius:999px;padding:5px 12px;text-decoration:none}')
    P.append('.nav a:hover{background:#eef3fa}')
    P.append('.sec{margin:34px 0 12px;font-size:17px;font-weight:600;color:#1f3a5f;border-left:4px solid #1f3a5f;padding-left:10px}')
    P.append('.cnt{font-size:12.5px;color:#6B7280;font-weight:400;margin-left:8px}')
    P.append('.ans{background:#fff;border:1px solid rgba(0,0,0,.08);border-radius:12px;padding:16px 18px;margin:12px 0}')
    P.append('.ans .who{font-size:12.5px;color:#6B7280;margin-bottom:8px}')
    P.append('.ans .who b{color:#1A1B1C}')
    P.append('.ans .txt{font-size:14px;white-space:pre-wrap;word-break:break-word}')
    P.append('.foot{font-size:12px;color:#8a8f98;margin-top:40px;border-top:1px solid rgba(0,0,0,.08);padding-top:12px}')
    P.append('</style></head><body><div class="wrap">')

    P.append('<div class="head"><h1>%s</h1>' % esc(qtitle))
    P.append('<div class="meta">问题 %s ｜ 抓取自知乎官方回答接口 ｜ 社区用户原创观点，非权威事实，仅供参考%s</div></div>' % (
        qid, (" ｜ " + esc(notes)) if notes else ""))

    P.append('<div class="cards">')
    P.append('<div class="card"><div class="num">%d</div><div class="lab">回答数</div></div>' % len(data))
    P.append('<div class="card"><div class="num">%d</div><div class="lab">累计赞同</div></div>' % total_votes)
    P.append('<div class="card"><div class="num">%s</div><div class="lab">时间跨度</div></div>' % esc(date_span))
    top = data[0]
    P.append('<div class="card"><div class="num">%d</div><div class="lab">最高赞（%s）</div></div>' % (
        top.get("vote", 0), esc(top.get("author"))))
    if total_thanks:
        P.append('<div class="card"><div class="num">%d</div><div class="lab">累计感谢</div></div>' % total_thanks)
    if top_region:
        P.append('<div class="card"><div class="num">%s</div><div class="lab">最多地区（%d 人）</div></div>' % (
            esc(top_region), top_region_n))
    P.append('</div>')

    # 核心观点速览
    P.append('<div class="vwrap"><h2>核心观点速览</h2>')
    P.append('<div style="font-size:12px;color:#6B7280;margin:-6px 0 8px;">高赞 Top %d · 摘要为回答开头原句截取</div>' % top_n)
    for i, a in enumerate(top_answers, 1):
        P.append('<div class="vitem"><div class="vrank">%d</div><div class="vbody">'
                 '<div class="who"><b>%s</b> · 赞 %s · %s</div>'
                 '<div class="sum">%s</div></div></div>' % (
                     i, esc(a.get("author") or ""), a.get("vote", 0), fmt_ts(a.get("created")),
                     esc(summary_sentence(a.get("text")))))
    P.append('<div style="font-size:12px;color:#6B7280;margin:12px 0 4px;">各主题代表观点</div>')
    for name, cnt, rep in theme_reps:
        P.append('<div class="trow"><div class="tname">%s（%d）</div>'
                 '<div class="sum"><b>%s</b>（赞%s）：%s</div></div>' % (
                     esc(name), cnt, esc(rep.get("author") or ""), rep.get("vote", 0),
                     esc(summary_sentence(rep.get("text"), 70))))
    P.append('</div>')

    order = sorted(groups.keys())
    P.append('<div class="nav">')
    for name in order:
        P.append('<a href="#%s">%s（%d）</a>' % (esc(name), esc(name), len(groups[name])))
    P.append('</div>')

    for name in order:
        items = sorted(groups[name], key=lambda a: a.get("vote", 0), reverse=True)
        P.append('<h2 class="sec" id="%s">%s<span class="cnt">%d 条</span></h2>' % (esc(name), esc(name), len(items)))
        for a in items:
            _ex = ""
            if a.get("thanks"):
                _ex += " · 感谢 %s" % a.get("thanks")
            _ip = (a.get("ip") or "").replace("IP 属地", "").strip()
            if _ip:
                _ex += " · %s" % esc(_ip)
            P.append('<div class="ans"><div class="who"><b>%s</b> · 赞 %s · 评论 %s%s · %s</div>'
                     '<div class="txt">%s</div></div>' % (
                         esc(a.get("author") or ""), a.get("vote", 0), a.get("comments", 0), _ex,
                         fmt_ts(a.get("created")), esc(a.get("text") or "")))

    P.append('<div class="foot">数据源：知乎官方回答接口（www.zhihu.com/api/v4/questions/%s/answers）｜ 回答为社区用户原创观点，整理时未做真实性背书；页面回答数与接口返回数可能有差（折叠/删除不返回）。</div>' % qid)
    P.append('</div></body></html>')
    return "\n".join(P)


def main():
    ap = argparse.ArgumentParser(description="知乎问题调研报告生成器")
    ap.add_argument("--answers", required=True, help="fetch_zhihu_answers 输出的回答 JSON")
    ap.add_argument("--out", required=True, help="输出 HTML 路径")
    ap.add_argument("--qid", required=True, help="问题 ID")
    ap.add_argument("--title", required=True, help="问题标题")
    ap.add_argument("--top", type=int, default=5, help="高赞速览条数（默认5）")
    ap.add_argument("--notes", default="", help="页头备注（如调研日期/主题说明）")
    args = ap.parse_args()

    data = json.load(open(args.answers, encoding="utf-8"))
    out = build(data, args.qid, args.title, args.notes, args.top)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(out)
    groups = {}
    for a in data:
        t = theme_of((a.get("text") or "") + (a.get("author") or ""))
        groups[t] = groups.get(t, 0) + 1
    print("报告已生成 ->", args.out)
    print("回答 %d 条 | 主题分布: %s" % (len(data), groups))


if __name__ == "__main__":
    main()
