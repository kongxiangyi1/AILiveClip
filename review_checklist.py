# -*- coding: utf-8 -*-
"""阶段2辅助: 生成人工待确认清单(一轮确认制)

用法: python review_checklist.py [segments.json | clips_refined.json] [transcript.json]
默认: temp/clips_refined.json(优先) / temp/segments.json, temp/transcript.json
输出: temp/review_checklist.txt + 控制台

内容: ① 完整段表(段号/标题/MMSS时间/分级/首句…尾句/reason)
      ② 全片专有名词候选清单(高频词, 人工一次扫完即可)
"""
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
TEMP = os.path.join(BASE, "temp")

# 常见口语/虚词(专有名词候选过滤)
STOPWORDS = set("""我们 你们 他们 她们 咱们 这个 那个 一个 两个 一些 什么 怎么 为什么 多少 几个
就是 还是 只是 但是 可是 而且 或者 因为 所以 如果 虽然 然后 不过 于是 甚至 其实 反正 当然 确实 可能
可以 不能 不会 没有 已经 还要 需要 应该 一定 必须 不要 不用 别怕 知道 觉得 认为 感觉 发现 看到 听到
大家 自己 别人 朋友 兄弟 老师 老板 粉丝 观众 主播 现在 今天 明天 昨天 时候 地方 东西 问题 情况 事情
一点 一下 一直 一起 越来越 差不多 真的 特别 比较 主要 其实 比如 还有 包括 非常 很了 太好了 慢慢
直播 直播间 视频 链接 商品 产品 价格 块钱 块钱一 一瓶 一箱 一杯 一两 一壶 二号 三号 一号 链接二
大家 大哥 大姐 大叔 阿姨 宝贝 各位 各位 谢谢 感谢 欢迎 不好意思 没听清 听不清 再说 再说 说话 聊天
话题 内容 东西 意思 觉得 感觉 好像 有点 有点 挺 挺 特别 尤其 反正 正常 一般 随便 直接 完全 基本
对吧 不是 是吧 我说 的这 你就 是我 我就 我我 你说 这种 说我 是你 我这 他妈 你要 这么 的时
的时候 是这 都是 说你 的人 的那 给你 有一 说这 为什 人家 是说 事儿 你你 就这 是一 里边 也不
你的 酒的 个东 的这个 个东西 个就 你不 我不 是那 这些 是不 这个东 这个东西 为啥 就是我 很多 在我
的是 的那个 给他 都不 品质 我也 来了 来说 个酒 之后 在这 就说 那么 也没 如说 我是 是他 是就
比如说 要是 嗯 啊 呃 哎 哦 呀 吧 嘛""".split())

REVIEW_LABEL = {0: "[OK] 无需复核", 1: "[可批量接受]", 2: "[必须确认]"}

# 口语虚字: 含这些字的 3-6 字组合大概率是口语碎片(我你他…), 降为参考区
VIRTUAL_CHARS = set("我你他她它这那的就了在也都不还么说个跟和着过要会能想觉知看听啊吧吗呢哦嗯")


def mmss(ms):
    s = max(0, int(ms)) // 1000
    return f"{s // 60:02d}{s % 60:02d}"


def find_first_sent(sentences, start_ms, end_ms):
    for s in sentences:
        if s["start_ms"] >= start_ms and s["start_ms"] < end_ms:
            return s
    return None


def find_last_sent(sentences, start_ms, end_ms):
    last = None
    for s in sentences:
        if s["end_ms"] > start_ms and s["end_ms"] <= end_ms:
            last = s
    return last


def extract_proper_nouns(transcript, min_freq=2):
    """返回 (multi, two): multi=3-6字候选(主清单), two=2字词(参考,噪声多)。
    不截断, 由调用方先做碎片抑制再截断。"""
    multi, two = {}, {}
    for s in transcript.get("sentences", []):
        for tok in re.findall(r"[\u4e00-\u9fff]+", s.get("text", "")):
            n = len(tok)
            if n < 2:
                continue
            seen = set()
            for L in range(2, min(6, n) + 1):
                for i in range(0, n - L + 1):
                    sub = tok[i:i + L]
                    if sub in STOPWORDS or sub in seen:
                        continue
                    seen.add(sub)
                    target = multi if L >= 3 else two
                    target[sub] = target.get(sub, 0) + 1
        for tok in re.findall(r"[A-Za-z0-9]{2,}", s.get("text", "")):
            multi[tok] = multi.get(tok, 0) + 1
    rank = lambda d: sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))
    return ([(w, c) for w, c in rank(multi) if c >= min_freq],
            [(w, c) for w, c in rank(two) if c >= min_freq])


def suppress_fragments(cands, all_cands):
    """抑制碎片: 若词 w 的频次大部分(>=70%)都被更长词 L 包含, 则 w 是碎片不展示。
    例: 有田x3 ⊂ 有田半亩x3 → 隐藏有田; 绝尘x49 ⊃ 良心绝尘x10(比例<0.7) → 保留绝尘"""
    kept = []
    for w, c in cands:
        is_frag = False
        for w2, c2 in all_cands:
            if len(w2) > len(w) and w in w2 and c2 >= c * 0.7:
                is_frag = True
                break
        if not is_frag:
            kept.append((w, c))
    return kept


def load_segments(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if "clips" in d:
        return d["clips"], True
    if "segments" in d:
        return d["segments"], False
    raise ValueError(f"未识别的结构(需要 clips[] 或 segments[]): {path}")


def main():
    seg_path = sys.argv[1] if len(sys.argv) > 1 else None
    tr_path = sys.argv[2] if len(sys.argv) > 2 else None
    if not seg_path:
        cand = os.path.join(TEMP, "clips_refined.json")
        seg_path = cand if os.path.exists(cand) else os.path.join(TEMP, "segments.json")
    if not tr_path:
        tr_path = os.path.join(TEMP, "transcript.json")

    if not os.path.exists(seg_path):
        print(f"错误: 段文件不存在: {seg_path}")
        sys.exit(1)
    if not os.path.exists(tr_path):
        print(f"错误: 转写文件不存在: {tr_path}")
        sys.exit(1)

    clips, is_refined = load_segments(seg_path)
    with open(tr_path, encoding="utf-8") as f:
        transcript = json.load(f)

    lines = []
    lines.append("=" * 70)
    lines.append("待确认清单(一轮确认制)")
    lines.append(f"段文件: {seg_path} ({'clips_refined(含吸附分级)' if is_refined else 'segments(语义边界)'})")
    lines.append(f"视频: {os.path.basename(transcript.get('video_path', ''))}")
    high = sum(1 for c in clips if c.get("review_priority", 0) == 2)
    low = sum(1 for c in clips if c.get("review_priority", 0) == 1)
    lines.append(f"共 {len(clips)} 段 | 必须确认: {high} | 可批量接受: {low} | OK: {len(clips)-high-low}")
    lines.append("=" * 70)
    lines.append("")

    for c in clips:
        idx = c.get("idx", c.get("index", 0)) + 1
        title = c.get("title", "")
        start, end = c.get("start_ms"), c.get("end_ms")
        prio = c.get("review_priority", 0)
        if prio not in REVIEW_LABEL:
            prio = 0 if c.get("confidence") == "high" else 1
        head = f"[{idx:02d}] {title}  {mmss(start)}-{mmss(end)}  {REVIEW_LABEL.get(prio, '')}"
        lines.append(head)
        first = find_first_sent(transcript["sentences"], start, end)
        last = find_last_sent(transcript["sentences"], start, end)
        if first:
            t = first["text"].strip().replace("\n", " ")
            lines.append(f"    首句: {t[:60]}{'…' if len(t) > 60 else ''}")
        if last and last is not first:
            t = last["text"].strip().replace("\n", " ")
            lines.append(f"    尾句: {t[:60]}{'…' if len(t) > 60 else ''}")
        reason = c.get("reason") or c.get("note") or ""
        if reason and prio >= 1:
            lines.append(f"    {reason}")
        lines.append("")

    lines.append("=" * 70)
    lines.append("专有名词候选(请扫一遍纠正同音错字):")
    lines.append("=" * 70)
    multi, two = extract_proper_nouns(transcript)
    all_cands = multi + two
    multi = suppress_fragments(multi, all_cands)
    two = suppress_fragments(two, all_cands)
    multi_hi = [x for x in multi if not any(ch in VIRTUAL_CHARS for ch in x[0])]
    multi_lo = [x for x in multi if any(ch in VIRTUAL_CHARS for ch in x[0])][:80]
    two = two[:100]
    lines.append("3-6字高置信(优先扫):")
    for w, c in multi_hi:
        lines.append(f"  {w}  x{c}")
    lines.append("")
    lines.append("3-6字含口语字(仅参考):")
    for w, c in multi_lo:
        lines.append(f"  {w}  x{c}")
    lines.append("")
    lines.append("2字词参考(噪声多, 仅辅助):")
    for w, c in two:
        lines.append(f"  {w}  x{c}")
    lines.append("")

    out = os.path.join(TEMP, "review_checklist.txt")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines))
    print(f"\n清单已保存: {out}")


if __name__ == "__main__":
    main()
