# -*- coding: utf-8 -*-
"""实测各 pip 镜像下载 FunASR 包的速度并排名。"""
import json, time, urllib.request

UA = {"User-Agent": "Mozilla/5.0"}

def get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout)

# 1) 拿到 funasr 最新 wheel 的 /packages/ 路径
for jurl in ("https://pypi.org/pypi/funasr/json",
             "https://pypi.tuna.tsinghua.edu.cn/pypi/funasr/json"):
    try:
        data = json.load(get(jurl))
        break
    except Exception:
        continue
ver = data["info"]["version"]
whl = next(u for u in data["urls"] if u["packagetype"] == "bdist_wheel")
pkg_path = whl["url"][whl["url"].index("/packages/"):]
print(f"最新版本: {ver}  wheel: {whl['url'].split('/')[-1]}  ({whl['size']/1024:.0f} KB)\n")

mirrors = {
    "PyPI 官方 (files.pythonhosted.org)": "https://files.pythonhosted.org" + pkg_path,
    "清华 tuna": "https://pypi.tuna.tsinghua.edu.cn" + pkg_path,
    "阿里云 aliyun": "https://mirrors.aliyun.com/pypi" + pkg_path,
    "腾讯云 tencent": "https://mirrors.cloud.tencent.com/pypi" + pkg_path,
    "华为云 huaweicloud": "https://mirrors.huaweicloud.com/repository/pypi" + pkg_path,
}

def speed(name, url):
    try:
        t0 = time.time(); total = 0
        with get(url, timeout=40) as r:
            while True:
                c = r.read(65536)
                if not c:
                    break
                total += len(c)
        dt = time.time() - t0
        return (name, total / dt / 1024, total, dt, None)
    except Exception as e:
        return (name, 0, 0, 0, str(e)[:50])

results = []
for n, u in mirrors.items():
    res = speed(n, u)
    results.append(res)
    if res[4] is None:
        print(f"  {n:38s} {res[1]:9.1f} KB/s  ({res[2]/1024:6.0f} KB / {res[3]:.1f}s)")
    else:
        print(f"  {n:38s} 失败: {res[4]}")

ok = [r for r in results if r[4] is None]
ok.sort(key=lambda x: -x[1])
print("\n=== 下载速度排名 ===")
for i, (n, sp, *_ ) in enumerate(ok, 1):
    print(f"  {i}. {n}: {sp:.1f} KB/s  (~{sp/1024:.2f} MB/s)")
if not ok:
    print("  所有镜像均不可达（可能沙箱限制外网直连）")
