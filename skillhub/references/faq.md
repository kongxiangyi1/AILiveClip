# 常见问题 FAQ(集中问答页)

> 详细背景分散在 discipline.md / stages.md / funasr-install.md, 本页只做**按症状检索**的入口。

## 安装与依赖

**Q: pip install 很慢/超时?**
换镜像: `pip install -i https://mirrors.cloud.tencent.com/pypi/simple funasr imageio-ffmpeg cryptography`(镜像速度排序见 funasr-install.md)。

**Q: 首次 probe 报 cryptography 缺失?**
`pip install cryptography`。它是授权验签必装项, 缺了闸口**明确报错**, 不会静默放行。

**Q: Windows 沙箱里 pip/uv 装不上, 提 jieba/oss2 构建失败?**
不要用 `uv`。先 `python -m ensurepip` 引导出 pip, 再前台 `pip install`; 且**不要**加 `--only-binary :all:`(oss2 只有 sdist)。详见 funasr-install.md。

**Q: 转写时报缺 AAC 解码器 / 无 WAV muxer?**
你 PATH 里的 ffmpeg 是精简编译版。脚本已通过 `imageio_ffmpeg.get_ffmpeg_exe()` 自动用完整版; 若仍报警, `pip install -U imageio-ffmpeg` 升级, 或跑 `doctor.py` 自检确认。

**Q: 模型缓存下载太慢?**
FunASR 模型约 1-2GB, 首次必下; 镜像见 funasr-install.md。装没装对先跑 `python "<SCRIPT_DIR>/doctor.py"`。

## 付费与授权

**Q: shell 里退出码 146 是什么?**
即 402(待支付): `shell 显示 = code & 0xFF`, 402 → 146。按 SKILL.md 退出码判据处理。

**Q: 已激活为什么还出 402 账单?**
授权文件 `~/.ai-live-clip/license.json` 丢失或换机未迁移才会出账。先跑 `python "<SCRIPT_DIR>/payment_gate.py" license` 核对;换机不走重复付款, 见 payment-buyer.md《换机 / 重装》。

**Q: 付款后 complete 报凭证无效?**
先用 `status --out-trade-no` 查订单状态; **结果不明不要重复付款**。

## 转写与切分

**Q: TS + HEVC 源能走 stream_copy 快切吗?**
不能。IDR 间隔约 10s, 流复制会花屏/黑屏, 直接走精确 reencode(见 SKILL.md 步骤 5 兜底模式)。

**Q: Non-monotonic DTS 大片警告?**
忽略(纪律#13)。以 cut_results.json 成功与否为准。

**Q: 段切错位置/错误边界?**
若 AI 边界正确但吸附规则会吸歪, 用 `pin_start`/`pin_end` 钉住(data-formats.md); 若语义本身错了, 回阶段2改 segments.json。

**Q: confidence=low 的段怎么处理?**
按 review_priority 分级复核(discipline.md 判定表); 不是所有 low 都需要人工逐个看。

## 切割与交付

**Q: 输出只有 mp4 没有封面?**
正常——本工具是粗切片器, 不生成封面, 供人工精剪。

**Q: 文件名里的 %、&、引号在某些平台报错?**
敏感字符清洗目前需手动(rename 脚本尚未进包), 见 README《已知限制》; 规避: 切割前改 segments.json 的 title。

**Q: 交付时要给什么才算完成?**
mp4 + cut_results.json + **compliance_report.md/.json**(缺报告=未交付, 纪律#28), 外加 AI 补的改标题/取授权/功效核验三项建议。

## 卸载与排障总原则

**Q: 脚本报"❌ 原因 + 下一步"还不够, 想看完整堆栈?**
设环境变量 `$env:AI_CLIP_DEBUG = "1"` 后重跑同一命令, 会打印完整 traceback(segmenter / cut_pipeline / compliance_scan / mute_profanity 等入口已统一包装, 不再裸抛)；根因不明时把这页堆栈反馈作者。

## 合规

**Q: 合规扫描误报(谐音/否定式/ASR 错字)?**
改 `scripts/compliance_rules.json` 词表回流, 而不是每次人工覆盖(纪律#28④); 漏报同理补词。

**Q: 报告里的黄色(YELLOW)片段怎么处置?**
按类别: 粗口→mute_profanity 静音; 极限词→改标题; 行业指控→改标题; 隐私→取授权。见 compliance-rules.md。
