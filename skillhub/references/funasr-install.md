# FunASR 安装与下载源选择（首次使用必读）

本技能阶段1 依赖 **FunASR**（阿里达摩院开源语音识别）做转写。首次使用前需在本机 Python 环境安装它及配套依赖。**真正的下载瓶颈是 `torch`（几百 MB～GB 级）**，它和 `funasr` 走同一个 pip 镜像，因此"选对镜像"对整体安装耗时影响很大。

## 一、获取 FunASR 的几种方式

| 方式 | 适用场景 | 命令 |
|---|---|---|
| **pip 安装（推荐）** | 绝大多数情况，本技能即用此方式 | `pip install funasr` |
| 源码安装 | 需修改 `examples/` 或跑流式 WebSocket 服务时 | `git clone https://github.com/modelscope/FunASR.git && cd FunASR && pip install -e ./` |
| 在线体验 | 不想本地配环境，浏览器跑公开样例 | 打开 https://colab.research.google.com/github/modelscope/FunASR/blob/main/examples/colab/funasr_quickstart.ipynb |
| 模型权重 | 首次转写时**自动**从 ModelScope 下载，无需手动下 | 见文末"模型权重从哪下" |

## 二、pip 镜像下载速度排序（2026-08-17 实测）

以下为 `funasr==1.4.2` wheel（954 KB）各源的**下载带宽实测**，由快到慢：

| 排名 | 镜像源 | 实测速度 | 索引地址 | 说明 |
|---|---|---|---|---|
| 🥇 1 | **腾讯云** | ~4.7 MB/s | `https://mirrors.cloud.tencent.com/pypi/simple` | 实测最快，**推荐** |
| 🥈 2 | 华为云 | ~2.5 MB/s | `https://mirrors.huaweicloud.com/repository/pypi/simple` | |
| 🥉 3 | 阿里云 | ~2.1 MB/s | `https://mirrors.aliyun.com/pypi/simple` | |
| 4 | 清华 tuna | ~2.4 MB/s | `https://pypi.tuna.tsinghua.edu.cn/simple` | pip 实测正常；urllib 直连 `/packages/` 会被防盗链拦 403，不影响 pip 安装 |
| 5 | PyPI 官方 | ~1.2 MB/s | `https://pypi.org/simple` | 境外，最慢 |

> 速度受网络环境影响，以上为单次实测参考值。核心结论：**国内镜像（尤其腾讯云）远快于 PyPI 官方**，安装 `torch` 等大依赖时差距更明显。

## 三、推荐安装命令（用最快的腾讯云镜像）

```powershell
pip install funasr torch torchaudio imageio-ffmpeg streamlit pillow numpy `
  -i https://mirrors.cloud.tencent.com/pypi/simple
```

> `torch` 体积大（CPU 版约 200MB+，CUDA 版数 GB）。无 NVIDIA 显卡用 CPU 版即可；有显卡且要 GPU 加速则装 CUDA 版 torch。

## 四、将腾讯云设为默认源（一劳永逸）

```powershell
pip config set global.index-url https://mirrors.cloud.tencent.com/pypi/simple
```

之后直接 `pip install funasr ...` 即走最快源。查看当前源：`pip config list`。

## 五、模型权重从哪下

FunASR 首次运行转写时，会自动从 **ModelScope**（`https://modelscope.cn`，国内直连可达）下载识别模型（`paraformer-zh` / `fsmn-vad` / `ct-punc` 等），**无需切换 pip 源、无需手动下载**。如想指定缓存目录：

```powershell
$env:MODELSCOPE_CACHE = "D:\modelscope_cache"          # 示例路径, 改成你的目录; 不设置则用 ModelScope 默认缓存位置
```

## 六、版本与运行环境

- 实测最新版 `funasr==1.4.2`（2026-08）。技能不锁版本，`pip install funasr` 默认装最新即可。
- Python 要求 ≥ 3.8；本技能在 **Windows + Python 3.12** 实测通过。
- 无 NVIDIA 显卡时，`asr_pipeline.py` 里模型 `device` 设为 `"cpu"`；有显卡设 `"cuda"`。

## 七、本机踩坑（Windows + uv 沙箱，2026-08-18 实测）

**不要用 `uv` 安装本技能依赖。** 本机 `uv` 在自己的 Windows 沙箱里把删除包装成"回收站 safe-delete"，回收站不可用时构建期删除失败 → `jieba`/`oss2` 等 **sdist 包构建失败**（`[safe-delete][SAFE_DELETE_FAIL_CLOSED]`）。`uv pip install` 和 `uv venv`+`python -m pip` 都会踩。

- **正确做法**：建好 venv 后用 `python -m ensurepip` 引导 pip，再 `python -m pip install ...`（需在 Bash 有写权限下跑）。
- **绝对不要 `--only-binary :all:`**：`funasr` 依赖 `oss2`，它**只有 sdist、没有 wheel**，强制纯二进制直接失败。
- **重安装务必前台 + 长 timeout（≥600s）**：后台 + 沙箱关闭的 Bash 命令不会被保留（会被杀），导致"装了半天环境是空的"。
- 实测成功命令（腾讯云镜像，约几分钟）：
  ```powershell
  uv venv --python 3.12 <VENV>
  <VENV>/Scripts/python.exe -m ensurepip
  <VENV>/Scripts/python.exe -m pip install funasr torch torchaudio imageio-ffmpeg streamlit pillow numpy `
    -i https://mirrors.cloud.tencent.com/pypi/simple
  ```
- 更多本机运维/工具坑（ffprobe 路径、路径对齐、验收脚本陷阱等）见 `references/lessons-learned.md`。
