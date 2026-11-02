# -*- coding: utf-8 -*-
"""全局切片注册表: 跨视频 / 跨切片号(账号) 的切片号去重与发布协调。

场景(对应 L3 多切片号矩阵):
  某主播的直播授权给多个「切片号」(视频号账号/矩阵号)各自切片发布。
  多个号发同一场同一片段 → 画面+音频高度重合 → 视频号判「高度相似」限流。
  本注册表用于跨号协调: 同一片段只放一个号发, 跨号相邻片段提示需差异化改编。

设计:
  1. 每个片段全局唯一 id(C0001...), 跨视频/跨号不撞号。
  2. 同一片段(clip_key=视频+起止+标题) 被任意号登记都复用同一 id(片段粒度唯一),
     并在 rec["accounts"] 记录哪些号登记过; 多号争用标 multi_account=True。
  3. 跨号同视频、时间区间重叠>50% 的片段 → overlap_with 软提示(画面可能相似, 需内容差异化)。
  4. 发布状态: pending / published / rejected / archived; 同片段已被某号 published 后,
     其他号再发即冲突(用 account_conflicts() 查出)。
  5. account_conflicts() 返回"被多号争用 / 已发仍多号登记"的片段, 发布前拦截预警。

用法:
  reg = ClipRegistry()
  gid, is_new = reg.register(video_path, seq, title, start_ms, end_ms, clip_dir, account="号A")
  reg.set_status(gid, "published", account="号A", post_id="media_x")
  conflicts = reg.account_conflicts()   # 被多号争用/已发的片段(高相似限流风险)
"""
import hashlib
import json
import os
from collections import Counter
from datetime import datetime


def _norm_path(p):
    return os.path.normpath(p).replace("\\", "/")


def video_key(video_path):
    """视频短码(md5 前 6 位), 用于跨视频区分与软去重分组。"""
    return hashlib.md5(_norm_path(video_path).encode("utf-8", "replace")).hexdigest()[:6]


def clip_key(video_path, start_ms, end_ms, title):
    """片段内容指纹: 同视频+同时间段+同标题 => 同一片段(幂等去重依据)。"""
    s = f"{_norm_path(video_path)}|{int(start_ms)}|{int(end_ms)}|{title.strip()}"
    return hashlib.md5(s.encode("utf-8", "replace")).hexdigest()[:12]


def _fmt_time_range(start_ms, end_ms):
    def tc(ms):
        t = int(ms // 1000)
        h, m, s = t // 3600, (t % 3600) // 60, t % 60
        return f"{h}{m:02d}{s:02d}" if h else f"{m:02d}{s:02d}"
    return f"{tc(start_ms)}-{tc(end_ms)}"


def _overlap_ratio(a0, a1, b0, b1):
    """两时间区间交并比(0~1)。"""
    inter = max(0, min(a1, b1) - max(a0, b0))
    if inter <= 0:
        return 0.0
    union = max(a1, b1) - min(a0, b0)
    return inter / union if union > 0 else 0.0


class ClipRegistry:
    def __init__(self, path=None):
        self.path = path or os.environ.get("CLIP_REGISTRY_PATH")
        if not self.path:
            work_root = os.environ.get("AI_LIVE_CLIP_WORKDIR") or os.getcwd()
            self.path = os.path.join(work_root, "output", "clip_registry.json")
        self.data = self._load()

    def _load(self):
        if os.path.exists(self.path):
            try:
                with open(self.path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                d.setdefault("version", 1)
                d.setdefault("last_id", 0)
                d.setdefault("clips", [])
                return d
            except (OSError, ValueError):
                pass
        return {"version": 1, "last_id": 0, "clips": []}

    def save(self):
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=2)

    def _next_id(self):
        self.data["last_id"] += 1
        return f"C{self.data['last_id']:04d}"

    def register(self, video_path, seq_in_video, title, start_ms, end_ms, clip_dir, account=None):
        """登记/更新一个切片(支持跨切片号协调)。

        返回 (global_id, is_new):
          - is_new=True  → 分配了新全局 id(新片段)
          - is_new=False → clip_key 已存在, 复用原 id 并刷新路径(片段粒度幂等去重)
            - 若登记的 account 与已有不同 → 追加到 rec["accounts"], 多号争用标 multi_account
        """
        now = datetime.now().isoformat(timespec="seconds")
        key = clip_key(video_path, start_ms, end_ms, title)
        vkey = video_key(video_path)
        norm_title = title.strip()
        acc = (account or "").strip() or None
        # 1) 同片段(任意号) → 复用 id, 追加 account
        for c in self.data["clips"]:
            if c.get("clip_key") == key:
                c["clip_dir"] = clip_dir
                c["seq_in_video"] = seq_in_video
                c["updated_at"] = now
                if acc:
                    c.setdefault("accounts", [])
                    if acc not in c["accounts"]:
                        c["accounts"].append(acc)
                        if len(c["accounts"]) > 1:
                            c["multi_account"] = True
                elif not c.get("accounts"):
                    c["account"] = c.get("account")
                return c["global_id"], False
        # 2) 新片段
        gid = self._next_id()
        # 跨视频同标题 soft 提示(不同视频确属不同切片, 不阻止)
        title_dup = None
        for c in self.data["clips"]:
            if c.get("video_key") != vkey and c.get("title", "").strip() == norm_title:
                title_dup = c["global_id"]
                break
        # 跨号同视频、相邻片段重叠>50% → 画面可能相似, 需内容差异化改编
        overlap_with = []
        for c in self.data["clips"]:
            ca = c.get("account")
            if c.get("video_key") == vkey and ca and ca != acc:
                if _overlap_ratio(start_ms, end_ms, c["start_ms"], c["end_ms"]) > 0.5:
                    overlap_with.append(c["global_id"])
        rec = {
            "global_id": gid,
            "video_path": _norm_path(video_path),
            "video_key": vkey,
            "seq_in_video": seq_in_video,
            "title": title,
            "start_ms": int(start_ms),
            "end_ms": int(end_ms),
            "time_range": _fmt_time_range(start_ms, end_ms),
            "clip_dir": clip_dir,
            "clip_key": key,
            "account": acc,
            "accounts": [acc] if acc else [],
            "multi_account": False,
            "title_dup_with": title_dup,
            "overlap_with": overlap_with or None,
            "status": "pending",
            "published_at": None,
            "platform": "视频号",
            "post_id": None,
            "created_at": now,
            "updated_at": now,
        }
        self.data["clips"].append(rec)
        return gid, True

    def set_status(self, global_id, status, account=None, **extra):
        for c in self.data["clips"]:
            if c["global_id"] == global_id:
                c["status"] = status
                c["updated_at"] = datetime.now().isoformat(timespec="seconds")
                if account:
                    acc = (account or "").strip()
                    if acc:
                        c["account"] = acc
                        c.setdefault("accounts", [])
                        if acc not in c["accounts"]:
                            c["accounts"].append(acc)
                            if len(c["accounts"]) > 1:
                                c["multi_account"] = True
                if status == "published":
                    c["published_at"] = c["updated_at"]
                for k, v in extra.items():
                    c[k] = v
                return True
        return False

    def account_conflicts(self):
        """高相似限流风险片段: 被多个切片号争用, 或已发布仍被多号登记。"""
        out = []
        for c in self.data["clips"]:
            if c.get("multi_account") or (c.get("status") == "published" and len(c.get("accounts", [])) > 1):
                out.append(c)
        return out

    def query(self, status=None, video_key=None, account=None):
        out = []
        for c in self.data["clips"]:
            if status and c.get("status") != status:
                continue
            if video_key and c.get("video_key") != video_key:
                continue
            if account and account not in (c.get("accounts") or []):
                continue
            out.append(c)
        return out

    def stats(self):
        cnt = Counter(c.get("status", "?") for c in self.data["clips"])
        acc = Counter()
        for c in self.data["clips"]:
            for a in (c.get("accounts") or []):
                acc[a] += 1
        return {"total": len(self.data["clips"]),
                "by_status": dict(cnt),
                "by_account": dict(acc)}


if __name__ == "__main__":
    reg = ClipRegistry()
    st = reg.stats()
    print(f"注册表: {reg.path}")
    print(f"总切片: {st['total']}  状态分布: {st['by_status']}  各切片号登记数: {st['by_account']}")
