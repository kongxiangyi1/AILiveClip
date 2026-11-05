# -*- coding: utf-8 -*-
"""环境自检: 一次性查清"能不能跑", 缺什么直接给命令。

用法(在目标工作区根目录执行):
    python <SCRIPT_DIR>/doctor.py            # 人读
    python <SCRIPT_DIR>/doctor.py --json     # 给 Agent 解析

只做只读检查(唯一的写操作是在工作区下创建 temp/ output/ .pay_cache/ 以验证可写)。
不联网、不付款、不改动任何授权文件。

退出码: 0 = 可以开始流水线; 1 = 有阻断项(见 [FAIL] 行); 2 = 自检本身跑不起来。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(SCRIPT_DIR)
MIN_PY = (3, 10)
PRICE_CNY_EXPECTED = "19.90"
# 买断价一致性检查用(与 payment_gate.py 的 AI_CLIP_PRICE_CNY 默认值同源)
REQUIRED_SCRIPTS = [
    "payment_gate.py", "licensing.py", "asr_pipeline.py", "transcribe_chunked.py",
    "segmenter.py", "cut_pipeline.py", "compliance_scan.py", "compliance_rules.json",
    "errors.py", "doctor.py",
    "config.py", "clip_registry.py", "clip_status.py", "update_registry.py",
    "mute_profanity.py", "review_checklist.py", "fix_brand_name.py",
]

results: list[dict] = []


def add(level: str, item: str, detail: str = "", fix: str = ""):
    results.append({"level": level, "item": item, "detail": detail, "fix": fix})


def check_python():
    v = sys.version_info[:3]
    if sys.version_info < MIN_PY:
        add("FAIL", "Python 版本", f"当前 {'.'.join(map(str, v))}, 要求 >= {'.'.join(map(str, MIN_PY))}",
            "安装 Python 3.10+ 后用该解释器重跑")
    else:
        add("OK", "Python 版本", f"{'.'.join(map(str, v))}  ({sys.executable})")


def check_module(mod: str, label: str, pip_name: str, required: bool, note: str = ""):
    """用 find_spec 探测, 不执行 import —— funasr 一 import 就带起 torch, 自检会卡几十秒。"""
    import importlib.util
    from importlib.metadata import PackageNotFoundError, version
    try:
        found = importlib.util.find_spec(mod) is not None
    except (ImportError, ValueError):
        found = False
    if found:
        try:
            ver = version(pip_name)
        except PackageNotFoundError:
            ver = "已安装"
        add("OK", label, ver)
        return True
    add("FAIL" if required else "WARN", label, "未安装" + (f" ({note})" if note else ""),
        f"pip install {pip_name}")
    return False


def _run(cmd: list[str], timeout: int = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def check_ffmpeg():
    """核心坑位: 系统 PATH 里的 ffmpeg 常是精简编译版(无 AAC 编码器)。"""
    exe = None
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        add("FAIL", "imageio-ffmpeg", "未安装, 无法定位完整版 FFmpeg", "pip install imageio-ffmpeg")
        return
    if not exe or not os.path.exists(exe):
        add("FAIL", "FFmpeg 可执行文件", f"路径不存在: {exe}", "pip install --force-reinstall imageio-ffmpeg")
        return
    add("OK", "FFmpeg(imageio-ffmpeg 自带)", exe)

    rc, out = _run([exe, "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-t", "0.1",
                    "-c:a", "aac", "-f", "null", "-"])
    if rc == 0:
        add("OK", "FFmpeg AAC 编码", "实测可编码, 是完整版")
    else:
        add("FAIL", "FFmpeg AAC 编码", "该 ffmpeg 缺 AAC 编码器(精简版): " + out.strip()[:160],
            "切勿改用系统 PATH 的 ffmpeg; 重装 imageio-ffmpeg: pip install --force-reinstall imageio-ffmpeg")

    rc2, fmts = _run([exe, "-hide_banner", "-encoders"])
    if rc2 == 0 and "libx264" not in fmts:
        add("WARN", "FFmpeg libx264", "未列出 libx264, 阶段4 精确重编码可能失败", "换用完整版 ffmpeg")

    sys_ff = shutil.which("ffmpeg")
    if sys_ff and os.path.normcase(sys_ff) != os.path.normcase(exe):
        add("INFO", "PATH 中另有 ffmpeg", f"{sys_ff} (脚本不使用它, 一律走 imageio-ffmpeg 自带版)")


def check_config_and_licence():
    cfg_path = os.path.join(PKG_DIR, "config.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        add("FAIL", "config.json", f"读取/解析失败: {e}", "确认技能包完整, 或重新下载安装")
        return
    add("OK", "config.json", cfg_path)

    pub = (cfg.get("license") or {}).get("public_key_pem", "")
    if not pub:
        add("FAIL", "授权公钥", "config.json 的 license.public_key_pem 为空, 无法本地验签",
            "重新下载完整包; 作者自建分发需自行写入公钥")
    if str(cfg.get("price_cny", "")) != PRICE_CNY_EXPECTED:
        add("WARN", "买断价一致性", f"包内 price_cny={cfg.get('price_cny')}, 期望 {PRICE_CNY_EXPECTED}",
            "三处价格须一致: 支付宝登记价 = 402 账单额 = 展示价")

    svc = (os.environ.get("AI_CLIP_BILLING_URL", "").strip()
           or str(cfg.get("service_url") or "").strip())
    add("INFO", "计费模式", f"402 服务端模式: {svc}" if svc else "本地授权模式(无服务端, 靠作者签发的授权码)")

    # 授权状态: 未激活不是故障(首次运行本就要付费), 只报事实
    sys.path.insert(0, SCRIPT_DIR)
    try:
        import payment_gate
    except Exception as e:  # 依赖缺失时不能把自检也带崩
        add("WARN", "闸口模块", f"payment_gate 导入失败: {e}", "先补齐下方依赖")
        return
    lic = payment_gate.load_license()
    key = lic.get("key", "")
    if not key:
        add("INFO", "本机授权状态", f"未激活(授权文件 {payment_gate.LICENSE_FILE} 不存在) -> "
                                   "首次 probe 会出 402 账单, 付一次后永久直通")
        return
    try:
        import licensing
        ok, payload, reason = licensing.verify_key(key)
    except Exception as e:
        add("WARN", "本机授权状态", f"验签过程异常: {e}", "pip install cryptography")
        return
    if ok:
        add("OK", "本机授权状态", f"已买断, 订单 {payload.get('oid', '')}, "
                                 f"{'永久有效' if licensing.is_perpetual(payload) else '到期 ' + payload.get('exp', '')}")
    else:
        add("FAIL", "本机授权状态", f"验签未通过: {reason}",
            "缺 cryptography 时: pip install cryptography; 其他原因向作者取新授权码后 "
            "python <SCRIPT_DIR>/payment_gate.py activate <授权码>")

    if payment_gate.SERVER_MODE and not shutil.which(os.environ.get("ALIPAY_BOT_CMD", "alipay-bot")):
        add("WARN", "alipay-bot", "买家侧支付 CLI 不在 PATH(仅首次付款需要)",
            "npx -y @alipay/agent-payment@latest install")


def check_package_files():
    """只查运行必需的文件是否到位(安装损坏)。文档间的引用一致性由维护者侧脚本负责,
    不在这里报 —— references/brand-words.md 之类本就该由使用者自建。"""
    missing = [f for f in REQUIRED_SCRIPTS if not os.path.exists(os.path.join(SCRIPT_DIR, f))]
    if missing:
        add("FAIL", "技能包文件", f"scripts/ 缺少: {', '.join(missing)}", "重新下载完整技能包")
    else:
        add("OK", "技能包文件", f"scripts/ 必需 {len(REQUIRED_SCRIPTS)} 个文件齐全")

    tmpl = os.path.join(PKG_DIR, "references", "brand-words.template.md")
    own = os.path.join(PKG_DIR, "references", "brand-words.md")
    if not os.path.exists(tmpl):
        add("WARN", "专有名词对照表", "缺少 references/brand-words.template.md(模板本身)",
            "重新下载完整技能包")
    elif not os.path.exists(own):
        add("INFO", "专有名词对照表", "尚未自建 references/brand-words.md -> "
                                     "标题里的品牌/人名无法自动订正(纪律 #8/#22)",
            "按 references/brand-words.template.md 向内容方索取正名清单后建立")
    else:
        add("OK", "专有名词对照表", own)


def check_workdir():
    root = os.environ.get("AI_LIVE_CLIP_WORKDIR") or os.getcwd()
    add("INFO", "工作根目录", root + ("  (AI_LIVE_CLIP_WORKDIR)" if os.environ.get("AI_LIVE_CLIP_WORKDIR")
                                        else "  (当前目录; 请在目标工作区根目录执行脚本)"))
    for d in ("temp", "output", ".pay_cache"):
        p = os.path.join(root, d)
        try:
            os.makedirs(p, exist_ok=True)
            probe = os.path.join(p, ".doctor_write_probe")
            with open(probe, "w", encoding="utf-8") as f:
                f.write("ok")
            os.remove(probe)
            add("OK", f"可写目录 {d}/", p)
        except OSError as e:
            add("FAIL", f"可写目录 {d}/", f"{p} 不可写: {e}",
                "换到可写的工作区目录, 或设 AI_LIVE_CLIP_WORKDIR 指向它")

    free_gb = shutil.disk_usage(root).free / 1024 ** 3
    if free_gb < 10:
        add("WARN", "磁盘剩余", f"{free_gb:.1f} GB, 模型 1-2GB + 切片产物按源体积预留", "清理出 >=10 GB 再跑")
    else:
        add("OK", "磁盘剩余", f"{free_gb:.1f} GB")


def check_resources():
    cpu = os.cpu_count() or 4
    mem_gb = 0.0
    try:
        import psutil
        mem_gb = psutil.virtual_memory().total / 1024 ** 3
    except ImportError:
        if platform.system() == "Windows":
            try:
                import ctypes

                class MEMORYSTATUSEX(ctypes.Structure):
                    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32)] + \
                               [(f"ull{i}", ctypes.c_uint64) for i in
                                ("TotalPhys", "AvailPhys", "TotalPageFile", "AvailPageFile",
                                 "TotalVirtual", "AvailVirtual", "AvailExtendedVirtual")]

                m = MEMORYSTATUSEX()
                m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                    mem_gb = m.ullTotalPhys / 1024 ** 3
            except Exception:
                mem_gb = 0.0
    if mem_gb <= 0:
        add("INFO", "CPU / 内存", f"{cpu} 核 (内存未探测到; 并行数按 CPU 保守取值, "
                                  "pip install psutil 可精确判定)")
        workers = max(2, min(4, cpu // 4))
    else:
        workers = max(2, min(4, cpu // 4, int(mem_gb // 8)))
        add("INFO", "CPU / 内存", f"{cpu} 核 / {mem_gb:.0f} GB")
    add("INFO", "建议并行数", f"转写 --workers {workers}; 切割 --workers 2(本机可到 {min(4, cpu // 4)})")


def _scan_cache_dirs(root: str, keywords: tuple[str, ...], max_depth: int = 2,
                     budget_s: float = 2.0) -> list[str]:
    """有界扫描模型缓存目录。递归 glob 会走完整棵模型树, 慢到不可用。"""
    import time
    t0 = time.monotonic()
    hits: list[str] = []
    frontier = [root]
    for _ in range(max_depth):
        nxt: list[str] = []
        for d in frontier:
            if time.monotonic() - t0 > budget_s:
                return hits
            try:
                with os.scandir(d) as it:
                    for e in it:
                        if not e.is_dir():
                            continue
                        if any(k in e.name.lower() for k in keywords):
                            hits.append(e.path)
                        else:
                            nxt.append(e.path)
            except OSError:
                continue
        frontier = nxt
        if not frontier:
            break
    return hits


def check_models():
    """FunASR 首次运行要下载 1-2GB 模型; 已缓存则省时间。"""
    home = os.path.expanduser("~")
    roots = [os.path.join(home, ".cache", "modelscope", "hub"),
             os.path.join(home, ".cache", "torch", "hub")]
    keywords = ("paraformer", "fsmn", "punc")
    hits: list[str] = []
    for r in roots:
        if os.path.isdir(r):
            hits.extend(_scan_cache_dirs(r, keywords))
            if hits:
                break
    if hits:
        add("OK", "FunASR 模型缓存", f"{len(hits)} 项, 首个: {hits[0]}")
    else:
        add("WARN", "FunASR 模型缓存", "未找到 paraformer/vad/punc 缓存, 首次转写会下载约 1-2 GB",
            "耐心等待; 下载慢时按 references/funasr-install.md 切镜像源")


def main() -> int:
    ap = argparse.ArgumentParser(description="直播切片工具环境自检")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出(供 Agent 解析)")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except (AttributeError, OSError, ValueError):
        pass

    check_python()
    check_module("funasr", "FunASR(阶段1 转写)", "funasr", True, "阶段1 必需")
    check_module("imageio_ffmpeg", "imageio-ffmpeg 包", "imageio-ffmpeg", True, "阶段4 必需")
    check_module("cryptography", "cryptography(闸口验签)", "cryptography", True, "probe/activate 必需")
    check_module("streamlit", "streamlit(可选审核界面)", "streamlit", False, "仅 app.py 需要")
    check_ffmpeg()
    check_config_and_licence()
    check_package_files()
    check_workdir()
    check_resources()
    check_models()

    fails = [r for r in results if r["level"] == "FAIL"]
    warns = [r for r in results if r["level"] == "WARN"]

    if args.json:
        print(json.dumps({"ready": not fails, "fail": len(fails), "warn": len(warns),
                          "items": results}, ensure_ascii=False, indent=2))
    else:
        icon = {"OK": "  [ OK ]", "INFO": "  [INFO]", "WARN": "  [WARN]", "FAIL": "  [FAIL]"}
        print("=" * 72)
        print("直播切片工具 · 环境自检")
        print("=" * 72)
        for r in results:
            print(f"{icon[r['level']]} {r['item']}: {r['detail']}")
            if r["level"] in ("FAIL", "WARN") and r["fix"]:
                print(f"         -> {r['fix']}")
        print("-" * 72)
        if fails:
            print(f"[结论] {len(fails)} 个阻断项, 先按上面的 -> 修复再重跑本脚本。")
            print("       依赖类问题一条命令通常即可: "
                  "python -m pip install funasr imageio-ffmpeg cryptography")
        elif warns:
            print(f"[结论] 可以开始流水线; {len(warns)} 个提示项(不阻断), 见上面 [WARN]。")
        else:
            print("[结论] 环境就绪, 可以从付费闸口开始: "
                  "python \"<安装目录>/scripts/payment_gate.py\" probe")
    return 1 if fails else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # 自检本身出错也要说人话
        print(f"[自检异常] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
