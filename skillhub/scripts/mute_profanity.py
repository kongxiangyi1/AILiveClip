# -*- coding: utf-8 -*-
"""阶段4 之后(可选): 对切片内的粗口时间区间做静音(视频流零改动, 仅把音频对应区间 volume=0)。

读取 temp/clips_refined.json 每段 start/end, 从 temp/transcript.json 取该范围内命中脏词的句子,
映射成 clip 内相对时间, 用 ffmpeg volume filter 静音。输出同目录 <name>_clean.mp4(保留原版)。

用法(在目标工作区根目录执行):
   python <skill>/scripts/mute_profanity.py --dry-run   # 只列出每段脏词区间
   python <skill>/scripts/mute_profanity.py            # 实际静音

路径由 config.py 的 TEMP_DIR / OUTPUT_DIR 决定(锚定 AI_LIVE_CLIP_WORKDIR 或当前工作目录),
见 references/… 与 config.py 文件头说明。
"""
import argparse
import glob
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config

# 脏词(按 transcript 实际写法覆盖)。宁可多静音, 避免漏过审。
BAD = ["他妈", "尼玛", "妈的", "婊", "艹", "狗日", "滚蛋", "傻逼", "sb", "煞笔",
       "傻×", "卧槽", "我草", "我操", "操你", "放屁", "屁", "操", "草"]


def load_sentences_and_clips():
    """从工作区 temp/ 读取 ASR 原文与精制片段。缺文件时给明确指引, 不抛裸异常。"""
    trans_path = os.path.join(Config.TEMP_DIR, "transcript.json")
    refined_path = os.path.join(Config.TEMP_DIR, "clips_refined.json")
    for p in (trans_path, refined_path):
        if not os.path.exists(p):
            print(f"[错误] 找不到 {p}")
            print("       请先跑完阶段1(转写)与阶段3(切点精修); "
                  "若脚本在别的目录执行, 用环境变量 AI_LIVE_CLIP_WORKDIR 指向工作区根目录。")
            sys.exit(2)
    with open(trans_path, encoding="utf-8") as f:
        trans = json.load(f)
    with open(refined_path, encoding="utf-8") as f:
        ref = json.load(f)
    return trans["sentences"], ref["clips"]


def profanity_intervals(sentences, cs, ce):
    """返回该 clip 内命中脏词的相对秒区间列表(已合并间隔 <0.3s 的相邻区间)。"""
    iv = []
    for s in sentences:
        st, en = s["start_ms"], s["end_ms"]
        if en < cs or st > ce:
            continue
        if any(b in s["text"] for b in BAD):
            rs = max(st, cs) - cs
            re_ = min(en, ce) - cs
            if re_ > rs:
                iv.append((rs / 1000.0, re_ / 1000.0))
    iv.sort()
    merged = []
    for a, b in iv:
        if merged and a - merged[-1][1] < 0.3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def find_mp4(out_root, idx):
    """按 `<序号两位>_*.mp4` 递归定位切片(兼容 output/<视频名>/<分段>/ 两层结构)。"""
    pat = os.path.join(out_root, "**", f"{idx + 1:02d}_*.mp4")
    matches = glob.glob(pat, recursive=True)
    matches = [m for m in matches if not m.endswith("_clean.mp4")]
    return matches[0] if matches else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印每段脏词区间, 不实际静音")
    args = ap.parse_args()

    sentences, clips = load_sentences_and_clips()
    out_root = Config.OUTPUT_DIR
    total = 0
    for clip in clips:
        idx = clip.get("idx", 0)
        cs, ce = clip["start_ms"], clip["end_ms"]
        merged = profanity_intervals(sentences, cs, ce)
        in_path = find_mp4(out_root, idx)
        if args.dry_run:
            print(f"[dry] clip#{idx+1} 时长{(ce-cs)/1000:.0f}s 脏词区间{len(merged)}个 -> {in_path or '未找到mp4'}")
            for a, b in merged[:6]:
                print(f"      {a:.2f}~{b:.2f}s")
            continue
        if not in_path:
            print(f"[skip] clip#{idx+1} 找不到 mp4")
            continue
        if not merged:
            print(f"[skip] clip#{idx+1} 无脏词区间, 跳过")
            continue
        enable = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in merged)
        filt = f"volume=enable='{enable}':volume=0"
        out_path = in_path[:-4] + "_clean.mp4"
        cmd = [Config.FFMPEG, "-y", "-i", in_path, "-af", filt,
               "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", out_path]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            print(f"[ok] clip#{idx+1} 静音 {len(merged)} 区间 -> {os.path.basename(out_path)}")
            total += 1
        else:
            print(f"[fail] clip#{idx+1} ffmpeg 失败:")
            print(r.stderr.decode("utf-8", "ignore")[-400:])
    if not args.dry_run:
        print(f"静音完成: {total}/{len(clips)} 段")


if __name__ == "__main__":
    main()
