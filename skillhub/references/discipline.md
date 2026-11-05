# 切片执行纪律(28条全文 + low 段分级)

> 迁移自项目 SKILL.md。纪律 #8 的专有名词修正对照表是作者内容领域(酒类/商业口播直播)的**示例**, 使用本技能时请按**自己的内容领域**维护该表, 格式不变: 左列为 ASR 错词, 右列为正确名。

1. **任何 JSON 手工修改后必须校验**: 运行 `json.load` 通过后才进下游(cut_pipeline)
2. **多行 Python 一律写脚本文件执行**, 不内联 PowerShell(引号解析陷阱)。含分号/引号/列表推导的单行
   `python -c` 在 PS 里必然踩坑, 直接写 `temp/xxx.py` 再 `python temp/xxx.py`。调试型分析脚本输出
   **统一写 UTF-8 文件**(`open(..., encoding="utf-8")` 写 txt)后用 Read 读, 不依赖控制台(chcp 65001
   对管道输出无效, GBK 乱码浪费轮次)
3. **low 段按 review_priority 分级复核(segmenter 已实现, 见"阶段3"说明)**:
   safe=无需复核 / low_risk=可批量接受 / high_risk=必须人工看 context 字段
4. **TS+HEVC 源(直播回放)直接 --mode reencode**: IDR 间隔~10s 注定 stream_copy 大量降级, 降级后与直接 reencode 耗时相当
5. **segmenter 输出后检查过渡句归属**: 句尾保护吸附若吞入 <=5s 过渡短句(提问/寒暄),
   在 segments.json 对应段加 `"pin_end": true`(或 pin_start)钉住边界, 重跑即可, 无需手改切点 JSON
   （注意：pin 某段 start 后需检查前段 end 是否 > 本段 pin_start 产生重叠, 是则同步 pin 前段 end, 见 lessons #7）
6. **进入任何新视频任务前先读项目 SKILL.md**, 全局 skill 仅作参考
7. **中文路径环境**: PowerShell 调试时先 `chcp 65001`; 生产流程走 Python subprocess
8. **专有名词必须校对(2026-08-13 教训)**: ASR 同音错字会污染品牌名/人名。
   语义判断可容忍错字, 但**标题/文件名/note 必须用正确专有名词**: 生成 segments.json 前先把全片专有名词
   (品牌/人名/地名)列成清单核对一次(不确定就列出来问用户), 再落标题。

   **修正对照表**: 逐条对照后再落标题, 禁止使用左侧 ASR 词。表在哪里分两种情况:
   - 本地安装(未公开发布): 见 `references/brand-words.private.md`(含真实品牌/人名, **不随包分发**)。
   - 格式模板见 `references/brand-words.template.md`。
   - 从平台安装的公开版: 该文件不存在, **首次使用时向用户索取**, 并按下表格式自建。

   对照表格式(**示例数据, 请替换为你自己的**):
   | ASR 词(错) | 正确名 |
   |---|---|
   | 同音错字1/同音错字2 | **正确品牌名** |
   | 同音错字3 | **正确人名** |
9. **清理输出目录以 cut_results.json 为准**: cut_pipeline 目录名 = `{idx+1:02d}_{sanitize(title)}_{起-止}`,
   sanitize 会把标题中 `:`/`"`/`*` 等替换为 `_`。按 JSON title 推导文件名会匹配失败,
   构造 keep 集合应读 cut_results.json 的 `file` 字段取目录名
10. **转写方案选择(2026-08-13 实测)**: 单视频一律整段 asr_pipeline(48.7min forward 236s≈12.4x, 已是 CPU 上限)。
    transcribe_chunked 分块并行**不增加 CPU 总吞吐**: 实测 10 块 4worker 总墙钟 340s 反而慢于整段,
    且块边界确定性切残句子(9 个边界 6 处切中完整句)+ 丢失边界静音锚点(617→602, 影响 segmenter 吸附)。
    仅两种场景用它: 多视频批量(模型跨视频复用) / 需断点续跑的 >2h 长视频
11. **先读数据再写验证代码(2026-08-13 教训)**: 写任何读取 JSON 的验证脚本前, 第一步先 Read 目标文件确认真实结构,
    禁止凭记忆猜字段名。已踩坑: cut_results.json 顶层键是 `results`(数组, 每项 index/title/file/duration_s/mode/start_offset_s),
    不是 `clips`; 验证脚本因此报 KeyError 白跑一轮
12. **首次执行脚本前核对 CLI(2026-08-13 教训)**: 先读脚本 argparse 部分或跑 `--help` 确认参数名,
    不凭记忆猜(本次 cut_pipeline.py 参数靠猜且侥幸正确, 属运气非流程)
13. **Non-monotonic DTS 警告可忽略**: HEVC 直播回放源时间戳乱序, reencode 切割日志会刷屏该警告,
    ffmpeg 自动修复, 输出时长/进度正常, 无需中断或排查
14. **切割后验证清单(2026-08-13 教训)**: ① 文件存在性+大小汇总 ② ffprobe 抽查首尾切片
    (编码/分辨率/时长, 确认 reencode 生效输出 h264+AAC) ③ 码率合理性核查: 同批切片码率显著偏离
    均值(如 350s 仅 18MB≈400kbps vs 均值 2500kbps)时取帧或查源码率追查原因, 不默默放过
15. **耗时估算规则(2026-08-13 实测)**: 不要用单段瞬时 speed 外推总墙钟。
    实测 125min 视频 29 段 QSV 4 路并行(workers 4) 总墙钟 ≈16 分钟(首片 17:22:28 → 完成 17:38:21);
    ASR 转写按 ~12.4x 速率估算(125min ≈ 10min); 语义分段+专有名词确认含人工等待 ≈16 分钟;
    segmenter 单次运行仅数秒
16. **一轮确认制(2026-08-13 实施)**: 阶段2 首次输出必须打包三件事, 用户一次回复, 禁止逐轮小步迭代:
    ① 专有名词候选清单(运行 `python "<SCRIPT_DIR>/review_checklist.py"` 自动生成 → temp/review_checklist.txt,
    3-6字高置信区优先扫, 含口语字/2字词降为参考区; 字段 schema: segments.json=segments[],
    clips_refined.json=clips[] 含 review_priority/start_offset_ms/end_offset_ms) ② 完整段表
    (段号/标题/MMSS/分级/首句…尾句, 只需扫 [必须确认] 段) ③ 过渡句归属规则已固定"归后段", 不再讨论
17. **品牌名修正必须贯穿全部产物(2026-08-13 教训)**: 用户确认正确名后, 同步改
    segments.json / clips_refined.json / cut_results.json 的 title/label/note/boundary_hint,
    **并重命名输出目录与 mp4 文件名**(用 `python "<SCRIPT_DIR>/fix_brand_name.py" <输出日期目录>`, 自动备份 _bak_brand)。
    transcript.json 是 ASR 原文**不要改**(review_checklist 候选区保留原词供人工核对)。
    教训: 纪律8的对照表早已写明正确名, 执行却全程用了 ASR 的同音错字——正确名必须从对照表引用,
    生成标题前显式读取核对, 不凭 ASR 输出或记忆
18. **段起点由语义切点决定, 静音不得进入切片(2026-08-13 原则澄清)**: 切片按话题切分,
    段起点必须是"对观众的第一句"(跳过直播前杂音/对旁人说话/测试/环境声), 由阶段2语义切分
    在切分前就确定——起点应在话题内容上, 静音部分不应被切进来。ASR 首字可能是杂音句
    (#01 起点落在 0.57s 杂音句"还有点事儿…", 其后 71.7s 静音), 不能直接信任 ASR 句子起点。
    段起点后 10s 内出现 >5s 静音锚点可提示复核(ASR 锚点误报率高, 实测 7/29 触发仅 3 真, 只作提示)
19. **切点合法性验证(silencedetect, 2026-08-13 实测验证)**: 纪律18 的自动验证防线, **不是主要机制**。
    语义切分正确时起点自然落在内容上, 本检查只兜底防漏, 不代替语义切分:
    - 命令: `ffmpeg -i 切片 -af "silencedetect=noise=-20dB:d=1" -f null NUL`
      **阈值必须 -20dB**: 直播底噪约 -30dB, -35dB 会漏检静音(本次初测误判"无静音"即此原因)
    - 规则: 切片前10s 静音总时长 >6s → 标记 high_risk, 回到阶段2 重新定语义起点(-ss/-to QSV 重切)
     - 实测判别力: 拦 #01修复前(6.8s)/#03(6.3s)/#04(7.3s) 三处真问题, 不误报 #21 说话中停顿(4.7s);
       修复后各片前10s 静音 <1.2s
     - 重切后同步: 文件名时间码(MMSS) + cut_results.json 的 file/duration_s
     - 定位真实语音起点: **-20dB silencedetect 会把稀疏/低音量语音误判为连续静音**(08-12 #01
       显示 119.8-147.9s"28s 静音"实为语音), 不能用它反推起点; 改用
       `ffmpeg -ss N -t 1 -af volumedetect` 逐秒扫, 第一个 mean>-30dB 的秒即语音起点
       (实测 #01 起点 0.57s→117s、#07→3.9s, 修复后前10s 静音 7.4s/6.3s→5.9s/5.2s 且无
       >6s 连续段)。起点落在语音起音处无静音锚点 → segments.json 该段 `pin_start:true` 强制
20. **阶段4 交付验收 checklist(2026-08-13 复盘强制项)**: 切割完成、汇报"交付"之前**必须**顺序执行,
    不得等用户反馈才做:
    ① `ffprobe`/`Get-ChildItem` 确认片数齐全(与 cut_results.json results 数一致) + 抽查首/中/尾片
       ffprobe 时长, 与 cut_results 的 duration_s 对比(偏差 >0.5s 说明重切未覆盖)
    ② **纪律19 静音验证全片扫描**(前10s 静音>6s 标记): 这是强制交付项, 不是可选兜底。本轮
       01 片 116s 静音就是交付后用户反馈才修, 属流程漏执行
    ③ 检查 output/ 无残留旧版本目录(同视频多轮切割会并存, 交付前核对清理)
    ④ cut_results.json 与目录实际文件一一对应(旧文件已删、新文件已写)
21. **分析脚本写作纪律(2026-08-13 复盘)**: ① 常量单位显式写(把"前10s"写成 WIN=10000 秒而非 10ms,
    产出荒谬值 37-235s 未立即触发警觉, 应加 sanity check: 统计结果超出物理上界(如"前10s静音>10s")
    立即停手检查而非继续分析) ② 列表索引 1-based/0-based 混用(CLIP_START[idx] 取到别段起点,
    换算时间全错但切片内偏移仍有效, 未察觉) ③ 写解析代码前先 dump 一段真实数据确认结构
    (sentences 是 str 列表非 dict, 白跑一轮 TypeError) ④ 检测/分析前先读对应纪律, 不凭记忆
    (-35dB 漏检已在纪律19, 首次却仍用 -35dB 被底噪误导)
22. **标题落盘前强制对照已知修正表(2026-08-13 教训重演)**: 纪律#17 的教训在 08-12 任务重演——
    标题/文件名/专有名词清单全部用了 ASR 同音错字, 用户交付后反馈才修正。
    强制流程(阶段2 定稿前执行, 不可省略):
    ① 通读纪律#8 已知修正对照表(左侧 ASR 词列表)
    ② 对 segments.json 每个 title 逐条自查: 出现左侧任何词 → 立即改为右侧正确名
    ③ 定稿后运行 grep 兜底: `Select-String -Path temp/segments.json -Pattern "你的ASR错词1|你的ASR错词2" -SimpleMatch`,
       有命中即修正后重跑 segmenter, 不进入切割
    ④ 专有名词清单给用户确认时, 清单本身必须已用正确名(本次清单写错字是源头污染, 用户
       不可能从错误清单里纠错)
    (重命名交付物用 `python "<SCRIPT_DIR>/fix_brand_name.py" <输出日期目录>`, 见纪律#17)
23. **写脚本前强制 Read 目标 JSON 确认字段类型(2026-08-15 教训)**: 任何读 JSON 的 fix/verify 脚本,
    写代码前必须先 Read 或 dump 单条记录确认真实 schema, 禁止凭记忆假设字段类型。
    已踩坑: fix_names.py 假设 `title:str`, 实际 clips 用 `idx:int`(切片报 TypeError);
    verify_output.py 假设字段存在却引用未定义变量 `dur`(报 NameError)。二者都因未先读数据
    而白跑一轮全量。
24. **静音扫描与语音复核合并一步(2026-08-15 实测)**: 纪律19 的 silencedetect 会把稀疏/低音量
    语音误判为连续静音(直播底噪约 -30dB)。verify 脚本对触发"前10s静音>6s"的段**自动**接
    volumedetect 逐秒扫并输出"首语音秒"; 首语音秒 ≤4s 即判定误报直接放行, 不再人工一轮。
    本次 18/29 段标 high_risk, volumedetect 复核全部 ≤4s, 无一真问题。
25. **修正专有名词后用 Python UTF-8 列真实目录名核对(2026-08-15)**: 控制台/管道 GBK 乱码
    不可作为"专有名词是否落对"的判据(cut_pipeline 完成摘要全乱码, 直接看会误判)。
    用 `os.listdir` 写 UTF-8 文件再 Read 核对<正确品牌名>/<正确人名>等是否落进目录名。
26. **HMMSS 时间码 1 小时制易误读(2026-08-15 汇报习惯)**: 中段切片如 `5858-10419`、`15347-20027`,
    `10419`=1:04:19、`20027`=2:00:27 是紧凑 5 位格式的正常表示, 不是错误。
    汇报时间码时标注"时/分/秒"防止用户误读。
27. **两段式执行缓解一次性大脚本风险(2026-08-15)**: fix/verify 类脚本先 `n=1` 小样本试跑
    (或加 `--dry`), 确认无变量名/字段错误后再全量, 单轮省 1 次失败重跑。
    与纪律21①/② 同源, 写脚本时一并遵守。
28. **阶段5 发布合规检查必跑, 交付必须带报告与建议(2026-10-29 新增)**: 切片交付 ≠ 能发布。
    口播原声直发的切片按静态存证审核, 比直播实时流更严; 同句风险话术还会被切片号矩阵复制 N 份。
    强制流程(阶段4 切割后、交付给用户前, 不可省略):
    ① `python <SCRIPT_DIR>/compliance_scan.py`(先 `--limit 3` 试跑, 纪律27)
       → `temp/compliance_report.md`(人读) + `temp/compliance_report.json`(结构化, 含 red_ranges/mute_ranges)
    ② 报告给出每片 等级(RED/YELLOW/GREEN) + 动作(DROP/CUT/MUTE/RETITLE/CONSENT/OK) + 待处理区间时间码;
       **AI 必须再补三项脚本判不了的**: 命中风险词的标题给改后版本 / 涉他人肖像隐私的片提示取授权 /
       功效宣称(美白·减肥·疗效)待核资质
    ③ 交付时先给结论速览(RED/YELLOW/GREEN 各几片), 再给 RED 逐片处置, 最后给 GREEN 可直接发布清单;
       两份报告文件用 present_files 一并交付 —— **只给 mp4 列表不给报告 = 未交付**
    ④ 误报要回流词表: 出现谐音/否定式("不骗人")/ASR 错字导致的误判, 改 `scripts/compliance_rules.json`
       而不是每次人工覆盖; 漏报同理补词。**词表是资产, 每次收尾花 30 秒维护**
    ⑤ 口径声明不可省: 报告需说明这是"按平台通行审核口径的风险预判, 不是平台审核结果",
       RED 不必然下架、GREEN 不必然安全, 最终以人工复核与实际发布为准

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

