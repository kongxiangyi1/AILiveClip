# ai-live-clip · 直播切片工具

把 1–5 小时的直播回放 / 录播长视频，按**内容话题**自动粗切成可独立传播的短视频片段，供人工精剪。

你只需要给一句指令和视频路径，其余全部自动完成：

```
把这段直播切片 E:\录播\07-10直播回放.ts
```

- **语义判断交给 AI**：话题分段、合并、过渡句归属、起标题、专有名词校对、切点复核
- **精确对齐交给脚本**：静音吸附、置信分级、时长兜底、FFmpeg 切割
- **全程本地执行**：视频不出本机，不上传任何服务器

## 费用

一次性买断 **¥19.90**，永久有效，不限视频数量与调用次数。

付费在对话内由支付宝 AI 付（A2M/402 协议）完成：首次运行付费闸口会出账单，付款后服务端签发一枚授权码
落到本机 `~/.ai-live-clip/license.json`；此后每次调用只做本地验签直通，**不再联网、不再产生账单**。
平台详情页展示为「19.90 元/次」是 SkillHub 的表单口径（平台没有"买断"选项），实际计费口径以上述为准。

> 闸口先于一切处理：未拿到授权不会开始转写。已激活的机器换视频、换目录都不用再付。
> 服务端不可用时可走离线授权码激活（向作者索取），见 `SKILL.md`《本地授权(离线兜底通道)》。

## 前置环境

| 档位 | 依赖 | 说明 |
|---|---|---|
| 必装 | Python 3.10+ | — |
| 必装 | FunASR | paraformer-zh + fsmn-vad + ct-punc，CPU 推理，首次运行下载模型约 1–2 GB |
| 必装 | imageio-ffmpeg | 提供完整版 FFmpeg（系统自带精简版常缺 AAC 编码器） |
| 必装 | cryptography | 授权码本地验签，缺了闸口会直接报错（不会静默放行） |
| 可选 | streamlit | 仅审核界面 `app.py` 需要 |

```powershell
python -m pip install funasr imageio-ffmpeg cryptography
python -m pip install streamlit        # 要用审核界面时再装
```

装完先跑一次自检，它会逐项告诉你缺什么、怎么修（含 FFmpeg 是否为完整版、模型是否已缓存、
授权状态、目录可写与磁盘余量）：

```powershell
python "<SCRIPT_DIR>/doctor.py"        # 退出码 0 才继续
```

`<SCRIPT_DIR>` 是本技能 `scripts/` 目录的绝对路径，取法见 `SKILL.md`《前置环境》。
所有脚本都在**目标工作区根目录**执行，`temp/` 与 `output/` 落在工作区。

脚本通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动定位 FFmpeg，无需手动配置。

> 仅在 Windows 上完整实测；其他平台需自行验证 FunASR 与 FFmpeg 可用性。

## 五阶段流水线

```
阶段0 [脚本]     环境自检 + 付费闸口            → doctor.py / payment_gate.py probe
阶段1 [脚本]     MP4/TS → 抽音 → FunASR 转写   → temp/transcript.json
阶段2 [AI]       通读转写 → 话题分段/合并/标题  → temp/segments.json
阶段3 [脚本]     静音吸附 + 置信分级 + 时长兜底 → temp/clips_refined.json
阶段4 [脚本]     FFmpeg 切割                    → output/{日期_原视频名}/
阶段5 [脚本+AI]  发布合规检查 + 三项人工建议    → temp/compliance_report.md / .json
```

AI 只需给出**语义边界近似值**，精确吸附由脚本完成。切点按风险分级（`safe` / `low_risk` / `high_risk`），AI 只对 `high_risk` 复核，避免全量人工介入。

阶段 5 是交付的一部分：**没有合规报告视为未交付**（纪律 #28）。它逐片给 RED/YELLOW/GREEN 等级、
处置动作（删除/剪掉/静音/改标题/取授权/可发）和待处理区间时间码。

## 可选后处理

- **Streamlit 审核界面**：`streamlit run "<SCRIPT_DIR>/app.py"`，图形化调边界后导出再走阶段 3
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
- **文件名敏感字符**：切片标题里的 `%`、`&`、引号在部分上传平台会报错，包内暂无自动清洗脚本 —— 规避办法是在阶段2 改 `segments.json` 的 title 后再切
- **不适用**：成片级精剪（转场/字幕/配乐）、纯 BGM 无语音素材、跨视频混剪

## 文档

- `SKILL.md` — AI 工作流指令（触发条件、前置环境、五阶段执行流程、付费闸口）
- `references/stages.md` — 各阶段详细规则与数据格式
- `references/discipline.md` — 切片执行纪律 28 条与踩坑清单
- `references/compliance-rules.md` — 阶段5 合规检查口径与词表维护
- `references/funasr-install.md` — FunASR 安装与常见问题
- `references/lessons-learned.md` — 运维与工具坑（含作者本机经验，非通用要求）
- `references/clip-registry.md` — 多账号分发的去重协调规则
- `references/brand-words.template.md` — 专有名词订正集模板（见下）
- `references/billing-integrator.md` — 仅开发者：计费服务端部署、402 协议五步、错误码表、密钥与环境变量
- `references/publishing.md` — 仅开发者：SkillHub 发布/更新的 CLI 流程与常见错误码
- `references/payment-buyer.md` — 买家向的付费与授权：买断口径、首次付费流程、换机/重装、退出码与环境变量
- `references/faq.md` — 按症状检索的常见问题（安装、付费、转写、切割、合规）
- `references/walkthrough.md` — 端到端走查：每个阶段的产物样例与参考耗时，第一次用照它走一遍

## 隐私

本技能不内置任何品牌名、人名或主播词表。专有名词校对所需的对照表由使用者在首次使用时按
`references/brand-words.template.md` 自行建立（建议存为 `references/brand-words.md`，不要对外分发）。
包内也不含任何私钥；授权码验签只用随包分发的公钥。
