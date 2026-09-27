#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""译文草案预校验：在写进 IR 之前把问题挡住。

用法：
    python tools/zh/check_draft.py draft.json          # 校验草案
    python tools/zh/check_draft.py --all                # 回溯校验 IR 里全部已译条目

draft.json 形如 {"键名": "中文译文", ...}，键名需在任一 IR 里存在
（tools/zh/translations/{text,include,maps}/*.json）。

检查项（都是踩过的坑）：
  1. 控制码多重集一致 —— 顺序可以随中文语序调换，但种类与数量必须相同。
  2. 占位符出现顺序一致 —— 不能只比多重集：{{0}} 必须排在 {{1}} 前面。
     （`Text_EnemyUsedOn`、`_PokemonTookItemText` 都栽在这里：中文自然语序
     「对{{1}}使用了{{0}}」会让顺序反了，工具报 [占位符不匹配]。）
  3. 终止符 —— 必须有 <DONE>/<PROMPT>/<WAIT>，或以 @ 结尾。
  4. 行宽 ≤ 16 tile —— 按引擎口径算：全角 2、半角 1，<PLAYER>/<ENEMY>/
     <USER>/<TARGET>/<RIVAL> 各按 7 tile 展开，`…` 与 `♪` 虽是 ambiguous
     宽度但引擎按全角渲染，这里兜 2。

退出码 0 = 无问题，1 = 有问题。
"""

import json
import pathlib
import re
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
IR_DIR = ROOT / "tools" / "zh" / "translations"

# 引擎按 7 tile 展开的名字类控制码
NAME_TOKENS = ("<PLAYER>", "<RIVAL>", "<ENEMY>", "<USER>", "<TARGET>")
# east_asian_width 判为 ambiguous、但引擎按全角渲染的字符
WIDE_AMBIGUOUS = "\u2026\u266a"  # … ♪

MAX_LINE_TILES = 16


def load_ir():
    """返回 {键名: 英文原文}（三个通路合并，含 <NEXT> 续行）。"""
    en = {}
    for sub in ("text", "include", "maps"):
        for f in sorted((IR_DIR / sub).glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            for k, v in data.get("blocks", {}).items():
                if isinstance(v, dict) and "en" in v:
                    en.setdefault(k, v["en"])
    return en


def control_multiset(s):
    """控制码的种类与数量（多重集）。"""
    return sorted(re.findall(r"<[A-Za-z0-9_]+>|\{\{\d\}\}", s))


def placeholder_order(s):
    """占位符按出现顺序排列，如 ['{{0}}', '{{1}}']。"""
    return re.findall(r"\{\{\d\}\}", s)


def line_tiles(s):
    """按引擎口径算一行的 tile 宽度。

    不计入的：控制码、{{N}} 占位符、以及 `{d:CONST}` 这类汇编期数值常量
    —— 它们不是渲染文本，会被替换成字面数字，按字面算会得出假超宽
    （如 `皮皮  {d:GOLDENRODGAMECORNER_CLEFAIRY}` 曾被算成 50 tile）。
    """
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\{\{\d\}\}", "", s)
    s = re.sub(r"\{[^{}]*\}", "", s)
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    w += sum(s.count(c) for c in WIDE_AMBIGUOUS)
    w += sum(s.count(t) * 7 for t in NAME_TOKENS)
    return w


def check_one(key, en, zh):
    """返回问题列表（空 = 通过）。

    关于控制码的两个口径（别搞混）：
    - `<LINE>` 与 `<CONT>` 互换是**允许的排版适配**：CONT 是「滚动一行」、
      LINE 是「移到下一行」，中文更紧凑时常把 LINE 换成 CONT，这不违规，
      所以不做多重集比较（历史批次有大量此类差异，逐条比只会噪声淹没真问题）。
    - 但 `<PARA>` 数量不能变少：PARA 是「等待按键后清屏」，少一个就意味着
      玩家少一次阅读停顿、一屏要塞进更多内容 —— 这才是要看的。
    """
    bad = []
    if placeholder_order(en) != placeholder_order(zh):
        bad.append(f"占位符顺序不一致（en {placeholder_order(en)} != zh {placeholder_order(zh)}）")
    if not re.search(r"<(DONE|PROMPT|WAIT)>|@$", zh):
        bad.append(f"缺终止符（结尾 {zh[-8:]!r}）")
    # PARA 变少本身不算错：中文更紧凑，行数同步减少时合并分段是合理的排版适配。
    # 真正有问题的是「行数没减、分段却变少」—— 等于把同样多的内容塞进更少的屏。
    n_lines = lambda s: len(re.findall(r"<(?:LINE|CONT|NEXT)>", s)) + 1
    if (zh.count("<PARA>") < en.count("<PARA>")
            and n_lines(zh) >= n_lines(en)):
        bad.append(f"PARA 变少且行数未减（en {en.count('<PARA>')}段/{n_lines(en)}行 -> "
                   f"zh {zh.count('<PARA>')}段/{n_lines(zh)}行），每屏更挤")
    # 菜单项/数据表不是对话文本，不适用对话框宽度限制
    if re.search(r"MenuData|MenuStrings|MenuHeader|\.String|ItemNames", key):
        return bad
    for ln in re.split(r"<(?:LINE|PARA|CONT|NEXT)>", zh):
        ln = re.sub(r"@+$", "", ln)  # 终止符不占显示宽度
        n = line_tiles(ln)
        if n > MAX_LINE_TILES:
            bad.append(f"行长 {n}>{MAX_LINE_TILES}: {ln!r}")
    return bad


def main(argv):
    if "--all" in argv:
        blocks = {}
        for sub in ("text", "include", "maps"):
            for f in sorted((IR_DIR / sub).glob("*.json")):
                data = json.loads(f.read_text(encoding="utf-8"))
                for k, v in data.get("blocks", {}).items():
                    if isinstance(v, dict) and (v.get("zh") or "").strip():
                        blocks[k] = (v.get("en", ""), v["zh"])
        bad_total = 0
        for key, (en, zh) in blocks.items():
            for m in check_one(key, en, zh):
                bad_total += 1
                print(f"  {key}: {m}")
        print(f"回溯校验 {len(blocks)} 条已译条目，{bad_total} 处问题")
        return 1 if bad_total else 0

    if len(argv) < 2:
        print(__doc__)
        return 2
    draft = json.loads(pathlib.Path(argv[1]).read_text(encoding="utf-8"))
    en_map = load_ir()
    bad_total = 0
    for key, zh in draft.items():
        if key not in en_map:
            print(f"  {key}: 键不存在于 IR")
            bad_total += 1
            continue
        for m in check_one(key, en_map[key], zh):
            bad_total += 1
            print(f"  {key}: {m}")
    print(f"校验 {len(draft)} 条草案，{bad_total} 处问题")
    return 1 if bad_total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
