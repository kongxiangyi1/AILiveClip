# 《直播切片工具》(ai-live-clip) Skill 评估报告

- 评估日期: 2026-10-13
- 评估对象: 已安装技能 `C:\Users\KONGXY\.workbuddy\skills\直播切片工具\`(SKILL.md 修订于 2026-09-30, 即当前线上版本)
- 对照材料: 官方 skill-creator 规范与 quick_validate.py; 项目仓库 `E:\WorkBuddyCliping1\ai-live-clip`; 两个发布包 `dist/ai-live-clip.zip`(免费版)与 `dist/ai-live-clip-pay.zip`(付费版); 全部 13 个脚本与 9 个 references 原文
- 评估方法: 5 轮递进 —— ①打包与元数据合规 ②SKILL.md 结构与触发设计 ③references 内容质量 ④脚本与文档一致性(逐条核对代码) ⑤安全与隐私。每条结论均附文件与行号证据。

---

## 总评

技能的整体工程质量**明显高于平均水准**: 纪律沉淀带实测数据、progressive disclosure 结构清晰、隐私三层防护、CLI 文档与 argparse 高度吻合、付费版支付约束严谨。主要问题集中在**三处**:

1. **元数据不合官方规范**(免费版 frontmatter 用 `summary` 而非 `description`, 缺 `agent_created`)——这是发布准入级的硬伤, 且平台技能注册信息里该技能的描述位已实际受损;
2. **安装文档自相矛盾**(funasr-install.md 第七节同一节内"禁用 uv"与"用 uv venv 成功命令"并存, 且与 lessons-learned.md 的镜像/锁版本结论直接冲突)——首次使用关键路径;
3. **三个脚本硬编码 skill 目录 temp**, 使 SKILL.md 主流程文档链(步骤2→3→5)与实际行为不符——可用性层面影响最大。

另发现已安装目录内堆积 **2GB 运行时产物**(scripts/output/ 29 个真实切片视频), 需清理。

问题分级统计: **P0 × 2 项, P1 × 9 项, P2 × 6 项**。

---

## 第 1 轮: 打包与元数据合规(发布准入)

### 做得好的

- `pack_zip.py` 的隐私防护是三层设计: `EXCLUDE_DOCS` 排除 `brand-words.private.md` → 打包后 `PRIVATE_TOKENS` 全文扫描(用户名/绝对路径/私有品牌词 15 个 token)→ 包内二次确认无 temp/output/pyc。实测两个公开 zip 均**不含**任何私有品牌词与用户名, 防护有效。
- zip 根目录统一为 `ai-live-clip`(frontmatter name), 规避中文目录名在部分平台解析异常——注释里写明了原因, 是对的。
- `brand-words.private.md` 采用"机制公开(discipline.md 泛指格式)/数据私有(本文件)"分离, 换主播只需替换数据文件, 设计干净。
- 付费版把 `payment_gate.py` 只放在 pay 包, 免费包不含支付代码, 边界正确。

### 问题

**[P0-1] 免费版 frontmatter 不符合官方校验, 平台描述位已实际受损**
- 证据: 官方 `quick_validate.py` 第 35 行强制要求 `description:` 字段; 本技能免费版 SKILL.md 第 6 行用的是 `summary:`。付费版 zip 的 SKILL.md 反而用的是 `description:` + `agent_created: true`(两版 frontmatter 已实测 diff), 说明正确格式作者本就掌握, 免费版是漏改。
- 实际影响: 当前平台技能列表中, 本技能的条目显示为"ai-live-clip: ai-live-clip", 描述位只回退了名称; 同目录下 silky-cut-pipeline、agnes-media-gen 等全部正常显示完整 description。即 `summary` 没有被平台识别为触发描述, **技能的场景匹配能力已受损**。
- 改法(SKILL.md 第 1-8 行):
  ```yaml
  # 现状
  summary: '将直播回放/录播长视频...仅 Windows 实测。'
  # 改为
  description: '将直播回放/录播长视频(1-5小时, MP4/TS)按话题内容自动粗切为可独立传播的短视频片段。当用户提供直播回放视频并要求按内容/话题切分, 或提到"直播切片""直播回放剪辑""带货视频粗剪""直播内容分割"时使用。四阶段流水线: FunASR 转写 → AI 语义分段 → 静音吸附精修 → FFmpeg 切割(支持关键帧感知流复制)。依赖 Python 3.10+ / FunASR / imageio-ffmpeg, 仅 Windows 实测。'
  agent_created: true
  ```

**[P0-2] 缺 `agent_created: true`, 后续无法通过 skill_manage 修改/删除本技能**
- 证据: skill-creator 规范明确"YAML frontmatter MUST include `agent_created: true` so that skill_manage can modify or delete the skill later"; 本技能免费版没有该字段。同批用户技能(agnes/silky-cut/wechat-channels/mnemonic)全部带此字段。
- 改法: 随 P0-1 一并加上。

**[P1-3] 已安装目录堆积 2GB 运行时产物, 且 temp 里躺着文档引用的"失踪脚本"**
- 证据: `scripts/output/` 含 29 个文件共 **2.0GB**(2026-09-19 场次真实切片, 标题含主播内容); `scripts/temp/` 含 15 个文件(transcript.json/segments.json/clips_refined.json + build_segments.py + clean_filenames.py + gen_publish_list.py + 3 个 run.log); `scripts/__pycache__/` 混合 312/313 两种 pyc。
- 双重影响: ①skill 目录被当数据盘用, 拖慢一切备份/同步; ②`scripts/temp/transcript.json` 等正是 lessons-learned.md #7 记录的"跨任务污染"现场(新任务读到上一场转写)。
- 改法: 历史 output 迁移到工作区(如 `E:\录播\2026-09-19\`)后删除; temp 清空; `__pycache__` 删除。zip 打包不受影响(pack_zip 已排除), 但本地安装目录必须瘦身。

### 本轮小结

发布链路(pack_zip.py)本身是可信的; 坏在 frontmatter 两行与本地目录卫生。P0 两项都是分钟级修复。

---

## 第 2 轮: SKILL.md 结构与触发设计

### 做得好的

- **触发条件具体可执行**: 4 类意图 + 5 个中文触发词, 覆盖"提供文件要求切分/关键词/长视频分割"三种入口。
- **纪律三档速查表(A 强制/B 写脚本/C 汇报)是点睛设计**: 27 条纪律不全读, 用前对档, 直接降低每次任务的上下文成本。编号与 references/discipline.md 对应关系在速查表开头已声明。
- **progressive disclosure 分层正确**: SKILL.md(11.6KB)只留触发/架构/流程/速查表, 细节按需加载 9 个 references, 符合官方三级加载范式。
- **分工边界写得清楚**: "AI 负责阶段2 全部语义判断, 脚本负责吸附与切割", 且明确"吸附只修切点卫生, 不修语义错误"。

### 问题

**[P1-4] 主流程文档链断裂: SKILL.md 声称 temp/output 落工作区, 但三个脚本硬编码 skill 目录**
- 证据链:
  - SKILL.md 第 95 行: "在**目标工作区根目录**执行脚本, `temp/` 与 `output/` 会落在工作区(可用环境变量 `AI_LIVE_CLIP_WORKDIR` 覆盖根目录)";
  - `config.py` 第 14 行 `_WORK_ROOT = AI_LIVE_CLIP_WORKDIR or os.getcwd()` → asr_pipeline/cut_pipeline 确实落 CWD;
  - 但 `segmenter.py` 第 366-370 行 `tr_path/out` **硬编码** `base/temp/`(base=脚本所在目录); `review_checklist.py` 第 19 行与 `fix_brand_name.py` 第 20 行 `TEMP = os.path.join(BASE, "temp")` 同样硬编码。
- 后果: 按 SKILL.md 步骤 2 生成工作区 `temp/segments.json` → 步骤 3 跑 `python segmenter.py`(无参数)→ 读的是 **skill 安装目录里上一场任务残留的** segments.json, 产物也写回 skill 目录 → 步骤 5 `python cut_pipeline.py temp/clips_refined.json` 在工作区找不到文件。整条文档链实际跑不通, 必须靠 lessons-learned.md #2 记录的人工复制兜底。
- 改法(推荐 A 为主、B 同步):
  - A(根治, 改脚本): segmenter.py 的 `tr_path`/`out` 默认值改为 `Config.TEMP_DIR` 下文件; review_checklist.py 与 fix_brand_name.py 的 `TEMP` 改为读 `Config.TEMP_DIR`(保留 `--input` 位置参数覆盖)。segmenter 已有 `run(transcript_path, segments_path, out_path)` 显式路径入口(第 278 行), 与 lessons #2 推荐的"替代做法"一致, 默认值改 Config 不破坏它。
  - B(文档同步): SKILL.md 步骤 3 改为 `python segmenter.py [--input temp/segments.json]`(需先给 segmenter 补 `--transcript/--output` 参数), 或在流程里保留一行显式路径说明。

**[P2-5] Streamlit 审核出口与主流程的衔接没有写明**
- 证据: SKILL.md 步骤 4 导出 `segments_approved.json`, 但步骤 5 切割用的是 `clips_refined.json`; app.py 第 140 行提示"下载后用 cut_pipeline.py 切割"——即审核稿有两条出路(直接切/回炉 segmenter), 文档未说明选哪条。直接切会跳过静音吸附(人工已调好边界的情况可接受), 回炉则人工调整可能被吸附覆盖。
- 改法: 在步骤 4 加一句判定规则——"人工只在 app.py 里做了删除/微调 → 直接 `cut_pipeline.py segments_approved.json`; 改动较大 → 把调整写回 segments.json 重跑 segmenter"。

**[P2-6] TS+HEVC 源模式选择三处口径不一**
- 证据: SKILL.md 速查表 A 档与 discipline.md #4 都写"TS+HEVC 源直接 --mode reencode"; SKILL.md 主流程步骤 5 却把 `--mode stream_copy` 列为推荐首位; stream_copy-optimization.md R6 实测"TS 源约 1/3 切点降级, 整体仍显著快于全量 reencode(约 3 倍起)"。
- 改法: 统一为一句话——"MP4 源(GOP~1.2s)直接 stream_copy; TS/HEVC 源(GOP 8.5s+)可 stream_copy 让 1/3 自动降, 也可直接 reencode 省心, 两者总耗时同量级"。

### 本轮小结

SKILL.md 的骨架是好的, 速查表 + 按需 references 值得保留; 要修的是"文档承诺的行为"与"脚本实际行为"之间的裂缝(P1-4)。

---

## 第 3 轮: references 内容质量

### 做得好的

- `data-formats.md`: 三个 JSON 的 schema 与代码逐字段核对一致(transcript 的 sentences/silences、segments 的 pin 字段、clips_refined 的 params/review_priority/context), 连 discipline #11 记录的"顶层键是 results 不是 clips"这种细节都对。
- `clip-registry.md`: 指纹规则(clip_key=md5)、多号争用判定、与 cut_pipeline 的 4 个集成点(L28-29/run 签名/global_id 回写/--account)全部与代码吻合, "仍待治本"一节诚实地写了 L2 指纹去重的未完成部分。
- `discipline.md`: 27 条全部带日期与实测锚点(236s/340s、7/29 触发 3 真、18/29 复核无一真问题), 是可复用的知识资产; #8 的修正表已正确地泛化为"格式示例 + 指向私有文件"。

### 问题

**[P0-3] funasr-install.md 第七节同一节内自相矛盾, 且在首次使用关键路径上**
- 证据: 第 61 行"**不要用 `uv` 安装本技能依赖**... `uv pip install` 和 `uv venv`+`python -m pip` 都踩"; 第 68 行的"实测成功命令"却以 `uv venv --python 3.12 <VENV>` 开头。同一个人按同一节的两句话会得到相反动作。
- 改法: 成功命令改为 `python -m venv <VENV>` + `<VENV>/Scripts/python.exe -m ensurepip`, 与 lessons-learned.md #1 对齐。

**[P1-5] funasr-install.md 与 lessons-learned.md 的镜像/锁版本结论直接冲突**
- 证据: funasr-install.md 第 20 行把腾讯云列为"🥇 推荐", 第 55 行"技能不锁版本, `pip install funasr` 默认装最新即可"; lessons-learned.md #0/#1 则记录"transformers 降级在 tencent 镜像卡死(27min 仅 13s CPU), **用 aliyun 镜像正常**", 且唯一稳定组合是 `torch 2.3.1 + transformers 4.49 + funasr 1.4.1`(根因: transformers>=5 要求 torch>=2.5 会禁用 PyTorch 导致模型注册失败)。
- 后果: 按 funasr-install.md 操作 = 走进一个已被实录损坏的环境(lessons #0 开头那句"envs/funasr-clip 已被污染, 勿用"就是这么来的)。
- 改法: funasr-install.md  §二/§三/§六 统一为 lessons 的结论: 安装用 aliyun; 锁版本清单(直接引用 lessons #0 的命令); 腾讯云仅作为"单纯下载 funasr wheel"场景的备注。

**[P1-6] lessons-learned.md 内部 venv 矛盾 + 未填占位符**
- 证据: #0 写明 `envs/funasr-clip` "⚠️ 已损坏, 勿用", 可用 venv 写成 `<你的 venv 路径>` 占位符; 但 #7 又说"环境探测直接走 funasr-clip venv(重申)... 直接按本文件 #0 的 envs/funasr-clip 路径跑"。
- 改法: #7 改为指向 lessons #0 的现行 venv 占位符说明; `<你的 venv 路径>` 保留占位符可以(公开包机器相关), 但要在占位符旁注明"本机实际路径见首次安装记录, 公开包不内置"。

**[P1-7] 4 处悬空脚本引用; 其中 2 个脚本实际躺在 scripts/temp/ 里**
- 证据:
  - discipline.md #24 引用"verify 脚本"(对 high_risk 段自动接 volumedetect)——scripts/ 内无此脚本;
  - lessons-learned.md #5 引用 `merge_short_tails.py`("已并入本次流程")——不在 scripts/;
  - lessons-learned.md #7 引用 `scripts/temp/rename_clips.py` 与 `build_segments.py`——rename_clips 不在, 而 **build_segments.py 此刻就在 scripts/temp/ 里**(2026-08-20 专场的链式对齐脚本);
  - lessons-learned.md #7 还建议"脚本可提升为 scripts/clean_filenames.py"——**clean_filenames.py 也已躺在 scripts/temp/**。
- 改法: 把 build_segments.py(ASR 时间戳重叠场的链式对齐, 有明确技术价值)与 clean_filenames.py(文件名敏感字符清洗, lessons 明确说"应固化为交付前标准步骤")提升为 scripts/ 正式成员并写进 SKILL.md 技能结构树; verify 脚本与 merge_short_tails 二选一——补一个最小 verify_output.py 进 scripts/(把 discipline #19/#24 的 silencedetect+volumedetect 合并流程固化, 顺带解决 #19 命令缺 `-t 10` 的问题), 或把相关纪律改写为纯命令 inline; rename_clips 的引用删除(clean_filenames 替代)。

**[P1-8] stages.md 四项过期**
- 证据: ①第 80-83 行命名规则树里画了 `{序号}_{标题}_{起-止}.jpg` 封面文件, 但技能明确"纯切片不生成封面"(SKILL.md/README/stages 注意事项三处都说不生成), 目录树是旧版残留; ②"各模块说明"只列了 6 个脚本, 缺 review_checklist/fix_brand_name/clip_registry/clip_status/update_registry/mute_profanity; ③第 110 行"规则与参数见下文'阶段3'", 而 stages.md 根本没有阶段3 章节(只有 1/2/4); ④第 105 行"完整代码以工作区内的实际文件为准"是项目仓库版措辞, 已安装版应写"以 scripts/ 内实际文件为准"。
- 改法: 四处一次性修订(删 jpg、补 6 脚本一行一个、删幽灵引用、改措辞)。

**[P1-9] discipline.md #19 的 silencedetect 命令缺 `-t 10`, 与 lessons #4 矛盾且会触发超时假死**
- 证据: discipline.md 第 70 行 `ffmpeg -i 切片 -af "silencedetect=noise=-20dB:d=1" -f null NUL`(无 -t 限制); lessons-learned.md #4 第 59 行明确记录"静音扫描只扫前 10s: 不加 `-t 10` 会解码整个切片(最长 12 分钟)→ 极慢、易触发 120s 超时假死(表现为脚本无输出 + exit 1)"。
- 改法: discipline #19 命令改为 `ffmpeg -ss 0 -t 10 -i 切片 -af "silencedetect=noise=-20dB:d=1" -f null NUL`, 并加一句"整段扫描同理必须限定时长"。

**[P2-7] discipline.md #6 "进入任何新视频任务前先读项目 SKILL.md"是项目版残留**
- 证据: discipline.md 第 16 行。已安装版没有"项目 SKILL.md"这个概念(项目仓库的 SKILL.md 还是旧单体版, 反而会造成错位)。
- 改法: 改为"进入新视频任务前先读本 SKILL.md 速查表对档"。

### 本轮小结

references 的数据格式与注册表两份是高质量样板; 扣分集中在"安装文档矛盾(P0/P1)"和"悬空引用(P1-7)"——后者本质是真实任务里立过功的一次性脚本没有归建。

---

## 第 4 轮: 脚本与文档一致性(逐条核对代码)

### 做得好的

- **CLI 文档与 argparse 逐条吻合**: cut_pipeline.py 的 `--workers/--mode/--max-snap-s/--account/--no-registry`、clip_status.py 的 `--conflicts/--publish/--reject/--export-csv`、mute_profanity.py 的 `--dry-run`、segmenter.run(tr,seg,out) 签名——SKILL.md/clip-registry.md/lessons 里出现的命令全部对得上, 没有一个是凭记忆写的。
- **fix_brand_name.py 的隐私设计对**: 错误词/正确名只从命令行或环境变量来, 不内置任何私有词表, 杜绝词表随包泄露。
- **stream_copy-optimization.md 的验证记录是工程范本**: R1-R6 六轮, 每轮有数据(121s vs 2.0s、0/34 首帧不一致暴露 CRA 陷阱、34 片 7.6s = reencode 的 1/190), 且诚实记录了 B 帧漂移 ≤1.05s 的遗留风险。

### 问题

**[P1-10] 编码器文档四重过期: 全部声称"优先 QSV", 代码已恒定 libx264**
- 证据: config.py 第 47-49 行 `candidates` 列表被清空并注释掉 `("h264_qsv", [])`, 第 72-74 行恒定回退 `("libx264", ["-preset","veryfast","-crf","23"])`; 代码注释写明原因(QSV 对 912x1920 + 90k tbr 的 HEVC TS 报 "Current frame rate is unsupported", 且 libx264 输出对视频号兼容性最好)。但 SKILL.md 第 466 行、stages.md 第 118/122 行、README、lessons-learned.md #0 四处仍写"优先 Intel QSV 硬件加速, 不可用时回退 libx264"。
- 评估: **代码的决策是对的**(有实测依据), 过期的是文档。按文档执行的 AI 会在耗时估算和预期管理上出错。
- 改法: 四处文档统一改为"编码器固定 libx264 veryfast+CRF23(曾探测 QSV, 对竖屏 HEVC 直播源不兼容且输出兼容性差, 已禁用); 如需 QSV 自行改回 config.py candidates"。

**[P1-11] review_checklist.py 的 mmss() 不用技能约定的 HMMSS, >1h 片段必然误读**
- 证据: review_checklist.py 第 42-44 行 `mmss()` 返回 `f"{s//60:02d}{s%60:02d}"`——3930s(1:05:30)渲染为 `6530`; 而 stages.md 第 87-93 行与 discipline #26 明确技能内时间码是紧凑 HMMSS(`10530`=1时05分30秒), #26 还专门警告"1 小时制易误读, 汇报时标注时分秒"。
- 后果: 5 小时直播的中后段(必然 >1h), 一轮确认清单里的时间码全部是 MMSS 语义, 与文件名/cut_results 的 HMMSS 对不上, 人工核对时要做心算换算——正是 #26 想避免的事故。
- 改法: `mmss()` 改为 `f"{s//3600}{s%3600//60:02d}{s%60:02d}"`(与 cut_pipeline 命名格式一致)。

**[P1-12] fix_brand_name.py 修正的可能是上一场任务的 JSON**
- 证据: fix_brand_name.py 第 58-59 行 rewrite 的 `TEMP/segments.json`、`TEMP/clips_refined.json` 是硬编码的 skill 目录路径; 而文档用法是 `python fix_brand_name.py <输出日期目录> <错误词> <正确名>`——用户会以为在修正"本次任务"。若没按 lessons #2 把文件复制进 scripts/temp, 它静默修改的是残留旧文件(还带 .bak_brand 备份)。
- 改法: 随 P1-4 的 A 方案一并把 TEMP 改为 Config.TEMP_DIR; 并在 SKILL.md 该命令处注明"需与本次任务的 temp/ 在同一工作区"。

**[P2-8] 免费/付费两条发布线基线漂移**
- 证据(实测 diff 两个 zip): pay 包的 SKILL.md(9-29, 14805B)旧于 free(9-30, 11602B); pay 的 discipline.md 比 free 大 20 字节(9-29 vs 9-30 两版); pay 的 review_checklist.py 是 8-17 版(free 是 9-30 版, 即 mmss/TEMP 问题修复前的旧文件——若先修 free 不同步 pay, 付费买家拿到有 bug 的版本)。
- 改法: 建立"先改已安装目录 → 重打 free 包 → 同源重打 pay 包(仅追加 PAYSKILL 块与 payment_gate.py)"的单基线流程; 或在 pack_zip.py 里把 pay 包也纳入同一 SKILL_DIR 源 + PAYSKILL 段落由脚本注入, 消灭手工维护两份 SKILL.md 的模式。

**[P2-9] 项目仓库整体落后于已安装技能, 无同步机制**
- 证据(md5 + mtime 逐文件核对): 项目缺 clip_registry.py/clip_status.py/update_registry.py 三个脚本; config.py(8-11 旧)/cut_pipeline.py(8-13 旧)/fix_brand_name.py(8-13 旧)/mute_profanity.py(9-28 早 5h)/review_checklist.py(8-13 旧)五个脚本是旧版; 项目 SKILL.md 是重构前的单体版(纪律 27 条内嵌、无 references、无注册表/静音步骤); 项目还多了 speedtest_*/pack_zip.py 等开发工具。
- 值得注意: pack_zip.py 的 SKILL_DIR 已指向**已安装目录**——即发布管线的事实源头就是 `~/.workbuddy/skills/直播切片工具/`, 项目仓库已退化为"文档 + 一次性脚本仓库"。
- 改法: 二选一——①明确定位: 已安装目录为唯一源头, 项目仓库只留 docs/ 与 temp/  Scratch, 在项目 README 写明"改技能请改已安装目录, 本仓库不参与发布"; ②若要保留项目为开发主线, 写一个 sync 脚本把已安装目录的 scripts/references/SKILL.md 反向同步回项目(注意排除私有 brand-words 与 output/temp)。

**[P2-10] 免费版 SKILL.md 的 SCRIPT_DIR 说明列举了 trae-cn 路径, 信息噪音**
- 证据: SKILL.md 第 95 行括号里列了 `~/.workbuddy/...`、`~/.trae-cn/...`、"或你解压 zip 的目录"三种; 付费版同一行已精简为只留 workbuddy 路径。免费版这行是更早的措辞。
- 改法: 与付费版对齐, 只留 `~/.workbuddy/skills/直播切片工具/scripts`。

### 本轮小结

代码与文档的匹配度整体很高(命令级全对), 失分在"行为级"承诺(路径落点、编码器、时间码格式)——这三类恰恰是 AI 执行时最容易被文档带歪的地方。

---

## 第 5 轮: 安全与隐私

### 做得好的

- `brand-words.private.md` 文件头有醒目警告 + pack 排除 + 包内 token 扫描, 三层纵深; "首次使用时向用户索取并自建"的公开版指引在 discipline #8/lessons #8/README 三处重复, 公开包使用者不会摸不到头脑。
- 付费版支付协议文档质量高: 402/Payment-Proof 重试/验付逐项核对/幂等(同一 Proof 只消费一次)/"结果不明不得要求重复付款"的硬约束齐全, 且明确"闸口只编排、不接触密钥、不自行签名"。
- 两个公开 zip 实测无私有品牌词、无用户名、无绝对路径泄露。

### 问题与风险

**[P2-11] 已安装目录的 2GB output 是本地隐私面(非发布泄露)**
- scripts/output/ 里 29 个切片标题含真实主播与内容话题。zip 不含它们(pack 已排除), 但用户若手动备份/分享 skill 目录会带出; 且占用 2GB。
- 改法: 随 P1-3 迁移清理。

**[P2-12] scripts/temp/ 的跨任务污染是活案例, 不是历史**
- lessons #7 记录了"scripts/temp 被新任务覆盖导致读到 08-19 另一场直播"的事故; 此刻 scripts/temp/ 里仍放着 09-19 那场的 transcript.json/segments.json/clips_refined.json 和 review_checklist.txt。硬编码默认值(P1-4)不除, 这个坑就会按周期复发。
- 改法: 随 P1-3/P1-4 一并根治。

### 本轮小结

发布侧隐私无实际问题; 本地侧的问题是"skill 目录被当工作区用"的同一个病根(P1-4), 治了脚本路径, 这轮的两个 P2 自然消解。

---

## 修改建议总表(按执行批次)

### 批次 1 — 发布阻断项(约 30 分钟, 改完即可重打包)

| # | 级别 | 位置 | 动作 |
|---|---|---|---|
| 1 | P0-1/P0-2 | 已安装 SKILL.md 第 1-8 行 | `summary:` → `description:`; 增加 `agent_created: true` |
| 2 | P0-3 | references/funasr-install.md 第 59-72 行 | 第七节重写: 删"禁用 uv 但成功命令用 uv venv"的矛盾, 统一为 `python -m venv` + ensurepip |
| 3 | P1-3 | 已安装 scripts/output, scripts/temp, __pycache__ | 2GB 历史切片迁往工作区后删除; temp/pycache 清空(build_segments.py 与 clean_filenames.py 先抢救出来, 见 #7) |

### 批次 2 — 正确性修复(约半天, 改脚本 + 改文档)

| # | 级别 | 位置 | 动作 |
|---|---|---|---|
| 4 | P1-4/P1-12 | segmenter.py 366-370 / review_checklist.py 19 / fix_brand_name.py 20 | 三处默认路径统一走 `Config.TEMP_DIR`(CWD 或 AI_LIVE_CLIP_WORKDIR); 给 segmenter 补 `--transcript/--output` 参数 |
| 5 | P1-10 | SKILL.md/stages.md/README/lessons #0 共 4 处 | "优先 QSV" → "固定 libx264 veryfast+CRF23(QSV 对竖屏 HEVC 不兼容已禁用, 原因注明)" |
| 6 | P1-11 | review_checklist.py 42-44 | `mmss()` 改 HMMSS(与 cut_pipeline 命名一致) |
| 7 | P1-7 | scripts/temp/build_segments.py, clean_filenames.py | 提升为 scripts/ 正式脚本, 写进 SKILL.md 技能结构树; discipline #24 的 verify 脚本落成 scripts/verify_output.py(含 `-t 10` 限制), 或删除该条引用; rename_clips/merge_short_tails 引用删除或补脚本 |
| 8 | P1-8 | references/stages.md | 删 .jpg 封面树、补 6 个脚本说明、删"见阶段3"幽灵引用、"工作区"改"scripts/" |
| 9 | P1-9 | references/discipline.md #19 | silencedetect 命令加 `-ss 0 -t 10`, 注明整段扫描必须限定时长(对齐 lessons #4) |
| 10 | P1-5/P1-6 | funasr-install.md §二/三/六 + lessons-learned.md #0/#7 | 镜像结论统一为 aliyun; 补锁版本清单(torch 2.3.1/transformers 4.49/funasr 1.4.1); #7 venv 指向改现行路径, 消除与 #0 的矛盾 |

### 批次 3 — 发布治理与口径统一(约 1-2 小时)

| # | 级别 | 位置 | 动作 |
|---|---|---|---|
| 11 | P2-8 | pack_zip.py | 建立单基线: free 包与 pay 包同源(PAYSKILL 块由脚本注入), 消灭两份手工 SKILL.md |
| 12 | P2-9 | 项目仓库 | 明确"已安装目录为唯一源头", 项目只留 docs/ 与开发 Scratch, 或在 README 写明同步策略 |
| 13 | P2-6 | discipline.md #4 / SKILL.md 速查表 | TS+HEVC 模式选择统一口径(见第 2 轮 P2-6 改法) |
| 14 | P2-7 | discipline.md #6 | "读项目 SKILL.md" → "读本 SKILL.md 速查表对档" |
| 15 | P2-5/P2-10 | SKILL.md 步骤 4 / 第 95 行 | 补审核稿出口判定规则; SCRIPT_DIR 路径列举精简(与付费版对齐) |

### 明确不建议改的

- **27 条纪律的编号与速查表分档机制**: 这是本技能最有价值的结构, 修订时只改内容别动编号(引用遍布 discipline/lessons/SKILL.md 三处)。
- **fix_brand_name.py 的"词表不内置"设计**: 正确且必要, 保持。
- **pack_zip.py 的隐私扫描 token 列表**: 随 brand-words.private.md 内容更新即可扩充, 机制本身保留。
- **付费版的支付硬约束文案**: 已达标, 批次 11 只做基线对齐, 不重写文案。

---

## 附: 评估方法与可信度说明

- 所有代码结论均来自对已安装目录 13 个脚本的实读(路径定义、argparse、函数签名逐行核对), 非文档转述;
- 所有文档矛盾均给出双方原文位置, 可逐条复核;
- 两个 zip 的内容清单与差异经实际解包 diff 得出;
- 未执行任何修改——本报告只做评估与建议, 是否执行、执行哪些批次, 待确认。
