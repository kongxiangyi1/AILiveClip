"""Streamlit审核界面: 查看、调整、导出切片

启动: streamlit run clipping/app.py
"""
import json
import os

import streamlit as st

st.set_page_config(page_title="直播切片审核", layout="wide")
st.title("直播切片审核")
st.caption("上传 segments.json,调整切点后导出")

# 文件上传
segments_file = st.file_uploader("上传 segments.json", type=["json"])

if segments_file is None:
    # 尝试读取默认路径(锚定到脚本所在目录)
    default_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp", "segments.json")
    if os.path.exists(default_path):
        with open(default_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        st.info(f"已自动加载: {default_path}")
    else:
        st.stop()
else:
    data = json.load(segments_file)

segments = data.get("segments", data.get("clips", []))
video_path = data["video_path"]

# 视频文件检查
video_exists = os.path.exists(video_path)
if not video_exists:
    st.warning(f"视频文件不存在: {video_path}（无法预览，但仍可调整参数）")

# 浏览器无法直接播放.ts等容器,优先使用转码后的MP4预览文件
preview_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp", "preview.mp4")
preview_exists = os.path.exists(preview_path)
if not preview_exists and video_exists and not video_path.lower().endswith(".mp4"):
    st.warning(
        "未找到 temp/preview.mp4，浏览器无法直接预览非 MP4 源视频。"
        "重新运行 asr_pipeline.py / transcribe_chunked.py 会自动生成预览文件。"
    )

# 删除状态按数据源重置,同一个文件内跨rerun保持
if segments_file is not None:
    source_key = f"upload:{segments_file.name}:{segments_file.size}"
else:
    try:
        stat = os.stat(default_path)
        source_key = f"default:{default_path}:{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        source_key = f"default:{default_path}"

if st.session_state.get("segments_key") != source_key:
    st.session_state.segments_key = source_key
    st.session_state.deleted_set = set()
else:
    st.session_state.setdefault("deleted_set", set())

# 统计信息
col_a, col_b, col_c = st.columns(3)
col_a.metric("切片数", len(segments))
low_conf = sum(1 for s in segments if s.get("confidence") == "low")
col_b.metric("需复核", low_conf)
total_dur = sum(
    s.get("duration_s", (s["end_ms"] - s["start_ms"]) / 1000) for s in segments
)
col_c.metric("总时长", f"{total_dur / 60:.1f}分钟")

st.divider()

# 切片列表
approved = []

for i, seg in enumerate(segments):
    conf = seg.get("confidence", "high")
    color = "🔴" if conf == "low" else "🟢"
    dur = seg.get("duration_s", (seg["end_ms"] - seg["start_ms"]) / 1000)
    is_deleted = i in st.session_state.deleted_set
    status_tag = " ~~已删除~~" if is_deleted else ""

    with st.expander(f"{color} #{i + 1} {seg['title']} ({dur:.0f}s){status_tag}"):
        col1, col2 = st.columns([3, 2])

        with col1:
            new_title = st.text_input("标题", value=seg["title"], key=f"title_{i}")
            c_start, c_end = st.columns(2)
            new_start = c_start.number_input(
                "开始(ms)", value=seg["start_ms"], step=1000, key=f"start_{i}"
            )
            new_end = c_end.number_input(
                "结束(ms)", value=seg["end_ms"], step=1000, key=f"end_{i}"
            )
            new_dur = (new_end - new_start) / 1000
            st.caption(f"时长: {new_dur:.1f}s")

            btn_col1, btn_col2 = st.columns(2)
            keep = btn_col1.button("保留", key=f"keep_{i}", type="primary")
            delete = btn_col2.button("删除", key=f"del_{i}")

            # 用session_state持久化删除状态,避免rerun后丢失
            if delete:
                st.session_state.deleted_set.add(i)
            if keep:
                st.session_state.deleted_set.discard(i)

            if i not in st.session_state.deleted_set:
                approved.append({
                    "title": new_title,
                    "start_ms": int(new_start),
                    "end_ms": int(new_end),
                    "duration_s": new_dur,
                    "confidence": conf,
                    "topic_raw": seg.get("topic_raw", "")
                })

        with col2:
            if preview_exists:
                st.video(preview_path, start_time=seg["start_ms"] / 1000)
            elif video_exists:
                st.video(video_path, start_time=seg["start_ms"] / 1000)

# 导出
st.divider()
st.subheader("导出")

if approved:
    deleted_count = len(st.session_state.deleted_set)
    st.success(f"保留 {len(approved)} 个切片,删除 {deleted_count} 个")
    output = {"video_path": video_path, "segments": approved}
    output_json = json.dumps(output, ensure_ascii=False, indent=2)
    st.download_button(
        "下载确认的 segments.json",
        output_json,
        file_name="segments_approved.json",
        mime="application/json"
    )
    st.caption("下载后用 cut_pipeline.py 切割: python cut_pipeline.py segments_approved.json")
else:
    st.warning("没有保留任何切片")
