# -*- coding: utf-8 -*-
"""文档一致性自检 —— 发版前必跑(评测扣分主因是「说明与实物不一致」, 本测试防止复发)。

检查项:
  1. 所有文档中引用的 scripts/*.py 必须真实存在(含 $SCRIPT_DIR、<SCRIPT_DIR>、scripts/ 三种写法)
  2. 不允许再出现「裸文件名」命令(python xxx.py), 那在工作区根目录必然 FileNotFound
  3. 文档中引用的 references/*.md 必须存在(private 文件属于使用者自建, 允许列表放行)
  4. SKILL.md 正文不再写死 x.y.z 版本号(只允许出现在 frontmatter)
  5. 纪律条数表述必须与 discipline.md 实际条数一致
  6. 引用包外计费服务端源码(payserver/)的行必须带「不在本包内/独立维护/不随」等标注

用法: python tests/test_docs_consistency.py   (scripts/tests 目录下直接跑)
退出码: 0 = 全部通过 / 1 = 存在不一致
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PKG = Path(__file__).resolve().parents[2]
DOCS = [PKG / "SKILL.md", PKG / "README.md",
        *sorted((PKG / "references").glob("*.md")),
        *sorted((PKG / "docs-maintainer").glob("*.md"))]
# private / brand-words.md 都是使用者自建资产, 文档教「复制 template 自建」是预期行为,
# 它们本来就不随包分发, 不能当成「引用了不存在的文档」。
REF_ALLOWLIST = {"brand-words.private.md", "brand-words.md"}
EXPECTED_DISCIPLINE = 28
OUT_OF_PKG_MARKERS = ("不在本包内", "不在本技能包内", "独立维护", "单独维护",
                      "不随", "不在这里", "不作为可执行入口")

violations: list[str] = []


def flag(docname: str, lineno: int, msg: str) -> None:
    violations.append(f"{docname}:{lineno} {msg}")


def check_doc_scripts(text: str, name: str) -> None:
    for m in re.finditer(r"python\s+\"\$SCRIPT_DIR/([\w.-]+\.py)\"", text):
        if not (PKG / "scripts" / m.group(1)).exists():
            flag(name, 0, f"引用了不存在脚本: scripts/{m.group(1)}")
    for m in re.finditer(r"<SCRIPT_DIR>/([\w.-]+\.py)", text):
        if not (PKG / "scripts" / m.group(1)).exists():
            flag(name, 0, f"引用了不存在脚本: scripts/{m.group(1)}")
    for m in re.finditer(r"`?scripts/([\w-]+\.py)", text):
        if not (PKG / "scripts" / m.group(1)).exists():
            flag(name, 0, f"引用了不存在脚本: scripts/{m.group(1)}")
    for m in re.finditer(r"^`(.*?)`", text, re.MULTILINE):
        code = m.group(1)
        if re.search(r"\bstreamlit run\b", code):
            target = re.search(
                r"streamlit run\s+\"?(\$SCRIPT_DIR|<SCRIPT_DIR>)/([\w.-]+\.py)", code)
            if target and not (PKG / "scripts" / target.group(2)).exists():
                flag(name, 0, f"streamlit 目标不存在: {target.group(2)}")
    # 裸文件名命令(不包引号/无 SCRIPT_DIR 前缀)
    # 文档可用 <!-- audit-ignore --> 标注「这行是反例说明」, 不参与本条检查
    for i, line in enumerate(text.splitlines(), 1):
        if "audit-ignore" in line:
            continue
        for m in re.finditer(r"(?<![\w\"'$])python\s+([A-Za-z_][\w-]*\.py)", line):
            script = m.group(1)
            if script in ("temp/", ) or script.startswith("temp"):
                continue
            flag(name, i, f"裸文件名命令(工作区根目录执行必然找不到): python {script}")
        # 两种占位符都合法: $SCRIPT_DIR(shell 变量) / <SCRIPT_DIR>(文档占位)
        if re.search(r"^\s*streamlit run\s+(?!\"?(?:\$SCRIPT_DIR|<SCRIPT_DIR>))", line):
            flag(name, i, f"streamlit 目标未用 SCRIPT_DIR: {line.strip()}")


def check_doc_refs(text: str, name: str) -> None:
    for m in re.finditer(r"references/([\w.-]+\.md)", text):
        fname = m.group(1)
        if fname in REF_ALLOWLIST:
            continue
        if not (PKG / "references" / fname).exists():
            flag(name, 0, f"引用了不存在文档: references/{fname}")


def check_versions(text: str, name: str, is_skill_main: bool) -> None:
    if not is_skill_main:
        return
    lines = text.splitlines()
    frontmatter_end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    for i, line in enumerate(lines, 1):
        if frontmatter_end and i <= frontmatter_end:
            continue
        for m in re.finditer(r"\b\d+\.\d+\.\d+\b", line):
            flag(name, i, f"正文写死版本号(应以 frontmatter version 为准): {m.group(0)}")


def check_discipline_counts(text: str, name: str) -> None:
    dm = PKG / "references" / "discipline.md"
    actual = len(set(re.findall(r"^(\d+)\.\s\*\*", dm.read_text(encoding="utf-8"), re.MULTILINE)))
    for m in re.finditer(r"纪律[^\d]{0,8}(\d+)\s*条|(\d+)\s*条全文", text):
        claim = int(m.group(1) or m.group(2))
        if claim != actual:
            flag(name, 0, f"纪律条数表述 {claim} != 实际 {actual}(references/discipline.md)")


def check_out_of_pkg_assets(text: str, name: str) -> None:
    for i, line in enumerate(text.splitlines(), 1):
        if "payserver/" in line and not any(mk in line for mk in OUT_OF_PKG_MARKERS):
            flag(name, i, "提及包外计费服务端但未标注「不在本包内/独立维护/不随」")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    for doc in DOCS:
        if not doc.exists():
            flag(str(doc.relative_to(PKG)), 0, "文档文件本身缺失")
            continue
        text = doc.read_text(encoding="utf-8")
        name = str(doc.relative_to(PKG))
        check_doc_scripts(text, name)
        check_doc_refs(text, name)
        check_versions(text, name, doc.name == "SKILL.md")
        check_discipline_counts(text, name)
        check_out_of_pkg_assets(text, name)

    if violations:
        print(f"❌ 一致性自检失败({len(violations)} 处):")
        for v in violations:
            print("  -", v)
        return 1
    print(f"✅ 一致性自检通过: {len(DOCS)} 份文档, 脚本/文档引用、版本口径、纪律条数全部对得上实物")
    return 0


if __name__ == "__main__":
    sys.exit(main())
