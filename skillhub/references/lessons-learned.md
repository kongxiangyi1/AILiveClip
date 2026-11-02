# 直播切片工具 — 实战经验沉淀（运维 / 工具坑）

> 整理自 2026-08-18 首跑 `直播回放-08月14日.mp4`（HEVC/912×1920/7660s）的真实复盘。
> 属于"本机运维层"知识，与 `discipline.md`（执行纪律）互补。每次任务前扫一眼可避免数小时空转。

---

## 0. 本机环境事实（2026-08-18 实测；2026-08-28 修订）

- **FunASR 运行 venv（⚠️ 已损坏，勿用）**：`envs/funasr-clip` 现已被污染为 torch 2.13.0 + modelscope 1.39.1 → FunASR 模型加载报 "not registered"、VAD 推理产出空段。**不要再用它。**
- **FunASR 运行 venv（✅ 现行可用）**：自建的 Python 3.12 专用 venv（下文记作 `envs/funasr-py312`，按你实际创建的位置替换，见 `references/funasr-install.md`），固定版本：
  ```powershell
  envs/funasr-py312/Scripts/python.exe -m pip install `
    torch==2.3.1 torchaudio==2.3.1 numpy==1.26.4 `
    modelscope==1.18.0 transformers==4.49.0 funasr==1.4.1 `
    onnxruntime soundfile imageio-ffmpeg "setuptools<81" more_itertools `
    -i https://mirrors.aliyun.com/pypi/simple/
  ```
  - **版本冲突根因**：`transformers>=5` 要求 `torch>=2.5` 会直接禁用 PyTorch 导致模型注册失败；`torch>=2.5` 又与 `funasr 1.4.1` 不兼容。锁 `torch 2.3.1 + transformers 4.49` 是唯一稳定组合。`setuptools<81` 供 `distutils` 兼容（Python 3.12 已移除 distutils）。
  - **pip 镜像坑**：`transformers` 降级在 tencent 镜像卡死（27min 仅 13s CPU，safe-delete 回收站挂起）；**用 aliyun 镜像**正常。
  - **modelscope 下载绕过（关键）**：`modelscope 1.18` 仍会尝试重新下载权重并在沙箱回收站清理时 FAIL_CLOSED 失败。本机权重已缓存在 `~/.cache/modelscope/hub/models/`：ASR=`iic/speech_seaco_paraformer_large_*`、VAD=`damo/speech_fsmn_vad_*`、PUNC=`iic/punc_ct-transformer_cn-en-*`（**中文 ct-punc 权重本地缺失**，用 cn-en 替代，标点正常）。运行时把这三个本地目录作为 `model_dir` 传给 `AutoModel`，**不要传模型 ID**（否则触发下载失败）。
- **ffmpeg**：来自 `imageio-ffmpeg`（`imageio_ffmpeg.get_ffmpeg_exe()`），功能完整（含 AAC/HEVC/WAV muxer）。
- **ffprobe：imageio-ffmpeg 不提供！** 本机可用 ffprobe 在 `D:\software\install\ffprobe.exe`（版本 7.1-essentials，工作正常）。任何验证/探针脚本必须**显式传此路径**，不要依赖 `imageio_ffmpeg`，也不要依赖 PATH 里那个可能缺失/精简的 ffprobe。
- **编码器**：本机有 Intel QSV，`cut_pipeline` 自动选 `h264_qsv`；单 worker `speed≈1.7x`，4 worker 并行 aggregate≈6–7x。HEVC 源重编码为 H.264/MP4 正常。
- **实测耗时锚点**：ASR `rtf_avg=0.072`（≈13.8x），7660s 视频纯转写 ~9.3min（含 ModelScope 模型首下载约 +3–5min）；20 片 reencode 切割 ~25min。

---

## 1. 安装（最重要！避免 ~50 分钟空转）

- **不要用 `uv` 安装。** 本机 `uv` 在自己的 Windows 沙箱里把文件删除包装成"回收站 safe-delete"，沙箱回收站不可用时删除失败 → `jieba`/`oss2` 等 **sdist 包构建失败**（`[safe-delete][SAFE_DELETE_FAIL_CLOSED]`）。`uv pip install` 和 `uv venv`+`python -m pip` 都踩。
- **正确做法**：用 `python -m venv` 建 venv，再 `python -m pip install`（需在 Bash 有写权限下跑，sdist 构建要正常 `os.remove`）。**镜像用 aliyun**（tencent 在 transformers 降级时会卡死）。
- **绝对不要 `--only-binary :all:`**：`funasr` 依赖 `oss2`，它**只有 sdist、没有 wheel**，强制纯二进制直接失败。
- **重安装务必前台 + 长 timeout（≥600s）**：后台 + 沙箱关闭的 Bash 命令不会被保留（会被杀），导致"装了半天环境是空的"。
- **实测成功命令（2026-08-28，重建 `envs/funasr`）**：见 #0 的版本清单（torch 2.3.1 / transformers 4.49 / funasr 1.4.1 固定组合）。装完后务必用本地权重目录（model_dir）绕过 modelscope 下载验证一次转写（见 #0）。

---

## 2. 路径约定（脚本间不一致，必须手动对齐）

- `asr_pipeline.py` 写 `temp/` 到 **`AI_LIVE_CLIP_WORKDIR`**（即录制文件夹）。
- `segmenter.py` / `review_checklist.py` 的 `TEMP` **硬编码**为技能自身 `scripts/temp`。
- 因此：阶段 1 后必须把 `录制文件夹/temp/transcript.json` **复制**到 `scripts/temp/transcript.json`；`clips_refined.json` 也落在 `scripts/temp/`。
- `cut_pipeline` 输出受 `AI_LIVE_CLIP_WORKDIR` 控制 → `录制/output/<日期>_.../`。
- **替代做法（推荐，避免污染 skill 目录）**：阶段1/2/3 的中间产物统一放在录制文件夹 `clip_0001/temp/`，直接调用 `segmenter.run(tr_path, seg_path, out_path)` 与 `cut_pipeline.run(clips_path, ...)` 的**显式路径**参数（运行时设 `AI_LIVE_CLIP_WORKDIR=clip_0001` 让 OUTPUT_DIR 落工作区），无需把 transcript 复制到 `scripts/temp/`。2026-08-28 即采用此模式跑通（某白酒带货专场）。

---

## 3. 输出目录名陷阱

- `extract_video_date` 在文件名找不到 `YYYYMMDD` 8 位数字时，回退用**文件修改时间**。
- 本次文件名 `直播回放-08月14日` 无 8 位数字 → 目录变成 `2026-08-17_直播回放-08月14日`（按 mtime，内容却是 8/14）。内容正确，但目录名与内容日期不符。
- 建议：命名视频时带 `YYYYMMDD`（如 `直播回放-20260814.mp4`），或切割后手动重命名目录对齐内容日期。

---

## 4. 验收脚本（纪律 #19/#20）的坑

- **静音扫描只扫前 10s**：`ffmpeg -i 整段 -af silencedetect` 会解码**整个切片**（最长 12 分钟）→ 极慢、易触发 120s 超时假死（表现为脚本无输出 + exit 1）。必须 `-t 10` 只处理开头。
- **ffprobe 查询必须含 `codec_type`**：`-show_entries stream=codec_name,width,height` 不含 `codec_type` 时，按 `codec_type=="video"` 过滤会拿不到视频流 → `v=None` → `'NoneType' has no attribute 'get'` 崩溃。修正：加 `stream=codec_type,codec_name,width,height`。
- **验证脚本先对单个样本试跑**，再全量跑；别直接全量再排错（本次在此浪费 ~10–15min）。

---

## 5. segmenter 的短尾泄漏

- segmenter 的 `merge_short` 在 `split_long` **之前**运行 → 超长段被强制拆分后，拆分出的 <45s 尾部碎片没被合并 → 出现 38s/41s 无意义碎片（片#3/#5 曾出现）。
- 修复：切割前跑一个后处理（如 `merge_short_tails.py`）把 <45s 碎片并入相邻段。已并入本次流程。

---

## 6. 沟通 / 流程

- 纪律 #16"一轮确认制"是好纪律，但**停在闸门时务必让用户清楚"还没切、在等你确认"**。本次用户看到空目录第一反应是"没切出来"，说明等待状态没传达清楚。
- 呈现确认清单时明确写：「现在 `output/` 是空的，回复确认我才下刀」；用户去翻空目录 = 想推进的强信号，直接继续即可。

---

## 7. 2026-08-21 复盘新增（07-10 直播回放任务）

> 本次任务在纪律/经验已较完整前提下，仍暴露若干"新坑"与"重演坑"，统一沉淀如下，避免下次再踩。

- **scripts/temp 跨任务污染（新坑）**：`scripts/temp/` 是 skill 全局共享工作区，任意新任务跑 asr_pipeline 都会覆盖 `transcript.json`/`segments.json`。本次做文案时读到的是 08-19 另一场直播内容。救回方式：以 `录制/output/<日期>/cut_results.json` 为稳定真相源（它不会被后续任务覆盖），文案/验收都从它取，不依赖 scripts/temp。长期方案：需留存的中间产物复制到录制文件夹 `temp/` 或 output，不要让 scripts/temp 成为唯一来源。
- **文件名敏感字符清洗应前置（新）**：`vs`/`%`/`&`/引号/空格等在某些上传平台报错。本次交付后才用 `scripts/temp/rename_clips.py` 手动清洗（`vs→对比`、`93%→百分之九十三`、去中文引号）。应固化为交付前标准步骤：cut_pipeline 后跑一次文件名清洗，或直接在 sanitize 里做语义化替换（而非简单 `_`）。脚本可提升为 `scripts/clean_filenames.py`。
- **pin 边界须查相邻重叠（新，segmenter 盲区）**：给某段加 `pin_start` 重跑后，段落起点被钉住，但**前一段的 end 仍按吸附结果**（可能 > 新 start）产生重叠。本次 pin 4 段后出现 3 处相邻重叠，需再给相邻段的对应端点加 pin 解决。规则：每次 pin 后检查「前段 end 是否 > 本段 pin_start」，是则同步 pin 前段 end（或本段 start 前移）。建议 segmenter 未来支持 pin 时联动前段。
- **AI 自生成备注也要同音一致性 grep（新）**：本次某场 #09 备注写"<人名写法A>"、#10 写"<同音写法B>"，是 Agent 自己生成时混写同音字，并非 ASR 原文。纪律 #8/#22 只覆盖"ASR 错词→正确名"，未覆盖"AI 生成文本自身前后不一致"。补：定稿后除 grep 已知错词，还要 grep 自己写的人名/品牌是否前后一致（如上例的两种写法、或"正确名/同音错字"混用）。
- **ffmpeg 末段 seek volumedetect n_samples=0（新，假阴性）**：验证切片尾静音时，`ffmpeg -ss (dur-1) -t 1 -af volumedetect` 在文件末尾常返回 `n_samples: 0`（无采样），被误判异常。实为 ffmpeg 末段 `-ss`+`-t` 采样坑，非缺陷。正确做法：尾静音验证改用 `-ss (dur-2)` 或全程 `silencedetect` 扫末段，不要依赖末段 1s volumedetect 的 mean_volume。
- **环境探测直接走 funasr-clip venv（重申）**：初次验证 funasr 不要先试 managed 3.13（无 funasr），直接按本文件 #0 的 `envs/funasr-clip` 路径跑。本次先试 3.13 失败、再误报 exit1、再诊断，浪费 1 轮。
- **输出目录内容日期（重演 #3）**：本次文件名 `07-10直播回放` 无 YYYYMMDD 8 位 → 目录回退 mtime `2026-08-13`。交付时手动改名 `2026-07-10_...`。建议 cut_pipeline 支持从 segments.json 读 `record_date` 字段决定目录名，或在命名视频时带 YYYYMMDD。

---

## 8. 单场专有名词订正集（数据在本机私有文件）

每场直播都有自己的人名/品牌词表，**按主播/场次累积维护**：

- 本地安装: 追加到 `references/brand-words.private.md` 的 B 节(**不随包分发**)。
- 公开版: 该文件不存在, 首次切某个主播时向用户索取, 自行新建。

维护要点:

- 先在聊天里列候选清单、用户订正后才落盘; 错误 token **不要**写进标题/备注
  (否则纪律 #22 的 grep 兜底会命中自己写的错词)。
- AI 自生成文本也要做同音一致性 grep: 定稿后 grep 自己写的人名/品牌是否前后混写
  (例如同一人在不同片段写了两种同音写法)。

**本场的技术坑(与词表无关, 通用)**: 若 ASR 相邻句时间戳**重叠严重**, 直接按话题边界分段会被
segmenter 吸附越界 → 必须用 `build_segments.py` 的**链式对齐(后段起点=前段终点) + 全段 pin**
才得 0 重叠 / 0 HIGH_RISK; 且 >600s 超长段会被强制拆出退化短段, 需**预拆分**到 <580s。
