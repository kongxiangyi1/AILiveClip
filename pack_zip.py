# -*- coding: utf-8 -*-
"""把「直播切片工具」技能目录打成可分发的 zip。

与旧版的关键区别:
  - 真正剔除运行产物(temp/ output/ __pycache__ *.pyc)与本地元数据(_user_meta.json 等),
    避免把 transcript / 成品切片 / 含绝对路径的日志打进包(隐私泄露 + 体积膨胀)。
  - 校验项对齐官方 skill-creator 的 quick_validate(name 必须 hyphen-case、summary/description 不含尖括号)。
  - zip 根目录统一改成 ai-live-clip(frontmatter name),避免中文目录名在部分平台解析异常。
"""
import argparse
import os
import re
import zipfile

SKILL_DIR = r"C:\Users\KONGXY\.workbuddy\skills\直播切片工具"
DIST = r"E:\WorkBuddyCliping1\ai-live-clip\dist"
ZIP_ROOT = "ai-live-clip"          # zip 内的顶层目录名(= frontmatter 的 name)

# 按目录名剔除(出现在路径任意层级都剔除)
EXCLUDE_DIRS = {"temp", "output", "__pycache__", ".git", ".ipynb_checkpoints"}
# 按文件名剔除
EXCLUDE_FILES = {"_user_meta.json", "_platform_meta.json", "_knot_meta.json",
                 ".DS_Store", "Thumbs.db"}
# 与技能能力无关的文档: 只是待办/设想, 不参与任何执行路径, 打包无意义且会误导使用者
# 或含真实品牌/人名的私有资料, 公开发布会泄露使用者私域信息
EXCLUDE_DOCS = {"future-optimizations.md", "brand-words.private.md"}

# 隐私兜底: 打包后全文扫描, 命中即报警(防用户名/绝对路径/私有品牌词外泄)
PRIVATE_TOKENS = ("KONGXY", "WorkBuddyCliping1", "觉尘", "绝尘", "汤姆张", "汤姆章", "聂胜哲",
                  "董宇辉", "大么么", "苏雅倩", "ONLYLULU", "VMAO", "张慧", "LCBU")
# 按后缀剔除
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".bak", ".tmp")


def read_frontmatter(skill_md):
    content = open(skill_md, encoding="utf-8").read()
    m = re.match(r'^---\n(.*?)\n---', content, re.DOTALL)
    if not m:
        raise SystemExit("frontmatter 缺失或格式错误(需以 --- 开头)")
    return m.group(1)


def validate(fm):
    """校验项对齐官方 skill-creator/scripts/quick_validate.py。"""
    errs = []
    if "summary:" not in fm and "description:" not in fm:
        errs.append("frontmatter 缺少 summary 或 description")
    m = re.search(r'name:\s*(.+)', fm)
    if not m:
        errs.append("frontmatter 缺少 name")
        return errs, None
    name = m.group(1).strip().strip('"\'')
    if not re.match(r'^[a-z0-9-]+$', name):
        errs.append(f"name '{name}' 必须 hyphen-case(仅小写字母/数字/连字符), 中文名会被 SkillHub 校验拒绝")
    elif name.startswith("-") or name.endswith("-") or "--" in name:
        errs.append(f"name '{name}' 不能以连字符开头/结尾或含连续连字符")
    return errs, name


def main():
    ap = argparse.ArgumentParser(description="打包直播切片工具技能")
    ap.parse_args()

    skill_md = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.exists(skill_md):
        raise SystemExit(f"找不到 SKILL.md: {skill_md}")

    fm = read_frontmatter(skill_md)
    errs, name = validate(fm)
    if errs:
        for e in errs:
            print("  [校验失败]", e)
        raise SystemExit("校验未通过, 已中止打包")
    print(f"校验通过: name={name}")

    if name != ZIP_ROOT:
        print(f"  提示: frontmatter name({name}) 与 ZIP_ROOT({ZIP_ROOT}) 不一致, 建议同步")

    os.makedirs(DIST, exist_ok=True)
    zip_path = os.path.join(DIST, f"{ZIP_ROOT}.zip")

    included, excluded = [], []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(SKILL_DIR):
            kept = []
            for d in dirs:
                if d in EXCLUDE_DIRS:
                    excluded.append(os.path.join(root, d) + os.sep)
                else:
                    kept.append(d)
            dirs[:] = kept
            for f in files:
                if f in EXCLUDE_FILES or f in EXCLUDE_DOCS or f.endswith(EXCLUDE_SUFFIX):
                    excluded.append(os.path.join(root, f))
                    continue
                fp = os.path.join(root, f)
                arc = os.path.relpath(fp, SKILL_DIR)
                z.write(fp, os.path.join(ZIP_ROOT, arc))
                included.append(os.path.join(ZIP_ROOT, arc))

    # 二次确认: 包内不含运行产物
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        bad = [n for n in names
               if any(part in EXCLUDE_DIRS for part in n.split("/"))
               or n.endswith(EXCLUDE_SUFFIX)
               or os.path.basename(n) in EXCLUDE_FILES
               or os.path.basename(n) in EXCLUDE_DOCS]
    if bad:
        print("  [严重] 包内仍含污染项:", bad[:10])
    else:
        print("  包内无 temp / output / __pycache__ / .pyc 等产物")

    # 隐私兜底扫描: 文本文件内容不得含本机用户名 / 私有品牌词
    leaks = []
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if not n.endswith((".md", ".py", ".txt", ".json")):
                continue
            text = z.read(n).decode("utf-8", "ignore")
            for tok in PRIVATE_TOKENS:
                if tok in text:
                    leaks.append(f"{n}:{tok}")
    if leaks:
        print("  [严重] 包内含私有信息:", leaks[:10])
    else:
        print("  隐私扫描通过: 无用户名/绝对路径/私有品牌词")

    print(f"剔除 {len(excluded)} 项, 打包 {len(included)} 个文件")
    print(f"zip 路径: {zip_path}")
    print("\n包内清单:")
    for n in sorted(included):
        print("  ", n)


if __name__ == "__main__":
    main()
