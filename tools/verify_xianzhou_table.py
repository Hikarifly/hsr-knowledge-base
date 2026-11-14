# -*- coding: utf-8 -*-
"""issue #20 独立复核：四语仙舟表结构 + 取值与官方词条逐字比对（只读，不写数据区）"""
import json, os, re, sys

ROOT = r"G:\HSR"
LANGS = ["en_us", "zh_tw", "ja_jp", "ko_kr"]
PH = {"en_us": "Pending", "zh_tw": "待補充", "ja_jp": "未収録", "ko_kr": "미수록"}
SRR = os.path.join(ROOT, "StarRailRes-master", "index_new")
IDMAP = {"en_us": "en", "zh_tw": "cht", "ja_jp": "jp", "ko_kr": "kr"}

def split_row(line):
    # 按未转义的 | 分列
    cells, buf, i = [], "", 0
    s = line.strip()
    assert s.startswith("|")
    s = s[1:]
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            buf += "|"; i += 2; continue
        if s[i] == "|":
            cells.append(buf.strip()); buf = ""; i += 1; continue
        buf += s[i]; i += 1
    if buf.strip():
        cells.append(buf.strip())
    return cells

def find_table(path):
    lines = open(path, encoding="utf-8").read().splitlines()
    for n, ln in enumerate(lines):
        if ln.startswith("|") and ("仙舟" in ln or "Xianzhou ship" in ln or "선주" in ln) and ("|" in ln[1:]):
            if any(x in ln for x in ("狀態", "状態", "Status", "상태")):
                rows = []
                k = n
                while k < len(lines) and lines[k].startswith("|"):
                    rows.append(lines[k]); k += 1
                return n + 1, rows
    return None, None

# 期望值：官方词条 id → 每语称号（Lead 已取证）
EXPECT = {
    "10105": {"en": "Divine Foresight", "cht": "神策將軍", "jp": "神策将軍", "kr": "신책 장군"},
    "10201": {"en": "Merlin's Claw",    "cht": "天擊將軍", "jp": "天撃将軍", "kr": "천격 장군"},
    "10204": {"en": "Flaming Heart",    "cht": "燭淵將軍", "jp": "燭淵将軍", "kr": "촉연 장군"},
    "10210": {"en": "Seer Strategist",  "cht": "戎韜將軍", "jp": "戎韜将軍", "kr": "융도 장군"},
    "10124": {"en": "Imbibitor Lunae",  "cht": "飲月君",   "jp": "飲月君",   "kr": "음월군"},
}
# 每语表中应出现的（称号, 词条id）——用于反查「写进去的字符串是否真等于官方原文」
TARGETS = {"en": ["10105", "10201", "10204", "10210", "10124"],
           "cht": ["10105", "10201", "10204", "10210", "10124"],
           "jp": ["10105", "10201", "10204", "10210", "10124"],
           "kr": ["10105", "10201", "10204", "10210", "10124"]}

ok = True
print("=== [A] 表结构 ===")
tables = {}
for L in LANGS:
    p = os.path.join(ROOT, L, "worldview", "势力与组织.md")
    start, rows = find_table(p)
    assert rows, f"{L}: 未找到仙舟表"
    counts = [len(split_row(r)) for r in rows]
    data_rows = rows[2:]
    print(f"  {L:6s} 行号 L{start}  行数={len(rows)} 数据行={len(data_rows)}  列数集合={set(counts)}")
    if set(counts) != {6} or len(data_rows) != 9:
        ok = False; print(f"    !! 结构异常")
    tables[L] = data_rows

print("\n=== [B] 占位计数（表内） ===")
EXP_PH = {"en_us": 13, "zh_tw": 10, "ja_jp": 13, "ko_kr": 13}
for L in LANGS:
    n = sum(c.count(PH[L]) for r in tables[L] for c in split_row(r))
    flag = "OK" if n == EXP_PH[L] else "!! 不符"
    if n != EXP_PH[L]: ok = False
    print(f"  {L:6s} {PH[L]} = {n}  期望 {EXP_PH[L]}  {flag}")

print("\n=== [C] 取值 vs 官方词条（逐字） ===")
for L in LANGS:
    srr_lang = IDMAP[L]
    d = json.load(open(os.path.join(SRR, srr_lang, "descriptions.json"), encoding="utf-8"))
    blob = "\n".join(r for r in tables[L])
    for cid in TARGETS[srr_lang]:
        official = EXPECT[cid][srr_lang]
        hit = official in blob
        if not hit: ok = False
        print(f"  [{L:5s}] {cid} 官方『{official}』→ 表内{'命中' if hit else '**缺失**'}")
    # 反向：表内称号必须来自官方串集合或已登记的存量专名
    legit = {EXPECT[c][srr_lang] for c in TARGETS[srr_lang]}
    ATTESTED = {"cht": {"冱淵君", "昆岡君", "炎庭君"}}  # zh_tw 存量（worldview 同文件 / 靈砂 页），已在 issue 方案中登记
    legit |= ATTESTED.get(srr_lang, set())
    found = set()
    for r in tables[L]:
        pats = [r"【([^】]+)】"] if srr_lang in ("cht", "jp", "kr") else [r"\(([^)]+)\)"]
        for pat in pats:
            for t in re.findall(pat, r):
                if t: found.add(t)
    extra = found - legit
    print(f"  [{L:5s}] 表内称号种类={sorted(found)}")
    if extra:
        ok = False; print(f"    !! 出现非官方串: {sorted(extra)}")

print("\n=== [D] 双链目标存在性 ===")
total = 0
for L in LANGS:
    for r in tables[L]:
        for target in re.findall(r"\[\[([^|\]\\]+)\\\|", r):
            total += 1
            fp = os.path.join(ROOT, target.replace("/", os.sep) + ".md")
            if not os.path.isfile(fp):
                ok = False; print(f"  !! {L} 死链: {target}")
print(f"  双链共 {total} 条，全部存在 = {ok}")

print("\n=== [E] 状态枚举白名单 ===")
WHITE = {"en_us": {"Active", "Destroyed"}, "zh_tw": {"現役", "已隕"},
         "ja_jp": {"現役", "墜落"}, "ko_kr": {"현역", "몰락"}}
for L in LANGS:
    vals = {split_row(r)[5] for r in tables[L]}
    bad = vals - WHITE[L]
    if bad: ok = False; print(f"  !! {L} 枚举外取值: {bad}")
    else: print(f"  {L:6s} 状态取值={sorted(vals)} OK")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
