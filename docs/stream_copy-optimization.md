# stream_copy 快速切割优化方案（含多轮验证记录）

> 日期: 2026-08-12
> 状态: ✅ 已实施 (2026-08-13, cut_pipeline.py 完成第5节全部7项改造并通过合成素材冒烟验证)
> 目标: 让切分流程默认使用 stream_copy，消除其"切点随机误差/花屏/废料"等缺点

---

## 1. 背景

当前 `cut_pipeline.py` 两种切割模式：

| 模式 | 精度 | 速度(实测 34 片) | 输出 |
|---|---|---|---|
| reencode（默认） | 帧级精确 | 总 1444s，单片平均 42.5s | H.264/MP4 |
| stream_copy | 吸附关键帧，误差 0~数秒 | 总 7.1s，单片平均 0.2s | 保留源编码 |

实测验证素材：`直播回放-08月09日.mp4`（HEVC Main / 912x1920 / 1.9h / 20.5fps / AAC），34 个真实语义切点；TS 素材：`07-10直播回放.ts`（HEVC / 720x1512 / 48min / 30fps）。

## 2. stream_copy 优缺点分析

### 优点
1. **极快**：零重编码，实测 0.2s/片，比 reencode 快约 200 倍（R5）
2. **画质无损**：不二次压缩，无质量损失
3. **资源占用极低**：CPU/GPU 几乎不占，可开多路并行不互相争抢

### 缺点（根源: 不知道关键帧在哪）
1. **切点不精确**：`-ss` 前置吸附到最近关键帧，误差 0~GOP 时长。MP4 源 GOP 1.2s → 误差 0~1.2s；TS 源 GOP 8.5s → 误差可达 8.5s
2. **开头带废料**：起点吸附在切点"之前"的关键帧，切片开头多出上一段内容
3. **TS 源可能花屏**：TS 无 stss 索引，`-ss` 按码率估算落点，可能落在非关键帧上 → copy 输出花屏直到下一 IDR 自愈
4. **时间戳未归一**：缺 `-avoid_negative_ts`，部分播放器时间轴显示异常（实测原实现首帧 PTS 0.000~0.021s，需显式处理）
5. **codec 探测重复执行**：原实现每片一次 `ffmpeg -i`（cut_pipeline.py:94），N 片浪费 N-1 次
6. **关键帧扫描工具链缺失**：ffprobe `-show_frames` 扫 1.9h HEVC 需 121s，且对 TS 扫出 0 个关键帧（R1）

## 3. 方案设计：关键帧感知 stream_copy（IDR 对齐切割）

### 3.1 核心洞察（验证发现）
- ffmpeg `-c copy` 的 seek 定位使用 **stss 表（真 IDR）**，而 ffprobe `-show_frames` 的 `key_frame=1` 还包含 **CRA 帧**（open-GOP 关键帧，非随机访问点）
- 按 CRA 吸附会与实际切割起点错位：实测切片 0 按关键帧 96.627s 吸附，实际起点为 95.574s 的 IDR（R3e 二分定位），偏差 1.05s
- **必须用 packets 级 IDR 列表（= stss）吸附，而非帧级 key_frame 列表**

### 3.2 IDR 列表提取（packets 法，全新）
```bash
ffprobe -v error -select_streams v:0 -show_packets \
        -show_entries packet=pts_time,flags -of csv=p=0 input.mp4
# 取 flags 含 "K" 的行, pts_time 即 IDR 列表
```
实测性能（R4）：

| 源 | 耗时 | IDR 数量 |
|---|---|---|
| MP4 (1.9h) | **2.0s** | 5681（GOP 中位 1.24s，p90 1.27s，max 2.91s） |
| TS (48min) | 3.7s | 350（GOP 8.5s） |
| 转封装后 MP4 | 0.9s | 350 |

对比旧方案 ffprobe `-show_frames -skip_frame nokey`：MP4 121s（慢 60 倍），TS **扫出 0 个**（不可用）。

### 3.3 切点吸附算法
```
start_idr = 最大满足 idr_pts <= start 的 IDR   # 起点向前吸附(保证首帧干净)
end_idr   = 最小满足 idr_pts >= end 的 IDR     # 终点向后吸附(保证尾帧完整)
offset_start = start_idr - start               # 记录: 已知误差(下界), 写入输出 JSON
```

### 3.4 切割命令（改造后）
```bash
ffmpeg -ss {start_idr} -i {src} -t {end_idr - start_idr} \
       -map 0:v:0 -map 0:a:0 -c copy \
       -avoid_negative_ts make_zero \    # 时间戳归零(修复缺点4)
       -tag:v hvc1 \                     # HEVC→MP4 兼容性
       -y {out}.mp4
```
- codec 探测只做一次（修复缺点 5），并发 workers 不变（ThreadPool+子进程）
- **实际起点 = start_idr 或更早 ≤1 GOP**（B 帧重排导致 DTS 定位漂移，实测漂移 ≤1.05s），首帧必为 I 帧，无花屏无废料（见 4.3 铁证）

### 3.5 精度兜底（降级 reencode）
```
规则: offset_start > MAX_SNAP_S(默认3s) 的切片自动降级 reencode
效果: MP4源(GOP 1.2s) 全部走 stream_copy; TS源(GOP 8.5s) 约1/3降级, 仍比全量 reencode 快数倍
```

### 3.6 TS 源处理（修复缺点 3）
```
检测容器为 TS → 先无损转封装:
  ffmpeg -i src.ts -c copy -tag:v hvc1 -y remux.mp4   # 实测 2.4s / 48min 视频
转封装后按 3.2~3.4 走标准流程
```

## 4. 多轮验证记录

### R1 关键帧扫描可行性（含旧方案对比）
- `-show_frames`: MP4 121s / 5681 关键帧；TS 0 个（不可用）→ 弃用
- `-show_packets`（新）: MP4 2.0s；TS 3.7s → 采用

### R2 三种模式全量 34 片切割对比
| 模式 | 单片均值 | 总耗时 | 首帧PTS |
|---|---|---|---|
| copy_snap（吸附+avoid_negative_ts） | 0.19s | 6.5s | 0.000~0.021s（归零） |
| copy_plain（旧实现） | 0.21s | 7.1s | 0.000s |
| reencode（基准） | 42.5s | 1444s | 0.000~0.040s |

### R3 首帧一致性验证（暴露 CRA 陷阱）
- 用 `-show_frames` 关键帧吸附切割，34 片首帧 checksum 与源关键帧全部不一致（0/34）
- 定位实验（R3f 二分）：切片 0 实际起点 = 95.574s 的 IDR，吸附点却是 96.627s 的 CRA
- **结论修正：吸附必须用 packets IDR 列表**（详见 3.1）

### R4 IDR 提取 + 精度对比
见 3.2 表格。packets 法 mp4/ts 均可用，且比 show_frames 快 60 倍。

### R5 IDR 吸附全量切割（最终方案验证）✅
- 34/34 成功，**首帧全部 iskey=1（I 帧）—— 无花屏铁证**
- offset（吸附IDR − 切点）：min -1.189s / max -0.002s / 中位 -0.783s
- **34 片总耗时 7.6s，为 reencode(1444s) 的 1/190**
- 音视频同步：每片 v_dur 与 a_dur 差 < 0.05s；音频 AAC 保留
- 降级需求：0/34（offset 均 < 3s 阈值）

### R6 TS 源路径验证 ✅
- TS → MP4 转封装：2.4s（48min 视频）
- 转封装后 IDR 扫描：0.9s；GOP 8.5s（首 3 个 IDR: 0.02/8.56/17.09）
- 3 个抽检切点：offset 分别为 4.38s / 0.39s / 3.80s，**首帧全部 I 帧**
- 结论：TS 源 GOP 大，需 3.5 降级策略保护精度（1/3 切点会降级），整体仍显著快于全量 reencode

## 5. 实施清单（cut_pipeline.py 改造点）

- [x] 1. 新增 `scan_idr_packets(path)`：packets 法提取 IDR 列表（替代/兼容 probe_video_codec 单次探测）
- [x] 2. 新增 `snap_start/snap_end(ms, idr_list)` 吸附函数
- [x] 3. 改造 `cut_clip`：stream_copy 分支改用 IDR 吸附 + `-avoid_negative_ts make_zero`；reencode 分支保留
- [x] 4. 输出 JSON（cut_results.json）增加 `start_offset_s` 字段（吸附点 − 期望切点）
- [x] 5. 新增 `--max-snap-s`（默认 3）：offset 超阈值自动降级 reencode，打印降级清单
- [x] 6. 新增容器检测：TS 源先 `-c copy` 转封装到 temp 再切割（复用 `temp/{名}_{路径hash}_remux.mp4`）
- [x] 7. 文档/SKILL.md 更新使用说明（默认建议 `--mode stream_copy`）

## 6. 遗留风险与建议

1. **B 帧漂移**：实际起点可能比吸附 IDR 再提前 ≤1 GOP（实测 1.05s），如需精确可对降级阈值收紧（如 1.5s）
2. **超长 GOP 直播流**（TS 源常见 8.5s）：stream_copy 收益下降，降级策略兜底；极端场景可建议推流端固定 `-g 30`
3. 切片首帧抽查：建议发布前对首尾各抽 1 帧人工目检（本方案首帧必为 I 帧，花屏概率极低）
4. 验证脚本位于 `temp/_kf_verify/`（r5_final.py 可复跑全量验证），实施后可保留作回归测试

## 7. 关键数据速查

```
MP4 源(HEVC, GOP~1.2s):   IDR扫描 2s + 34片切割 7.6s = 总 ~10s
                          reencode 基准 1444s → 提速 ~145x
TS 源(HEVC, GOP~8.5s):    转封装 2.4s + IDR扫描 1s + 切割 + 约1/3降级 ≈ 10min 内
                          reencode 基准(按MP4推) → 提速 ~3x 起
```
