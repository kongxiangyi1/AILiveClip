"""阶段3.5: 语义边界精修 — 吸附到停顿 + 置信标注 + 时长兜底

输入: temp/transcript.json (FunASR) + temp/segments.json (AI 语义段)
输出: temp/clips_refined.json (供 cut_pipeline 直接切割 + Streamlit 审核)

核心规则(经 2026-08-08 五轮真实数据验证):
1. 锚点 = 字符级静音 >= 1.5s (32条, 足够覆盖各段边界)
2. 吸附: 切点在 ±15s window 内找最近锚点中心, 命中则落在 锚点中心-300ms
3. 短句保护: 吸附后若切入 <= 8s 的短句内部 => 拒绝, 保持原边界 && confidence=low
4. 长句内静音: 切点落入长句内部但邻近真实长静音(<2.5s) => 可接受(high)
5. 时长兜底: <45s 并入相邻段; > 上限 按内部最长停顿拆分(标 low)
6. 首段入点 / 末段出点 保持 AI 原值 不吸附 (防吃片头/片尾)

用法: python segmenter.py [--input segments.json] [--output clips_refined.json]
"""
import json
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from config import Config

# ---- 验证结论固化的参数 (见表: design-five-stage-suggestion.md §9) ----
PAUSE_MIN_MS = 1500      # 吸附锚点阈值: 字符级静音>=1.5s
WINDOW_S = 15.0          # 吸附搜索窗口 ±15s
MARGIN_S = 0.3           # 切点偏移: 静音中心-300ms
SHORT_SENT_MS = 8000     # 短句保护: 吸附进 <=8s 句内 => 拒绝
LONG_GAP_OK_MS = 2500    # 长句内静音可接受距离
MERGE_MIN_S = Config.MIN_DURATION_S   # <45s 并入
SPLIT_MAX_S = Config.MAX_DURATION_S   # >600s 拆分
SENT_END_TOL_S = 0.5   # 句尾保护: 切点贴近句子 end 的容差(秒)
SENT_START_TOL_S = 0.5 # 句首保护: 切点贴近句子 start 的容差(秒)

# ---- low 段分级(2026-08-13 复盘沉淀: 消除人工逐条核对负担) ----
# review_priority:
#   0 = safe      confidence=high                -> 无需复核
#   1 = low_risk  low 且两端 |offset|<=500ms     -> 批量接受(±300ms 类防残词微调), 可抽查
#   2 = high_risk low 且任一端口 offset|>500ms   -> 必须人工复核(大偏移吸附, 可能吞句/截句)
# 阈值依据: 2026-08-13 实测 low 段中无害微调全在 -300ms, 真正需人工的是 +6s/+8s 吸附
REVIEW_LOW_OFFSET_MAX_MS = 500
REVIEW_CONTEXT_SENTENCES = 3   # high_risk 段每端附带的上下文句数
REVIEW_CONTEXT_CHARS = 30      # 上下文每句截断字数


def _review_priority(confidence, start_offset_ms, end_offset_ms):
    """按置信度+两端吸附偏移分级(任一端口大偏移即 high_risk).
    注意: low 段可能一端 low 小微调、另一端 high 但吸附 +6s(如句尾保护落长句内),
    只检查 low 端会漏报大偏移, 故两端都要看."""
    if confidence == "high":
        return 0
    if (abs(start_offset_ms) > REVIEW_LOW_OFFSET_MAX_MS
            or abs(end_offset_ms) > REVIEW_LOW_OFFSET_MAX_MS):
        return 2
    return 1


def _build_context(transcript, cut_ms, n=2, max_chars=REVIEW_CONTEXT_CHARS):
    """切点附近上下文: 覆盖切点的句子 + 其前后各 n-1 句(默认前后各1, 最多3句),
    每句截断 max_chars. 返回字符串列表, 供 high_risk 复核时一眼判断是否吞句/截句."""
    sents = transcript.get("sentences", [])
    cut_s = cut_ms / 1000.0
    idx = None
    for i, s in enumerate(sents):
        if s["start_ms"] / 1000 <= cut_s <= s["end_ms"] / 1000:
            idx = i
            break
    if idx is None:
        return []
    lo = max(0, idx - (n - 1))
    hi = min(len(sents), idx + n)
    return [s["text"][:max_chars] for s in sents[lo:hi]]


def _build_pauses(transcript):
    """从 transcript.json 的 silences 生成吸附锚点 (= 停顿中心, 秒)"""
    pauses = [{
        "start": s / 1000, "end": e / 1000, "mid": (s + e) / 2 / 1000
    } for s, e, d in ((x["start_ms"], x["end_ms"], x["duration_ms"])
                      for x in transcript.get("silences", []))
      if d >= PAUSE_MIN_MS]
    return pauses


def _snap(cut_s, pauses):
    """±15s 窗口内全部停顿中心,按距离升序返回; 无则空列表"""
    cands = []
    for p in pauses:
        d = abs(p["mid"] - cut_s)
        if d <= WINDOW_S:
            cands.append((d, p["mid"]))
    cands.sort(key=lambda x: x[0])
    return [mid for _, mid in cands]


def _locate_sentence(transcript, cut_s):
    """返回覆盖 cut 的句子(start_s, end_s), 未覆盖则 None"""
    for x in transcript.get("sentences", []):
        if x["start_ms"] / 1000 < cut_s < x["end_ms"] / 1000:
            return x["start_ms"] / 1000, x["end_ms"] / 1000
    return None


def _sentence_end_near(transcript, cut_s, tol_s=SENT_END_TOL_S):
    """返回 end 贴近 cut_s 的句子 end 值(秒)。
    FunASR 句末时间戳常有数百 ms 误差(句尾字被标早),
    切点恰好落/贴近句子end时若直接"句外安全"跳过吸附, 句尾字会被切丢。
    判断: 切点到某句 end 距离 <= tol 且在该句 start 之后。
    """
    best = None
    for x in transcript.get("sentences", []):
        s = x["start_ms"] / 1000
        e = x["end_ms"] / 1000
        if s <= cut_s and abs(cut_s - e) <= tol_s:
            best = e
    return best


def _sentence_start_near(transcript, cut_s, tol_s=SENT_START_TOL_S):
    """返回 start 贴近 cut_s 的句子 start 值(秒)。
    FunASR 句首时间戳常有数百 ms 误差(句首字被标晚),
    切点恰好落在句子 start 上时被视为"句外安全"而跳过吸附,
    下句句首字(如"今晚")会残留在上一切片尾部。
    判断: 某句 cover 或紧贴切点, 且切点与其 start 距离 <= tol。
    """
    best = None
    for x in transcript.get("sentences", []):
        s = x["start_ms"] / 1000
        e = x["end_ms"] / 1000
        if e > cut_s - tol_s and abs(cut_s - s) <= tol_s:
            best = s
    return best


def boundary_ok(transcript, pauses, cut_s):
    """吸附后的切点是否安全(不可截断短句)。
    False=危险(截短句或句内无静音); True=安全(句外 或 长句内邻近真实长静音)
    cut_s: 秒; pauses: mid 为秒
    """
    loc = _locate_sentence(transcript, cut_s)
    if loc is None:
        return True, "句外"
    s, e = loc
    if e - s <= SHORT_SENT_MS / 1000:
        return False, "短句内"
    near = any(abs(p["mid"] - cut_s) < LONG_GAP_OK_MS / 1000 for p in pauses)
    if near:
        return True, "长句内邻近长静音"
    return False, "长句内无近静音"


def refine_boundary(transcript, pauses, cut_s, is_first, is_last):
    """精修单个切点。
    is_first: 该切点是首段入点 => 不动
    is_last : 该切点是末段出点 => 不动
    返回 (切点秒, confidence, reason)
    """
    if is_first or is_last:
        return cut_s, "high", "首尾端点不吸附"

    # 句尾保护: 切点贴近句子 end 时, ASR 句末时间戳可能低估句尾字(如"呢"),
    # 直接视为"句外安全"会切丢句尾字。改为吸附到该句之后的停顿中心-300ms。
    sent_end = _sentence_end_near(transcript, cut_s)
    if sent_end is not None:
        cands = _snap(cut_s, pauses)
        for m in cands:
            if m <= sent_end:
                continue
            new = max(m - MARGIN_S, sent_end + 0.05)
            if new < cut_s:
                continue
            ok, why = boundary_ok(transcript, pauses, new)
            if ok:
                return new, "high", f"句尾保护吸附到静音前{m - cut_s:+.1f}s"
        # 无可用停顿锚点: 不直接保持原值, 落入句首保护继续判断

    # 句首保护: 切点贴近句子 start 时视为"句外安全"会让下句首字(如"今晚")
    # 残留在上一段末尾。改为吸附到该句之前最近的停顿中心-300ms。
    sent_start = _sentence_start_near(transcript, cut_s)
    if sent_start is not None:
        cands = _snap(cut_s, pauses)
        for m in cands:
            if m >= sent_start:
                continue
            new = max(m - MARGIN_S, 0.0)
            ok, why = boundary_ok(transcript, pauses, new)
            if ok:
                return new, "high", f"句首保护吸附到静音前{m - cut_s:+.1f}s"
        # 兜底: 句首也无紧邻停顿(仅 ASR 对齐误差) → 微调-300ms 防残词, 交人工复核
        return max(cut_s - MARGIN_S, 0.0), "low", "句首无紧邻停顿,微调-300ms防残词,待复核"

    # 原切点本身安全(句外 或 长句内邻近静音) → 无需吸附
    raw_ok, raw_why = boundary_ok(transcript, pauses, cut_s)
    if raw_ok:
        return cut_s, "high", f"原切点安全({raw_why}),不做吸附"

    cands = _snap(cut_s, pauses)
    if not cands:
        return cut_s, "low", f"±15s无锚点且截句({raw_why}),保持原值({cut_s:.1f}s)"

    # 吸附候选按距离尝试,选不截短句的最近锚点
    for m in cands:
        new = max(m - MARGIN_S, 0.0)
        ok, why = boundary_ok(transcript, pauses, new)
        if ok:
            return new, "high", f"吸附到静音前{m - cut_s:+.1f}s"
    return cut_s, "low", f"首选吸附点截句,原值({cut_s:.1f}s)待复核"


def apply_duration_rules(clips, pauses):
    """时长兜底: 先 merge_short(<45s 并入相邻段) 再 split_long(>600s 拆分)。
    注意顺序: 拆分产生的短尾段不会再回头合并(避免把刚拆开的段又粘回去)。"""
    return split_long(merge_short(clips), pauses)


def merge_short(clips):
    """< MERGE_MIN_S 的段并入前一段; 首段若短则并入后一段"""
    if not clips:
        return clips
    out = []
    for i, clip in enumerate(clips):
        dur = clip["end_ms"] - clip["start_ms"]
        if dur < MERGE_MIN_S * 1000 and len(out):
            # 并入前一个: 前一个终点 = 本段终点
            prev = out[-1]
            prev["end_ms"] = clip["end_ms"]
            prev["confidence"] = ("low" if clip["confidence"] == "low"
                                  else prev.get("confidence"))
            prev["reason"] = (prev.get("reason", "") + "; 并入短片段"
                              if prev.get("reason") else "并入短片段")
            continue
        out.append(dict(clip))
    # 首段短片段: 上面循环里 len(out)==0 被保留, 这里并入后一段
    while len(out) > 1 and (out[0]["end_ms"] - out[0]["start_ms"]) < MERGE_MIN_S * 1000:
        first = out.pop(0)
        nxt = out[0]
        nxt["start_ms"] = first["start_ms"]
        nxt["confidence"] = ("low" if first["confidence"] == "low"
                             else nxt.get("confidence"))
        nxt["reason"] = ("并入首部短片段" + ("; " + nxt["reason"] if nxt.get("reason") else ""))
    return out


def split_long(clips, pauses):
    """时长 > SPLIT_MAX_S 的段按内部最长停顿拆分; 拆分点标 low"""
    result = []
    for clip in clips:
        if clip["end_ms"] - clip["start_ms"] <= SPLIT_MAX_S * 1000:
            result.append(clip)
            continue
        total_dur = clip["end_ms"] - clip["start_ms"]
        n_parts = int(total_dur / 1000 // SPLIT_MAX_S) + 1
        seg_start = clip["start_ms"]
        internal = [p for p in pauses
                    if clip["start_ms"] < p["mid"] * 1000 < clip["end_ms"]]
        for k in range(n_parts):
            target = seg_start + SPLIT_MAX_S * 1000
            if k == n_parts - 1:
                part_end = clip["end_ms"]
            else:
                cands = [p for p in internal
                         if abs(p["mid"] * 1000 - target) <= 120000]
                if cands:
                    part_end = max(cands, key=lambda p: p["end"] - p["start"])["mid"] * 1000
                else:
                    part_end = target
            result.append({
                **clip,
                "start_ms": seg_start,
                "end_ms": part_end,
                "confidence": "low",
                "reason": "超长强制拆分,请复核",
            })
            seg_start = part_end
    return result


def run(transcript_path, segments_path, out_path):
    with open(transcript_path, "r", encoding="utf-8") as f:
        transcript = json.load(f)
    with open(segments_path, "r", encoding="utf-8") as f:
        seg_data = json.load(f)

    pauses = _build_pauses(transcript)
    segs = seg_data["segments"]
    n = len(segs)

    clips = []
    for i, seg in enumerate(segs):
        st, en = seg["start_ms"] / 1000, seg["end_ms"] / 1000
        # 人工钉住边界(segments.json 的 pin_start/pin_end): 不吸附, 置信直接 high。
        # 用途: 复核 high_risk 后, 若认定语义边界正确(如吸附会吞过渡句),
        # 在 segments.json 对应段加 "pin_end": true 重跑即可, 无需手改切点 JSON。
        if seg.get("pin_start"):
            new_st, st_conf, st_reason = st, "high", "人工钉住起点,不吸附"
        else:
            new_st, st_conf, st_reason = refine_boundary(
                transcript, pauses, st, i == 0, False)
        if seg.get("pin_end"):
            new_en, en_conf, en_reason = en, "high", "人工钉住终点,不吸附"
        else:
            new_en, en_conf, en_reason = refine_boundary(
                transcript, pauses, en, False, i == n - 1)

        # 切点级置信取低(任一端不可靠即 low)
        if st_conf == "low" or en_conf == "low":
            conf, reason = "low", (st_reason if st_conf == "low" else en_reason)
        elif st_reason and en_reason:
            # 两端都 high 时合并 reason(否则终点信息被起点覆盖, 如 pin 标记丢失)
            conf, reason = "high", f"起点:{st_reason}; 终点:{en_reason}"
        else:
            conf, reason = "high", (st_reason or en_reason)

        # 吸附偏移(ms, 正值=切点向后移) + low 段分级(两端偏移都要看)
        st_off = round(new_st * 1000 - seg["start_ms"])
        en_off = round(new_en * 1000 - seg["end_ms"])
        priority = _review_priority(conf, st_off, en_off)

        clip = {
            "idx": i,
            "title": seg.get("title", f"片段{i + 1}"),
            "label": seg.get("topic_raw", ""),
            "start_ms": round(new_st * 1000),
            "end_ms": round(new_en * 1000),
            "confidence": conf,
            "reason": reason,
            "boundary_hint": seg.get("note", ""),
            "start_offset_ms": st_off,
            "end_offset_ms": en_off,
            "review_priority": priority,
        }
        # high_risk 段附切点上下文, 复核无需再翻 transcript.json
        if priority == 2:
            ctx_start = _build_context(transcript, new_st * 1000)
            ctx_end = _build_context(transcript, new_en * 1000)
            clip["context"] = {
                "start": {"orig_ms": seg["start_ms"], "snapped_ms": clip["start_ms"],
                          "offset_ms": st_off, "sentences": ctx_start},
                "end": {"orig_ms": seg["end_ms"], "snapped_ms": clip["end_ms"],
                        "offset_ms": en_off, "sentences": ctx_end},
            }
        clips.append(clip)

    clips = apply_duration_rules(clips, pauses)
    for new_idx, c in enumerate(clips):
        c["idx"] = new_idx

    result = {
        "video_path": seg_data["video_path"],
        "source_segments": segments_path,
        "params": {
            "pause_min_ms": PAUSE_MIN_MS, "window_s": WINDOW_S,
            "margin_s": MARGIN_S, "short_sent_ms": SHORT_SENT_MS,
            "long_gap_ok_ms": LONG_GAP_OK_MS,
            "merge_min_s": MERGE_MIN_S, "split_max_s": SPLIT_MAX_S,
        },
        "duration_ms": transcript.get("duration_ms", 0),
        "clips": clips,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    base = os.path.dirname(os.path.abspath(__file__))
    seg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        base, "temp", "segments.json")
    tr_path = os.path.join(base, "temp", "transcript.json")
    out = os.path.join(base, "temp", "clips_refined.json")
    res = run(tr_path, seg_path, out)
    print(f"产出 {len(res['clips'])} 段 -> {out}")
    for c in res["clips"]:
        dur = (c["end_ms"] - c["start_ms"]) / 1000
        prio = c["review_priority"]
        tag = {0: "safe", 1: "low_risk", 2: "HIGH_RISK"}[prio]
        print(f"  #{c['idx']} [{c['start_ms']}->{c['end_ms']}] "
              f"{dur:.1f}s conf={c['confidence']} {tag} "
              f"offset(start={c['start_offset_ms']:+.0f}ms,end={c['end_offset_ms']:+.0f}ms) "
              f"re={c['reason'][:48]}")
    # 分级汇总: 人工只需看 HIGH_RISK
    risky = [c for c in res["clips"] if c["review_priority"] == 2]
    lowrisk = [c for c in res["clips"] if c["review_priority"] == 1]
    if risky:
        print(f"  [需人工复核 {len(risky)}] 段: " +
              ", ".join(f"#{c['idx']}({c['title'][:12]})" for c in risky))
        for c in risky:
            ctx = c.get("context", {})
            print(f"    #{c['idx']} 起|{ctx.get('start', {}).get('offset_ms', 0):+.0f}ms: "
                  + " / ".join(ctx.get("start", {}).get("sentences", [])))
            print(f"    #{c['idx']} 止|{ctx.get('end', {}).get('offset_ms', 0):+.0f}ms: "
                  + " / ".join(ctx.get("end", {}).get("sentences", [])))
    else:
        print("  [无需人工复核]")
    if lowrisk:
        print(f"  [{len(lowrisk)}] 段为 low_risk(±500ms 内微调), 可批量接受")