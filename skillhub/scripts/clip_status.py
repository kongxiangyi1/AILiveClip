# -*- coding: utf-8 -*-
"""切片发布管理: 查询注册表 / 跨号冲突预警 / 标记状态 / 导出发布表。

用法:
  python clip_status.py                         列出全部切片(表格)
  python clip_status.py --status pending        只看待发布
  python clip_status.py --video-key a1b2c3      只看某视频
  python clip_status.py --account 号A           只看某切片号登记的片段
  python clip_status.py --conflicts             查看被多号争用/已发的冲突片段(高相似限流风险)
  python clip_status.py --publish C0003 --post-id media_x --account 号A   标记某号已发布
  python clip_status.py --reject C0004          标记不发布
  python clip_status.py --export-md publish.md  导出 Markdown 发布表
  python clip_status.py --export-csv publish.csv 导出 CSV 发布表
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clip_registry import ClipRegistry


def print_table(clips):
    if not clips:
        print("(无切片)")
        return
    print(f"{'ID':<7}{'状态':<10}{'号':<8}{'时长':>6}  {'标题':<22} 时间区间 标记")
    print("-" * 90)
    for c in clips:
        dur = (c["end_ms"] - c["start_ms"]) / 1000
        acc = (c.get("account") or "-")[:7]
        flags = []
        if c.get("multi_account"):
            flags.append("⚠多号争用[" + ",".join(c.get("accounts", [])) + "]")
        if c.get("overlap_with"):
            flags.append("相邻相似↔" + ",".join(c["overlap_with"]))
        if c.get("title_dup_with"):
            flags.append("跨视频同标题↔" + c["title_dup_with"])
        print(f"{c['global_id']:<7}{c['status']:<10}{acc:<8}{dur:>5.0f}s  "
              f"{c['title'][:20]:<22} {c['time_range']} {';'.join(flags)}")


def export_md(clips, path):
    lines = ["# 切片发布表", "",
             "| 全局ID | 状态 | 切片号 | 视频 | 序号 | 标题 | 时间区间 | 时长 | 多号争用 | 目录 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for c in clips:
        dur = (c["end_ms"] - c["start_ms"]) / 1000
        vname = os.path.basename(c["video_path"]).replace("|", "/")
        ma = ",".join(c.get("accounts", [])) if c.get("multi_account") else ""
        lines.append(f"| {c['global_id']} | {c['status']} | {c.get('account') or '-'} | {vname} | "
                     f"{c['seq_in_video']:02d} | {c['title']} | {c['time_range']} | "
                     f"{dur:.0f}s | {ma} | {c['clip_dir']} |")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"已导出 Markdown → {path}")


def export_csv(clips, path):
    fields = ["global_id", "status", "account", "accounts", "video_path", "video_key",
              "seq_in_video", "title", "time_range", "start_ms", "end_ms", "clip_dir",
              "multi_account", "overlap_with", "title_dup_with", "published_at", "post_id"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for c in clips:
            w.writerow(c)
    print(f"已导出 CSV → {path}")


def main():
    p = argparse.ArgumentParser(description="切片发布管理(跨切片号协调, 基于全局注册表)")
    p.add_argument("--status", help="按状态过滤: pending/published/rejected/archived")
    p.add_argument("--video-key", help="按视频短码过滤")
    p.add_argument("--account", help="按切片号/账号过滤(显示该号登记的片段)")
    p.add_argument("--conflicts", action="store_true", help="只显示被多号争用/已发的冲突片段")
    p.add_argument("--publish", metavar="GLOBAL_ID", help="标记该切片为已发布")
    p.add_argument("--reject", metavar="GLOBAL_ID", help="标记该切片为不发布")
    p.add_argument("--post-id", help="配合 --publish: 平台作品 id")
    p.add_argument("--export-md", metavar="PATH", help="导出 Markdown 发布表")
    p.add_argument("--export-csv", metavar="PATH", help="导出 CSV 发布表")
    p.add_argument("--registry", help="指定注册表路径(覆盖默认/环境变量)")
    args = p.parse_args()

    reg = ClipRegistry(args.registry)

    if args.publish:
        ok = reg.set_status(args.publish, "published", account=args.account, post_id=args.post_id)
        reg.save()
        print(f"{'OK' if ok else '未找到'} 标记 {args.publish} → published"
              + (f" (号={args.account})" if args.account else "")
              + (f" (post_id={args.post_id})" if args.post_id else ""))
        return
    if args.reject:
        ok = reg.set_status(args.reject, "rejected")
        reg.save()
        print(f"{'OK' if ok else '未找到'} 标记 {args.reject} → rejected")
        return

    if args.conflicts:
        clips = reg.account_conflicts()
        st = reg.stats()
        print(f"注册表: {reg.path}")
        print(f"⚠ 跨号冲突(高相似限流风险)片段: {len(clips)} 条")
        print("=" * 90)
        print_table(clips)
        if args.export_csv:
            export_csv(clips, args.export_csv)
        if args.export_md:
            export_md(clips, args.export_md)
        return

    clips = reg.query(status=args.status, video_key=args.video_key, account=args.account)
    clips.sort(key=lambda c: (c["video_key"], c.get("account") or "", c["seq_in_video"]))

    st = reg.stats()
    print(f"注册表: {reg.path}")
    print(f"总切片 {st['total']} | 状态 {st['by_status']} | 各号登记数 {st['by_account']} | 显示 {len(clips)} 条")
    print("=" * 90)
    print_table(clips)

    if args.export_md:
        export_md(clips, args.export_md)
    if args.export_csv:
        export_csv(clips, args.export_csv)


if __name__ == "__main__":
    main()
