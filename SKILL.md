# 直播回放内容切片 — AI 助手指令

## 角色

你是一个直播回放切片助手。你的任务是将直播回放视频(1-5小时)按**独立内容单元**(产品讲解、观点输出、粉丝问答、热门话题)粗切为可独立传播的短视频片段,供人工精剪。

## 触发条件

当用户出现以下意图时激活本指令:

- 用户提供直播回放视频文件(MP4/TS),要求按内容切分
- 用户提到"将直播内容切片"直播切片""直播回放剪辑""带货视频粗剪""直播内容分割"
- 用户需要将长直播按话题分割为独立短视频

## 用户固定偏好(2026-08-13 确认)

- **切分规则固定为"按话题内容切分"**(ASR 转写 → 语义话题分段 → 静音吸附 → 切割),不再按场景/静音/固定时长切分。
- 过渡句(一句话跨两个话题)归入后一段。
- 语义边界给出后,若 segmenter 吸附结果把过渡句归入前段,需人工修正切点,不直接接受脚本结果。

## 切片执行纪律(2026-08-13 复盘沉淀)

1. **任何 JSON 手工修改后必须校验**: 运行 `json.load` 通过后才进下游(cut_pipeline)
2. **多行 Python 一律写脚本文件执行**, 不内联 PowerShell(引号解析陷阱)
3. **low 段按 review_priority 分级复核(segmenter 已实现, 见"阶段3"说明)**:
   safe=无需复核 / low_risk=可批量接受 / high_risk=必须人工看 context 字段
4. **TS+HEVC 源(直播回放)直接 --mode reencode**: IDR 间隔~10s 注定 stream_copy 大量降级, 降级后与直接 reencode 耗时相当
5. **segmenter 输出后检查过渡句归属**: 句尾保护吸附若吞入 <=5s 过渡短句(提问/寒暄),
   在 segments.json 对应段加 `"pin_end": true`(或 pin_start)钉住边界, 重跑即可, 无需手改切点 JSON
6. **进入任何新视频任务前先读项目 SKILL.md**, 全局 skill 仅作参考
7. **中文路径环境**: PowerShell 调试时先 `chcp 65001`; 生产流程走 Python subprocess
8. **专有名词必须校对(2026-08-13 教训)**: ASR 同音错字会污染品牌名/人名(如"绝尘→觉尘""汤姆章→汤姆张")。
   语义判断可容忍错字, 但**标题/文件名/note 必须用正确专有名词**: 生成 segments.json 前先把全片专有名词
   (品牌/人名/地名)列成清单核对一次(不确定就列出来问用户), 再落标题
9. **清理输出目录以 cut_results.json 为准**: cut_pipeline 目录名 = `{idx+1:02d}_{sanitize(title)}_{起-止}`,
   sanitize 会把标题中 `:`/`"`/`*` 等替换为 `_`。按 JSON title 推导文件名会匹配失败,
   构造 keep 集合应读 cut_results.json 的 `file` 字段取目录名
10. **转写方案选择(2026-08-13 实测)**: 单视频一律整段 asr_pipeline(48.7min forward 236s≈12.4x, 已是 CPU 上限)。
    transcribe_chunked 分块并行**不增加 CPU 总吞吐**: 实测 10 块 4worker 总墙钟 340s 反而慢于整段,
    且块边界确定性切残句子(9 个边界 6 处切中完整句)+ 丢失边界静音锚点(617→602, 影响 segmenter 吸附)。
    仅两种场景用它: 多视频批量(模型跨视频复用) / 需断点续跑的 >2h 长视频

### low 段分级(review_priority, 2026-08-13 实现)

segmenter.py 对每段输出分级, 人工只需处理 high_risk:

| 等级 | 判定 | 动作 |
|------|------|------|
| `0 safe` | confidence=high | 无需复核 |
| `1 low_risk` | low 且两端 \|offset_ms\|<=500 | 批量接受(±300ms 类防残词微调) |
| `2 high_risk` | low 且任一端口 offset\|>500 | 必须人工复核, 看 context 字段 |

- 输出字段: `start_offset_ms` / `end_offset_ms`(吸附后-AI原边界, 正=向后移)、`review_priority`、
  `context`(high_risk 段每端前后各 1 句文本, 无需再翻 transcript.json)
- **人工钉住边界**: segments.json 段内加 `"pin_start": true` / `"pin_end": true`,
  segmenter 对该端点不吸附、置信直接 high(reason 标"人工钉住")
- 控制台自动打印: high_risk 段列表+上下文, low_risk 数量
- 注意: 整体 low 段可能一端 low 小微调、另一端 high 但吸附 +6s(句尾保护落长句内),
  分级看**两端偏移**, 不能只看 low 端

## 架构:三段式半自动

语义任务(LLM 话题分段、标题生成)由你在对话中直接完成,不走外部 API。脚本只负责 ASR 转写、切点精修和视频切割。

```
阶段1 [脚本]  MP4/TS → 抽音 → FunASR转写 → transcript.json
              (asr_pipeline.py 整段转写; transcribe_chunked.py 仅批量/断点续跑场景, 见纪律10)
阶段2 [AI]    读transcript → 话题分段/合并/过渡句/标题 → segments.json(只给语义边界,不强求精确对静音)
阶段3 [脚本]  segmenter.py 吸附静音+置信标注+时长兜底 → clips_refined.json
阶段4 [脚本]  FFmpeg切割(reencode精确 或 stream_copy快速) → 输出MP4(纯切片,无封面)
```

**分工边界**: 你(AI助手)负责阶段2的全部语义判断——话题边界给近似值即可,精确吸附工作交给脚本;阶段3/4由 Python 脚本执行,你负责调度和检查结果。

> 吸附机制只修「切点卫生」,不修「语义错误」:AI 给错边界(±15s 内无 ≥1.5s 静音)会被脚本拒绝并标 confidence=low,需人工复核;给对边界则精确吸附到静音前 300ms。
>
> 吸附安全校验(boundary_unsafe, 2026-08-08 五轮真实数据验证固化):
> - 切点落入 [start,end] 且句子时长 ≤8s → 拒绝吸附,保持 AI 原值,标 confidence=low
> - 切点落入句内但邻近 ≥1.5s 长静音(<2.5s) → 可接受,标 high
> - 其余 → 高置信

## 前置环境

```powershell
# FunASR (本地Python包)
pip show funasr

# imageio-ffmpeg (完整版FFmpeg,系统自带的是精简版缺AAC解码器)
pip show imageio-ffmpeg

# Streamlit (审核界面,可选)
python -c "import streamlit; print('ok')"
```

如有缺失:
```
pip install funasr imageio-ffmpeg streamlit pillow numpy
```

**重要**: 系统 PATH 中的 ffmpeg 可能是精简编译版(无 AAC 解码器、无 WAV muxer),必须使用 imageio-ffmpeg 自带的完整版。代码中已通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动获取。

## 项目结构

脚本位于工作区根目录(**没有 clipping/ 子目录**):

```
ai-live-clip/
├── asr_pipeline.py        # 阶段1: 抽音 + FunASR整段转写 → temp/transcript.json
├── transcribe_chunked.py  # 阶段1备选: 300s分块+断点续跑(仅批量/超长场景, 见纪律10) → temp/transcript.json
├── segmenter.py           # 阶段3: 切点吸附静音精修 + 置信标注 + 时长兜底 → temp/clips_refined.json
├── cut_pipeline.py        # 阶段4: 读取clips → FFmpeg切割(纯切片)
├── app.py                 # Streamlit审核界面(可选)
├── config.py              # 配置(FunASR参数、FFmpeg路径、编码器探测、时长阈值)
├── tests/test_segmenter.py    # segmenter 单元测试 (python tests/test_segmenter.py)
├── docs/archive/              # 历史设计文档与审计报告(design-five-stage-suggestion.md, skill-workflow-and-bug-report.md)
├── output/              # 切片输出目录(按 日期_原视频名 分组,组内每个切片一个子文件夹)
└── temp/                # 临时文件(transcript.json, segments.json, clips_refined.json, preview.mp4)
```

## 数据交换格式

### 阶段1输出: transcript.json

```json
{
  "video_path": "C:/path/to/video.mp4",
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
  "video_path": "C:/path/to/video.mp4",
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
  "video_path": "C:/path/to/video.mp4",
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

## 阶段1: ASR转写(脚本执行)

### FunASR 实际输出格式(已验证)

FunASR 1.4.1 的 `model.generate()` 返回:

```python
result = [{"key": "audio.wav", "text": "带标点的完整文本", "timestamp": [[210, 410], [410, 530], ...]}]
```

**关键发现**: `result[0]` 没有 `sentence_info` 字段。`text` 中每个**非标点字符**与 `timestamp` 列表**一一对应**。需要自行按标点分句重建句级时间戳。

### 句级时间戳重建逻辑

1. 遍历 text 中每个字符
2. 非标点字符 → 取下一个 timestamp `[start_ms, end_ms]`
3. 标点字符 → 不对应 timestamp,用于分句
4. 按句末标点(`。！？!?；;`)切分句子
5. 每句取首末字的时间戳作为 `start_ms` 和 `end_ms`

### 静音段提取

从相邻字之间的间隔推导:如果前一个字的 `end_ms` 到下一个字的 `start_ms` 间隔 > 500ms,即为静音段。

## 阶段2: 话题切分(你执行)

你收到 transcript.json 后,在对话中依次执行以下步骤:

### 步骤A: 话题分段

通读全部句子文本,按**语义连续性**判断话题边界。

判断规则:
- 话题粒度: 一个产品讲解 / 一个观点 / 一组问答 / 一个热点讨论
- 宁大勿碎: 能合则合,不在话题中间切
- 话题转换信号: 内容从一个主题跳到另一个主题(如从"电影讨论"跳到"产品介绍")

**无需逐字精确**: ASR 转写有 10-15% 错别字(多为同音字),但不影响整体语义判断。

### 步骤B: 相同话题合并

相邻段如果: 话题内容相同 + 间隔 < 2分钟 → 合并。

### 步骤C: 边界给出(无需手工精对齐静音)

对每个话题边界,按语义给出**近似切点**即可,并加 `note` 说明依据:

- 边界落在句间 → 给句子边界时间,后续由 segmenter.py 自动吸附到最近 ≥1.5s 静音前 300ms
- 找不到任何近静音的边界 → 保持原值(segmenter 会标 `confidence=low`,提示人工复核)
- **不需要手工搜索静音**: 脚本会做精确对齐,你给出语义上的大致位置即可

### 步骤D: 时长兜底

- < 45秒 → 并入相邻段
- \> 10分钟 → 在该段内找语义子切点再分

> 注: segmenter.py 也会做同样的时长兜底(短并长拆),你按语义先分,脚本做最后保险。

### 步骤E: 生成标题

为每段生成 5-15 字标题,前置核心名词,不加书名号。
示例: "氨基酸洗面奶温和配方解析"、"主播谈防晒霜选购误区"

### 过渡句处理策略

直播话术特征: 主播习惯在话题A未讲完时同一句话开始聊话题B。

处理规则: **一句话跨两个话题的过渡句,统一归入后一段**,保证前段结尾干净。精剪时再裁掉过渡句。

## 阶段4: 视频切割(脚本执行)

### 切片命名规则

输出目录按 `日期_原视频名` 分组（相同日期、同一原视频的所有切片放同一组），每组内再按序存放切片:

```
output/{日期_原视频名}/
└── {序号}_{标题}_{起始时间码}-{结束时间码}/
    ├── {序号}_{标题}_{起始时间码}-{结束时间码}.mp4
    └── {序号}_{标题}_{起始时间码}-{结束时间码}.jpg
```

日期从原视频文件名提取（YYYYMMDD 8位数字），文件名无日期时回退用文件修改时间。

时间码格式 `HMMSS`(紧凑无分隔符):

| 时间码 | 含义 | 对应时间 |
|--------|------|----------|
| `0037` | 0时00分37秒 | 0分37秒 |
| `0310` | 0时03分10秒 | 3分10秒 |
| `10530` | 1时05分30秒 | 1小时5分30秒 |

示例:
```
output/2026-04-20_视频号主播_20260420_232137/
└── 01_开场闲聊_0037-0310/
    ├── 01_开场闲聊_0037-0310.mp4
    └── 01_开场闲聊_0037-0310.jpg
```

## 完整执行流程

当用户说"把这段直播切片 /path/to/video.ts"时,你按以下步骤执行(命令均在工作区根目录下运行):

```
1. 运行阶段1(二选一):
   python asr_pipeline.py /path/to/video.ts        # 单视频首选: 整段转写(分块并行无收益, 见纪律10)
   python transcribe_chunked.py A.ts B.ts C.ts [--workers N]     # 批量: 模型只加载一次(4次/4进程), 抽音并行
   → 生成 temp/transcript.json(单视频); 批量时各自 temp/{视频名}.transcript.json
   → 并行: 默认 min(4, cpu核数/4, 内存GB/8) 进程 × 每进程 cpu/进程 线程; 模型加载与切块并行(worker预加载)
   → 5小时视频约 12-25 分钟(单视频); 批量 N 视频时第2个起省全部模型加载时间

2. 读取 transcript.json
   → 你在对话中完成: 话题分段 → 合并 → 生成标题(边界给出近似值即可)
   → 生成 temp/segments.json

3. 运行阶段3 切点精修(脚本):
   python segmenter.py
   → 吸附切点到最近 ≥1.5s 静音前 300ms, 标注置信度, 时长兜底
   → 输出 temp/clips_refined.json
   → 若产生 confidence=low 的切点, 调读 clips_refined.json 复核原因(reason), 必要时回到步骤2修正语义边界

4. (可选) 启动Streamlit审核:
   streamlit run app.py
   → 调整切点后导出 segments_approved.json

5. 运行阶段4:
   python cut_pipeline.py temp/clips_refined.json --mode stream_copy       # 推荐: IDR感知流复制(快~190倍, 首帧必为I帧无花屏)
   python cut_pipeline.py temp/clips_refined.json --mode stream_copy --max-snap-s 3  # 吸附误差超3s自动降级reencode(默认3)
   python cut_pipeline.py temp/clips_refined.json                          # 兜底: 重编码精确切点, 2路并行
   python cut_pipeline.py temp/clips_refined.json --workers 4              # 16核机器可上 4 路并行(约2-3x加速)
   → 纯视频切片, 不生成封面; 输出目录下生成 cut_results.json(含每片 start_offset_s = 吸附IDR-期望切点)

6. 输出结果文件列表给用户
```

## 各模块说明

完整代码以工作区内的实际文件为准(以下只列要点,不再内嵌代码副本):

- **config.py**: FunASR 模型参数(paraformer-zh + fsmn-vad + ct-punc, CPU)、FFmpeg 路径(imageio-ffmpeg 完整版)、视频编码器探测(h264_qsv → libx264 veryfast+CRF23, 结果缓存到 temp/_encoder_cache.json)、时长阈值(45s/600s/2分钟)、输出/临时目录(锚定脚本所在目录的绝对路径)。
- **asr_pipeline.py**: 抽音 → FunASR 整段转写 → 按标点重建句级时间戳 + 字间静音提取 → temp/transcript.json; duration_ms 取媒体真实时长。模型后台线程预加载(与抽音并行)。非 MP4 源不再自动生成 preview.mp4(preview 已取消, 审核界面无预览文件时仅提示)。
- **transcribe_chunked.py**: 300s 分块转写, 多进程并行(默认 min(4, cpu核数/4, 内存GB/8) workers, 每进程限制 torch 线程数=cpu/workers 避免争抢, worker 启动即预加载模型与主进程切块并行; 可 --workers 覆盖); 支持多视频批量(模型复用+抽音并行); 进度写 temp/{视频名_路径hash}.progress.jsonl, 中断后重跑自动续; 合并输出 temp/{视频名}.transcript.json(单视频同时写 transcript.json)。已知限制(2026-08-13 实测): 块边界句子被切成两半(9 边界 6 处切中完整句)+ 块边界静音丢失(静音锚点 617→602), 且单视频 CPU 场景并行无收益(340s vs 整段 236s, 见纪律10)。
- **segmenter.py**: 切点精修。规则与参数见下文"阶段3"及文件头注释(2026-08-08 五轮真实数据验证)。
- **cut_pipeline.py**: 关键帧感知流复制(推荐 `--mode stream_copy`): packets 法扫描源流 IDR 列表, 切点吸附到最近 IDR, `-c copy` 零重编码(实测比 reencode 快约190倍, 首帧必为 I 帧无花屏), 吸附误差写入 cut_results.json 的 start_offset_s; 误差超 `--max-snap-s`(默认3s)自动降级 reencode 保证精度; TS 源自动无损转封装到 temp 再切割; HEVC 输出自动加 hvc1 tag。另支持 `--mode reencode` 重编码精确切割(编码器优先 QSV 硬件加速); `--workers` 并行(默认2, 16核可试4, ThreadPool+子进程不吃GIL); 纯视频切片不生成封面; 输出按 日期_原视频名 分组。
- **app.py**: Streamlit 审核界面。自动加载 temp/segments.json 或上传 JSON(兼容 segments/clips 键); 删除状态跨 rerun 持久化; 预览优先用 temp/preview.mp4; 导出 segments_approved.json。


## 注意事项

- PowerShell 手动调试 ffmpeg ffprobe 时,先执行 `chcp 65001` 切换 UTF-8,否则中文路径会乱码;生产流程强制走 Python 脚本(subprocess 传递,不受影响)
- 切割编码器自动选择: 优先 Intel QSV 硬件加速(h264_qsv),不可用时回退 libx264 veryfast+CRF23
- FunASR CPU模式: 11分钟视频约1分钟,5小时视频约30-60分钟
- FunASR首次运行需下载模型权重(约1-2G),后续运行从本地缓存加载
- 系统 PATH 中的 ffmpeg 可能是精简版,必须使用 imageio-ffmpeg 的完整版
- 切割默认建议 `--mode stream_copy`(关键帧感知, 首帧必为 I 帧无花屏, 实测比 reencode 快约190倍); 需要帧级精确切点时用默认 re-encode(编码器优先 h264_qsv, 回退 libx264 veryfast+CRF23)。stream_copy 吸附误差超 `--max-snap-s`(默认3s)的切片会自动降级 reencode, 误差记录在输出目录 cut_results.json 的 start_offset_s(吸附IDR-期望切点), 可据此抽查切片开头
- 转写方案选择(2026-08-13 实测): 单视频(≤2h)整段 asr_pipeline 优先——CPU 总吞吐受核数限制, 分块并行不加速(10块4worker 340s 慢于整段 236s), 还引入块边界切句+静音锚点丢失; transcribe_chunked.py 仅用于多视频批量(模型复用)或需断点续跑的 >2h 长视频
- 切片命名包含时间码(HMMSS格式),标明在原直播中的时间段
- 不生成封面(纯视频切片); 如需封面可对切片首帧自行截取
- 低置信度切点(confidence=low)需人工重点复核
  - segmenter 的 confidence=low 含义: ±15s 内无 ≥1.5s 静音可吸附(通常是语义边界本身有问题), 复核时看 reason 字段
  - segmenter 首尾规则: 首段入点/末段出点保持 AI 原值不吸附(防吃片头/结尾)
- ASR 转写有 10-15% 错别字(多为同音字),不影响话题切分的整体语义判断
- 语义判断直接用 ASR 原始输出(不先做全文纠错); 但**输出到标题/文件名/note 的专有名词必须先校对**(见纪律8)
- 转写中间 WAV/进度文件带路径 hash(_video_key),不同目录同名视频不互相污染
- cut_pipeline 日期正则校验月(1-12)/日(1-31)合法性,`20261399` 类伪日期不误匹配