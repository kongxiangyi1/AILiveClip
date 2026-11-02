"""配置: FunASR参数、路径、时长阈值"""
import json
import os
import subprocess
import sys

# 脚本所在目录,用于锚定绝对路径
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 工作根目录(temp/output 的父目录):
# 优先环境变量 AI_LIVE_CLIP_WORKDIR, 否则用当前工作目录(CWD)。
# 作为 skill 运行时, 在目标工作区根目录执行脚本, temp/output 即落在该工作区,
# 不污染 skill 安装目录。
_WORK_ROOT = os.environ.get("AI_LIVE_CLIP_WORKDIR") or os.getcwd()

def _get_ffmpeg():
    """获取完整版FFmpeg路径(imageio-ffmpeg自带,非系统精简版)"""
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"

def _detect_video_encoder(ffmpeg_path):
    """探测硬件编码器可用性,返回 ('encoder_name', extra_args)。

    探测顺序: h264_qsv(Intel核显) → libx264 CPU编码兜底。
    通过实际编码一帧小样来验证,避免仅凭 -encoders 输出误判(驱动缺失时编码会失败)。
    结果缓存到 temp/_encoder_cache.json, 避免每次 import 都跑一次 ffmpeg。
    """
    ffmpeg_path = ffmpeg_path or "ffmpeg"
    cache_path = os.path.join(_WORK_ROOT, "temp", "_encoder_cache.json")

    # 读缓存(按 ffmpeg 路径做 key, 换了 ffmpeg 会重新探测)
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cache = json.load(f)
        if cache.get("ffmpeg") == ffmpeg_path:
            return cache["encoder"], cache["args"]
    except (OSError, ValueError, KeyError):
        pass

    # 统一使用 libx264 软编码: QSV 硬件编码在部分环境对非常规帧率/竖屏源
    # (如本机 912x1920 + 90k tbr 的 HEVC TS) 会报
    # "Current frame rate is unsupported / Error while opening encoder",
    # 且 libx264 输出的 H.264 对视频号兼容性最好。如需 QSV 加速可改回 h264_qsv。
    candidates = [
        # ("h264_qsv", []),
    ]

    result = None
    for encoder, extra in candidates:
        probe_output = os.path.join(_WORK_ROOT, "temp", "_enc_probe_out.mp4")
        try:
            if os.path.exists(probe_output):
                os.remove(probe_output)
            # 用320x240极简图片+静音音轨做实际编码验证(QSV不支持极低分辨率如8x8)
            subprocess.run([
                ffmpeg_path, "-y", "-f", "lavfi",
                "-i", "color=c=black:s=320x240:d=0.1:r=25",
                "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono",
                "-t", "0.1",
                "-c:v", encoder, *extra,
                "-c:a", "aac",
                "-f", "null", "NUL"  # Windows 专有; 本项目仅 Windows 使用
            ], check=True, capture_output=True, timeout=30)
            result = (encoder, list(extra))
            break
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            continue

    if result is None:
        # CPU 兜底
        result = ("libx264", ["-preset", "veryfast", "-crf", "23"])

    # 写缓存
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"ffmpeg": ffmpeg_path, "encoder": result[0], "args": result[1]}, f)
    except OSError:
        pass
    return result

class Config:
    # FunASR (CPU模式)
    FUNASR_MODEL = "paraformer-zh"
    FUNASR_VAD = "fsmn-vad"
    FUNASR_PUNC = "ct-punc"
    DEVICE = "cpu"

    # FFmpeg (imageio-ffmpeg完整版,支持AAC解码和WAV封装)
    FFMPEG = _get_ffmpeg()

    # 视频编码器 (探测: 优先 h264_qsv 硬件加速, 回退 libx264 CPU)
    VIDEO_ENCODER, VIDEO_ENCODER_ARGS = _detect_video_encoder(FFMPEG)

    # 切片阈值
    MIN_DURATION_S = 45
    MAX_DURATION_S = 600
    MAX_GAP_MS = 120000  # 合并间隔阈值(2分钟)

    # 路径(绝对路径,锚定到工作根目录 _WORK_ROOT,见文件头)
    OUTPUT_DIR = os.path.join(_WORK_ROOT, "output")
    TEMP_DIR = os.path.join(_WORK_ROOT, "temp")

    # Python路径(当前解释器)
    PYTHON = sys.executable
