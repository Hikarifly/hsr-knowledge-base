#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""verify_docs.py — 文档体系结构校验（批 2 / issue #17 的验收器）

用法（**仓库根目录**）：
    python tools/verify_docs.py

检查项（任一项失败 → exit 1）：
  [D1] ADR 数量 12–18 条，每条含 Status / Context / Decision / Consequences 四要素
  [D2] docs/adr/README.md 的映射表覆盖 决策记录 中的全部 D 号，且各出现一次（0 遗漏 / 0 重复）
  [D3] GLOSSARY.md 条目数 ≥ 25，且每条含「中文 / 英文 / 定义 / 易混点」四列
  [D4] docs/ 下每个 .md 都能从根 README.md 两跳内到达（README → docs/README.md → 目标）
  [D5] docs/README.md 里没有任何指向不存在文件的相对链接

只读：不修改任何文件。
"""
import re
import subprocess
import sys
from collections import deque
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
ADR_DIR = DOCS / "adr"
DECISIONS = DOCS / "prompts" / "决策记录.md"
GLOSSARY = ROOT / "GLOSSARY.md"
DOCS_INDEX = DOCS / "README.md"

fails = []


def fail(tag, msg):
    fails.append("[%s] %s" % (tag, msg))
    print("  ! %s" % msg)


def read(p):
    return p.read_text(encoding="utf-8", errors="replace")


def check_adr():
    files = sorted(p for p in ADR_DIR.glob("ADR-*.md"))
    if not (12 <= len(files) <= 18):
        fail("D1", "ADR 数量 = %d，要求 12–18" % len(files))
    bad = []
    for p in files:
        s = read(p)
        for key in ("**Status**", "## Context", "## Decision", "## Consequences"):
            if key not in s:
                bad.append("%s 缺 %s" % (p.name, key))
    for b in bad:
        fail("D1", b)
    print("[D1] ADR: %d 条（12–18）｜四要素缺失 %d 处" % (len(files), len(bad)))
    return files


def check_mapping():
    dec = read(DECISIONS)
    nums = sorted(set(re.findall(r"\|\s*\*?\*?(D-\d{3})", dec)))
    idx = read(ADR_DIR / "README.md")
    # 映射表：只取第二节表格里以 `D-0xx` 开头、末列为 ADR 链接的行
    rows = re.findall(r"^\|\s*`(D-\d{3})`\s*\|[^|]*\|[^|]*\|\s*\[?(ADR-\d{4})", idx, re.M)
    seen = [d for d, _ in rows]
    missing = [d for d in nums if d not in seen]
    dup = sorted({d for d in seen if seen.count(d) > 1})
    unknown = [d for d in seen if d not in nums]
    if missing:
        fail("D2", "映射缺 %d 条：%s" % (len(missing), ", ".join(missing[:10])))
    if dup:
        fail("D2", "映射重复：%s" % ", ".join(dup))
    if unknown:
        fail("D2", "映射含决策记录里不存在的编号：%s" % ", ".join(unknown))
    adrs = {a for _, a in rows}
    print("[D2] 映射: 决策 %d 条 / 已映射 %d 条 / 涉及 ADR %d 条｜缺 %d 重复 %d"
          % (len(nums), len(seen), len(adrs), len(missing), len(dup)))


def check_glossary():
    s = read(GLOSSARY)
    rows = [l for l in s.splitlines() if l.startswith("|") and l.count("|") >= 5]
    terms = []
    for l in rows:
        first = l.strip().strip("|").split("|")[0].strip()
        if first == "中文" or re.match(r"^-{3,}$", first):
            continue
        terms.append(l)
    if len(terms) < 25:
        fail("D3", "GLOSSARY 条目 = %d，要求 ≥ 25" % len(terms))
    print("[D3] GLOSSARY: %d 条（≥25）" % len(terms))


def links_in(p):
    """返回 [(显示名, 目标)]，只取 markdown 内联链接；跳过外链与锚点。"""
    out = []
    for m in re.finditer(r"\[([^\]]+)\]\(([^)#][^)]*)\)", read(p)):
        target = m.group(2).strip()
        if re.match(r"^[a-z]+://", target) or target.startswith("mailto:"):
            continue
        out.append((m.group(1), target))
    return out


def check_reachability():
    # 只校验**版本库跟踪**的 docs 文件：未入库的用户文件不参与（不属于交付物）
    tracked = set()
    try:
        out = subprocess.run(["git", "ls-files", "docs"], cwd=str(ROOT),
                             capture_output=True, text=True, encoding="utf-8").stdout
        tracked = {(ROOT / l.strip()).resolve() for l in out.splitlines() if l.strip().endswith(".md")}
    except Exception:
        pass
    docs_files = sorted(p for p in DOCS.rglob("*.md") if not tracked or p.resolve() in tracked)
    # 跳数 1：根 README.md 能直接到达的 docs 文件
    hop1 = set()
    for _, t in links_in(ROOT / "README.md"):
        tgt = (ROOT / t).resolve()
        if DOCS in tgt.parents or tgt.parent == DOCS:
            hop1.add(tgt)
    if DOCS_INDEX.resolve() not in hop1:
        fail("D4", "根 README.md 未链接 docs/README.md（总入口不可达）")
    hop2 = set(hop1)
    queue = deque([DOCS_INDEX.resolve()])
    while queue:
        cur = queue.popleft()
        if not cur.exists() or cur.suffix != ".md":
            continue
        for _, t in links_in(cur):
            tgt = (cur.parent / t).resolve()
            if tgt.suffix == ".md" and tgt.exists():
                hop2.add(tgt)
    unreachable = [p for p in docs_files if p.resolve() not in hop2]
    for p in unreachable[:10]:
        fail("D4", "两跳内不可达：%s" % p.relative_to(ROOT))
    print("[D4] 可达性: docs 共 %d 个 .md｜两跳可达 %d｜不可达 %d"
          % (len(docs_files), len(docs_files) - len(unreachable), len(unreachable)))
    return len(unreachable)


def check_index_links():
    bad = 0
    for _, t in links_in(DOCS_INDEX):
        if t.endswith("/"):
            tgt = DOCS_INDEX.parent / t
            if not tgt.is_dir():
                fail("D5", "目录不存在：%s" % t)
                bad += 1
            continue
        tgt = (DOCS_INDEX.parent / t).resolve()
        if not tgt.exists():
            fail("D5", "链接目标不存在：%s" % t)
            bad += 1
    print("[D5] docs/README.md 链接: 失效 %d 条" % bad)


def main():
    print("=== 文档体系结构校验（批 2） ===")
    if not ADR_DIR.is_dir():
        fail("D1", "缺少 docs/adr/ 目录")
        print("\nRESULT: FAIL\n  " + "\n  ".join(fails))
        return 1
    check_adr()
    check_mapping()
    check_glossary()
    check_reachability()
    check_index_links()
    if fails:
        print("\nRESULT: FAIL 失败项 %d：" % len(fails))
        for f in fails:
            print("  - " + f)
        return 1
    print("\nRESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
