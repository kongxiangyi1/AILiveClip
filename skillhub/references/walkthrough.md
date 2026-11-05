# 端到端走查(Walkthrough)

> 本页演示一条最小完整链路: 从一句指令到交付清单的**每一步产物长什么样**。
> 样例沿用 data-formats.md 的真实字段(该文件是字段权威口径, 漂移以它为准)。

## 输入

用户一句话:
```
把这段直播切片 E:\录播\0710直播回放.ts
```

**步骤0 先过付费闸口**(见 SKILL.md 与 references/payment-buyer.md):
```
python "<SCRIPT_DIR>/payment_gate.py" probe    # 已激活 → 退出码 0, 直通
```

## 阶段1: 转写 (asr_pipeline.py)

```
python "<SCRIPT_DIR>/asr_pipeline.py" E:\录播\0710直播回放.ts
```
→ 产物 `temp/transcript.json`(整体结构 + 句级时间戳, 完整字段见 data-formats.md):

```json
{
  "video_path": "E:\\录播\\0710直播回放.ts",
  "duration_ms": 708015,
  "sentences": [ {"text": "今天给大家分享一款洗面奶", "start_ms": 210, "end_ms": 4050} ],
  "silences":  [ {"start_ms": 6800, "end_ms": 7200, "duration_ms": 400} ]
}
```

## 阶段2: 话题分段 (AI 在对话中完成)

AI 读取 transcript, 给出语义边界(近似值即可)并整理专有名词, 写出 `temp/segments.json`:

```json
{
  "video_path": "E:\\录播\\0710直播回放.ts",
  "segments": [
    { "title": "氨基酸洗面奶成分与用法讲解", "start_ms": 210, "end_ms": 320000,
      "duration_s": 319.8, "confidence": "high", "pin_end": true }
  ]
}
```

规则: 过渡句归后段; 边界正确但会被吸附吸歪的端点用 `pin_start`/`pin_end` 钉住。

## 阶段3: 静音吸附 (segmenter.py)

```
python "<SCRIPT_DIR>/segmenter.py"
```
→ 产物 `temp/clips_refined.json`(吸附到 ≥1.5s 静音前 300ms + 置信 + 时长兜底):

```json
{
  "clips": [
    { "idx": 0, "title": "氨基酸洗面奶成分与用法讲解", "start_ms": 210, "end_ms": 320000,
      "confidence": "high", "reason": "吸附到静音前-1.2s", "end_offset_ms": -1200,
      "review_priority": 0 }
  ]
}
```
→ `confidence=low` 的片段先复核 reason, 必要时回阶段2修边界。

## 阶段4: 切割 (cut_pipeline.py)

```
python "<SCRIPT_DIR>/cut_pipeline.py" temp/clips_refined.json --mode stream_copy
```
→ 产物目录:

```
output/2026-07-10_0710直播回放/
├── cut_results.json                     # 每片切割结果与吸附误差(start_offset_s)
├── 01_氨基酸洗面奶成分与用法讲解_00-05.mp4
├── 02_..._05-12.mp4
└── ...
```

## 阶段5: 发布合规检查 (compliance_scan.py)

```
python "<SCRIPT_DIR>/compliance_scan.py" --limit 3     # 先小样本试跑(纪律#27)
python "<SCRIPT_DIR>/compliance_scan.py"               # 全量
```
→ 产物 `temp/compliance_report.md` + `temp/compliance_report.json`。报告摘要长这样:

```
结论速览: RED 0 片 / YELLOW 1 片 / GREEN 5 片
YELLOW:
  片 03 「美白成分一讲到底」  命中: 功效宣称(美白)  动作: RETITLE(改标题)  待处理: 全片
AI 必补建议:
  ① 改后标题: 「氨基酸洗面奶的温和清洁思路」(避开功效宣称)
  ② 涉他人肖像/隐私片段: 无
  ③ 功效宣称待核: 片 03(需品牌方资质佐证)
```

## 最终交付清单(缺一不算完成)

- `output/{日期_原视频名}/` 下全部 mp4(粗切片, 无封面)
- `cut_results.json` + `compliance_report.md` 与 `.json`
- 对话内的: 结论速览(RED/YELLOW/GREEN 各几片)、YELLOW 分类处置、AI 三项建议

## 本次跑的参考耗时(125 分钟视频, 供预估)

| 阶段 | 耗时 |
|---|---|
| 阶段1 转写 | ≈10-12 分钟(5 小时源视频 12-25 分钟) |
| 阶段2+3 语义分段与吸附 | 对话内人工等待为主, segmenter 仅数秒 |
| 阶段4 stream_copy 切割 | ≈16 分钟(4 路并行, 125min 源实测) |
| 阶段5 合规扫描 | 数秒到数分钟(词表规模而定) |
