# ai-live-clip — 直播回放内容切片流水线

将直播回放视频（1-5 小时）按**独立内容单元**（产品讲解、观点输出、粉丝问答、热门话题）自动粗切为可独立传播的短视频片段，供人工精剪。

**使用者只需提供原视频路径 + 切片要求，其余全部自动完成。** 语义判断（话题分段、标题生成、切点复核）由 AI Agent 在对话中完成，脚本负责转写、切点精修与视频切割。

## 核心特性

- **四阶段流水线**：语义任务（AI Agent）与脚本解耦，AI 只需给出语义边界近似值，精确吸附交给脚本
- **一键式使用**：`把这段直播切片 /path/to/video.ts` → 全流程自动，最终输出切片文件列表
- **切点分级（review_priority）**：`0=safe / 1=low_risk / 2=high_risk`，AI Agent 自动复核 high_risk 切点
- **人工钉住边界（pin）**：`segments.json` 加 `"pin_start"/"pin_end": true` 即可钉住切点不被吸附规则带偏
- **静音吸附**：切点自动吸附到最近 ≥1.5s 静音前 300ms；±15s 内无静音则标记 `confidence=low` 拒绝吸附
- **关键帧感知切割**：`--mode stream_copy` 用 IDR 扫描流复制（比 reencode 快约 190 倍），误差超限自动降级 reencode
- **硬件加速**：切割优先 Intel QSV（h264_qsv），回退 libx264；FunASR CPU 推理实测约 12.4x 实时
- **断点续跑**：超长视频/批量场景可用分块转写（300s 块 + progress 续跑）

## 架构

```
阶段1 [脚本]    MP4/TS → 抽音 → FunASR 转写 → temp/transcript.json
                (asr_pipeline.py 整段转写; transcribe_chunked.py 仅批量/断点续跑场景)
阶段2 [AI Agent] 读 transcript → 话题分段/合并/过渡句/标题 → temp/segments.json
                (只给语义边界近似值, 不要求精确对齐静音)
阶段3 [脚本]    segmenter.py 吸附静音 + 置信分级 + 时长兜底 → temp/clips_refined.json
阶段4 [脚本]    FFmpeg 切割 (stream_copy 快速 / reencode 精确) → output/ 切片
```

## 责任分工

| 角色 | 负责内容 |
|------|---------|
| 使用者（人工） | 只做两件事：提出切片要求、给出原视频路径 |
| AI Agent | 全部语义任务：通读转写结果、话题分段与合并、定边界、起标题、专有名词校对（不确定时向使用者确认）、调度各阶段脚本、复核 high_risk 切点、验收并汇报结果 |
| Python 脚本 | 阶段1 转写、阶段3 切点精修、阶段4 切割（全自动，无需使用者介入） |

## 环境要求

- Python 3.10+
- FunASR（paraformer-zh + fsmn-vad + ct-punc，CPU 模式，首次运行下载模型约 1-2GB）
- imageio-ffmpeg（完整版 FFmpeg，系统自带精简版缺 AAC 解码器）

```powershell
pip install funasr imageio-ffmpeg streamlit pillow numpy
```

> 代码通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动使用完整版 FFmpeg，无需手动配置。

## 使用流程

### 使用者视角（只需一句指令）

```
把这段直播切片 E:\录播\07-10直播回放.ts
```

AI Agent 收到后自动完成以下全部工作，最终输出切片文件列表。过程中除专有名词需确认外（见步骤 2f），**使用者无需任何操作**。

### 内部执行流程（AI Agent + 脚本自动执行）

**步骤 1：ASR 转写（[脚本]）**

```powershell
python asr_pipeline.py /path/to/video.ts
# → temp/transcript.json (句级时间戳 + 静音段)
```

首次运行会下载模型（约 1-2GB），后续从本地缓存加载。单视频（≤2h）一律用整段转写（见"已知限制"）。

**步骤 2：语义分段（[AI Agent]）**

AI Agent 收到 `transcript.json` 后，按顺序完成：

1. **通读全部句子**，按语义连续性分段——话题粒度：一个产品讲解 / 一个观点 / 一组问答 / 一个热点讨论，**宁大勿碎**
2. **合并相邻同话题段**（话题相同且间隔 < 2 分钟）
3. **定边界**：给语义近似值即可，无需手工搜索静音（脚本会吸附）；一句话跨两个话题的**过渡句归入后一段**
4. **时长兜底**：< 45 秒并入相邻段；> 10 分钟在段内找子切点拆分
5. **起标题**：每段 5-15 字，前置核心名词，不加书名号
6. **专有名词校对（必做）**：把全片品牌名/人名/地名列成清单核对一次（ASR 有 10-15% 同音错字，如"绝尘→觉尘"），**不确定时列出清单向使用者确认**，确认无误后才落标题

然后写出 `temp/segments.json`：

```json
{"video_path": "...", "segments": [
  {"title": "氨基酸洗面奶成分与用法讲解", "start_ms": 210, "end_ms": 320000,
   "confidence": "high", "topic_raw": "洗面奶产品讲解", "note": "边界依据",
   "pin_start": false, "pin_end": true}]}
```

**步骤 3：切点精修（[脚本]）**

```powershell
python segmenter.py
# → temp/clips_refined.json
```

自动完成：切点吸附到最近 ≥1.5s 静音前 300ms、置信分级、时长兜底（短并长拆）。控制台打印 high_risk 切点列表与上下文、low_risk 数量。

**步骤 4：切点复核（[AI Agent]，自动处理）**

AI Agent 打开 `temp/clips_refined.json`，按 `review_priority` 分级处理：

| 等级 | 判定 | 处理 |
|------|------|------|
| `0 safe` | 高置信吸附 | 跳过 |
| `1 low_risk` | 吸附偏移 ≤500ms | 批量接受 |
| `2 high_risk` | 任一端口偏移 >500ms | AI Agent 看该段的 `context` 字段（吸附前后切点 + 前后各 1 句文本）判断对错 |

修正方式（不手改 clips_refined.json，它是脚本产物）：

- 语义边界给错 → 回到 `segments.json` 修正 `start_ms` / `end_ms` → 重跑步骤 3
- 边界正确但被吸附规则带偏（如过渡短句被吞入前段）→ 在 `segments.json` 对应段加 `"pin_end": true`（或 `"pin_start": true`）钉住 → 重跑步骤 3

可选：`streamlit run app.py` 打开图形审核界面，调整后导出 `segments_approved.json` 再走步骤 3。

**步骤 5：切割（[脚本]）**

```powershell
python cut_pipeline.py temp/clips_refined.json --mode stream_copy   # 推荐: 关键帧感知流复制
python cut_pipeline.py temp/clips_refined.json                       # 兜底: reencode 精确切点
# → output/{日期_原视频名}/ 按片输出, 附 cut_results.json
```

**步骤 6：验收与汇报（[AI Agent]）**

- 查看 `output/{日期_原视频名}/cut_results.json`：stream_copy 模式下 `start_offset_s` 为吸附 IDR 帧与期望切点的误差，过大说明该片开头可能吞/缺画面
- 抽查切片开头
- 向使用者输出切片文件列表

## 数据交换格式

**transcript.json**（阶段1 输出）：句级时间戳 + 静音段
```json
{"video_path": "...", "duration_ms": 708015,
 "sentences": [{"text": "今天给大家分享一款洗面奶", "start_ms": 210, "end_ms": 4050}],
 "silences": [{"start_ms": 6800, "end_ms": 7200, "duration_ms": 400}]}
```

**segments.json**（阶段2 输出，AI Agent 生成）：见上方步骤 2。`pin_start` / `pin_end` 为可选钉住标记。

**clips_refined.json**（阶段3 输出，segmenter 生成）：在 segments 基础上增加 `start_offset_ms`、`end_offset_ms`（吸附偏移，正=向后移）、`review_priority`（0/1/2）、`context`（high_risk 段切点上下文文本）。

## 输出结构

```
output/{日期_原视频名}/
├── cut_results.json                # 每片切割结果与吸附误差(start_offset_s)
├── 01_{标题}_{起-止}.mp4
├── 02_{标题}_{起-止}.mp4
└── ...
```

- 目录名 = `{序号:02d}_{sanitize(标题)}_{起始时间码}-{结束时间码}`，时间码为 HMMSS 紧凑格式
- 纯视频切片，不生成封面

## 项目结构

```
ai-live-clip/
├── asr_pipeline.py        # 阶段1: 抽音 + FunASR 整段转写 → transcript.json
├── transcribe_chunked.py  # 阶段1备选: 300s 分块 + 断点续跑(批量/超长场景)
├── segmenter.py           # 阶段3: 静音吸附 + 置信分级 + 时长兜底
├── cut_pipeline.py        # 阶段4: FFmpeg 切割(stream_copy / reencode)
├── app.py                 # Streamlit 审核界面(可选): streamlit run app.py
├── config.py              # 模型/FFmpeg/编码器探测/时长阈值配置
├── tests/test_segmenter.py    # segmenter 单元测试
├── docs/                  # 设计文档与历史审计报告
├── SKILL.md               # AI Agent 工作流指令(切片纪律/完整执行流程/注意事项)
├── output/                # 切片输出(不入库)
└── temp/                  # 中间产物(不入库)
```

## 测试

```powershell
python tests/test_segmenter.py
```

## 已知限制与设计取舍

- **转写方案（2026-08-13 实测）**：单视频（≤2h）用整段转写——CPU 总吞吐受核数限制，分块并行不加速（10 块 4 worker 340s 慢于整段 236s），且块边界确定性切残句子并丢失静音锚点；`transcribe_chunked.py` 仅用于**多视频批量**（模型复用）或**需断点续跑的 >2h 长视频**
- **ASR 错字**：转写有 10-15% 同音错字，不影响语义分段；但标题/文件名中的**专有名词必须先校对**（如"绝尘→觉尘"），不确定时向使用者确认
- **过渡句归属**：一句话跨两个话题时归入后一段
- **吸附机制只修切点卫生，不修语义错误**：AI 边界 ±15s 内无静音会被拒绝并标 low，需复核
- **TS+HEVC 源**：IDR 间隔约 10s，stream_copy 会大量降级，建议直接 `--mode reencode`（QSV 硬件加速）
- **切点分级**：`low_risk`（偏移 ≤500ms）可批量接受，`high_risk`（任一端口偏移 >500ms）必须复核 context

## 相关文档

- [SKILL.md](SKILL.md) — AI Agent 工作流指令（切片纪律、完整执行流程、各模块说明）
- [docs/design-five-stage-suggestion.md](docs/archive/design-five-stage-suggestion.md) — 五阶段设计
- [docs/stream_copy-optimization.md](docs/stream_copy-optimization.md) — 关键帧感知切割优化
- [docs/future-optimizations.md](docs/future-optimizations.md) — 优化方向
