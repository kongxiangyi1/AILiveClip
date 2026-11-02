# -*- coding: utf-8 -*-
"""分段断点续跑转写: 把视频切成 300s 块并行 FunASR 转写, 结果写 chunk jsonl,
中途被杀后可续跑(自动, 无需参数), 全部完成后 merge 成 transcript 文件。
用法:
  python transcribe_chunked.py <video> [--workers N]          # 单个视频
  python transcribe_chunked.py A.ts B.ts C.ts [--workers N]   # 批量: 模型只加载一次, 抽音并行
并行: 多进程(默认 min(4, cpu核数/4, 内存GB/8) 个 worker), 每进程独立加载 FunASR 模型,
worker 启动即预加载模型(与主进程切分 wav 并行), 每进程限制 torch 线程数 = cpu/worker 数。
批量输出: 每个视频单独 merge 到 temp/{视频名}.transcript.json; 单视频时同时写 temp/transcript.json。
注意: 300s 块边界处的句子会被切成两半(块间无重叠), 属已知限制。
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config

CHUNK_S = 300
SENTENCE_END_PUNCT = set("。！？!?；;")
ALL_PUNCT = set("，。！？、；：""''（）《》【】…—·!?;,\"'()<>[]{} \t\n")
TEMP = Config.TEMP_DIR

_model = None  # worker 进程内懒加载, 跨 chunk 复用


def extract_wav(video, wav):
    if os.path.exists(wav):
        return
    subprocess.run([Config.FFMPEG, "-i", video, "-vn", "-ac", "1", "-ar", "16000", "-y", wav], check=True)


def _video_key(video):
    """视频唯一标识: 文件名 + 绝对路径短hash, 防止不同目录同名视频串数据"""
    name = os.path.splitext(os.path.basename(video))[0]
    h = hashlib.md5(os.path.abspath(video).encode("utf-8")).hexdigest()[:8]
    return f"{name}_{h}"


def progress_path(video):
    return os.path.join(TEMP, f"{_video_key(video)}.progress.jsonl")


def _get_model():
    global _model
    if _model is None:
        from funasr import AutoModel
        _model = AutoModel(model=Config.FUNASR_MODEL, vad_model=Config.FUNASR_VAD,
                           punc_model=Config.FUNASR_PUNC, device=Config.DEVICE,
                           disable_update=True)
    return _model


def _init_worker(threads):
    """每个 worker 进程只分到 cpu/worker 数量的线程, 防止进程间线程争抢;
    同时立即预加载模型(与主进程切分 wav 并行, 首个任务到达时模型已就绪)"""
    os.environ["OMP_NUM_THREADS"] = str(threads)
    try:
        import torch
        torch.set_num_threads(threads)
    except ImportError:
        pass
    _get_model()


def _transcribe_chunk(k, st, en, part_path):
    """转写单个 300s 块(worker 进程内执行, 模型进程内复用)。
    返回 (k, sentences, silences), 时间戳已偏移到全局。格式与 asr_pipeline 一致。
    """
    res = _get_model().generate(input=part_path, batch_size_s=CHUNK_S)
    sentences, silences = [], []
    all_chars = []
    prev_end_ms = None
    for r in res:
        text = r.get("text", "")
        tss = r.get("timestamp", [])
        ci = 0
        for ch in text:
            if ch in ALL_PUNCT:
                all_chars.append({"char": ch, "start_ms": -1, "end_ms": -1})
                continue
            if ci < len(tss):
                ts = tss[ci]
                all_chars.append({"char": ch, "start_ms": int(ts[0]), "end_ms": int(ts[1])})
                if prev_end_ms is not None:
                    gap = int(ts[0]) - prev_end_ms
                    if gap > 500:
                        silences.append({"start_ms": prev_end_ms, "end_ms": int(ts[0]), "duration_ms": gap})
                prev_end_ms = int(ts[1])
                ci += 1
    # 分句
    cur = []
    for c in all_chars:
        cur.append(c)
        if c["char"] in SENTENCE_END_PUNCT:
            timed = [x for x in cur if x["start_ms"] >= 0]
            if timed:
                sentences.append({"text": "".join(x["char"] for x in cur).strip(),
                                  "start_ms": timed[0]["start_ms"], "end_ms": timed[-1]["end_ms"]})
            cur = []
    timed = [x for x in cur if x["start_ms"] >= 0]
    if timed:
        sentences.append({"text": "".join(x["char"] for x in cur).strip(),
                          "start_ms": timed[0]["start_ms"], "end_ms": timed[-1]["end_ms"]})

    # 偏移到全局时间
    for s in sentences:
        s["start_ms"] += st * 1000
        s["end_ms"] += st * 1000
    for s in silences:
        s["start_ms"] += st * 1000
        s["end_ms"] += st * 1000
    return k, sentences, silences


def _probe_wav(wav):
    probe = subprocess.run([Config.FFMPEG, "-i", wav], capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not m:
        raise RuntimeError(f"无法解析音频时长: {probe.stderr[-300:]}")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def _default_workers():
    """内存感知默认并行数: min(4, cpu核数/4, 内存GB/8), 防止多 worker 模型加载 OOM"""
    cpu = os.cpu_count() or 4
    w = max(2, min(4, cpu // 4))
    try:
        import psutil
        mem_gb = psutil.virtual_memory().total / 1024 ** 3
        w = max(2, min(w, int(mem_gb // 8)))
    except ImportError:
        pass
    return w


def _prepare_video(video):
    """抽音 + 探测 + 读续跑进度(纯 IO, 可在线程池中并行执行)"""
    wav = os.path.join(TEMP, f"_chunk_{_video_key(video)}.wav")
    extract_wav(video, wav)
    dur = _probe_wav(wav)
    n = max(1, int(dur // CHUNK_S) + (1 if dur % CHUNK_S else 0))
    done = {}
    pf = progress_path(video)
    if os.path.exists(pf):
        for line in open(pf, encoding="utf-8"):
            d = json.loads(line)
            done[d["chunk"]] = d
    return {"video": video, "wav": wav, "dur": dur, "n": n, "pf": pf,
            "pending": [k for k in range(n) if k not in done]}


def _merge_video(meta):
    """把一个视频的 progress 合并为 transcript; 单视频时同时写 transcript.json 兼容现有流程"""
    all_sent, all_sil = [], []
    for line in open(meta["pf"], encoding="utf-8"):
        d = json.loads(line)
        all_sent += d["sentences"]
        all_sil += d["silences"]
    all_sent.sort(key=lambda x: x["start_ms"])
    all_sil.sort(key=lambda x: x["start_ms"])
    out = {
        "video_path": os.path.abspath(meta["video"]),
        "duration_ms": int(meta["dur"] * 1000),
        "sentences": all_sent,
        "silences": all_sil,
    }
    op = os.path.join(TEMP, f"{_video_key(meta['video'])}.transcript.json")
    with open(op, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    if meta["single"]:
        with open(os.path.join(TEMP, "transcript.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[视频{meta['vi'] + 1}] 合并完成: {op}  句子 {len(all_sent)} 静音 {len(all_sil)}")
    return op


def split_and_transcribe(videos, workers=None):
    single = len(videos) == 1

    # 并行抽音+探测(ffmpeg 子进程, IO 型, 与模型加载并行)
    metas = []
    with ThreadPoolExecutor(max_workers=min(4, len(videos))) as ex:
        futs = [ex.submit(_prepare_video, v) for v in videos]
        for i, fut in enumerate(futs):
            m = fut.result()
            m["vi"], m["single"] = i, single
            metas.append(m)
            print(f"视频{i + 1}/{len(videos)}: 总时长 {m['dur']:.1f}s → {m['n']} 块, "
                  f"已完成 {m['n'] - len(m['pending'])}, 待处理 {len(m['pending'])}")

    jobs = [(m["vi"], k, k * CHUNK_S, min((k + 1) * CHUNK_S, m["dur"]))
            for m in metas for k in m["pending"]]

    if jobs:
        cpu = os.cpu_count() or 4
        w = workers or _default_workers()
        threads = max(2, cpu // w)
        print(f"并行转写: {len(jobs)} 块, {w} workers × {threads} 线程, 模型加载与切块并行")

        failed = []
        with ProcessPoolExecutor(max_workers=w, initializer=_init_worker, initargs=(threads,)) as ex:
            futs = {}
            # 流式提交: 每切好一块立即提交, worker 启动即预加载模型(与切块并行)
            for vi, k, st, en in jobs:
                meta = metas[vi]
                tmp = f"{meta['wav']}.part{k}.wav"
                subprocess.run([Config.FFMPEG, "-y", "-ss", str(st), "-t", str(en - st),
                                "-i", meta["wav"], "-ac", "1", "-ar", "16000", tmp],
                               check=True, capture_output=True)
                futs[ex.submit(_transcribe_chunk, k, st, en, tmp)] = (vi, k, tmp, st, en)
            for fut in as_completed(futs):
                vi, k, tmp, st, en = futs[fut]
                meta = metas[vi]
                try:
                    _, sentences, silences = fut.result()
                    with open(meta["pf"], "a", encoding="utf-8") as f:
                        f.write(json.dumps({"chunk": k, "start_s": st,
                                            "sentences": sentences, "silences": silences},
                                           ensure_ascii=False) + "\n")
                    print(f"  [视频{vi + 1}] chunk {k + 1}/{meta['n']} [{st}s-{en}s]: "
                          f"{len(sentences)}句", flush=True)
                except Exception as e:
                    failed.append((vi + 1, k))
                    print(f"  [视频{vi + 1}] chunk {k + 1}/{meta['n']} 转写失败: {e}", flush=True)
                finally:
                    try:
                        os.remove(tmp)
                    except OSError:
                        pass  # 沙箱环境回收站不可用时 os.remove 会被拦截, 临时分块残留无害
        if failed:
            print(f"警告: {len(failed)} 块转写失败(视频,块号): {failed}, "
                  f"其余结果已写入, 直接重跑同命令可续传")

    for m in metas:
        _merge_video(m)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="分段并行 FunASR 转写(断点续跑, 支持批量)")
    p.add_argument("videos", nargs="+", help="视频文件路径(可多个, 批量时模型只加载一次)")
    p.add_argument("--workers", type=int, default=None,
                   help="并行进程数(默认 min(4, cpu核数/4, 内存GB/8); 每进程线程数自动 = cpu/workers)")
    args = p.parse_args()
    split_and_transcribe(args.videos, args.workers)
