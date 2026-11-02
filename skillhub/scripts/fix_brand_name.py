# -*- coding: utf-8 -*-
"""专有名词批量修正(ASR 同音错字 → 正确名), 配套纪律 #8。

改: temp/segments.json, temp/clips_refined.json, output/*/cut_results.json 的字符串字段
重命名: 输出目录下含错误词的目录及内部 mp4
不动: transcript.json(ASR 原文保留)

用法(错误词/正确名必须由调用方给出, 脚本不内置任何私有词表):
  python fix_brand_name.py <输出日期目录> <错误词> <正确名>
  FIX_OLD=<错误词> FIX_NEW=<正确名> python fix_brand_name.py <输出日期目录>
"""
import json
import os
import shutil
import sys

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.path.dirname(os.path.abspath(__file__))
TEMP = os.path.join(BASE, "temp")


def walk_replace(obj, old, new):
    if isinstance(obj, str):
        return obj.replace(old, new)
    if isinstance(obj, list):
        return [walk_replace(x, old, new) for x in obj]
    if isinstance(obj, dict):
        return {k: walk_replace(v, old, new) for k, v in obj.items()}
    return obj


def rewrite_json(path, backup=True):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if backup:
        shutil.copy2(path, path + ".bak_brand")
    new_data = walk_replace(data, OLD, NEW)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(new_data, f, ensure_ascii=False, indent=2)
    with open(path, encoding="utf-8") as f:  # 纪律1: 改后 json.load 校验
        json.load(f)
    print(f"已修正: {path}")


def main():
    args = sys.argv[1:]
    # 错误词/正确名只来自命令行或环境变量, 不内置默认值(避免私有词随包分发)
    OLD = (args[1] if len(args) > 1 else "") or os.environ.get("FIX_OLD", "")
    NEW = (args[2] if len(args) > 2 else "") or os.environ.get("FIX_NEW", "")
    out_dir = args[0] if args else ""
    if not out_dir or not OLD or not NEW:
        raise SystemExit(
            "用法: python fix_brand_name.py <输出日期目录> <错误词> <正确名>\n"
            "或用环境变量: FIX_OLD=<错误词> FIX_NEW=<正确名>\n"
            "对照表见 references/brand-words.private.md(本机私有, 不随包分发)")

    rewrite_json(os.path.join(TEMP, "segments.json"))
    rewrite_json(os.path.join(TEMP, "clips_refined.json"))

    cr = os.path.join(out_dir, "cut_results.json")
    with open(cr, encoding="utf-8") as f:
        results = json.load(f)
    shutil.copy2(cr, cr + ".bak_brand")

    renamed = 0
    print(f"替换规则: {OLD} -> {NEW}")
    for c in results["results"]:
        old_file = c["file"]
        new_file = old_file.replace(OLD, NEW)
        if old_file == new_file:
            c["title"] = c["title"].replace(OLD, NEW)
            continue
        old_dir = os.path.dirname(old_file)
        new_dir = os.path.dirname(new_file)
        old_base = os.path.basename(old_file)
        new_base = os.path.basename(new_file)
        if os.path.exists(old_dir) and not os.path.exists(new_dir):
            os.rename(old_dir, new_dir)
        if os.path.exists(os.path.join(new_dir, old_base)) and old_base != new_base:
            os.rename(os.path.join(new_dir, old_base), os.path.join(new_dir, new_base))
        c["file"] = new_file
        c["title"] = c["title"].replace(OLD, NEW)
        renamed += 1

    with open(cr, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    with open(cr, encoding="utf-8") as f:
        json.load(f)
    print(f"已重命名 {renamed} 个切片并修正: {cr}")


if __name__ == "__main__":
    main()