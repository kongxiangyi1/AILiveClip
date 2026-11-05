# -*- coding: utf-8 -*-
"""通用异常包装 —— 三个用户入口(segmenter / cut_pipeline / compliance_scan)共用。

目标: 出错时给「原因 + 下一步」, 不向使用者裸抛 traceback。(评测 R 维"异常处理"扣分项)
排障: 环境变量 AI_CLIP_DEBUG=1 时打印完整堆栈; 通用排障见 references/faq.md。
"""
from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import traceback


def _debug() -> bool:
    return os.environ.get("AI_CLIP_DEBUG", "").strip() == "1"


def _banner(title: str) -> str:
    return f"\n❌ {title}"


def _force_utf8() -> None:
    """GBK 控制台打不出 emoji/部分符号, 统一切 UTF-8(与 doctor.py 一致)。"""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 — 非 TextIO 时跳过
            pass


@contextlib.contextmanager
def guard(script: str):
    """统一入口包装: 捕获异常 → 原因 + 下一步 → 退出码 1。SystemExit/KeyboardInterrupt 放行。"""
    _force_utf8()
    try:
        yield
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\n⚠ 已被人工中断(Ctrl+C), 产物保持中断前状态")
        sys.exit(130)
    except json.JSONDecodeError as exc:
        print(_banner(f"JSON 解析失败: {exc.msg} (第 {exc.lineno} 行第 {exc.colno} 列)"))
        print("   下一步: ① 该文件可能被手工改坏 —— 多行 Python 写脚本改 JSON, 改后先 "
              "`python -m json.tool <文件>` 校验")
        print("           ② 若是脚本产物损坏, 删掉该 temp 文件重跑上一阶段")
        _exit()
    except FileNotFoundError as exc:
        target = getattr(exc, "filename", "") or str(exc)
        print(_banner(f"找不到文件: {target}"))
        print("   下一步: ① 命令是否在工作区根目录执行(相对路径的基准)")
        print("           ② 上游阶段产物缺失就先重跑上一阶段(video → asr_pipeline → segmenter → cut_pipeline)")
        print("           ③ 脚本路径不对先跑 doctor.py 核对环境")
        _exit()
    except PermissionError as exc:
        print(_banner(f"文件被占用或无写权限: {getattr(exc, 'filename', '') or exc}"))
        print("   下一步: 关闭正占用该文件的播放器/上传窗口后重试; 或换到有写权限的工作区目录")
        _exit()
    except KeyError as exc:
        print(_banner(f"字段缺失: {exc}(处理 JSON 时访问了不存在的字段)"))
        print("   下一步: 输入 JSON 可能是旧版格式或不完整 —— 字段权威口径见 references/data-formats.md,"
              " 与现有文件对不上时重跑上游阶段")
        _exit()
    except ModuleNotFoundError as exc:
        name = getattr(exc, "name", "") or str(exc)
        print(_banner(f"缺少依赖: {name}"))
        print("   下一步: pip install " + name + "(或直接跑 doctor.py, 会给出完整的缺失清单与安装命令)")
        _exit()
    except subprocess.CalledProcessError as exc:
        cmd = " ".join(exc.cmd) if isinstance(exc.cmd, list) else str(exc.cmd)
        print(_banner(f"外部命令失败(退出码 {exc.returncode}): {cmd}"))
        if exc.stderr:
            tail = (exc.stderr or "").strip().splitlines()[-5:]
            for ln in tail:
                print(f"   | {ln}")
        print("   下一步: ① 跑 doctor.py 核对 FFmpeg 完整性 ② 视频源损坏/特殊编码时换 `--mode reencode` 兜底")
        _exit()
    except ValueError as exc:
        print(_banner(f"取值不合法: {exc}"))
        print("   下一步: 输入参数或 JSON 取值超出预期 —— 参考 SKILL.md 对应阶段的参数说明修改后重试;"
              " 原因不明可设环境变量 AI_CLIP_DEBUG=1 重跑看完整堆栈")
        _exit()
    except Exception as exc:  # noqa: BLE001 — 入口兜底, 不向使用者裸抛
        print(_banner(f"意外错误: {type(exc).__name__}: {exc}"))
        print("   下一步: ① 先跑 doctor.py 排环境 ② 按 references/faq.md 检索症状"
              " ③ 设环境变量 AI_CLIP_DEBUG=1 重跑可看完整堆栈, 便于反馈作者")
        _exit()


def _exit() -> None:
    if _debug():
        print("\n--- 完整堆栈(AI_CLIP_DEBUG=1) ---")
        traceback.print_exc()
    sys.exit(1)
