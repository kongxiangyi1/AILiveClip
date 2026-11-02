# -*- coding: utf-8 -*-
"""补登已有切片到全局注册表(新流程会自动登记, 本脚本用于迁移历史切片)。

扫描 {工作根}/output 下所有 cut_results.json, 解析每个切片的时间区间与标题,
登记到 clip_registry(同 clip_key 自动去重, 不重复编号)。

用法: python update_registry.py [--registry PATH] [--dry-run]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clip_registry import ClipRegistry


def _parse_time_range(tr):
    """'HMMSS-HMMSS' -> (start_ms, end_ms)"""
    m = re.match(r"^(\d+)-(\d+)$", tr.strip())
    if not m:
        return None, None

    def to_ms(s):
        h = int(s[:-4]) if len(s) > 4 else 0
        mm = int(s[-4:-2])
        ss = int(s[-2:])
        return (h * 3600 + mm * 60 + ss) * 1000

    try:
        return to_ms(m.group(1)), to_ms(m.group(2))
    except ValueError:
        return None, None


def main():
    p = argparse.ArgumentParser(description="历史切片补登到全局注册表")
    p.add_argument("--registry", help="注册表路径(覆盖默认/环境变量)")
    p.add_argument("--dry-run", action="store_true", help="只统计不写入")
    args = p.parse_args()

    reg = ClipRegistry(args.registry)
    work_root = os.environ.get("AI_LIVE_CLIP_WORKDIR") or os.getcwd()
    output_dir = os.path.join(work_root, "output")

    if not os.path.isdir(output_dir):
        print(f"未找到 output 目录: {output_dir}")
        return

    new_cnt = reuse_cnt = 0
    scanned = 0
    for root, _dirs, files in os.walk(output_dir):
        if "cut_results.json" not in files:
            continue
        scanned += 1
        with open(os.path.join(root, "cut_results.json"), "r", encoding="utf-8") as f:
            data = json.load(f)
        video_path = data.get("video_path")
        if not video_path:
            continue
        for r in data.get("results", []):
            fname = os.path.basename(r.get("file", ""))
            m = re.search(r"_(\d+-\d+)\.mp4$", fname)
            if not m:
                continue
            start_ms, end_ms = _parse_time_range(m.group(1))
            if start_ms is None:
                continue
            if args.dry_run:
                new_cnt += 1
                continue
            _, is_new = reg.register(video_path, r.get("index", 0), r.get("title", ""),
                                     start_ms, end_ms, os.path.dirname(r["file"]))
            if is_new:
                new_cnt += 1
            else:
                reuse_cnt += 1

    if args.dry_run:
        print(f"[dry-run] 扫描到 {scanned} 个 cut_results.json, 预计登记 {new_cnt} 条")
        return

    reg.save()
    print(f"补登完成: 扫描 {scanned} 个结果文件, 新增 {new_cnt} / 复用 {reuse_cnt} → {reg.path}")


if __name__ == "__main__":
    main()
