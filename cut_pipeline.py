"""阶段4: 读取segments/clips JSON → FFmpeg切割(纯视频切片, 不生成封面)

用法: python cut_pipeline.py <segments.json或clips_refined.json路径>
     [--workers N]           并行切割进程数(默认2, 16核可试4)
     [--mode reencode|stream_copy]  切割模式(默认 reencode)
     [--max-snap-s N]        吸附误差超N秒自动降级reencode(仅stream_copy, 默认3)
输出: output/{日期_原视频名}/{切片}/ 每个切片含MP4, 输出目录下生成 cut_results.json
同一日期同一原视频的所有切片放入同一个分组目录

--mode reencode:    重编码精确切割(默认). 切点精确到帧, 输出 H.264/MP4 兼容性最好.
--mode stream_copy: 关键帧感知流复制快速切割(推荐, 实测比reencode快约190倍).
                    先用 ffprobe -show_packets 扫描源流 IDR 列表(=stss随机访问点,
                    非含CRA的帧级key_frame), 切点起点向前/终点向后吸附到最近 IDR,
                    -c copy 零重编码切割, 首帧必为 I 帧无花屏无废料,
                    吸附误差(吸附点-切点)写入 cut_results.json 的 start_offset_s 字段;
                    误差超过 --max-snap-s 的切片自动降级 reencode 保证精度;
                    TS 源无 stss 索引, 自动无损转封装到 temp 再切割; HEVC 输出自动加 hvc1 tag.
"""
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config


def extract_video_date(video_path):
    """从视频文件名提取日期(YYYYMMDD格式8位数字)，找不到则用文件修改时间"""
    base = os.path.basename(video_path)
    for m in re.finditer(r'(20\d{2})(\d{2})(\d{2})', base):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    ts = os.path.getmtime(video_path)
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def build_video_dir_name(video_path):
    """按"日期_原视频名"构造视频分组目录，相同日期同一视频共用"""
    date = extract_video_date(video_path)
    name = os.path.splitext(os.path.basename(video_path))[0]
    return f"{date}_{sanitize_filename(name)}"


def sanitize_filename(name):
    """清理文件名非法字符,限制长度"""
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name[:40]


def format_timecode(ms):
    """毫秒转紧凑时间码: HMMSS (如 0037=0分37秒, 10530=1时05分30秒)"""
    total_s = int(ms // 1000)
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    if h:
        return f"{h}{m:02d}{s:02d}"
    return f"{m:02d}{s:02d}"


def probe_video_codec(video_path):
    """探测第一个视频流的编码名称(h264/hevc/...), 失败返回 None"""
    p = subprocess.run([Config.FFMPEG, "-i", video_path], capture_output=True, text=True, errors="replace")
    m = re.search(r"Stream #0:\d+.*?Video:\s*(\w+)", p.stderr)
    return m.group(1) if m else None


def get_ffprobe():
    """获取 ffprobe 路径(优先与 ffmpeg 同目录的完整版)"""
    fp = os.path.join(os.path.dirname(Config.FFMPEG), "ffprobe.exe")
    if os.path.exists(fp):
        return fp
    import shutil
    return shutil.which("ffprobe") or "ffprobe"


def scan_idr_packets(video_path):
    """packets 法提取 IDR 列表(= stss 随机访问点, ffmpeg -c copy seek 的真实落点)

    不用 -show_frames(解码级 key_frame 含 CRA 帧, 与实际切割起点错位,
    MP4 慢 60 倍且对 TS 扫不出关键帧):
      ffprobe -show_packets -show_entries packet=pts_time,flags → flags 含 K 的行
    实测: MP4(1.9h) 2.0s / 5681 个; TS(48min) 3.7s / 350 个
    返回排序 IDR 时间列表(s); 失败返回 None
    """
    ffprobe = get_ffprobe()
    p = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0",
         "-show_packets", "-show_entries", "packet=pts_time,flags",
         "-of", "csv=p=0", video_path],
        capture_output=True, timeout=600)
    if p.returncode != 0:
        return None
    idr = []
    for line in p.stdout.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        if len(parts) >= 2 and "K" in parts[1]:
            try:
                idr.append(float(parts[0]))
            except ValueError:
                pass
    idr.sort()
    return idr or None


def snap_start(ms, idr_list):
    """起点向前吸附: 最大满足 idr_pts <= start 的 IDR(保证首帧干净)"""
    t = ms / 1000.0
    prev = [k for k in idr_list if k <= t + 1e-6]
    return prev[-1] if prev else idr_list[0]


def snap_end(ms, idr_list):
    """终点向后吸附: 最小满足 idr_pts >= end 的 IDR(保证尾帧完整)"""
    t = ms / 1000.0
    nxt = [k for k in idr_list if k >= t - 1e-6]
    return nxt[0] if nxt else idr_list[-1]


def is_ts_container(video_path):
    """TS 源无 stss 索引, -ss 按码率估算落点可能落在非关键帧 → copy 花屏, 需先转封装"""
    return os.path.splitext(video_path)[1].lower() in (".ts", ".m2ts", ".mts")


def remux_to_mp4(video_path, codec=None):
    """TS 源无损转封装到 temp(实测 2.4s / 48min), 返回转封装后路径(已存在则复用)"""
    name = sanitize_filename(os.path.splitext(os.path.basename(video_path))[0])
    key = hashlib.md5(video_path.encode("utf-8", "replace")).hexdigest()[:8]
    out = os.path.join(Config.TEMP_DIR, f"{name}_{key}_remux.mp4")
    if not os.path.exists(out):
        os.makedirs(Config.TEMP_DIR, exist_ok=True)
        args = [Config.FFMPEG, "-i", video_path, "-c", "copy"]
        if codec and codec.lower() in ("hevc", "h265"):
            args += ["-tag:v", "hvc1"]
        subprocess.run(args + ["-y", out], check=True)
    return out


def cut_clip(video_path, start_ms, end_ms, title, index, output_dir, mode="reencode",
             workers=2, idr_list=None, codec=None, max_snap_s=3.0):
    """切割单个片段(纯视频, 无封面), 每个片段存入独立子文件夹
    命名格式: {序号}_{标题}_{起始时间码}-{结束时间码}
    示例: 01_开场闲聊_0037-0310 (原直播0分37秒至3分10秒)
    mode: reencode=重编码精确切点(默认) / stream_copy=IDR吸附流复制(快)
    idr_list: 源流 IDR 时间列表(s), stream_copy 模式必需
    max_snap_s: 吸附误差(秒)超此阈值自动降级 reencode(仅 stream_copy)
    返回: (输出路径, start_offset_s(吸附IDR-期望切点, 非copy模式为None), 是否stream_copy)
    """
    safe_title = sanitize_filename(title)
    time_range = f"{format_timecode(start_ms)}-{format_timecode(end_ms)}"
    clip_name = f"{index:02d}_{safe_title}_{time_range}"
    clip_dir = os.path.join(output_dir, clip_name)
    os.makedirs(clip_dir, exist_ok=True)

    output_path = os.path.join(clip_dir, f"{clip_name}.mp4")

    duration_s = (end_ms - start_ms) / 1000
    start_sec = start_ms / 1000

    start_offset_s = None
    use_copy = False
    if mode == "stream_copy" and idr_list:
        # 起点吸附到最近 <=切点 IDR(首帧必为I帧无花屏), 终点吸附到最近 >=切点 IDR(尾帧完整)
        start_idr = snap_start(start_ms, idr_list)
        end_idr = snap_end(end_ms, idr_list)
        start_offset_s = start_idr - start_sec
        # 误差超阈值 → 降级 reencode 保证精度
        use_copy = -start_offset_s <= max_snap_s
    if use_copy:
        # 流复制: -ss 前置 seek 到 IDR, -c copy 零重编码, 秒级完成
        args = [
            Config.FFMPEG, "-ss", str(start_idr), "-i", video_path,
            "-t", str(end_idr - start_idr),
            "-map", "0:v:0", "-map", "0:a:0",
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",  # 时间戳归零, 修复播放器时间轴显示异常
        ]
        # HEVC 装 mp4 默认写 hev1 tag, Windows 播放器/剪映兼容性不如 hvc1, 显式指定
        if codec and codec.lower() in ("hevc", "h265"):
            args += ["-tag:v", "hvc1"]
        subprocess.run(args + ["-y", output_path], check=True)
    else:
        # 重编码切割(re-encode确保精确切点; 编码器优先QSV硬件加速)
        # 两段式seek: TS无索引, 单段前置 -ss 按码率估算落点, 落点常不在 IDR 上,
        # HEVC缺参考帧导致首帧花屏/灰帧(直到下一IDR自愈).
        # 先前置seek到 t-10s 预热解码, 再后置 -ss 精确到切点, 保证首帧干净.
        pre_sec = max(0.0, start_sec - 10)
        args = [
            Config.FFMPEG,
            "-ss", str(pre_sec),
            "-i", video_path,
            "-ss", str(start_sec - pre_sec),
            "-t", str(duration_s),
            "-c:v", Config.VIDEO_ENCODER, *Config.VIDEO_ENCODER_ARGS,
            "-c:a", "aac",
        ]
        # CPU编码(libx264)时每路ffmpeg默认用满全部核, 多路并行互相争抢,
        # 显式按核数/workers分配线程数; QSV走GPU不受影响
        if "qsv" not in Config.VIDEO_ENCODER:
            args += ["-threads", str(max(2, os.cpu_count() // workers))]
        subprocess.run(args + ["-y", output_path], check=True)

    return output_path, start_offset_s, use_copy


def run(segments_path, workers=2, mode="reencode", max_snap_s=3.0):
    """读取segments.json并批量切割(ThreadPool并行, ffmpeg为子进程不吃GIL)"""
    if not os.path.exists(segments_path):
        print(f"错误: 文件不存在: {segments_path}")
        sys.exit(1)

    with open(segments_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    video_path = data["video_path"]
    segments = data.get("segments", data.get("clips", []))

    if not os.path.exists(video_path):
        print(f"错误: 视频文件不存在: {video_path}")
        sys.exit(1)

    os.makedirs(Config.OUTPUT_DIR, exist_ok=True)

    # 视频分组目录: output/{日期_原视频名}/
    video_dir_name = build_video_dir_name(video_path)
    video_out_dir = os.path.join(Config.OUTPUT_DIR, video_dir_name)
    os.makedirs(video_out_dir, exist_ok=True)

    print(f"开始切割: {len(segments)}个片段")
    print(f"源视频: {video_path}")
    print(f"输出目录: {video_out_dir}")
    idr_list = None
    codec = None
    if mode == "stream_copy":
        codec = probe_video_codec(video_path)
        work_path = video_path
        if is_ts_container(video_path):
            work_path = remux_to_mp4(video_path, codec)
            print(f"TS源: 已无损转封装 → {work_path}")
        t0 = time.time()
        idr_list = scan_idr_packets(work_path)
        if idr_list:
            print(f"模式: stream_copy(IDR感知, {workers}路并行). 源编码 {codec or '未知'}, "
                  f"IDR {len(idr_list)} 个, 扫描耗时 {time.time() - t0:.1f}s, "
                  f"吸附误差>{max_snap_s}s 自动降级 reencode")
        else:
            print(f"警告: IDR 扫描失败, 全部降级 reencode(精确切点)")
        # 切割用转封装后的文件(TS 转封装后才有 stss 索引)
        video_path = work_path
    else:
        print(f"模式: reencode(精确切点, {workers}路并行)")
    print("-" * 50)

    from concurrent.futures import ThreadPoolExecutor, as_completed
    results = []
    total = len(segments)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {}
        for i, seg in enumerate(segments):
            title = seg["title"]
            start_ms = seg["start_ms"]
            end_ms = seg["end_ms"]
            duration = seg.get("duration_s", (end_ms - start_ms) / 1000)
            futs[ex.submit(cut_clip, video_path, start_ms, end_ms, title, i + 1,
                           video_out_dir, mode, workers, idr_list, codec, max_snap_s)] = (i, title, duration)
        for fut in as_completed(futs):
            i, title, duration = futs[fut]
            try:
                output_path, start_offset_s, use_copy = fut.result()
                offset_str = f" offset={start_offset_s:+.3f}s" if start_offset_s is not None else ""
                degrade_str = " [降级reencode]" if mode == "stream_copy" and not use_copy else ""
                results.append({
                    "index": i + 1, "title": title, "file": output_path,
                    "duration_s": duration, "mode": "stream_copy" if use_copy else "reencode",
                    "start_offset_s": start_offset_s,
                })
                print(f"  [{i + 1}/{total}] {title} OK{offset_str}{degrade_str} → {os.path.dirname(output_path)}", flush=True)
            except subprocess.CalledProcessError as e:
                print(f"  [{i + 1}/{total}] {title} 切割失败(FFmpeg错误): {e}", flush=True)
            except (OSError, KeyError) as e:
                print(f"  [{i + 1}/{total}] {title} 切割失败({type(e).__name__}): {e}", flush=True)

    results.sort(key=lambda r: r["index"])
    print("-" * 50)
    print(f"完成! {len(results)}/{len(segments)}个切片已保存到: {video_out_dir}")

    if mode == "stream_copy":
        degraded = [r["index"] for r in results if r["mode"] != "stream_copy"]
        if degraded:
            print(f"警告: 吸附误差超 --max-snap-s({max_snap_s}s) 已降级 reencode 的切片: {degraded}")

    # 结果清单 JSON(start_offset_s = 吸附IDR - 期望切点, 已知误差下界)
    results_path = os.path.join(video_out_dir, "cut_results.json")
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump({"video_path": data["video_path"], "mode": mode, "max_snap_s": max_snap_s,
                   "results": results}, f, ensure_ascii=False, indent=1)
    print(f"结果清单: {results_path}")

    # 输出文件列表
    for r in results:
        clip_subdir = os.path.dirname(r['file'])
        print(f"  {r['title']} ({r['duration_s']:.0f}s) → {clip_subdir}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="阶段4: 批量切割切片(纯视频, 无封面)")
    p.add_argument("segments", help="segments.json 或 clips_refined.json 路径")
    p.add_argument("--workers", type=int, default=2, help="并行切割数(默认2, 16核可试4)")
    p.add_argument("--mode", choices=["reencode", "stream_copy"], default="reencode",
                   help="reencode=重编码精确切点(默认) / stream_copy=IDR吸附流复制快速切割(推荐)")
    p.add_argument("--max-snap-s", type=float, default=3.0,
                   help="吸附误差(秒)超此阈值自动降级reencode(仅stream_copy, 默认3)")
    args = p.parse_args()
    run(args.segments, args.workers, args.mode, args.max_snap_s)
