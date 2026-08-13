# segmenter 核心逻辑单元测试
# 运行: python tests/test_segmenter.py
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from segmenter import (
    _build_pauses, _snap, _locate_sentence, boundary_ok,
    refine_boundary, merge_short, split_long,
    _review_priority, _build_context,
)

TR = {
    "sentences": [
        {"text": "长句A" * 3, "start_ms": 10000, "end_ms": 20000},
        {"text": "短句", "start_ms": 20000, "end_ms": 24000},
        {"text": "长句B" * 4, "start_ms": 25000, "end_ms": 45000},
    ],
    "silences": [
        {"start_ms": 18000, "end_ms": 24000, "duration_ms": 6000},
        {"start_ms": 30000, "end_ms": 31500, "duration_ms": 1500},
        {"start_ms": 50000, "end_ms": 50100, "duration_ms": 100},
    ],
}

pauses = _build_pauses(TR)
assert len(pauses) == 2, f"应只有2个锚点 >=1.5s, got {len(pauses)}"

assert abs(_snap(21.5, pauses)[0] - 21.0) < 1e-6
assert _snap(55, pauses) == []

ok1, w1 = boundary_ok(TR, pauses, 21.0)
assert ok1 is False and w1 == "短句内", (ok1, w1)
ok2, w2 = boundary_ok(TR, pauses, 31.0)
assert ok2 is True and w2 == "长句内邻近长静音", (ok2, w2)

st, conf, r = refine_boundary(TR, pauses, 5.0, True, False)
assert st == 5.0 and conf == "high"
st3, conf3, r3 = refine_boundary(TR, pauses, 23.0, False, False)
assert conf3 == "high" and abs(st3 - 30.45) < 1e-6, (st3, conf3, r3)

TR2 = {
    "sentences": [{"text": "xA", "start_ms": 10000, "end_ms": 20000},
                  {"text": "yB", "start_ms": 25000, "end_ms": 45000}],
    "silences": [{"start_ms": 28500, "end_ms": 30500, "duration_ms": 2000}]}
p2 = _build_pauses(TR2)
st2, conf2, r2 = refine_boundary(TR2, p2, 28.6, False, False)
assert conf2 == "high" and abs(st2 - 28.6) < 1e-6, (st2, conf2, r2)

clips = [
    {"idx": 0, "start_ms": 0, "end_ms": 100000, "confidence": "high", "reason": ""},
    {"idx": 1, "start_ms": 100000, "end_ms": 130000, "confidence": "high", "reason": ""},
]
merged = merge_short(clips)
assert len(merged) == 1 and merged[0]["end_ms"] == 130000

longclip = [{"idx": 0, "start_ms": 0, "end_ms": 700000, "confidence": "high",
             "reason": "", "title": "x", "label": ""}]
split = split_long(longclip, _build_pauses({
    "silences": [{"start_ms": 560000, "end_ms": 565000, "duration_ms": 5000}]}))
assert len(split) == 2

# ---- low 段分级: _review_priority ----
assert _review_priority("high", 999999, -999999) == 0, "high 恒为 safe"
assert _review_priority("low", 0, 0) == 1
assert _review_priority("low", -300, -300) == 1, "-300ms 防残词微调应可批量接受"
assert _review_priority("low", 500, 0) == 1, "恰好 500ms 边界归 low_risk"
assert _review_priority("low", -501, 0) == 2, "start 超 500ms 应 high_risk"
assert _review_priority("low", 0, 7938) == 2, "end +8s 大偏移应 high_risk"
assert _review_priority("low", 6058, -300) == 2, "一端 low 微调另一端 +6s 也应 high_risk(漏报回归)"

# ---- 上下文附带: _build_context ----
ctx = _build_context(TR, 21000)  # 落在"短句"上
assert ctx and "短句" in ctx[1], ctx
assert len(ctx) == 3, f"前后各1句, 共3句, got {len(ctx)}: {ctx}"
assert all(len(s) <= 30 for s in ctx), "每句截断30字"
ctx_none = _build_context(TR, 999999)  # 无句子覆盖
assert ctx_none == []

TR4 = {"sentences": [
    {"text": "s0", "start_ms": 1000, "end_ms": 2000},
    {"text": "s1", "start_ms": 3000, "end_ms": 4000},
    {"text": "s2", "start_ms": 5000, "end_ms": 6000},
    {"text": "s3", "start_ms": 7000, "end_ms": 8000},
    {"text": "s4", "start_ms": 9000, "end_ms": 10000},
], "silences": []}
ctx5 = _build_context(TR4, 5500)  # 落在第3句, 应取 前1+自身+后1
assert ctx5 == ["s1", "s2", "s3"], ctx5
assert _build_context(TR4, 1000) == ["s0", "s1"], "首句贴边: 只有后1句"
assert _build_context(TR4, 9500) == ["s3", "s4"], "末句贴边: 只有前1句"

# ---- 人工钉住边界: run() 中 pin 路径 ----
from segmenter import run as seg_run
import json, tempfile, os

TR3 = {
    "sentences": [{"text": "长句X" * 4, "start_ms": 70000, "end_ms": 110000}],
    "silences": [{"start_ms": 69000, "end_ms": 72000, "duration_ms": 3000}],
}

def _mini_segments(pin_end=False):
    seg0 = {"title": "t0", "start_ms": 10000, "end_ms": 70000}
    if pin_end:
        seg0["pin_end"] = True
    return {"video_path": "x.mp4", "segments": [
        seg0,
        {"title": "t1", "start_ms": 70000, "end_ms": 120000},
    ]}

def _tmp(path, data):
    p = os.path.join(tempfile.mkdtemp(), path)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return p

_t = _tmp("t.json", TR3)
_s = _tmp("s.json", _mini_segments())
_o = _tmp("o.json", {})
res = seg_run(_t, _s, _o)
c0 = res["clips"][0]
# 切点贴句子 start(70.0), 停顿锚点在切点之后(70.5), 句首保护无前置锚点 -> 兜底-300ms
assert c0["end_ms"] == 69700, f"无 pin 时 70000 应微调为 69700, got {c0['end_ms']}"
assert c0["confidence"] == "low"

_s2 = _tmp("s2.json", _mini_segments(pin_end=True))
res2 = seg_run(_t, _s2, _o)
c2 = res2["clips"][0]
assert c2["end_ms"] == 70000, f"pin 后终点保持原值, got {c2['end_ms']}"
assert c2["end_offset_ms"] == 0 and c2["confidence"] == "high"
assert c2["review_priority"] == 0
assert "钉住" in c2["reason"]

print("全部断言通过")