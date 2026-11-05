---
name: ai-live-clip
slug: ai-live-clip
displayName: 直播切片工具
version: 1.4.0
summary: '【买断 ¥19.90, 永久有效, 不限视频数量】将直播回放/录播长视频(1-5小时, MP4/TS)按话题内容自动粗切为可独立传播的短视频片段。当用户提供直播回放视频并要求按内容/话题切分, 或提到"直播切片""直播回放剪辑""带货视频粗剪""直播内容分割"时使用。五阶段流水线: FunASR 转写 → AI 语义分段 → 静音吸附精修 → FFmpeg 切割(支持关键帧感知流复制) → 发布合规检查(逐片风险分级+处置建议+报告)。依赖 Python 3.10+ / FunASR / imageio-ffmpeg / cryptography, 仅 Windows 实测; 首次使用先跑 scripts/doctor.py 自检。执行流水线前必须先通过付费闸口(scripts/payment_gate.py, 402 协议 / 本地 RSA 授权验签), 未取得授权不得开始转写。'
license: MIT
description: '买断制付费技能(¥19.90 一次性买断, 非订阅、非按次; 授权永久有效, 不限视频数量与调用次数)。把 1-5 小时的直播回放/录播长视频(MP4/TS)按话题内容自动粗切为可独立传播的短视频片段: FunASR 本地转写 → AI 语义话题分段 → 静音吸附精修 → FFmpeg 关键帧感知切割 → 发布合规检查并输出风险报告与处置建议。全流程在用户本机运行, 视频与字幕不上传任何服务器。'
tags:
  - 视频剪辑
  - 直播切片
  - 短视频
  - FunASR
  - 本地处理
---

# 直播切片工具 — 直播回放内容切片

## 角色

你是一个直播回放切片助手。你的任务是将直播回放视频(1-5小时)按**独立内容单元**(产品讲解、观点输出、粉丝问答、热门话题)粗切为可独立传播的短视频片段,供人工精剪。

## 触发条件

当用户出现以下任一意图时激活本指令:

- 提供直播回放/录播视频文件(MP4/TS),要求按内容或话题切分
- 提到下列任一说法: 「直播切片」「直播回放剪辑」「带货视频粗剪」「直播内容分割」
  「把直播按话题拆开」「直播回放拆条」「录播剪成一条条短视频」「长直播转短视频」
  「clip this livestream replay」「split the livestream recording by topic」
- 给一个 1-5 小时的长视频,希望得到"能独立发布的多条短视频"这一结果

**不适用(不要激活)**: 需要成片级精剪(转场/字幕/配乐/包装)、纯 BGM 无语音的素材、
只要单点截取一两段(手动剪更快)、以及需要跨视频混剪的场景——这些本流水线不产出。

## 用户固定偏好(2026-08-13 确认)

- **切分规则固定为"按话题内容切分"**(ASR 转写 → 语义话题分段 → 静音吸附 → 切割),不再按场景/静音/固定时长切分。
- 过渡句(一句话跨两个话题)归入后一段。
- 语义边界给出后,若 segmenter 吸附结果把过渡句归入前段,需人工修正切点,不直接接受脚本结果。

> **纪律三档速查表**(2026-08-15 整理): 28 条全文见 `references/discipline.md`, 编号沿用该文, 用前先对档, 避免逐条全读。
>
> **A. 强制档(每次任务必做, 顺序推进, 不可省略)**
> - #8+#22 专有名词校对(标题落盘前对照修正表+grep兜底) / #16 一轮确认制
> - #10 转写方案(单视频整段 asr_pipeline) / #4 TS+HEVC 源直接 reencode
> - #5 segmenter 后检查过渡句归属用 pin / #18 段起点语义决定+静音不进片
> - #3 low 段按 review_priority 分级复核
> - #20 阶段4 交付验收 checklist(含 #19 静音验证强制项) / #17 品牌名修正贯穿全部产物
> - #28 阶段5 合规检查必跑, 交付必须带报告 + 改标题/取授权/核功效三项建议
>
> **B. 写脚本档(仅写新脚本时触发, 不增加主流程成本)**
> - #2 多行 Python 写脚本文件 + UTF-8 文件输出 / #7 chcp 65001 中文路径
> - #1 JSON 修改后 json.load 校验 / #9 清理输出目录以 cut_results.json 为准
> - #11 先读数据再写验证代码 / #12 首次执行前核对 CLI / #21 分析脚本写作纪律
> - #23 写脚本前 Read 目标 JSON 确认字段类型 / #27 两段式试跑防大脚本崩溃
> - #24 静音扫描与语音复核合并一步 / #25 修正专有名词后用 UTF-8 列真实目录名核对
>
> **C. 汇报/知识档(零成本习惯, 不打断流程)**
> - #6 进新任务先读项目 SKILL.md / #13 Non-monotonic DTS 警告可忽略
> - #15 耗时估算规则 / #26 HMMSS 时间码标注格式

## 架构: AI 与脚本分工(五阶段流水线)

语义任务(LLM 话题分段、标题生成)由你在对话中直接完成,不走外部 API。脚本只负责 ASR 转写、切点精修和视频切割。

```
阶段1 [脚本]  MP4/TS → 抽音 → FunASR转写 → transcript.json
              (asr_pipeline.py 整段转写; transcribe_chunked.py 仅批量/断点续跑场景, 见纪律10)
阶段2 [AI]    读transcript → 话题分段/合并/过渡句/标题 → segments.json(只给语义边界,不强求精确对静音)
阶段3 [脚本]  segmenter.py 吸附静音+置信标注+时长兜底 → clips_refined.json
阶段4 [脚本]  FFmpeg切割(reencode精确 或 stream_copy快速) → 输出MP4(纯切片,无封面)
阶段5 [脚本+AI] compliance_scan.py 逐片风险分级(RED/YELLOW/GREEN)+处置动作+待处理区间
                → compliance_report.md/.json; AI 补标题改写/授权/功效三项建议 → 随交付一起给用户
```

**分工边界**: 你(AI助手)负责阶段2的全部语义判断——话题边界给近似值即可,精确吸附工作交给脚本;阶段3/4由 Python 脚本执行,你负责调度和检查结果。

> 吸附机制只修「切点卫生」,不修「语义错误」:AI 给错边界(±15s 内无 ≥1.5s 静音)会被脚本拒绝并标 confidence=low,需人工复核;给对边界则精确吸附到静音前 300ms。
>
> 吸附安全校验(boundary_unsafe, 2026-08-08 五轮真实数据验证固化):
> - 切点落入 [start,end] 且句子时长 ≤8s → 拒绝吸附,保持 AI 原值,标 confidence=low
> - 切点落入句内但邻近 ≥1.5s 长静音(<2.5s) → 可接受,标 high
> - 其余 → 高置信

> 代码通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动使用完整版 FFmpeg, 无需手动配置; 系统 PATH 中的 ffmpeg 可能是精简编译版(无 AAC 解码器), 切勿依赖。

## 前置环境

**一条命令查清环境**（首次使用必跑，之后换机器/换环境再跑）：

```powershell
python "<SCRIPT_DIR>/doctor.py"          # 人读; 加 --json 给 Agent 解析
```

它检查 Python 版本、三个必装依赖、FFmpeg 是否为完整版（实测 AAC 编码而非只看版本号）、
授权状态、模型缓存、工作目录可写与磁盘余量，并对每个缺失项直接打印修复命令。
退出码 `0` 才继续，`1` 先按 `[FAIL]` 行修复。

依赖分三档（`doctor.py` 的判定口径与此一致）：

| 档位 | 包 | 用途 | 缺失后果 |
|---|---|---|---|
| **必装** | `funasr` | 阶段1 转写 | 阶段1 无法运行 |
| **必装** | `imageio-ffmpeg` | 提供完整版 FFmpeg | 阶段4 无法运行 |
| **必装** | `cryptography` | 授权码 RSA 验签 | `probe`/`activate` 报错退出，⛔ 不会静默放行 |
| 可选 | `streamlit` | 仅审核界面 `app.py` | 不影响流水线 |
| 自动带入 | `torch` 等 | `funasr` 的依赖 | 无需单独安装，体积大、首次下模型 1-2 GB |

```powershell
python -m pip install funasr imageio-ffmpeg cryptography      # 必装三件
python -m pip install streamlit                               # 只在要用审核界面时装
```

> 下载慢/超时？见 `references/funasr-install.md` 的镜像速度排序，推荐腾讯云镜像
> （`-i https://mirrors.cloud.tencent.com/pypi/simple`）。
> 作者本机环境经验（**非通用要求，可跳过**）：禁用 `uv` 安装、用 `python -m ensurepip` 引导 pip、
> 不加 `--only-binary :all:`。原因与细节见 `references/funasr-install.md` 第七节、`references/lessons-learned.md`。

**重要**: 系统 PATH 中的 ffmpeg 可能是精简编译版(无 AAC 编码器、无 WAV muxer),必须使用 imageio-ffmpeg 自带的完整版。代码中已通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动获取。

### `SCRIPT_DIR` 是什么，怎么拿到

下文所有命令里的 `<SCRIPT_DIR>` 都指**本技能 `scripts/` 目录的绝对路径**，不是工作目录。

- **Agent 执行时**：你加载本技能时就知道 `SKILL.md` 的绝对路径，`SCRIPT_DIR` = 它同级的 `scripts/`。
  直接拼出绝对路径再执行，⛔ 不要递归扫描整个用户目录去找脚本（慢且会卡住任务）。
- **人工执行时**：PowerShell 里先设一次变量，后续命令引用它：

```powershell
$env:SD = "C:\Users\<你>\.workbuddy\skills\ai-live-clip\scripts"     # 按你的实际安装路径
python "$env:SD/doctor.py"
```

> ⛔ 不要写成裸 `python asr_pipeline.py` —— 脚本不在工作区，那样必然 `No such file`。<!-- audit-ignore -->
> **执行位置**则在**目标工作区根目录**：`temp/`、`output/`、`.pay_cache/` 都落在那里
> （可用环境变量 `AI_LIVE_CLIP_WORKDIR` 指定工作区根目录）。
> 阶段2 的语义分段/标题/专有名词校对由 AI 在对话中完成, 不在此处执行。

## 完整执行流程

当用户说"把这段直播切片 /path/to/video.ts"时,你按以下步骤执行(命令均在工作区根目录下运行)。
第一次接手本技能时，先照 `references/walkthrough.md` 走一遍——它给出每步产物的样例，便于确认没跑偏。

**每阶段产物检查点** —— 跑完一步就核对"该存在的文件是否存在、内容是否合理"，
不要攒到最后一起查（返工和"找不到文件"大多来自这里）：

| 阶段 | 跑完应存在 | 一眼核对 |
|---|---|---|
| 0 闸口 | `~/.ai-live-clip/license.json` | `payment_gate.py license` 报「已买断」 |
| 1 转写 | `temp/transcript.json` | `python -m json.tool temp/transcript.json` 通过, 句数 > 0 |
| 2 分段 | `temp/segments.json` | 段数在合理区间(1-5h 常见 15-40 段), 首尾覆盖全片时长 |
| 3 精修 | `temp/clips_refined.json` | 看 `review_priority` 分布; 全 0 且段数很多时反而可疑 |
| 4 切割 | `output/{日期}_{视频名}/` + `cut_results.json` | mp4 数 == 段数, 每条非零体积, 文件名里品牌名已订正 |
| 5 合规 | `temp/compliance_report.md` + `.json` | RED+YELLOW+GREEN 计数之和 == 片数, 否则有片被漏扫 |

```
0. 【环境自检 + 付费闸口 · 必做, 先于一切处理】
   python "<SCRIPT_DIR>/doctor.py"     # 退出码 0 才继续; 有 [FAIL] 先按提示修
   买断制 ¥19.90, 走支付宝 AI 付(A2M/402)。有授权 = 本地验签直通(零费用),
   无授权 = 触发一次付费。机制见下文《付费与授权(机制摘要)》。

   python "<SCRIPT_DIR>/payment_gate.py" probe                 # 已激活 -> 放行(0); 未激活 -> 402 出账
   python "<SCRIPT_DIR>/payment_gate.py" pay                   # 账单交 alipay-bot, 对话内付款, 等用户付
   python "<SCRIPT_DIR>/payment_gate.py" complete              # 付款后带凭证取回授权码并落盘
   python "<SCRIPT_DIR>/payment_gate.py" complete --proof <凭证>  # 官方 CLI 未回传凭证时, 手动指定
   python "<SCRIPT_DIR>/payment_gate.py" activate <授权码>      # 离线/人工通道: 用作者发的码激活

   → 退出码 `0` 才进入步骤 1; 其余一律**停止**, 不开始转写
   ⛔ 不得自行伪造授权码或凭证、不得把私钥放进包里、不得跳过闸口先跑流水线
   ⛔ 已激活时**不得**再发起付费请求(否则用户会被重复计费), probe 必须先查本地授权

   **退出码判据**: `0` 放行 / `402` 待支付 / `2` 授权码无效 / `3` 闸口不可用
   / `4` 授权验签失败或无法落盘 / `5` 已付款但取不到凭证
   ⚠ shell 中看到的退出码是 `code & 0xFF`, 所以 `402` 显示为 **146**; 判据以文档值(402)为准

1. 运行阶段1(二选一):
   python "<SCRIPT_DIR>/asr_pipeline.py" /path/to/video.ts        # 单视频首选: 整段转写(分块并行无收益, 见纪律10)
   python "<SCRIPT_DIR>/transcribe_chunked.py" A.ts B.ts C.ts [--workers N]     # 批量: 模型只加载一次(4次/4进程), 抽音并行
   → 生成 temp/transcript.json(单视频); 批量时各自 temp/{视频名}.transcript.json
   → 并行: 默认 min(4, cpu核数/4, 内存GB/8) 进程 × 每进程 cpu/进程 线程; 模型加载与切块并行(worker预加载)
   → 5小时视频约 12-25 分钟(单视频); 批量 N 视频时第2个起省全部模型加载时间

2. 读取 transcript.json
   → 你在对话中完成: 话题分段 → 合并 → 生成标题(边界给出近似值即可)
   → 生成 temp/segments.json

3. 运行阶段3 切点精修(脚本):
   python "<SCRIPT_DIR>/segmenter.py"
   → 吸附切点到最近 ≥1.5s 静音前 300ms, 标注置信度, 时长兜底
   → 输出 temp/clips_refined.json
   → 若产生 confidence=low 的切点, 抽读 clips_refined.json 复核原因(reason), 必要时回到步骤2修正语义边界

4. (可选) 启动Streamlit审核:
   streamlit run "<SCRIPT_DIR>/app.py"
   → 调整切点后导出 segments_approved.json

5. 运行阶段4:
   python "<SCRIPT_DIR>/cut_pipeline.py" temp/clips_refined.json --mode stream_copy       # 推荐: IDR感知流复制(快~190倍, 首帧必为I帧无花屏)
   python "<SCRIPT_DIR>/cut_pipeline.py" temp/clips_refined.json --mode stream_copy --max-snap-s 3  # 吸附误差超3s自动降级reencode(默认3)
   python "<SCRIPT_DIR>/cut_pipeline.py" temp/clips_refined.json                          # 兜底: 重编码精确切点, 2路并行
   python "<SCRIPT_DIR>/cut_pipeline.py" temp/clips_refined.json --workers 4              # 16核机器可上 4 路并行(约2-3x加速)
   → 纯视频切片, 不生成封面; 输出目录下生成 cut_results.json(含每片 start_offset_s = 吸附IDR-期望切点)

6. (可选) 后处理:
   python "<SCRIPT_DIR>/cut_pipeline.py" temp/clips_refined.json --mode stream_copy --account 号A   # 切割时按切片号登记进全局注册表
   python "<SCRIPT_DIR>/clip_status.py" --conflicts        # 多切片号场景: 查高相似限流风险(发布前必查)
   python "<SCRIPT_DIR>/mute_profanity.py" --dry-run       # 粗口静音: 先列区间核对
   python "<SCRIPT_DIR>/mute_profanity.py"                 # 粗口静音(视频流不变, 仅音频区间归零)

7. 运行阶段5 发布合规检查(必做, 见纪律28):
   python "<SCRIPT_DIR>/compliance_scan.py"                    # 逐片扫描 -> temp/compliance_report.md + .json
   python "<SCRIPT_DIR>/compliance_scan.py" --limit 3          # 两段式试跑(纪律27), 确认字段无误再全量
   python "<SCRIPT_DIR>/compliance_scan.py" --rules my.json    # 自定义词表(默认 scripts/compliance_rules.json)
   → 输出每片 等级(RED/YELLOW/GREEN) + 处置动作(DROP/CUT/MUTE/RETITLE/CONSENT/OK) + 待处理区间(片内/全片时间码)
   → AI 必须接着补三项脚本判不了的建议: ① 命中风险词的标题给改后版本 ② 涉他人肖像/隐私的片提示取授权 ③ 功效宣称复核

8. 输出结果文件列表 + **合规报告摘要与建议** 给用户(缺报告视为未交付, 见纪律28):
   - 结论速览: RED / YELLOW / GREEN 各多少片
   - RED 逐片: 命中类别 + 处置动作 + 待处理区间时间码
   - YELLOW 按类别汇总(粗口→静音 / 极限词→改标题 / 行业指控→改标题 / 隐私→取授权)
   - GREEN 清单: 可直接发布
   - AI 建议: 改后标题清单、需授权片号、功效宣称待核片号
   → 交付时列出两份报告的**绝对路径**(temp/compliance_report.md 与 .json);
     若当前 Agent 环境提供文件展示工具(如 present_files), 一并调用把两份报告带上
```

## 本地授权(离线兜底通道)

沙箱服务端不可用、或用户已通过人工渠道付款时使用: 作者用本地私钥签发授权码,
用户在本机激活; 之后每次 `probe` 只做**本地验签**, 全程不发网络请求、不上传任何数据。

### 三段流程

| 阶段 | 谁 | 做什么 | 用的工具 |
|---|---|---|---|
| 收款 | 用户 → 支付宝 | 一次性付 ¥19.90 | 支付宝(收款码/订单, 人工对账) |
| 签发 | **作者本机** | 用本地私钥签出 `LIVECLIP1.xxx.yyy` 发给用户 | `issue_license.py` —— ⛔ **作者私有工具, 不随包分发**(私钥不能进包) |
| 激活 | 用户本机 | 写入 `~/.ai-live-clip/license.json` | `python "<SCRIPT_DIR>/payment_gate.py" activate <授权码>` |

> 买家侧只需要 `activate` 这一步。签发命令由作者在**自己的机器**上执行，形如
> `issue_license.py --out-trade-no <支付宝订单号> [--machine <机器码>] [--days N]`，
> 首次使用前需 `--gen-keys --write-config` 生成密钥对（私钥落作者本机
> `~/.ai-live-clip/keys/private_key.pem`，公钥写进包内 `config.json`）。**这两个动作都不在本技能包里，也不可能在。**
>
> 本通道的对账靠人工：作者收到订单号才签发，⛔ 不要在没有付款记录时发码。
> `config.json` 的 `alipay.pay_url` 在本模式下留空即可——平台不代收，技能侧不挂任何收款链接。

### 授权码

```
格式   LIVECLIP1.<payload_base64url>.<签名_base64url>
载荷   {"oid": 订单号, "amt": "19.90", "mid": 机器码或"*", "iat": 签发时间, "exp": ""或到期, "mode": "perpetual"}
签名   RSA-SHA256(PKCS1v15) 公钥随包 / 私钥仅在作者本机
可选   --machine <机器码> 绑定单机;  --days N 限时授权
```

- 验签在 `scripts/licensing.py`, 含**机器码匹配、过期判断、金额一致性、base64 规范化**四道校验
- 无 `cryptography` 时无法验签 → 明确报错并提示 `pip install cryptography`, ⛔ 不得放行
- 240 次随机单字符篡改压测: 原码 100% 通过, 篡改 100% 拒绝

### 红线

- ⛔ 私钥(`~/.ai-live-clip/keys/private_key.pem`)**永不进技能包 / 仓库 / 日志 / 对话**
- ⛔ 不得自行伪造或推导授权码放行; 不得因"没网/嫌麻烦"跳过闸口
- ⛔ 不得把 `config.json` 的 `price_cny` 与支付宝实收金额改得不一致(三处价格必须一致: 收款金额 = config.price_cny = 展示价 = **19.90**)
- ★ 一台机器激活一次即可, 换机/重装需重新激活(通用码 `mid="*"` 可直接在新机激活)

## 付费与授权（机制摘要）

钱和授权的关系，三句话讲清：

1. 本技能是**一次性买断 ¥19.90**。402 协议本身只有按次计费，买断是这样落地的：
   验付通过后**服务端签发一枚 RSA 授权码**，客户端落盘；这不是订阅、也不是按次。
2. 授权码落在 `~/.ai-live-clip/license.json`。此后每次 `probe` 只做**本地验签**，
   不联网、不出账 —— **换视频、换目录、换调用次数都不再产生账单**，服务端挂了也不影响已买断的用户。
3. SkillHub 的发布表单**没有"买断"选项**，详情页统一显示「19.90 元/次」并附平台免责声明
   （平台不代收，钱直接进开发者账户）。所以描述里务必写明「买断制 · 付一次永久有效」，
   否则用户看到"按次收费"会产生预期差。

**主通道（402 沙箱服务端）**的命令与退出码判据已在上面《完整执行流程》步骤 0；
**兜底通道（作者人工发码）**见上一节《本地授权(离线兜底通道)》。两条通道拿到的是同一种授权码，
验签逻辑完全相同（`scripts/licensing.py`）。

> 买家侧的**操作**细节（付款步骤、退出码、换机/重装、排障环境变量）都在 `references/payment-buyer.md`；
> 用户报"付过了 yet 又要付费""换机后授权失效"这类问题时先查 `references/faq.md`，不要凭记忆答。

买家侧另有一条必须守的：⛔ **已激活时不得再发起付费请求**（否则用户被重复计费），
`probe` 必须先查本地授权、命中即返回。其余红线见上一节。

首次付款需要买家机器上的官方支付能力；找不到 `alipay-bot` 时先装：
`npx -y @alipay/agent-payment@latest install`。

> 服务端怎么部署、五步协议（precreate → 402 → 验付 → 业务校验 → 履约确认）、错误码表、
> 服务端开关（`strict_verify` / `allow_unconfirmed` / `bind_machine` 等）、两套私钥的区别、
> 客户端环境变量与接入进度 —— 全部在 `references/billing-integrator.md`（**仅开发者需要，日常使用不必读**）。

## 发布与更新（仅开发者）

日常使用不涉及。要点只有三条：需先完成实名认证并创建 API Token；`skillhub publish <目录> --dry-run`
先预检再正式发布；更新时保持 `slug` 不变、只改 frontmatter 的 `version`（版本号唯一来源）。
完整流程、frontmatter 硬性要求与常见错误码（401/403/409/429）见 `references/publishing.md`。

## 引用文档(按需加载)

- `references/discipline.md` — 切片执行纪律 28 条全文 + low 段分级(review_priority)判定表
- `references/data-formats.md` — 三个阶段 JSON 数据交换格式(transcript / segments / clips_refined)
- `references/stages.md` — 阶段1/2/4 实现细节(句级时间戳重建、步骤 A-E、切片命名规则、模块说明、注意事项)
- `references/stream_copy-optimization.md` — stream_copy 快速切割方案与验证记录(阶段4 选模式/调参时读, 平时不必读)
- `references/funasr-install.md` — FunASR 安装与 pip 镜像下载速度排序(首次使用必读, 下载慢时切腾讯云镜像)
- `references/lessons-learned.md` — **本机运维/工具坑**(uv 沙箱禁装、ffprobe 路径、路径对齐、验收脚本陷阱、segmenter 短尾、目录名回退), 每次任务前扫一眼避免空转
- `references/clip-registry.md` — **多切片号去重**: 全局注册表(clip_registry / clip_status / update_registry)的跨号协调规则与发布流程, 多账号分发场景必读
- `references/compliance-rules.md` — **阶段5 发布合规检查**: 等级/动作口径、词表维护方法、已知误报模式、AI 必补的三项建议(每次交付前对照)
- `references/billing-integrator.md` — **仅开发者**: 服务端部署/402 五步协议/错误码表/服务端开关/密钥与环境变量/接入进度(改收款链路才读)
- `references/publishing.md` — **仅开发者**: SkillHub 发布与更新的 CLI 流程、frontmatter 要求、常见错误码
- `references/payment-buyer.md` — **付费与授权(买家向)**: 买断口径、首次付费与后续直通、换机/重装、退出码与环境变量(付款相关问题先读这页, 不必读服务端文档)
- `references/faq.md` — **按症状检索的常见问题**(装不上/付款异常/切错位置/只有 mp4 没封面/合规误报), 报错时先来这里
- `references/walkthrough.md` — **端到端走查**: 一句指令 → 五阶段每步产物长什么样 + 参考耗时(第一次用建议照它走一遍)
- `references/brand-words.template.md` — **专有名词订正集模板**(真实品牌/人名, 阶段2 落标题前对照。表内容是使用者的客户信息, **不随包分发**; 首次使用时向用户索取正名清单, 按模板自建 `references/brand-words.md`)

## 技能结构

```
直播切片工具/
├── SKILL.md                   # 本文件: 触发/架构/速查表/执行流程
├── config.json                # 买断价 + 沙箱 service_url + 授权公钥(随包分发, ⛔ 不含任何私钥)
├── scripts/                  # 五阶段执行脚本(在目标工作区运行)
│   ├── payment_gate.py       # 步骤0 付费闸口: probe/pay/complete(402 沙箱) + activate/machine-code(离线兜底)
│   ├── licensing.py          # 授权码核心: 机器码 / 签发 / 验签(RSA + HMAC 降级), 与服务端同格式
│   ├── doctor.py             # 环境自检: 依赖/FFmpeg完整版/模型缓存/授权状态/可写目录(首次使用先跑)
│   ├── asr_pipeline.py       # 阶段1: 抽音 + FunASR 整段转写 -> transcript.json
│   ├── transcribe_chunked.py # 阶段1备选: 300s 分块 + 断点续跑(批量/超长场景)
│   ├── segmenter.py          # 阶段3: 静音吸附 + 置信分级 + 时长兜底 -> clips_refined.json
│   ├── cut_pipeline.py       # 阶段4: 读取 clips -> FFmpeg 切割
│   ├── app.py                # Streamlit 审核界面(可选)
│   ├── config.py             # 模型/FFmpeg/编码器探测/时长阈值配置
│   ├── review_checklist.py   # 专有名词候选清单生成(阶段2 前跑, 供 AI 校对用)
│   ├── fix_brand_name.py     # 品牌名修正后统一重命名产物
│   ├── clip_registry.py      # 全局切片注册表(跨视频/跨切片号去重) — 详见 references/clip-registry.md
│   ├── clip_status.py        # 注册表查询 / 跨号冲突预警 / 发布状态标记 / 导出发布表
│   ├── update_registry.py    # 历史切片补登进注册表
│   ├── mute_profanity.py     # 阶段4 之后可选: 切片内粗口区间静音(视频流不变)
│   ├── compliance_scan.py    # 阶段5: 发布合规检查 -> compliance_report.md/.json
│   ├── compliance_rules.json # 阶段5 词表(直接编辑即可, 无需改 py)
│   ├── errors.py             # 入口异常包装: 「原因 + 下一步」, AI_CLIP_DEBUG=1 才打完整堆栈
│   └── tests/test_segmenter.py
└── references/               # 详细参考(按需读入)
```

> 上面就是**包内全部内容**。作者侧的 `issue_license.py`（签发授权码）与计费服务端源码
> （`AILiveClip` 仓库的 `payserver/`，**不随本包分发**）都不在这里，也不会在任何版本在这里——私钥不能随包分发。
> 买家侧要拿到授权，只有两条路：走 402 闸口付款后服务端自动签发，或向作者索取授权码后
> `payment_gate.py activate <授权码>`。
