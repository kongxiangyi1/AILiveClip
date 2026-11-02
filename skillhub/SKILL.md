---
name: ai-live-clip
slug: ai-live-clip
displayName: 直播切片工具
version: 1.3.1
summary: '【买断 ¥19.90, 永久有效, 不限视频数量】将直播回放/录播长视频(1-5小时, MP4/TS)按话题内容自动粗切为可独立传播的短视频片段。当用户提供直播回放视频并要求按内容/话题切分, 或提到"直播切片""直播回放剪辑""带货视频粗剪""直播内容分割"时使用。五阶段流水线: FunASR 转写 → AI 语义分段 → 静音吸附精修 → FFmpeg 切割(支持关键帧感知流复制) → 发布合规检查(逐片风险分级+处置建议+报告)。依赖 Python 3.10+ / FunASR / imageio-ffmpeg, 仅 Windows 实测。执行流水线前必须先通过付费闸口(scripts/payment_gate.py, 402 协议 / 本地 RSA 授权验签), 未取得授权不得开始转写。'
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

当用户出现以下意图时激活本指令:

- 用户提供直播回放视频文件(MP4/TS),要求按内容切分
- 用户提到"将直播内容切片"直播切片""直播回放剪辑""带货视频粗剪""直播内容分割"
- 用户需要将长直播按话题分割为独立短视频

## 用户固定偏好(2026-08-13 确认)

- **切分规则固定为"按话题内容切分"**(ASR 转写 → 语义话题分段 → 静音吸附 → 切割),不再按场景/静音/固定时长切分。
- 过渡句(一句话跨两个话题)归入后一段。
- 语义边界给出后,若 segmenter 吸附结果把过渡句归入前段,需人工修正切点,不直接接受脚本结果。

> **纪律三档速查表**(2026-08-15 整理): 27 条全文见 `references/discipline.md`, 编号沿用该文, 用前先对档, 避免逐条全读。
>
> **A. 强制档(每次任务必做, 顺序推进, 不可省略)**
> - #8+#22 专有名词校对(标题落盘前对照修正表+grep兜底) / #16 一轮确认制
> - #10 转写方案(单视频整段 asr_pipeline) / #4 TS+HEVC 源直接 reencode
> - #5 segmenter 后检查过渡句归属用 pin / #18 段起点语义决定+静音不进片
> - #3 low 段按 review_priority 分级复核
> - #20 阶段4 交付验收 checklist(含 #19 静音验证强制项) / #17 品牌名修正贯穿全部产物
> - #28(新) 阶段5 合规检查必跑, 交付必须带报告 + 改标题/取授权/核功效三项建议
>
> **B. 写脚本档(仅写新脚本时触发, 不增加主流程成本)**
> - #2 多行 Python 写脚本文件 + UTF-8 文件输出 / #7 chcp 65001 中文路径
> - #1 JSON 修改后 json.load 校验 / #9 清理输出目录以 cut_results.json 为准
> - #11 先读数据再写验证代码 / #12 首次执行前核对 CLI / #21 分析脚本写作纪律
> - #23(新) 写脚本前 Read 目标 JSON 确认字段类型 / #27(新) 两段式试跑防大脚本崩溃
> - #24(新) 静音扫描与语音复核合并一步 / #25(新) 修正专有名词后用 UTF-8 列真实目录名核对
>
> **C. 汇报/知识档(零成本习惯, 不打断流程)**
> - #6 进新任务先读项目 SKILL.md / #13 Non-monotonic DTS 警告可忽略
> - #15 耗时估算规则 / #26(新) HMMSS 时间码标注格式

## 架构:三段式半自动

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

```powershell
# FunASR (本地Python包)
pip show funasr

# imageio-ffmpeg (完整版FFmpeg,系统自带的是精简版缺AAC解码器)
pip show imageio-ffmpeg

# Streamlit (审核界面,可选)
python -c "import streamlit; print('ok')"

# cryptography (授权码验签, 两种模式都必装: 服务端签发的码也要靠它本地验签)
python -c "import cryptography; print('ok')"
```

如有缺失:
```
pip install funasr imageio-ffmpeg streamlit pillow numpy cryptography
```
> `cryptography` 缺失时 `probe` / `activate` 会明确报错并提示安装, ⛔ 不会静默放行。
> 作者签发授权码同样需要它(本机可用已装好的 funasr-clip 环境)。
> **本机禁用 `uv` 安装**（Windows 沙箱 safe-delete 会让 `jieba`/`oss2` 等 sdist 构建失败）：用 `python -m ensurepip` 引导 pip 后前台 `pip install`，且**不要** `--only-binary :all:`（`oss2` 只有 sdist）。详见 `references/funasr-install.md` 第七节与 `references/lessons-learned.md`。
> 下载慢/超时? 见 `references/funasr-install.md` 的镜像速度排序, 推荐用腾讯云镜像(`-i https://mirrors.cloud.tencent.com/pypi/simple`)。

**重要**: 系统 PATH 中的 ffmpeg 可能是精简编译版(无 AAC 解码器、无 WAV muxer),必须使用 imageio-ffmpeg 自带的完整版。代码中已通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动获取。

> 以下命令中的 `SCRIPT_DIR` 指代本技能的 `scripts/` 目录（安装路径取决于你的 Agent 环境，如 `~/.workbuddy/skills/直播切片工具/scripts`、`~/.trae-cn/skills/ai-live-clip/scripts` 或你解压 zip 的目录）。在**目标工作区根目录**执行脚本, `temp/` 与 `output/` 会落在工作区(可用环境变量 `AI_LIVE_CLIP_WORKDIR` 覆盖根目录)。
> 阶段2 的语义分段/标题/专有名词校对由 AI 在对话中完成, 不在此处执行。

## 完整执行流程

当用户说"把这段直播切片 /path/to/video.ts"时,你按以下步骤执行(命令均在工作区根目录下运行):

```
0. 【付费闸口 · 必做, 先于一切处理】本技能为**买断制 ¥19.90**, 走支付宝 AI 付(A2M/402),
   服务端部署在 **WorkBuddy 应用沙箱**(零服务器 / 零域名 / 平台分配 HTTPS 地址,
   详见下方《沙箱服务端(支付宝 AI 付 · A2M 402)》)

   ★ **买断如何落地**: 402 协议本身是**按次**计费; 买断由本技能实现 ——
     验付通过后服务端签发一枚 **RSA 授权码**, 客户端落盘到 `~/.ai-live-clip/license.json`;
     之后每次 `probe` 只做**本地验签**即直通放行, **根本不请求服务端**。
     所以: 有授权 = 买断直通(零费用), 无授权 = 触发一次付费。

   python scripts/payment_gate.py probe                 # 已激活 -> 放行(0); 未激活 -> 402 出账
   python scripts/payment_gate.py pay                   # 账单交 alipay-bot, 对话内付款, 等用户付
   python scripts/payment_gate.py complete              # 付款后带凭证取回授权码并落盘
   python scripts/payment_gate.py complete --proof <凭证>  # 官方 CLI 未回传凭证时, 手动指定
   python scripts/payment_gate.py activate <授权码>      # 离线/人工通道: 用作者发的码激活

   → 退出码 `0` 才进入步骤 1; 其余一律**停止**, 不开始转写
   ⛔ 不得自行伪造授权码或凭证、不得把私钥放进包里、不得跳过闸口先跑流水线
   ⛔ 已激活时**不得**再发起付费请求(否则用户会被重复计费), probe 必须先查本地授权

   **退出码判据**: `0` 放行 / `402` 待支付 / `2` 授权码无效 / `3` 闸口不可用
   / `4` 授权验签失败或无法落盘 / `5` 已付款但取不到凭证
   ⚠ shell 中看到的退出码是 `code & 0xFF`, 所以 `402` 显示为 **146**; 判据以文档值(402)为准

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

6. (可选) 后处理:
   python cut_pipeline.py temp/clips_refined.json --mode stream_copy --account 号A   # 切割时按切片号登记进全局注册表
   python clip_status.py --conflicts        # 多切片号场景: 查高相似限流风险(发布前必查)
   python mute_profanity.py --dry-run       # 粗口静音: 先列区间核对
   python mute_profanity.py                 # 粗口静音(视频流不变, 仅音频区间归零)

6. 运行阶段5 发布合规检查(必做, 见纪律28):
   python <SCRIPT_DIR>/compliance_scan.py                    # 逐片扫描 -> temp/compliance_report.md + .json
   python <SCRIPT_DIR>/compliance_scan.py --limit 3          # 两段式试跑(纪律27), 确认字段无误再全量
   python <SCRIPT_DIR>/compliance_scan.py --rules my.json    # 自定义词表(默认 scripts/compliance_rules.json)
   → 输出每片 等级(RED/YELLOW/GREEN) + 处置动作(DROP/CUT/MUTE/RETITLE/CONSENT/OK) + 待处理区间(片内/全片时间码)
   → AI 必须接着补三项脚本判不了的建议: ① 命中风险词的标题给改后版本 ② 涉他人肖像/隐私的片提示取授权 ③ 功效宣称复核

7. 输出结果文件列表 + **合规报告摘要与建议** 给用户(缺报告视为未交付, 见纪律28):
   - 结论速览: RED / YELLOW / GREEN 各多少片
   - RED 逐片: 命中类别 + 处置动作 + 待处理区间时间码
   - YELLOW 按类别汇总(粗口→静音 / 极限词→改标题 / 行业指控→改标题 / 隐私→取授权)
   - GREEN 清单: 可直接发布
   - AI 建议: 改后标题清单、需授权片号、功效宣称待核片号
   → 两份报告文件用 present_files 一并交付(temp/compliance_report.md 与 .json)
```

## 本地授权(离线兜底通道)

沙箱服务端不可用、或用户已通过人工渠道付款时使用: 作者用本地私钥签发授权码,
用户在本机激活; 之后每次 `probe` 只做**本地验签**, 全程不发网络请求、不上传任何数据。

### 三段流程

| 阶段 | 谁 | 做什么 |
|---|---|---|
| 收款 | 用户 → 支付宝 | 一次性付 ¥19.90(config.json 的 `alipay.pay_url` 为收款入口, 留空则由平台代收) |
| 签发 | 作者(本机) | `python scripts/issue_license.py --out-trade-no <支付宝订单号>` → 得到 `LIVECLIP1.xxx.yyy` |
| 激活 | 用户(本机) | `python scripts/payment_gate.py activate <授权码>` → 写入 `~/.ai-live-clip/license.json` |

首次签发前执行一次: `python scripts/issue_license.py --gen-keys --write-config`
(私钥落 `~/.ai-live-clip/keys/private_key.pem`, 公钥写进包内 `config.json`)

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

## 沙箱服务端(支付宝 AI 付 · A402)—— 本技能采用的收款方式

### 为什么必须有服务端

> ⚠️ **更正记录(2026-10-29)**: 早前曾依据技能详情页的营销描述判断「平台代收、无需服务端」,
> 该结论**已被官方手册推翻**。SkillHub 对每个付费技能都挂着免责声明:
> 「SkillHub 仅提供展示、投诉处理及其他平台服务, **不作为该笔交易的收款方**」——
> 平台不代收, 钱直接进开发者账户。官方上架手册的 Pay Skill vs 普通 Skill 对比表写明:
> **Pay Skill 必须有服务端**(出账 + 验付 + 履约确认)。

但"必须有服务端" ≠ 自己买服务器。官方提供**应用沙箱**方案: 零依赖 / 零服务器 / 零域名,
部署到 WorkBuddy 应用沙箱后由平台分配 `https://随机名.app.workbuddy.host`, 自带证书、免备案。

| 环节 | 谁做 |
|---|---|
| 预下单 `alipay.aipay.agent.payment.precreate` | **本沙箱服务端** |
| 402 账单下发(`amtPaymentLink` 预下单号 + `Payment-Needed` Base64URL 账单) | **本沙箱服务端** |
| 拉起支付宝收银台 / 用户付款 | 用户本人(Agent 只编排, 不代付) |
| 验付 `alipay.aipay.agent.payment.verify` | **本沙箱服务端** |
| 签发买断授权码(履约) | **本沙箱服务端** |
| 履约确认 `alipay.aipay.agent.fulfillment.confirm` | **本沙箱服务端**(确认成功才交付) |
| 之后每次 probe 验签放行 | **客户端本地**(不联网、不计费) |

### 买断的落地机制(关键)

官方体系只有按次计费, **没有"买断"这个概念**。买断是这样实现的:

```
第 1 次: probe(无授权) -> 服务端 402 出账 -> pay 付款 -> complete 取回授权码 -> 落盘
第 2 次起: probe 见本地授权 -> 本地 RSA 验签通过 -> 直接放行, 零网络请求、零费用
```

因此**有授权时绝不能再请求服务端**(否则会被重复计费)——客户端 `probe` 已把"本地验签"
放在最前面, 命中即返回。换机/重装后凭旧授权码可走服务端「恢复」分支拿回, 同样不重复扣费。

### 部署与配置(开发者侧, 一次性)

```bash
# 1) 建服务拿 serviceId(每个定价一个, 形如 API_xxx), 用支付宝 CLI, 不用网页填表
alipay-cli service save --name "直播切片工具买断" --price 19.90

# 2) 把 ailiveclip-pay-server/ 发布到 WorkBuddy 应用沙箱 -> 得到 HTTPS 地址

# 3) 注入密钥(私钥只在运行时入库, 绝不进包); 首次调用不需要 token
curl -X POST https://<沙箱地址>/admin/setup -H "Content-Type: application/json" -d '{
  "app_id": "<应用ID>", "seller_id": "<收款PID>",
  "private_key": "<支付宝应用私钥 PEM 全文>",
  "license_private_key": "<授权码签发私钥 PEM 全文>",
  "resources": [{"path":"/api/license","goods_name":"直播切片工具买断","price":"19.90","service_id":"API_xxx"}]
}'
# 返回里的 admin_token 必须记好: 之后再改配置都要带 X-Admin-Token
# ⛔ PEM 只出现在 curl 命令里, 不写进任何文件/SKILL.md/日志/仓库

# 4) 把沙箱地址填进包内 config.json 的 service_url
```

**5 个必填参数**: `app_id`(open.alipay.com)、`seller_id`(b.alipay.com 收款 PID)、
`private_key`(支付宝应用私钥, PKCS#1/PKCS#8 都支持)、`license_private_key`(**授权码签发私钥**)、
`service_id`(= `API_XXXX`, CLI 建)。

> 🔴 **两套私钥不可混用**: `private_key` 用于支付宝支付签名; `license_private_key` 用于签发
> 授权码, 其对应公钥已写进包内 `config.json`。混用会导致授权码在客户端验签失败。
> 服务端 setup 时会自签自验一次并在返回里报告结果。

### 服务端开关(沙箱不能注入环境变量, 一律走 config 表)

| 字段 | 默认 | 说明 |
|---|---|---|
| `strict_verify` | `true` | 🔴 上线必须为 true。false = 跳过支付宝验付, 任何人可伪造凭证白嫖 |
| `allow_unconfirmed` | `false` | 履约确认失败时是否放行。官方要求确认成功才交付 |
| `bind_machine` | `false` | 授权码是否绑机器。false = 换机/重装仍可用(推荐) |
| `reissue_limit` | `5` | 同一订单重签次数上限(仅在绑机模式生效) |
| `pay_window` | `1800` | 账单有效期(秒) |
| `resource_id_mode` | `url` | `url`=scheme://host/path; 若支付宝要求相对路径改 `path` |

### 三步走协议(官方, 与《AI按量付费 产品接入指南》对齐)

```
第0步(服务端内部) alipay.aipay.agent.payment.precreate -> amt_pre_pay_id

POST /api/license                      无凭证 -> 402
                                       amtPaymentLink: <amt_pre_pay_id>        ← 产品接入指南口径
                                       Payment-Needed: b64url({"protocol":{...},"method":{...}})
                                                                                ← SkillHub 口径
POST /api/license + Payment-Proof 头   验付 -> 签发授权码 -> 履约确认 -> 200 + license_key
POST /api/license + body.license_key   凭旧码恢复(换机/重装), 不重新出账
GET  /api/license/status?out_trade_no= 排障: 只回订单状态, 不回授权码
```

`Payment-Proof` = `b64url({"protocol":{"payment_proof":..,"trade_no":..},"method":{"client_session":..}})`。
★ 协议硬约束: **带凭证重试时 Body 必须与首次完全一致**, 否则服务端映射不回原订单
(客户端已把首次 body 存进 `.pay_cache/state.json` 并原样重发)。

### 三段式调用(与官方 probe/pay/complete 一致)

```bash
python scripts/payment_gate.py license                          # 查本机买断授权(不发网络请求)
python scripts/payment_gate.py probe [--video <路径>]            # 已买断 -> 本地验签放行(0); 未买断 -> 402 出账
python scripts/payment_gate.py pay                              # 交给官方支付能力, 展示支付入口, 等用户付款
python scripts/payment_gate.py complete                         # 付款后带凭证取回授权码并落盘(不重新下单)
python scripts/payment_gate.py complete --out-trade-no <订单号>   # 先查原单再取货(凭证由 CLI 输出自动捕获)
python scripts/payment_gate.py complete --proof <凭证>           # CLI 未回传凭证时手动指定
python scripts/payment_gate.py status  --out-trade-no <订单号>    # 排障: 查订单状态(不重新付款)
python scripts/payment_gate.py activate <授权码>                 # 离线/人工通道激活
```

**首次之后不用再付**: 首次付款成功后, 服务端签发的授权码持久化到 `~/.ai-live-clip/license.json`。
之后每次 `probe` **先做本地 RSA 验签**, 命中即放行 —— 不联网、不出账,
**换视频、换目录、换调用次数都有效**。服务端挂掉也不影响已买断用户。

### 五条契约(⛔ 逐条不可违反)

| # | 契约 | 行为 |
|---|---|---|
| 0 | **预下单** | 服务端先调 `alipay.aipay.agent.payment.precreate` 取得 `amt_pre_pay_id`，再出 402。⛔ 少了这步支付宝侧没有这笔单，用户付款必然失败 |
| 1 | **402 账单下发** | 未持有效凭证时计费服务返回 `HTTP 402` + `amtPaymentLink`(预下单号) + `Payment-Needed` 头(out_trade_no/amount/resource_id/pay_before, RSA2 签名)。收到 402 **即暂停交付**, 把账单交给支付宝支付能力完成支付, ⛔ 不得跳过 |
| 2 | **携带凭证重试** | 支付成功后携带 `Payment-Proof` **重试同一资源请求**(订单号不变, ⛔ 不重新下单) |
| 3 | **验付调用** | 服务端调 `alipay.aipay.agent.payment.verify` 验签, 核对 active/金额/订单号/资源一致; 任一不符返回 402 重新支付。**结果不明先查原订单, ⛔ 不要求重复付款** |
| 4 | **履约确认** | 资源生成后先调 `alipay.aipay.agent.fulfillment.confirm`, **确认成功才交付**; 失败返回 502, 可用同一 Payment-Proof 重试(不重复扣费) |
| 5 | **订单持久化与幂等** | 订单/凭证哈希落库, out_trade_no 唯一, 同一 Payment-Proof 仅消费一次(防重放), 同凭证重复重试返回同一授权 |

### 官方协议校准(2026-10-31 · SkillHub 教程 + 支付宝《AI按量付费 产品接入指南》2026-10-21)

> 两份官方文档口径互补, 本技能**已按两者交集实现**:
> - 《产品接入指南》(支付宝): 定义服务端三步接口 `precreate` → `payment.verify` → `fulfillment.confirm`,
>   402 响应头为 **`amtPaymentLink`**(承载预下单号 `amt_pre_pay_id`)
> - 《SkillHub 教程》: 定义买家侧交互, 402 响应头为 **`Payment-Needed`**(Base64URL 完整账单)
>
> ⚠️ 两者不冲突: 本服务端**同时下发两个头**, 账单体内也内嵌 `amt_pre_pay_id`, 买家侧 CLI 任一取法都能拿到。

**服务端五步**(缺一不可, 顺序不能颠倒):

| # | 步骤 | 接口 / 动作 |
|---|---|---|
| 0 | **预下单** | `alipay.aipay.agent.payment.precreate`(out_trade_no / service_id / goods_name / amount / pay_before) → 取得 `amt_pre_pay_id` |
| 1 | **402 出账** | 返回 `HTTP 402` + `amtPaymentLink: <amt_pre_pay_id>` + `Payment-Needed: b64url({protocol,method})` |
| 2 | **验付** | 读 `Payment-Proof` → `alipay.aipay.agent.payment.verify`(传 payment_proof / trade_no / client_session) |
| 3 | **业务校验** | ★ 只看接口 `code=10000` 不够, 必须核对 `active=true`、`amount`、`out_trade_no`、`resource_id`, 且 `trade_no` 未被重复履约 |
| 4 | **履约确认** | `alipay.aipay.agent.fulfillment.confirm` 成功才交付资源(本技能交付的是买断授权码) |

| 字段 | 位置 | 说明 |
|---|---|---|
| `402` | 状态码 | 需付费; 返回时须同时下发账单 |
| `amtPaymentLink` | **响应头** | ★ 产品接入指南口径, 值 = 预下单号 `amt_pre_pay_id` |
| `Payment-Needed` | **响应头** | ★ SkillHub 口径, 值 = b64url 完整账单(本技能其中也含 `amt_pre_pay_id`) |
| `Payment-Proof` | 请求头 | 支付凭证 |
| `Payment-Validation` | **响应头** | ★ 验付通过后由服务端返回, 客户端据此判定"已履约" |
| `Idempotency-Key` | 请求头 | 调用方生成的幂等键, 防重复扣款/重复履约(本技能已落库) |
| `payment-order-id` | 响应体/请求头 | 订单号, 贯穿全程(本技能响应体同时给 `payment_order_id` / `out_trade_no` 兼容旧客户端) |
| `pay-url` | 响应体 | 支付宝收银台地址 |
| `ack` | 响应体 | 履约回执 `{status:"FULFILLED", result:...}` |

**★ 放行判据(缺一不可)**:
```
HTTP 200  +  响应头 Payment-Validation  +  body.fulfillment_confirmed == true
```
只看到 200 不算成功 —— 客户端已按此判据实现, 缺头会返回 502 并不交付授权码。

**错误码表**(服务端已实现, 响应体 `code` 字段):

| code | HTTP | 含义 | 处理 |
|---|---|---|---|
| `OK` | 200 | 验付通过并履约完成 | 读 `ack` 与 `license_key` |
| `PAYMENT_REQUIRED` | 402 | 未付费 | 带 `Payment-Proof` 重试 |
| `PROOF_INVALID` | 402 | 凭证无效/已使用 | 重新支付取新凭证 |
| `AMOUNT_MISMATCH` | 402 | 金额与账单不符 | 核对后重新支付 |
| `PAYMENT_EXPIRED` | 402 | 账单超时 | 重新 probe 下单 |
| `INVALID_PARAM` | 400 | 参数缺失/非法 | 修正入参 |
| `ORDER_NOT_FOUND` | 404 | 订单不存在 | 重新 probe |
| `ALIPAY_GATEWAY_ERROR` | 502 | 支付宝网关失败 | 稍后重试, 不重复扣款 |

**买家侧 CLI 确切参数**(⛔ 不要臆造参数名):
```bash
alipay-bot 402-buyer-pay --session-id <会话ID> --file <probe 落盘的 payment-needed 文件> \
  --resource-url <资源URL> --method <GET|POST> --intent-summary "原始请求：<摘要>"
alipay-bot 402-query-payment-status --out-shake-no <交易号> --resource-url <URL> --method <方法>
```
票据**必须原样落盘**(Base64URL 原始串, 不解码、不改写), 否则 `alipay-bot` 读不到账单。
`--body` / `--headers` **官方 CLI 并无此参数**, 本技能默认不下传(需时可用环境变量开启)。

### 红线

- ⛔ **不自行实现付款或签名** —— 一律调用官方 `alipay-bot` / 支付宝官方能力
- ⛔ **私钥只在服务端**(环境变量/KMS), 不进 Skill 包、日志、代码仓库
- ⛔ **不绕过闸口** —— 未拿到授权前不进入阶段 1 转写(转写耗时 12-25 分钟, 先跑再收费等于漏收)
- ★ **三处价格必须一致**: 支付宝服务登记价 = 服务端 402 账单金额 = SkillHub 展示价 = **19.90 元**
  (两位小数; 客户端常量 `AI_CLIP_PRICE_CNY` 默认 19.90, 与服务端账单不符时脚本会打印告警)
- ★ **平台展示口径**: SkillHub 详情页统一标注「按次收费」并附免责声明, 平台表单**没有"买断"选项** ——
  **买断是本技能服务端的实现**(见上条), 平台侧仍按"19.90 元/次"展示。
  因此发布时: 表单价格填 **19.90**; 描述与案例里务必写明「**买断制 · 付一次永久有效**」,
  否则用户看到"按次收费"会产生预期差 → 投诉, 或审核误判"价格不符"
- ★ **计费口径: 买断** —— 付一次即永久有效。**resource_id 是固定的买断资源标识, 与具体视频无关**,
  因此换任何视频、调用多少次都**不再产生账单**; 只有**首次**(本机无授权时)才需要付款
- ⛔ **卖家侧无权创建"付款链接"给用户直接点** —— 付款必须由买家侧发起(`alipay-bot 402-buyer-pay`)。
  注意区分: 服务端调 `precreate` 拿的是**预下单号**(官方第一步, 必须做),
  不是收银台链接。若 `precreate` 返回 `insufficient-isv-permissions`,
  原因是**「AI按量付费」产品未开通 / 服务未注册 / serviceId 未生效**, 不是接口不该调 ——
  去支付宝开放平台开通产品并注册服务后即可; 沙箱阶段用固定 `api_mock_service_id`
- ⛔ **买家账号不能等于商家账号** —— 否则支付宝拒绝支付(`买家和卖家不能相同`)。
  自测付款时**必须换一个支付宝账号**, 不能用你自己的商家号付给自己
- ⛔ **不向用户暴露支付链接原文、二维码原文或交易号** —— 只展示金额与"请在弹出的收银台完成支付"

### 个人接入五步(官方路径)与当前进度

官方个人接入 = 入驻 → 接入支付宝(集成/部署/自测) → 改造 Pay Skill → 发布 → 买家验证。
本技能的对应状态：

| # | 官方步骤 | 状态 | 说明 / 阻塞项 |
|---|---|---|---|
| 1 | **入驻 SkillPay** | ⏳ 待办 | 手机号验证，个人无需企业认证 |
| 2.1 | **技术集成与沙箱** | ✅ 已完成 | 自建零依赖 Python 服务端（`ailiveclip-pay-server/`），本地自测 **34/34 通过**；沙箱 `service_id` 用固定 `api_mock_service_id` |
| 2.2 | **开通「AI 按量付费」产品** | ⏳ 待办 | 需卖家支付宝账号扫码开通，**只能本人操作** |
| 2.3 | **注册服务拿 serviceId** | ⏳ 待办 | 服务地址须填沙箱公网 HTTPS 地址；注册后得到 `API_xxx` |
| 2.4 | **生产密钥配置与部署** | ⏳ 部分 | `license_private_key` ✅ 已在本地；缺 `app_id` / `seller_id` / 应用私钥 / 支付宝公钥 |
| 2.5 | **上线自测（真实 0.01 元）** | ⏳ 待办 | 需**另一个**支付宝账号付款；自测时把登记价临时改为 0.01 |
| 3 | **改造 Pay Skill** | ✅ 已完成 | `payment_gate.py` 已按 402 → 付款 → 同单重试实现 |
| 4 | **发布 SkillHub** | ✅ dry-run 通过 | `ai-live-clip@1.3.0`；真实提交待第 2 步完成后进行 |
| 5 | **买家验证** | ⏳ 待办 | 从 SkillHub 装上架版本，用买家号真实付款验收 |

> 当前唯一硬阻塞是 **2.2～2.4：需要本人登录支付宝开通产品、注册服务、配生产密钥**。
> `alipay-cli auth status` 显示 `Not logged in`，需执行 `alipay-cli auth login` 扫码。
> 也可用官方集成 Skill 让 Agent 自动完成沙箱配置：
> `npx -y @alipay/alipay-aipay@latest install`

### 买家侧依赖

```bash
npx -y @alipay/agent-payment@latest install    # 买家支付能力(alipay-bot)
```

> 首次使用若提示找不到 `alipay-bot`, 先执行上面这条安装命令再重试。
> 脚本只做编排, 不接触支付密钥, 也不自行实现支付。

### 客户端环境变量(一般不设, 排障时用)

| 变量 | 默认 | 用途 |
|---|---|---|
| `AI_CLIP_BILLING_URL` | 取 `config.json` 的 `service_url` | 沙箱 HTTPS 地址。环境变量优先于配置文件; 两者都空则退回本地授权模式 |
| `AI_CLIP_RESOURCE_PATH` | `/api/license` | 买断资源端点(须与服务端注册的 path 一致) |
| `AI_CLIP_PRICE_CNY` | `19.90` | 买断价; 与 402 账单金额不符时打印告警 |
| `AI_CLIP_HTTP_RETRIES` / `_SLEEP` | `2` / `3` | 网络故障与 5xx 的重试次数 / 间隔秒 |
| `AI_CLIP_OFFLINE_GRACE` | `1` | 已买断用户遇服务端故障是否离线放行(置 `0` 关闭) |
| `AI_CLIP_LICENSE_FILE` | `~/.ai-live-clip/license.json` | 授权文件路径(仅联调覆盖用) |
| `ALIPAY_BOT_CMD` / `_BODY_FLAG` / `_HEADERS_FLAG` | `alipay-bot` / `--body` / `--headers` | alipay-bot CLI 适配(参数名以本机安装的官方文档为准) |

## 发布到 SkillHub(官方 CLI 流程)

> 依据 `skillhub.cn/tutorials`「通过 CLI 和 Agent 发布 Skill」(2026-10-31 核对)。
> 网页端一次性和少量发布足够; 经常迭代建议走 CLI。

**前置(浏览器, 一次性)**

1. 手机号验证码登录/注册 → 右上角头像 → 个人中心 → **实名认证**(人脸核身)
   ⛔ 未实名无法创建 API Token, 也无法发布
2. 个人中心 → `API keys`(直链 `/dashboard/keys`) → 创建, 得到 `skh_xxx`
   ⚠ **只显示一次**, 立刻保存; 丢了删掉重建即可

**CLI**

```bash
skillhub login --key <skh_xxx> --host https://api.skillhub.cn   # ✓ Logged in as @handle
skillhub auth whoami                                            # 校验 userId/handle/role
skillhub publish <skill目录> --host https://api.skillhub.cn --dry-run   # 先预检
skillhub publish <skill目录> --host https://api.skillhub.cn --changelog "首次发布"
```

成功输出 `✓ Published: skillId=xxxxx status=pending_review`, 进审核;
审核通过后详情页可见。**更新**时保持 `slug` 不变, 改 `version` 并重发即可。

**frontmatter 要求**(本包已满足)

| 字段 | 要求 | 本包 |
|---|---|---|
| `slug` | 必填 · kebab-case · 2-128 字符 · **全网唯一** | `ai-live-clip` |
| `version` | 必填 · 合法 SemVer | `1.3.0` |
| `displayName` | 必填 | `直播切片工具` |
| `summary`/`description`/`tags`/`license` | 建议有, 不作为阻断 | 均已填 |

⚠️ `slug: ai-live-clip` 是通用词, **很可能已被占用** → 发布报 `409 slug 已被其他用户占用`。
建议改成带个人标识的, 例如 `ai-live-clip-<你的handle>`(改 `slug` 不影响技能内部逻辑)。

**常见错误**: `401 invalid api key`(Token 失效, 重建) / `403 请先完成实名认证` /
`409 slug 冲突`(改 slug) / `429 请求过于频繁`(等 1 分钟) / 发布后详情页"未找到"(审核中, 属正常)。

## 引用文档(按需加载)

- `references/discipline.md` — 切片执行纪律 27 条全文 + low 段分级(review_priority)判定表
- `references/data-formats.md` — 三个阶段 JSON 数据交换格式(transcript / segments / clips_refined)
- `references/stages.md` — 阶段1/2/4 实现细节(句级时间戳重建、步骤 A-E、切片命名规则、模块说明、注意事项)
- `references/stream_copy-optimization.md` — stream_copy 快速切割方案与验证记录(阶段4 选模式/调参时读, 平时不必读)
- `references/funasr-install.md` — FunASR 安装与 pip 镜像下载速度排序(首次使用必读, 下载慢时切腾讯云镜像)
- `references/lessons-learned.md` — **本机运维/工具坑**(uv 沙箱禁装、ffprobe 路径、路径对齐、验收脚本陷阱、segmenter 短尾、目录名回退), 每次任务前扫一眼避免空转
- `references/clip-registry.md` — **多切片号去重**: 全局注册表(clip_registry / clip_status / update_registry)的跨号协调规则与发布流程, 多账号分发场景必读
- `references/compliance-rules.md` — **阶段5 发布合规检查**: 等级/动作口径、词表维护方法、已知误报模式、AI 必补的三项建议(每次交付前对照)
- `references/brand-words.private.md` — **专有名词订正集**(真实品牌/人名, 阶段2 落标题前对照; **不随公开发布包分发**, 首次使用时请向用户索取品牌修正表并自建此文件)

## 技能结构

```
直播切片工具/
├── SKILL.md                   # 本文件: 触发/架构/速查表/执行流程
├── config.json                # 买断价 + 沙箱 service_url + 授权公钥(随包分发, ⛔ 不含任何私钥)
├── scripts/                  # 四阶段执行脚本(在目标工作区运行)
│   ├── payment_gate.py       # 步骤0 付费闸口: probe/pay/complete(402 沙箱) + activate/machine-code(离线兜底)
│   ├── licensing.py          # 授权码核心: 机器码 / 签发 / 验签(RSA + HMAC 降级), 与服务端同格式
│   ├── issue_license.py      # 作者侧签发授权码(⛔ 私钥只在作者本机, 不随包分发)
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
│   └── tests/test_segmenter.py
└── references/               # 详细参考(按需读入)
```
