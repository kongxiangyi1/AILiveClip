# 后期优化清单（stream_copy 切割）

> 日期: 2026-08-12
> 来源: `docs/stream_copy-optimization.md` 实施后复验（2026-08-12，34 片真实素材 + 合成视频冒烟全过）
> 性质: 非阻塞项，不影响当前功能正确性，按优先级排期处理

## 优先级说明
- P1: 建议尽快处理（成本低收益明显）
- P2: 涉及真实数据场景，先观察再处理
- P3: 探索性优化，视业务需要

---

## P1. stream_copy 切割时 ffmpeg stderr 未捕获（终端刷屏）

- **位置**: `cut_pipeline.py:191`（stream_copy 分支 `subprocess.run(args + ["-y", output_path], check=True)` 未加 `capture_output=True`）
- **现象**: 每片切割时 ffmpeg 版本 banner、流信息、进度条全部打到终端，34 片刷屏严重，掩盖管道自己的进度输出
- **现状**: reencode 分支（cut_pipeline.py:211）同样未捕获，行为一致；功能不受影响
- **建议**: 两分支统一 `capture_output=True`，失败时（CalledProcessError）打印 stderr 尾部 300 字便于排查
- **成本**: 改 2 处，约 5 分钟

## P1. `remux_to_mp4` 复用缓存文件时不校验 hvc1 tag

- **位置**: `cut_pipeline.py:140`（`if not os.path.exists(out)` 直接复用）
- **现象**: 若 `temp/{名}_{hash}_remux.mp4` 已存在（旧版本转封装未打 hvc1 tag，或源 video_path 相同但 codec 探测结果变化的极端场景），复用文件可能缺 `-tag:v hvc1`
- **风险**: HEVC 源切出的 mp4 播放器/剪映兼容性下降；MP4 源不受影响（不走此函数）
- **建议**: 缓存键纳入 codec（如 `{name}_{hash}_{codec}_remux.mp4`），或复用前 ffprobe 校验 codec_tag，缺失则重转
- **成本**: 改 3~5 行

## P2. B 帧漂移：实际起点可能比吸附 IDR 再提前 ≤1 GOP

- **来源**: `docs/stream_copy-optimization.md` 第 6 节
- **现象**: `-ss` 前置按 **DTS** 定位 IDR，与显示 pts 列表存在偏差；实测切片 0 吸附 96.627s，实际起点 95.574s（漂移 1.05s）
- **影响**: `cut_results.json` 的 `start_offset_s` 记录的是"吸附点−切点"，为误差下界；实际提前量 ≤ offset + 1 GOP
- **缓解（已实现）**: `--max-snap-s` 阈值兜底（offset>3s 降级 reencode）；MP4 源 GOP 1.2s 时漂移 ≤ ~2.3s，可接受
- **后续方向（如需精确到帧）**:
  1. 收紧阈值（如 `--max-snap-s 1.5`）——零成本，但 TS 长 GOP 源降级率上升
  2. 对 offset+漂移 超标的切片做"首尾微重编码"（smart trim：仅对关键帧→切点的小段重编码拼接），实现复杂度高，见 P3

## P2. TS 源长 GOP（8.5s）导致约 1/3 切片降级

- **来源**: `docs/stream_copy-optimization.md` 第 6 节
- **现象**: TS 源实测 GOP 8.5s，吸附误差可达 8.5s；`--max-snap-s 3` 下约 1/3 切片自动降级 reencode
- **影响**: TS 源整体仍快于全量 reencode（约 3x），但不如 MP4 源（190x）明显
- **后续方向**:
  1. 建议推流端固定 GOP（`-g 30` ≈ 1s），从源头解决——最有效但依赖外部
  2. TS 转封装后若 GOP 仍大，可考虑对该源的 `--max-snap-s` 单独调参（如 5~8s，接受吸附误差换速度）
  3. 在 `cut_results.json` 中增加 GOP 统计字段，便于按源决策

## P3. 精确吸附探测：copy 起点二分探测

- **现象**: ffmpeg copy 模式实际起点由 DTS 定位决定，无法从 `-show_packets`（显示 pts）精确预测
- **思路**: 对每个切点用 `-ss X -c copy -f null` 类命令探测真实起点（约 0.2s/片），可得到精确 offset 写入 cut_results.json
- **权衡**: 34 片增加 ~7s 开销（切割本身仅 7.6s，翻倍），仅当"精确 offset 记录"成为硬需求时再做

## P3. 回归验证脚本固化

- **现状**: 验证脚本位于 `temp/_kf_verify/`（r5_final.py 全量 34 片、smoke_impl.py 合成冒烟），随 temp 目录易被清理
- **建议**: 提取为 `tests/test_cut_pipeline.py`（合成视频冒烟部分已具备断言），纳入常规回归；r5_final.py 保留为真实素材验收脚本
- **注意**: 真实素材路径（`E:\Clipping\剪影\视频号\录制\...`）在测试中需参数化或跳过

## P3. 并行切割时磁盘 IO 争抢

- **现象**: 34 片 stream_copy 总耗时 7.6s（workers=4），主要瓶颈是 4 个进程同时读同一 1.9h 源文件 + 写 output
- **方向**: 单片耗时已低至 0.2s，意义不大；若未来切片数量暴增（数百片），可考虑按输出顺序串行分片写入或缓冲
- **结论**: 当前规模无需处理

---

## 记录方式约定

- 每项含：位置/现象/风险/建议/成本
- 修复后从本清单移除，并在 `docs/stream_copy-optimization.md` 第 5 节实施清单中打勾
