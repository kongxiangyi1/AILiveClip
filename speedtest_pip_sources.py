# -*- coding: utf-8 -*-
"""用统一的 pip download 方法实测各源下载 FunASR 的真实速度并排名。"""
import subprocess, time, os, sys, tempfile

PY = sys.executable
# (显示名, 镜像 simple 索引地址)
sources = [
    ("腾讯云",   "https://mirrors.cloud.tencent.com/pypi/simple"),
    ("华为云",   "https://mirrors.huaweicloud.com/repository/pypi/simple"),
    ("阿里云",   "https://mirrors.aliyun.com/pypi/simple"),
    ("清华 tuna", "https://pypi.tuna.tsinghua.edu.cn/simple"),
    ("PyPI 官方", "https://pypi.org/simple"),
]
vers = "1.4.2"
whl_bytes = 954 * 1024  # funasr wheel 近似大小

results = []
for name, idx in sources:
    d = tempfile.mkdtemp(prefix=f"pipdl_{name}_")
    t0 = time.time()
    try:
        subprocess.run(
            [PY, "-m", "pip", "download", f"funasr=={vers}", "--no-deps",
             "-i", idx, "-d", d],
            capture_output=True, text=True, timeout=120,
        )
        dt = time.time() - t0
        sp = whl_bytes / dt / 1024 / 1024  # MB/s
        results.append((name, sp, dt, None))
        print(f"{name:12s} {sp:6.2f} MB/s  ({dt:.1f}s)")
    except Exception as e:
        results.append((name, 0, 0, str(e)[:40]))
        print(f"{name:12s} 失败: {e}")
    finally:
        try:
            import shutil
            shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass

results.sort(key=lambda x: -x[1])
print("\n=== 统一 pip download 实测排名 ===")
for i, (n, sp, dt, _ ) in enumerate(results, 1):
    print(f"  {i}. {n}: {sp:.2f} MB/s")
