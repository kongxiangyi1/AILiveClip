# -*- coding: utf-8 -*-
"""阶段5 · 发布合规检查。

读取 temp/clips_refined.json + temp/transcript.json，按切片边界扫描合规风险词，
输出:
  - temp/compliance_report.md   人读报告(结论 + 逐片明细 + 处置建议 + AI 待办)
  - temp/compliance_report.json 结构化结果(含待处理区间, 供 mute/cut 后续消费)

纯标准库, 不依赖 funasr/torch, 任意 Python 3.8+ 可直接跑。

用法(工作区根目录):
  python <SCRIPT_DIR>/compliance_scan.py
  python <SCRIPT_DIR>/compliance_scan.py --limit 3              # 两段式试跑(纪律27)
  python <SCRIPT_DIR>/compliance_scan.py --rules my_rules.json  # 自定义词表
  python <SCRIPT_DIR>/compliance_scan.py --clips X.json --transcript Y.json --out R.md

词表: 默认读 <SCRIPT_DIR>/compliance_rules.json(可直接编辑); 优先级
  --rules > 工作区 ./compliance_rules.json > 脚本同目录 > 内置兜底表
"""
import argparse
import json
import os
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# 内置兜底表(合规词表文件缺失时使用, 精简版)
FALLBACK_RULES = {
    "version": 1,
    "thresholds": {"drop_red_sentences_ge": 5, "drop_red_cover_ratio_ge": 0.5,
                   "escalate_privacy_distinct_ge": 3, "merge_gap_ms": 1500},
    "categories": [
        {"id": "politics", "name": "政治/时政敏感", "level": "RED", "action": "CUT",
         "advice": "删除或消音命中句, 标题同步改写。",
         "words": ["翻墙", "梯子", "VPN", "外网", "内网", "粉红", "反贼", "遥遥领先",
                   "新闻联播", "焦点访谈", "央视", "政治", "占山为王", "热兵器",
                   "没有信仰", "无神论", "谣言", "逃离", "愚弄", "抹黑", "带节奏"]},
        {"id": "profanity", "name": "低俗粗口", "level": "YELLOW", "action": "MUTE",
         "advice": "用 mute_profanity.py 对区间静音。",
         "words": ["他妈", "妈的", "我操", "傻逼", "鸡巴", "牛马", "畜生"]},
    ],
}

LEVEL_ORDER = {"RED": 2, "YELLOW": 1, "GREEN": 0}
ACTION_TEXT = {
    "DROP": "不建议发布(删不干净)",
    "CUT": "删句/消音后可发",
    "MUTE": "粗口区间静音后可发",
    "RETITLE": "改标题/改口播后可发",
    "CONSENT": "需取得当事人授权后发",
    "REVIEW": "人工复核后决定",
    "OK": "可直接发布",
}
# 同类多动作时取更严格者
ACTION_RANK = {"OK": 0, "REVIEW": 1, "RETITLE": 2, "CONSENT": 3, "MUTE": 4, "CUT": 5, "DROP": 6}


# ---------- IO ----------
def read_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_rules(explicit=None):
    candidates = []
    if explicit:
        candidates.append(explicit)
    candidates.append(os.path.join(os.getcwd(), 'compliance_rules.json'))
    candidates.append(os.path.join(SCRIPT_DIR, 'compliance_rules.json'))
    for p in candidates:
        if os.path.isfile(p):
            try:
                r = read_json(p)
                if r.get('extends'):
                    base = json.loads(json.dumps(FALLBACK_RULES))
                    ids = {c['id']: i for i, c in enumerate(base['categories'])}
                    for c in r.get('categories', []):
                        if c['id'] in ids:
                            base['categories'][ids[c['id']]] = c
                        else:
                            base['categories'].append(c)
                    base['thresholds'].update(r.get('thresholds', {}))
                    base['_source'] = p
                    return base
                r['_source'] = p
                return r
            except Exception as e:
                print(f"[warn] 词表 {p} 解析失败: {e}, 回退内置表", file=sys.stderr)
    base = json.loads(json.dumps(FALLBACK_RULES))
    base['_source'] = '<builtin>'
    return base


def extract_clips(obj):
    if isinstance(obj, list):
        return obj
    for k in ('clips', 'segments', 'items', 'data'):
        if isinstance(obj.get(k), list):
            return obj[k]
    raise ValueError('clips 文件结构无法识别(需 list 或含 clips/segments 键)')


def extract_sentences(obj):
    if isinstance(obj, list):
        return obj
    for k in ('sentences', 'sents', 'utterances'):
        if isinstance(obj.get(k), list):
            return obj[k]
    raise ValueError('transcript 文件结构无法识别(需含 sentences 键)')


# ---------- 时间 ----------
def fmt_ms(ms):
    s = max(0, int(ms)) // 1000
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def fmt_s(sec):
    sec = max(0, int(sec))
    return f"{sec // 60:02d}:{sec % 60:02d}"


def merge_ranges(ranges, gap_ms):
    """合并相近区间。ranges: [(start_ms, end_ms, text, [cat_id])]"""
    if not ranges:
        return []
    ranges = sorted(ranges, key=lambda r: r[0])
    out = [list(ranges[0])]
    for r in ranges[1:]:
        last = out[-1]
        if r[0] - last[1] <= gap_ms:
            last[1] = max(last[1], r[1])
            last[2] = last[2] + " / " + r[2]
            for c in r[3]:
                if c not in last[3]:
                    last[3].append(c)
        else:
            out.append(list(r))
    return out


# ---------- 扫描 ----------
def scan_clip(clip, sents, rules, th):
    st, en = int(clip['start_ms']), int(clip['end_ms'])
    title = str(clip.get('title') or '')
    duration = max(1, en - st)

    body = [s for s in sents if st <= int(s['start_ms']) < en]
    text = "\n".join(str(s.get('text', '')) for s in body)

    cats = {}       # id -> {"name","level","action","advice","words":{w:n}}
    hit_sents = []  # (start,end,text,[cat_id])

    for c in rules['categories']:
        found = {}
        for w in c.get('words', []):
            n = text.count(w)
            if n:
                found[w] = n
        if found:
            cats[c['id']] = {"id": c['id'], "name": c['name'],
                             "level": c.get('level', 'YELLOW'),
                             "action": c.get('action', 'REVIEW'),
                             "advice": c.get('advice', ''),
                             "words": found, "n_sent": 0}

    # 各类命中句数(用于弃片判定)
    for s in body:
        t = str(s.get('text', ''))
        for c in rules['categories']:
            if c['id'] in cats and any(w in t for w in c.get('words', [])):
                cats[c['id']]['n_sent'] += 1

    for s in body:
        t = str(s.get('text', ''))
        a, b = int(s['start_ms']), int(s.get('end_ms', s['start_ms']))
        cs = [c['id'] for c in rules['categories']
              if any(w in t for w in c.get('words', []))]
        if cs:
            hit_sents.append((a, b, t, cs))

    # 等级: 最高类别等级; 隐私类命中不同词 >=N 提 RED
    level = 'GREEN'
    for v in cats.values():
        if LEVEL_ORDER.get(v['level'], 0) > LEVEL_ORDER[level]:
            level = v['level']
    priv = cats.get('privacy')
    if priv and len(priv['words']) >= int(th.get('escalate_privacy_distinct_ge', 3)):
        level = 'RED'
        priv['level'] = 'RED'
        priv['action'] = 'CONSENT'

    # 红色区间(用于删句/消音)
    red_ids = {c['id'] for c in rules['categories']
               if c.get('level') == 'RED' and c['id'] in cats}
    # 仅当隐私类本身被升级为 RED 时才纳入删句区间(否则普通隐私命中只提示授权)
    if priv and priv['level'] == 'RED':
        red_ids.add('privacy')
    red_ranges = merge_ranges([r for r in hit_sents if set(r[3]) & red_ids],
                              int(th.get('merge_gap_ms', 1500)))
    mute_ranges = merge_ranges([r for r in hit_sents if 'profanity' in r[3]],
                               int(th.get('merge_gap_ms', 1500)))

    # 弃片判定: ① 违法/违规工具类成片主题(该类命中 >=2 句) ② 红句极多 ③ 红句覆盖时长过半
    red_cover = sum(min(r[1], en) - max(r[0], st) for r in red_ranges)
    drop = False
    if cats.get('illegal_tool', {}).get('n_sent', 0) >= int(th.get('drop_tool_sentences_ge', 2)):
        drop = True
    if len(red_ranges) >= int(th.get('drop_red_sentences_ge', 8)):
        drop = True
    if duration and red_cover / duration >= float(th.get('drop_red_cover_ratio_ge', 0.7)):
        drop = True

    # 动作
    if drop:
        action = 'DROP'
    elif level == 'RED':
        # 隐私升级为 RED 的, 授权优先于删句
        action = 'CONSENT' if (priv and priv['level'] == 'RED') else 'CUT'
    elif level == 'YELLOW':
        acts = [v['action'] for v in cats.values()]
        action = sorted(acts, key=lambda a: ACTION_RANK.get(a, 1), reverse=True)[0]
        if 'profanity' in cats and action in ('OK', 'REVIEW'):
            action = 'MUTE'
    else:
        action = 'OK'

    # 标题风险(标题最扎眼, 单独标)
    title_hits = []
    for c in rules['categories']:
        for w in c.get('words', []):
            if w in title:
                title_hits.append({"cat": c['name'], "level": c.get('level', 'YELLOW'), "word": w})

    return {
        "idx": clip.get('idx'),
        "seq": None,
        "title": title,
        "start_ms": st,
        "end_ms": en,
        "duration_s": round(duration / 1000, 1),
        "level": level,
        "action": action,
        "drop": drop,
        "categories": sorted(cats.values(), key=lambda v: -LEVEL_ORDER.get(v['level'], 0)),
        "title_hits": title_hits,
        "red_ranges": [{"start_ms": r[0], "end_ms": r[1],
                        "rel_start_s": round((r[0] - st) / 1000, 1),
                        "rel_end_s": round((r[1] - st) / 1000, 1),
                        "text": r[2][:120], "cats": r[3]} for r in red_ranges],
        "mute_ranges": [{"start_ms": r[0], "end_ms": r[1],
                         "rel_start_s": round((r[0] - st) / 1000, 1),
                         "rel_end_s": round((r[1] - st) / 1000, 1),
                         "text": r[2][:120]} for r in mute_ranges],
        "n_sent": len(body),
    }


# ---------- 报告 ----------
def build_md(res, rules, meta):
    L = []
    A = L.append
    th = res['thresholds']
    A("# 发布合规检查报告")
    A("")
    A(f"- 生成时间: {meta['generated_at']}")
    A(f"- 切片表: `{meta['clips_path']}` / 转写: `{meta['transcript_path']}`")
    A(f"- 词表: `{meta['rules_path']}` (v{rules.get('version', '?')})")
    A(f"- 切片数: {res['n_clips']} / 命中句 {res['n_hit_sents']} 条")
    A("")
    A("> 口径说明: 本报告是按**短视频平台通行审核口径**做的发布前风险预判(关键词+语境密度), "
      "**不是平台审核结果**。RED 不等于必定下架, GREEN 也不等于绝对安全; "
      "最终以人工复核与实际发布结果为准。")
    A("")
    A("## 一、结论速览")
    A("")
    A("| 等级 | 片数 | 含义 |")
    A("|---|---|---|")
    for lv, desc in (("RED", "发布前必须处理(删句/改标题/弃用)"),
                     ("YELLOW", "建议处理后发布(消音/改标题/取授权)"),
                     ("GREEN", "未命中风险词, 可直接发布")):
        A(f"| **{lv}** | {len(res['summary'][lv])} | {desc} |")
    A("")
    A("处置动作分布: " + "、".join(
        f"{k}×{v}" for k, v in sorted(res['action_count'].items(),
                                      key=lambda x: -x[1])))
    A("")

    # RED 明细
    A("## 二、RED 明细（逐片处置建议）")
    A("")
    reds = [c for c in res['clips'] if c['level'] == 'RED']
    if not reds:
        A("无。")
    for c in reds:
        seq = c['seq']
        A(f"### [{seq:02d}] {c['title']}  ({fmt_ms(c['start_ms'])}-{fmt_ms(c['end_ms'])}, {c['duration_s']}s)")
        A("")
        A(f"- **等级 RED** · 建议动作: **{c['action']} — {ACTION_TEXT.get(c['action'], '')}**")
        for cat in c['categories']:
            ws = "、".join(f"`{w}`×{n}" for w, n in sorted(cat['words'].items(), key=lambda x: -x[1]))
            A(f"- 命中【{cat['name']}】({cat['level']}): {ws}")
            if cat.get('advice'):
                A(f"  - 处理口径: {cat['advice']}")
        if c['title_hits']:
            A("- ⚠ 标题命中: " + "、".join(f"`{h['word']}`({h['cat']})" for h in c['title_hits'])
              + " —— **标题必须改写**")
        if c['red_ranges']:
            A("")
            A(f"- 待处理区间（共 {len(c['red_ranges'])} 段，片内相对时间 / 全片绝对时间）:")
            A("")
            A("| 片内 | 全片 | 内容 |")
            A("|---|---|---|")
            for r in c['red_ranges']:
                txt = r['text'].replace('|', '/')
                if len(txt) > 60:
                    txt = txt[:57] + "…"
                A(f"| {fmt_s(r['rel_start_s'])} → {fmt_s(r['rel_end_s'])} "
                  f"| {fmt_ms(r['start_ms'])} | {txt} |")
        A("")

    # YELLOW 汇总
    A("## 三、YELLOW 汇总（按风险类别）")
    A("")
    yel = [c for c in res['clips'] if c['level'] == 'YELLOW']
    if not yel:
        A("无。")
    else:
        by_cat = {}
        for c in yel:
            for cat in c['categories']:
                by_cat.setdefault(cat['name'], {"advice": cat.get('advice', ''),
                                                "action": cat.get('action', 'REVIEW'),
                                                "clips": []})
                by_cat[cat['name']]['clips'].append((c['seq'], c['title'], cat['words']))
        for name, d in sorted(by_cat.items(), key=lambda x: -len(x[1]['clips'])):
            A(f"### {name}（{len(d['clips'])} 片 · 建议 {d['action']} = {ACTION_TEXT.get(d['action'], '')}）")
            A("")
            A(f"- 处理口径: {d['advice']}")
            for seq, title, words in d['clips']:
                ws = "、".join(f"{w}×{n}" for w, n in sorted(words.items(), key=lambda x: -x[1]))
                A(f"  - [{seq:02d}] {title} → {ws}")
            A("")

    # GREEN
    A("## 四、可直接发布（GREEN）")
    A("")
    grn = [c for c in res['clips'] if c['level'] == 'GREEN']
    if not grn:
        A("无。")
    else:
        A("| # | 标题 | 时长 |")
        A("|---|---|---|")
        for c in grn:
            A(f"| {c['seq']:02d} | {c['title']} | {fmt_s(c['duration_s'])} |")
    A("")

    # AI 待办
    A("## 五、交付时由 AI 完成的待办（脚本判不了）")
    A("")
    todo = 1
    retitle = [c for c in res['clips'] if c['title_hits']]
    if retitle:
        A(f"{todo}. **标题改写**: 以下 {len(retitle)} 片标题含风险词, 交付时给出改后标题 ——")
        A("")
        for c in retitle:
            A(f"   - [{c['seq']:02d}] {c['title']} → 命中 "
              + "、".join(f"`{h['word']}`" for h in c['title_hits']))
        A("")
        todo += 1
    consent = [c for c in res['clips'] if c['action'] == 'CONSENT']
    if consent:
        A(f"{todo}. **授权确认**: {len(consent)} 片涉及连麦粉丝/家人/前任的肖像声音或婚姻年龄信息, "
          "发布前需取得当事人同意 —— " + "、".join(f"[{c['seq']:02d}]" for c in consent))
        A("")
        todo += 1
    health = [c for c in res['clips'] if any(x['id'] == 'health_claim' for x in c['categories'])]
    if health:
        A(f"{todo}. **功效宣称复核**: {len(health)} 片含美白/减肥/疗效类表述, 核对是否有特证或资质 —— "
          + "、".join(f"[{c['seq']:02d}]" for c in health))
        A("")
        todo += 1
    A(f"{todo}. **人工抽查**: 对 RED 片按上表区间试听确认, 关键词扫描有误报(如谐音、正当引用)时以人工判断为准。")
    A("")
    A("---")
    A("")
    A("### 附: 判定阈值")
    A("")
    for k, v in th.items():
        A(f"- `{k}` = {v}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description='阶段5 发布合规检查')
    ap.add_argument('--clips', default=os.path.join('temp', 'clips_refined.json'))
    ap.add_argument('--transcript', default=os.path.join('temp', 'transcript.json'))
    ap.add_argument('--out', default=os.path.join('temp', 'compliance_report.md'))
    ap.add_argument('--json-out', default=os.path.join('temp', 'compliance_report.json'))
    ap.add_argument('--rules', default=None, help='自定义词表 JSON')
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 片(试跑用)')
    args = ap.parse_args()

    for p in (args.clips, args.transcript):
        if not os.path.isfile(p):
            print(f"[error] 找不到 {p}", file=sys.stderr)
            return 2

    rules = load_rules(args.rules)
    th = rules.get('thresholds', {})
    clips = extract_clips(read_json(args.clips))
    sents = extract_sentences(read_json(args.transcript))
    sents = [s for s in sents if 'start_ms' in s and 'text' in s]
    sents.sort(key=lambda s: int(s['start_ms']))
    if args.limit:
        clips = clips[:args.limit]

    results = [scan_clip(c, sents, rules, th) for c in clips]
    for i, r in enumerate(results, 1):
        r['seq'] = i

    summary = {"RED": [], "YELLOW": [], "GREEN": []}
    action_count = {}
    for r in results:
        summary[r['level']].append({"seq": r['seq'], "title": r['title'],
                                    "action": r['action'],
                                    "cats": [x['name'] for x in r['categories']]})
        action_count[r['action']] = action_count.get(r['action'], 0) + 1

    res = {
        "n_clips": len(results),
        "n_hit_sents": sum(len(r['red_ranges']) + len(r['mute_ranges']) for r in results),
        "thresholds": th,
        "summary": summary,
        "action_count": action_count,
        "clips": results,
    }
    meta = {
        "generated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        "clips_path": os.path.abspath(args.clips),
        "transcript_path": os.path.abspath(args.transcript),
        "rules_path": rules.get('_source', '?'),
    }

    md = build_md(res, rules, meta)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or '.', exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(md)
    with open(args.json_out, 'w', encoding='utf-8') as f:
        json.dump({"meta": meta, **res}, f, ensure_ascii=False, indent=1)

    print(f"[ok] 报告: {os.path.abspath(args.out)}")
    print(f"[ok] 结构化: {os.path.abspath(args.json_out)}")
    print(f"RED {len(summary['RED'])} / YELLOW {len(summary['YELLOW'])} / GREEN {len(summary['GREEN'])}")
    for r in results:
        if r['level'] == 'RED':
            print(f"  [{r['seq']:02d}] {r['action']:8s} {r['title']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
