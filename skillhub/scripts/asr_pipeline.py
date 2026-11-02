"""阶段1: MP4 → FunASR转写 → transcript.json

用法: python asr_pipeline.py <视频文件路径>
输出: clipping/temp/transcript.json
"""
import json
import os
import re
import subprocess
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import Config

# 标点符号(用于分句)
SENTENCE_END_PUNCT = set("。！？!?；;")
ALL_PUNCT = set("，。！？、；：""''（）《》【】…—·!?;,\"'()<>[]{} \t\n")

# 模型预加载(后台线程, 与抽音并行; 转写时若未加载完则等待)
_model_lock = threading.Lock()
_model_holder = {"model": None}


def _load_model():
    from funasr import AutoModel
    return AutoModel(
        model=Config.FUNASR_MODEL,
        vad_model=Config.FUNASR_VAD,
        punc_model=Config.FUNASR_PUNC,
        device=Config.DEVICE,
        disable_update=True,
    )


def _start_model_preload():
    """后台线程立即开始加载模型(与 extract_audio 并行), 返回线程句柄"""
    def _load():
        with _model_lock:
            if _model_holder["model"] is None:
                _model_holder["model"] = _load_model()
    t = threading.Thread(target=_load, daemon=True)
    t.start()
    return t


def _get_model(preload_thread=None):
    if preload_thread is not None:
        preload_thread.join()
    with _model_lock:
        return _model_holder["model"]


def extract_audio(video_path, audio_path):
    """从视频提取16kHz单声道WAV"""
    subprocess.run([
        Config.FFMPEG, "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-y", audio_path
    ], check=True)


def probe_duration_ms(media_path):
    """用 ffmpeg -i 输出解析媒体真实时长(ms); 解析失败返回 None"""
    probe = subprocess.run([Config.FFMPEG, "-i", media_path],
                           capture_output=True, text=True, errors="replace")
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.\d+)", probe.stderr)
    if not m:
        return None
    h, mnt, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
    return int((h * 3600 + mnt * 60 + s) * 1000)


def make_preview(video_path):
    """生成低码率预览 MP4 供 Streamlit 审核界面在浏览器中播放。

    仅当源视频非 .mp4(如 .ts 浏览器无法直接播放)时生成;
    失败不影响主流程(审核界面会显示提示并禁用预览)。
    """
    if video_path.lower().endswith(".mp4"):
        return None
    preview_path = os.path.join(Config.TEMP_DIR, "preview.mp4")
    try:
        subprocess.run([
            Config.FFMPEG, "-i", video_path,
            "-vf", "scale=480:-2",
            "-c:v", Config.VIDEO_ENCODER, *Config.VIDEO_ENCODER_ARGS,
            "-c:a", "aac", "-b:a", "64k",
            "-y", preview_path
        ], check=True, capture_output=True)
        print(f"  预览文件已生成: {preview_path}")
        return preview_path
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"  警告: 预览文件生成失败(不影响转写): {e}")
        return None


def transcribe(audio_path, preload_thread=None):
    """
    FunASR转写,返回句级时间戳和静音段。

    FunASR输出结构:
    result = [{"key": ..., "text": "带标点的完整文本", "timestamp": [[start_ms, end_ms], ...]}]
    text中每个非标点字符与timestamp一一对应。
    preload_thread: _start_model_preload() 返回的线程, 转写前 join 等待加载完成
    """
    model = _get_model(preload_thread)
    results = model.generate(
        input=audio_path,
        batch_size_s=300,
    )

    all_chars = []       # [{char, start_ms, end_ms}, ...]
    all_silences = []    # [{start_ms, end_ms, duration_ms}, ...]
    prev_end_ms = None   # 上一段的最后时间(用于检测段间静音)

    for res in results:
        text = res.get("text", "")
        timestamps = res.get("timestamp", [])

        # 把text中的非标点字符和timestamp一一对应
        char_idx = 0
        for ch in text:
            if ch not in ALL_PUNCT:
                if char_idx < len(timestamps):
                    ts = timestamps[char_idx]
                    all_chars.append({
                        "char": ch,
                        "start_ms": int(ts[0]),
                        "end_ms": int(ts[1])
                    })
                    # 检测相邻字之间的静音
                    if prev_end_ms is not None:
                        gap = int(ts[0]) - prev_end_ms
                        if gap > 500:
                            all_silences.append({
                                "start_ms": prev_end_ms,
                                "end_ms": int(ts[0]),
                                "duration_ms": gap
                            })
                    prev_end_ms = int(ts[1])
                    char_idx += 1
            else:
                # 标点字符不对应timestamp,用于分句
                all_chars.append({
                    "char": ch,
                    "start_ms": -1,   # 标点无时间戳
                    "end_ms": -1
                })

    # 按标点分句
    sentences = build_sentences(all_chars)
    return sentences, all_silences


def build_sentences(chars):
    """
    从字列表构建句子。
    按句末标点(。！？)分句,每句包含 start_ms 和 end_ms。
    """
    sentences = []
    current_chars = []

    for c in chars:
        current_chars.append(c)
        if c["char"] in SENTENCE_END_PUNCT:
            # 句末标点,结束当前句子
            sent = finalize_sentence(current_chars)
            if sent:
                sentences.append(sent)
            current_chars = []

    # 处理最后未以标点结尾的部分
    if current_chars:
        sent = finalize_sentence(current_chars)
        if sent:
            sentences.append(sent)

    return sentences


def finalize_sentence(chars):
    """将字列表转为句子对象"""
    text = "".join(c["char"] for c in chars).strip()
    if not text:
        return None

    # 找到第一个和最后一个有时间戳的字
    timed = [c for c in chars if c["start_ms"] >= 0]
    if not timed:
        return None

    start_ms = timed[0]["start_ms"]
    end_ms = timed[-1]["end_ms"]

    return {
        "text": text,
        "start_ms": start_ms,
        "end_ms": end_ms
    }


def run(video_path):
    """完整阶段1流程"""
    if not os.path.exists(video_path):
        print(f"错误: 视频文件不存在: {video_path}")
        sys.exit(1)

    os.makedirs(Config.TEMP_DIR, exist_ok=True)
    audio_path = os.path.join(Config.TEMP_DIR, "audio.wav")

    # 步骤0: 后台立即加载模型(与抽音并行)
    preload_thread = _start_model_preload()

    # 步骤1: 抽音轨
    print(f"[1/3] 抽取音轨: {video_path}")
    extract_audio(video_path, audio_path)

    # 步骤2: FunASR转写(模型已在后台加载, 最多等加载完成)
    print(f"[2/3] FunASR转写中(CPU模式,请耐心等待)...")
    sentences, silences = transcribe(audio_path, preload_thread)
    print(f"  转写完成: {len(sentences)}句, {len(silences)}个静音段")

    # 优先用媒体真实时长(ffprobe口径), 失败回退末句时间戳
    real_ms = probe_duration_ms(video_path)
    duration_ms = real_ms if real_ms else (sentences[-1]["end_ms"] if sentences else 0)

    transcript = {
        "video_path": os.path.abspath(video_path),
        "duration_ms": duration_ms,
        "sentences": sentences,
        "silences": silences
    }

    output_path = os.path.join(Config.TEMP_DIR, "transcript.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)

    print(f"[3/3] 转写结果已保存: {output_path}")
    print(f"  句子数: {len(sentences)}")
    print(f"  静音段数: {len(silences)}")
    print(f"  视频时长: {duration_ms / 1000 / 60:.1f}分钟")

    # 清理临时音频
    try:
        if os.path.exists(audio_path):
            os.remove(audio_path)
    except OSError:
        pass  # 沙箱环境回收站不可用时 os.remove 会被拦截, 残留临时文件无害

    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python asr_pipeline.py <视频文件路径>")
        sys.exit(1)
    run(sys.argv[1])
