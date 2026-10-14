# -*- coding: utf-8 -*-
"""阶段4 之后: 对每段切片里的粗口时间区间做静音(体积零改动视频, 只把音频对应区间 volume=0)。
读取 temp/clips_refined.json 的每段 start/end, 从 temp/transcript.json 取该范围内含脏词的句子,
映射到 clip 内相对时间, 用 ffmpeg volume filter 静音。输出同目录 <name>_clean.mp4(保留原版备份)。
"""
import json, os, sys, glob, subprocess, argparse

SKILL = r"C:\Users\KONGXY\.workbuddy\skills\直播切片工具\scripts"
sys.path.insert(0, SKILL)
import config

# 脏词(按 transcript 实际写法覆盖)。宁可多静音, 避免漏过审。
BAD = ["他妈", "尼玛", "妈的", "婊", "艹", "狗日", "滚蛋", "傻逼", "sb", "煞笔",
       "傻×", "卧槽", "我草", "我操", "操你", "放屁", "屁", "操", "草"]

def load():
    trans = json.load(open(os.path.join(SKILL, "temp", "transcript.json"), encoding="utf-8"))
    ref = json.load(open(os.path.join(SKILL, "temp", "clips_refined.json"), encoding="utf-8"))
    return trans["sentences"], ref["clips"]

def profanity_intervals(S, cs, ce):
    iv = []
    for s in S:
        st, en = s["start_ms"], s["end_ms"]
        if en < cs or st > ce:
            continue
        if any(b in s["text"] for b in BAD):
            rs = max(st, cs) - cs
            re = min(en, ce) - cs
            if re > rs:
                iv.append((rs / 1000.0, re / 1000.0))
    iv.sort()
    merged = []
    for a, b in iv:
        if merged and a - merged[-1][1] < 0.3:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印每段脏词区间, 不实际静音")
    args = ap.parse_args()

    S, clips = load()
    out_root = config.Config.OUTPUT_DIR
    total = 0
    for clip in clips:
        idx = clip.get("idx", 0)
        cs, ce = clip["start_ms"], clip["end_ms"]
        merged = profanity_intervals(S, cs, ce)
        # 定位 mp4: output 下递归搜索 <两位idx+1>_*.mp4 (支持 output/<视频名>/<分段>/ 两层结构)
        pat = os.path.join(out_root, "**", f"{idx+1:02d}_*.mp4")
        matches = glob.glob(pat, recursive=True)
        in_path = matches[0] if matches else None
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
        cmd = [config.Config.FFMPEG, "-y", "-i", in_path, "-af", filt,
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
