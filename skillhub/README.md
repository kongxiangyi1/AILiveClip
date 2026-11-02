# ai-live-clip · 直播切片工具

把 1–5 小时的直播回放 / 录播长视频，按**内容话题**自动粗切成可独立传播的短视频片段，供人工精剪。

你只需要给一句指令和视频路径，其余全部自动完成：

```
把这段直播切片 E:\录播\07-10直播回放.ts
```

- **语义判断交给 AI**：话题分段、合并、过渡句归属、起标题、专有名词校对、切点复核
- **精确对齐交给脚本**：静音吸附、置信分级、时长兜底、FFmpeg 切割
- **全程本地执行**：视频不出本机，不上传任何服务器

## 前置环境

| 依赖 | 说明 |
|---|---|
| Python | 3.10+ |
| FunASR | paraformer-zh + fsmn-vad + ct-punc，CPU 推理，首次运行下载模型约 1–2 GB |
| imageio-ffmpeg | 提供完整版 FFmpeg（系统自带精简版常缺 AAC 解码器） |

```powershell
pip install funasr imageio-ffmpeg streamlit pillow numpy
```

脚本通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动定位 FFmpeg，无需手动配置。

> 仅在 Windows 上完整实测；其他平台需自行验证 FunASR 与 FFmpeg 可用性。

## 四阶段流水线

```
阶段1 [脚本]     MP4/TS → 抽音 → FunASR 转写   → temp/transcript.json
阶段2 [AI]       通读转写 → 话题分段/合并/标题  → temp/segments.json
阶段3 [脚本]     静音吸附 + 置信分级 + 时长兜底 → temp/clips_refined.json
阶段4 [脚本]     FFmpeg 切割                    → output/{日期_原视频名}/
```

AI 只需给出**语义边界近似值**，精确吸附由脚本完成。切点按风险分级（`safe` / `low_risk` / `high_risk`），AI 只对 `high_risk` 复核，避免全量人工介入。

## 可选后处理

- **Streamlit 审核界面**：`streamlit run scripts/app.py`，图形化调边界后导出再走阶段 3
- **专有名词校对**：`review_checklist.py` 生成候选清单，确认后用 `fix_brand_name.py` 统一修正标题与产物文件名
- **多切片号去重**：`clip_registry.py` / `clip_status.py` / `update_registry.py` 维护全局注册表，防止同一段内容在不同账号重复发布（详见 `references/clip-registry.md`）
- **粗口静音**：`mute_profanity.py` 对切片内敏感区间静音，视频流不变

## 输出结构

```
output/{日期_原视频名}/
├── cut_results.json          # 每片切割结果与吸附误差
├── 01_{标题}_{起-止}.mp4
├── 02_{标题}_{起-止}.mp4
└── ...
```

产出为纯视频切片，不生成封面。

## 已知限制

- **ASR 同音错字**：转写有 10–15% 同音错字，不影响语义分段，但**标题与文件名中的品牌名 / 人名必须先校对**，不确定时应向使用者确认
- **吸附只修切点卫生，不修语义错误**：边界 ±15s 内无静音会被标记低置信并拒绝吸附，需人工复核
- **TS + HEVC 源**：IDR 间隔约 10s，关键帧流复制会大量降级，建议直接走精确重编码
- **超长 / 批量场景**：`transcribe_chunked.py` 提供 300s 分块与断点续跑；单视频 ≤2h 用整段转写更快

## 文档

- `SKILL.md` — AI 工作流指令（触发条件、切片纪律、完整执行流程）
- `references/stages.md` — 各阶段详细规则与数据格式
- `references/discipline.md` — 切片执行纪律与踩坑清单
- `references/funasr-install.md` — FunASR 安装与常见问题
- `references/lessons-learned.md` — 本机运维与工具坑
- `references/clip-registry.md` — 多账号分发的去重协调规则

## 隐私

本技能不内置任何品牌名、人名或主播词表。专有名词校对所需的对照表由使用者在首次使用时自行提供（`references/` 下有占位示例与建立方法）。
