# 切片号去重方案 — L3 全局发布管理（注册表）

## 背景
原 `cut_pipeline.py` 用 segments 列表顺序号 `01/02…` 作为切片号（文件名 `{序号}_{标题}_{时间码}`），
**仅在单个 `output/{日期_原视频名}/` 目录内唯一**。

**关键场景（多切片号矩阵）**：某主播的直播授权给多个「切片号」（视频号账号/矩阵号）
各自切片发布 → 多个号发同一场同一片段，画面+音频高度重合 → 视频号判「高度相似」限流。
注册表因此扩展 `account`（切片号）维度，做**跨号协调**：同一片段只放一个号发，跨号相邻片段提示需差异化。

## 方案
新增**全局注册表 `clip_registry.json`**，给每个切片分配全局唯一 id（`C0001…`），跨视频/跨号不撞号；
同视频同一片段（视频+时间段+标题 指纹一致）重跑复用 id（幂等去重）；跨号争用与相邻重叠做预警。

## 文件清单
| 文件 | 作用 |
|---|---|
| `scripts/clip_registry.py` | 注册表模块（`ClipRegistry`：`register` / `set_status` / `query` / `account_conflicts` / `stats`） |
| `scripts/clip_status.py` | 查询 / 跨号冲突预警 / 标记状态 / 导出发布表 |
| `scripts/update_registry.py` | 历史切片补登到注册表 |
| `scripts/cut_pipeline.py` | 已集成：切割后自动登记（新增 `--account` / `--no-registry`） |

## 去重与跨号协调规则
- `clip_key = md5(归一化视频路径 | start_ms | end_ms | 标题)[:12]`：片段粒度指纹
- **同 `clip_key`（任意号）→ 复用同一 `global_id`**（片段唯一，不重复编号），并把登记的 `account` 累加进 `rec["accounts"]`
- **多号争用**：`rec["accounts"]` 含 ≥2 个号 → `multi_account=True`；该片段被多个号发即「高度相似」限流风险
- **跨号相邻重叠**：同视频、不同号、时间区间交并比 `>0.5` 的片段 → `overlap_with` 软提示（画面可能相似，需内容差异化改编）
- **跨视频同标题**：`title_dup_with` 软提示（不同视频确属不同切片，不阻止登记）
- `account_conflicts()`：返回被多号争用 / 已发布仍多号登记的片段，发布前拦截预警
- 全局 id 自增 `C0001, C0002, …`，跨所有视频/账号唯一

## 用法
```bash
# 1) 切割时带上切片号(账号)自动登记（推荐每个号用固定 --account）
python cut_pipeline.py segments.json --mode stream_copy --account 号A
#    → 终端打印 "注册表: 新增 N / 复用 M (切片号=号A) → <path>"

# 2) 查看全部 / 按状态 / 按视频 / 按切片号
python clip_status.py
python clip_status.py --status pending
python clip_status.py --video-key a1b2c3
python clip_status.py --account 号A

# 3) 跨号冲突预警（高相似限流风险片段，发布前必查）
python clip_status.py --conflicts
python clip_status.py --conflicts --export-csv conflicts.csv

# 4) 标记发布状态（带 --account 记录是哪个号发的）
python clip_status.py --publish C0003 --post-id media_xxx --account 号A
python clip_status.py --reject  C0004

# 5) 导出发布管理表
python clip_status.py --export-md  publish.md
python clip_status.py --export-csv publish.csv

# 6) 历史切片补登
python update_registry.py --dry-run
python update_registry.py
```

## 注册表位置
默认 `{AI_LIVE_CLIP_WORKDIR 或 cwd}/output/clip_registry.json`；
可用环境变量 `CLIP_REGISTRY_PATH` 覆盖（**多个号共享同一张表时，把 CLIP_REGISTRY_PATH 指向同一个绝对路径**，
才能跨号检测到争用/重叠）。

## 与现有流程的集成点（cut_pipeline.py）
- L28-29 `from clip_registry import ClipRegistry`
- `run(segments_path, …, account=None)`：`register(..., account=account)` 写回 `r["global_id"]`
- `cut_results.json` 的每条结果新增 `global_id` 字段
- argparse 新增 `--account`（切片号标识）/ `--no-registry`（跳过写表）

## 仍待办（治本）
- **L2 画面/音频指纹级去重**：当前 `overlap_with` 仅用时间区间交并比代理画面相似。要真对齐平台判定，
  需对切片抽关键帧 pHash + 音频指纹，切片两两算相似度 `>0.85` 发布前拦截（需装 opencv/numpy 等到隔离 venv）。
- 平台合规路径：原创作者可在「创作者中心-账号管理-原创保护记录」给各切片号授权。
