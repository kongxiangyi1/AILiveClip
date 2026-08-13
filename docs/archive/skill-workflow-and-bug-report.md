# 直播回放切片 Skill — 流程梳理与 Bug 检查报告

> 梳理日期：2026-08-11
> 工作区：`E:\WorkBuddyCliping1\ai-live-clip\`
> 范围：SKILL.md + config.py / asr_pipeline.py / transcribe_chunked.py / segmenter.py / cut_pipeline.py / app.py

---

## 一、Skill 定位

把 1–5 小时的直播回放视频（MP4/TS）按**独立内容单元**（产品讲解 / 观点输出 / 粉丝问答 / 热门话题）粗切成可独立传播的短视频片段，供人工精剪。

**核心设计思想：三段式半自动**

- 语义判断（话题分段、标题生成）由 AI 助手在对话中完成，不走外部 API；
- 机械工作（ASR 转写、切点吸附、视频切割）由 Python 脚本完成；
- 吸附机制只修「切点卫生」，不修「语义错误」——AI 给错边界会被标 `confidence=low` 交人工复核。

## 二、架构总览

```
阶段1 [脚本]  asr_pipeline.py / transcribe_chunked.py
              视频 → 抽音(16kHz单声道WAV) → FunASR转写 → temp/transcript.json
阶段2 [AI]    读 transcript.json → 话题分段/合并/标题 → temp/segments.json
              (只给语义近似边界，不手工对静音)
阶段3 [脚本]  segmenter.py
              吸附静音锚点 + 句首/句尾保护 + 置信标注 + 时长兜底
              → temp/clips_refined.json
阶段4 [脚本]  cut_pipeline.py
              FFmpeg 切割 + 多帧抽样选封面 → output/{日期_视频名}/{切片}/
可选  [脚本]  app.py  Streamlit 人工审核界面（调整/删除/导出）
```

### 模块职责表

| 文件 | 阶段 | 职责 | 输入 | 输出 |
|---|---|---|---|---|
| `asr_pipeline.py` | 1 | 一次性整段 FunASR 转写 | 视频文件 | `temp/transcript.json` |
| `transcribe_chunked.py` | 1（备选） | 300s 分块转写 + 断点续跑 | 视频文件 | `temp/transcript.json`（同格式） |
| （AI 对话） | 2 | 话题分段、合并、起标题 | transcript.json | `temp/segments.json` |
| `segmenter.py` | 3 | 切点吸附静音 + 置信标注 + 时长兜底 | transcript.json + segments.json | `temp/clips_refined.json` |
| `cut_pipeline.py` | 4 | FFmpeg 切割 + 封面截帧 | clips_refined.json（兼容 segments.json） | `output/.../*.mp4 + *.jpg` |
| `app.py` | 可选 | Streamlit 审核、微调、导出 | segments/clips JSON | `segments_approved.json`（下载） |
| `config.py` | — | FunASR 参数、FFmpeg 路径、编码器探测、阈值 | — | — |

## 三、前置环境

```powershell
pip install funasr imageio-ffmpeg streamlit pillow numpy
```

- **FFmpeg 必须用 imageio-ffmpeg 自带的完整版**（系统 PATH 中的可能是精简版，缺 AAC 解码器 / WAV muxer）。代码通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动获取。
- FunASR 首次运行自动下载模型权重（约 1–2 GB），之后走本地缓存。
- CPU 转写速度参考：11 分钟视频 ≈ 1 分钟；5 小时视频 ≈ 30–60 分钟。
- pillow / numpy 用于封面帧抽样（cut_pipeline），缺失时静默降级为中点截帧（见 Bug P1-5）。

## 四、数据交换格式

### 4.1 transcript.json（阶段1输出）

```json
{
  "video_path": "C:/path/to/video.mp4",
  "duration_ms": 708015,
  "sentences": [{"text": "今天给大家分享一款洗面奶", "start_ms": 210, "end_ms": 4050}],
  "silences":  [{"start_ms": 6800, "end_ms": 7200, "duration_ms": 400}]
}
```

### 4.2 segments.json（阶段2输出，AI 生成）

```json
{
  "video_path": "C:/path/to/video.mp4",
  "segments": [{
    "title": "氨基酸洗面奶成分与用法讲解",
    "start_ms": 210, "end_ms": 320000,
    "duration_s": 319.8,
    "confidence": "high",
    "topic_raw": "洗面奶产品讲解",
    "note": "边界依据说明（可选）"
  }]
}
```

### 4.3 clips_refined.json（阶段3输出）

```json
{
  "video_path": "...",
  "duration_ms": 708015,
  "params": {"pause_min_ms": 1500, "window_s": 15, "margin_s": 0.3,
             "short_sent_ms": 8000, "long_gap_ok_ms": 2500,
             "merge_min_s": 45, "split_max_s": 600},
  "clips": [{
    "idx": 0, "title": "...", "start_ms": 210, "end_ms": 320000,
    "confidence": "high", "reason": "吸附到静音前-1.2s",
    "label": "...", "boundary_hint": "..."
  }]
}
```

`cut_pipeline.py` 与 `app.py` 均兼容 `segments` / `clips` 两种键。

## 五、详细执行流程

### 步骤 1：ASR 转写（脚本）

```powershell
# 方式A：一次性转写（短视频）
python asr_pipeline.py <视频路径>

# 方式B：分块断点续跑（长视频推荐，300s/块，中断后重跑自动续）
python transcribe_chunked.py <视频路径>
```

内部步骤：
1. FFmpeg 抽取 16kHz 单声道 WAV；
2. FunASR（paraformer-zh + fsmn-vad + ct-punc，CPU）转写；
3. **句级时间戳重建**：FunASR 返回的 `text` 中每个非标点字符与 `timestamp` 列表一一对应，按句末标点（。！？!?；;）分句，取首末字时间戳作为句子边界；
4. **静音段提取**：相邻字间隔 > 500ms 记为一个静音段；
5. 写 `temp/transcript.json`，清理临时 WAV。

### 步骤 2：AI 话题切分（对话中完成）

依次执行：

- **A. 话题分段**：按语义连续性判断边界。粒度 = 一个产品讲解 / 一个观点 / 一组问答 / 一个热点；宁大勿碎。
- **B. 相同话题合并**：相邻段话题相同且间隔 < 2 分钟 → 合并。
- **C. 边界给近似值**：不需要手工找静音，segmenter 会自动吸附；找不到近静音的边界保持原值即可（脚本会标 low）。建议加 `note` 说明依据。
- **D. 时长兜底**：< 45s 并入相邻段；> 10 分钟找语义子切点再分（segmenter 会再做一次保险）。
- **E. 生成标题**：5–15 字，前置核心名词，不加书名号。
- **过渡句规则**：一句话跨两个话题时，整句归入**后一段**，保证前段结尾干净。

产出 `temp/segments.json`。

### 步骤 3：切点精修（脚本）

```powershell
python segmenter.py [segments.json路径]   # 默认 temp/segments.json
```

对每个切点按以下优先级处理：

1. **首尾保护**：首段入点 / 末段出点保持 AI 原值不吸附（防吃片头/片尾）。
2. **句尾保护**：切点贴近某句 end（±0.5s）时，ASR 句末时间戳常低估句尾字，改为吸附到该句**之后**的停顿中心 −300ms。
3. **句首保护**：切点贴近某句 start（±0.5s）时，吸附到该句**之前**最近停顿中心 −300ms；无紧邻停顿时微调 −300ms 并标 `low`。
4. **原切点安全**（句外，或长句内邻近 ≥2.5s 静音）→ 不吸附，标 high。
5. **吸附**：±15s 窗口内找 ≥1.5s 的静音锚点，按距离升序尝试，落在锚点中心 −300ms；会截断 ≤8s 短句的吸附点被拒绝。
6. 无锚点可吸附 → 保持原值，标 `confidence=low`，需人工复核。

然后做**时长兜底**：
- `merge_short`：< 45s 的段并入前一段；
- `split_long`：> 600s 的段按内部最长停顿拆分（拆分点标 low）。

输出 `temp/clips_refined.json`。**若有 confidence=low，读取 reason 复核，必要时回到步骤 2 修正语义边界。**

### 步骤 4（可选）：Streamlit 人工审核

```powershell
streamlit run app.py
```

- 自动加载 `temp/segments.json`，也可上传任意 segments/clips JSON；
- 每个切片可改标题、调起止时间、删除（删除状态跨 rerun 持久化）；
- 🔴 标记 confidence=low 的切片需重点复核；
- 预览优先使用 `temp/preview.mp4`（浏览器不支持 .ts 直接播放）；
- 导出 `segments_approved.json` 下载，再交给 cut_pipeline 切割。

### 步骤 5：视频切割（脚本）

```powershell
python cut_pipeline.py temp/clips_refined.json
```

- 编码器自动探测：优先 h264_qsv（Intel 核显硬加速，启动时实际编码一帧验证），失败回退 libx264 veryfast + CRF23；
- 前置 `-ss` + `-t` 精确切割，re-encode 保证切点帧精度；
- **封面**：片内间隔抽样 5–20 帧，160px 降采样后计算灰度标准差，取信息量最大的一帧（避开直播纯灰/纯黑帧）；两段式 seek（先到 t−10s 预热再精确到 t）解决 HEVC/TS 上直接 seek 解出灰色填充帧的问题。

### 步骤 6：输出命名规则

```
output/{日期_原视频名}/
└── {序号}_{标题}_{起始时间码}-{结束时间码}/
    ├── {序号}_{标题}_{起始时间码}-{结束时间码}.mp4
    └── {序号}_{标题}_{起始时间码}-{结束时间码}.jpg
```

- 日期从原视频文件名提取（YYYYMMDD 8 位数字），无日期回退文件修改时间；
- 时间码为紧凑格式：无小时 `MMSS`（如 `0037`=37 秒、`0310`=3分10秒），有小时 `HMMSS`（如 `10530`=1小时5分30秒）；
- 标题经非法字符清理并截断至 40 字符。

## 六、关键参数表（segmenter.py，经真实数据验证固化）

| 参数 | 值 | 含义 |
|---|---|---|
| PAUSE_MIN_MS | 1500 | 吸附锚点：≥1.5s 的字符级静音 |
| WINDOW_S | 15 | 吸附搜索窗口 ±15s |
| MARGIN_S | 0.3 | 切点落在静音中心前 300ms |
| SHORT_SENT_MS | 8000 | 吸附进 ≤8s 短句内部 → 拒绝 |
| LONG_GAP_OK_MS | 2500 | 长句内邻近 2.5s 静音视为可接受 |
| SENT_END_TOL_S / SENT_START_TOL_S | 0.5 | 句尾/句首保护容差 |
| MERGE_MIN_S | 45 | 短于 45s 并入相邻段 |
| SPLIT_MAX_S | 600 | 长于 600s 强制拆分 |

---

## 七、Bug 检查报告

### P0 — 影响正确性，建议尽快修

**P0-1 `transcribe_chunked.py`：同名视频互相污染（数据错误且无任何提示）** —— ✅ 已修复（2026-08-11）
- ~~中间 WAV 命名为 `_chunk_{basename}.wav`，进度文件为 `{basename}.progress.jsonl`，都不含路径信息~~
- 修复：新增 `_video_key()` = 文件名 + 绝对路径 md5 前8位，WAV 与进度文件均带 hash，不同目录同名视频不再串数据；同一路径重跑仍可正常续跑。

**P0-2 `segmenter.py merge_short()`：首个短片段永远不会被合并** —— ✅ 已修复（2026-08-11）
- ~~注释承诺「第一个则并入后一个」，但 `len(out)==0` 时直接 append 保留~~
- 修复：正向循环后追加 while 循环，首段 <45s 时并入后一段（更新 start_ms/confidence/reason）；已实测验证。

**P0-3 SKILL.md 与实际代码严重脱节** —— ✅ 已修复（2026-08-11）
- 修复：命令路径全部去掉 `clipping/` 前缀；删除约 470 行过时的内嵌代码副本，改为指向实际文件的模块要点说明；补充 transcribe_chunked.py / tests/ / docs/ 到项目结构；阶段编号统一为 1/2/3/4；补充 pillow+numpy 依赖、preview.mp4、分块转写说明。

### P1 — 功能缺失 / 静默降级

**P1-4 `app.py` 引用的 `temp/preview.mp4` 全流程无人生成** —— ✅ 已修复（2026-08-11）
- 修复：`asr_pipeline.py` 新增 `make_preview()`（480p 低码率 MP4，仅非 MP4 源时生成，失败只警告）；`transcribe_chunked.py` 合并完成后同样调用；`app.py` 在源视频非 MP4 且无 preview 时显示明确提示。

**P1-5 封面抽样依赖 pillow + numpy，未声明、缺失时静默失效** —— ✅ 已修复（2026-08-11）
- 修复：SKILL.md 前置环境补充 pillow/numpy；`_frame_std()` 单独捕获 ImportError 并打印一次性警告（回退中点帧），不再静默。

**P1-6 `app.py` 总时长统计对 clips_refined.json 恒为 0** —— ✅ 已修复（2026-08-11）
- 修复：`duration_s` fallback 改为 `(end_ms - start_ms) / 1000`。

**P1-7 SKILL.md 引用的文件不存在** —— ✅ 已解决（2026-08-11）
- ~~项目结构列了 `tests/test_segmenter.py`，实际没有 tests/ 目录~~ → 文件已补充，且实测 `python tests/test_segmenter.py` 全部断言通过；
- ~~多处引用 `docs/design-five-stage-suggestion.md` §9 验证结论，docs/ 目录是空的~~ → 文件已补充，引用链恢复完整。

### P2 — 小问题 / 健壮性（全部已于 2026-08-11 修复）

**P2-8 `config.py` 探测开销与死代码** —— ✅ 已修复
- 编码器探测结果缓存到 `temp/_encoder_cache.json`（按 ffmpeg 路径做 key，换 ffmpeg 自动重探）；删除未使用的 `probe_input` 变量；`NUL` 写法加注"仅 Windows"注释。

**P2-9 `segmenter.py` 先并后拆的顺序问题** —— ✅ 已修复
- `apply_duration_rules()` 顺序调整为与 run() 一致的「先 merge_short 后 split_long」并加注释说明（拆分尾段不回头合并），run() 改为直接调用它，消除两份逻辑。

**P2-10 `asr_pipeline.py` `duration_ms` 口径** —— ✅ 已修复
- 新增 `probe_duration_ms()` 解析 ffmpeg Duration 输出取媒体真实时长，失败才回退末句时间戳；与 chunked 版口径一致。

**P2-11 `transcribe_chunked.py` 杂项** —— ✅ 已修复
- docstring 移除误导性的 `--resume`（实际为自动续跑），补充块边界句子截断的已知限制说明；Duration 正则匹配失败时打印 ffmpeg 输出并友好退出。

**P2-12 `cut_pipeline.py` 杂项** —— ✅ 已修复
- 切割循环补充捕获 `OSError`/`KeyError`，单切片失败不再中断整批；日期正则增加月(1-12)/日(1-31)合法性校验，`20261399` 类伪日期不再误匹配。

---

## 八、修复状态总结（2026-08-11）

**12 项全部处理完毕**：P0×3 + P1×4 + P2×5。

回归验证：
- `python tests/test_segmenter.py` 全部断言通过；
- 6 个模块全部通过语法检查；
- 编码器探测缓存生效（h264_qsv 命中，缓存文件已生成）；
- merge_short 首段合并修复经断言实测验证。
