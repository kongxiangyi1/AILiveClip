# 数据交换格式

### 阶段1输出: transcript.json

```json
{
  "video_path": "<视频文件绝对路径>",
  "duration_ms": 708015,
  "sentences": [
    {"text": "今天给大家分享一款洗面奶", "start_ms": 210, "end_ms": 4050}
  ],
  "silences": [
    {"start_ms": 6800, "end_ms": 7200, "duration_ms": 400}
  ]
}
```

### 阶段2输出: segments.json (由你生成)

```json
{
  "video_path": "<视频文件绝对路径>",
  "segments": [
    {
      "title": "氨基酸洗面奶成分与用法讲解",
      "start_ms": 210,
      "end_ms": 320000,
      "duration_s": 319.8,
      "confidence": "high",
      "topic_raw": "洗面奶产品讲解",
      "note": "…",
      "pin_start": false,
      "pin_end": true
    }
  ]
}
```

- `pin_start` / `pin_end`(可选): 人工钉住边界, segmenter 对该端点不吸附、置信直接 high(reason 标"人工钉住")。
  用于「AI 边界正确但吸附规则会把它吸歪」的场景(如过渡句归后段时)。
- 钉住边界可以是句子边界(句首/句尾), 不要求落在静音前。

### 阶段3输出: clips_refined.json (由 segmenter.py 生成)

```json
{
  "video_path": "<视频文件绝对路径>",
  "duration_ms": 708015,
  "params": {"pause_min_ms": 1500, "window_s": 15, "margin_s": 0.3, "short_sent_ms": 8000, "long_gap_ok_ms": 2500, "merge_min_s": 45, "split_max_s": 600},
  "clips": [
    {
      "idx": 0,
      "title": "氨基酸洗面奶成分与用法讲解",
      "start_ms": 210,
      "end_ms": 320000,
      "confidence": "high",
      "reason": "吸附到静音前-1.2s",
      "start_offset_ms": 0,
      "end_offset_ms": -1200,
      "review_priority": 0,
      "context": {"start": {"orig_ms": 210, "snapped_ms": 210, "offset_ms": 0, "sentences": ["…"]}, "end": {"…"}}
    }
  ]
}
```

- `start_offset_ms` / `end_offset_ms`: 吸附后 - AI 原边界(正=向后移), 即分级与人工复核的依据
- `review_priority`: 0=safe / 1=low_risk / 2=high_risk(判定规则见"切片执行纪律"分级表)
- `context`: 仅 high_risk 段存在, 每端含吸附前后切点 + 前后各 1 句文本, 复核无需再翻 transcript.json

`clips_refined.json` 供 cut_pipeline.py 直接切割,也兼容 app.py 审核(`segments`/`clips` 键均可读)。

