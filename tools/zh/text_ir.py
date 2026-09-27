"""把 data/text/*.asm 的文本块抽成可翻译 IR，并把译文重新编码回同名 section/label。

由 tools/zh/zh.py 的 text-extract / text-encode / text-validate 子命令调用。

设计要点
--------
1. 生成的文件是源文件的**逐行变换副本**：未翻译的块原样保留原始汇编。
   因此「零翻译」时生成的 .asm 与英文构建逐字节等价，天然可回归比对。
2. 已翻译的块输出纯 db 字节流。text_ram / text_decimal / text_sound / page /
   text_far 这类带操作数的宏无法用字符串表达，抽成 {{n}} 占位符，编码时原样插回。
3. 块内条件汇编（if/else/endc）在抽取时按构建宏求值，只保留命中分支——
   中文构建目标是 `zh`（无 FAITHFUL/DEBUG/HUFFMAN），所以分支是确定的。
4. 译文里的控制码标记（<LINE>/<DONE>/<PLAYER>...）沿用 zh.encode_string 的约定。
5. 编进 main.o 的 engine/*.asm 用同一套 IR。这类文件除了 text 宏，还有大量
   `db "..."` 形式的菜单/界面字符串表，所以额外支持「逐条字符串」条目：
   键形如 MainMenu.Strings#0（全局标签.局部标签#序号），替换时只重写那一行。
"""

import json
import os
import re

from zh import (
    LINE_TILE_LIMIT,
    LINE_TILE_WARN,
    TranslationError,
    charmap_keys_by_length,
    encode_string,
    line_tile_widths,
    load_charmap,
)

# 无操作数、可直接映射成控制码标记的宏。
# 值 "" 表示「把字符串参数原样拼进标记串」。
SIMPLE_MACROS = {
    "text": "",
    "text_start": "<START>",
    "text_end": "@",
    "line": "<LINE>",
    "next": "<NEXT>",
    "next1": "<LNBRK>",
    "cont": "<CONT>",
    "para": "<PARA>",
    "done": "<DONE>",
    "prompt": "<PROMPT>",
    "text_pause": "<PAUSE>",
    "text_promptbutton": "<WAIT>",
    "text_today": "<DAY>",
    "text_plural": "<PLURAL>",
    "text_asm": "<ASM>",
}

# 带操作数：抽成 {{n}} 占位符，汇编行原样保留。
OPAQUE_MACROS = {
    "page",
    "text_ram",
    "text_decimal",
    "text_far",
    "text_farend",
    "text_sound",
}

CONDITIONAL_MACROS = {"if", "elif", "else", "endc"}

LABEL_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)::?$")
LOCAL_LABEL_RE = re.compile(r"^(\.[A-Za-z_][A-Za-z0-9_]*)::?$")
SECTION_RE = re.compile(r"^\s*SECTION\b")
MACRO_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\b(.*)$")
MARKER_RE = re.compile(r"\{\{(\d+)\}\}")
# 恰好一个字符串字面量的 db 行，例如 db "Continue@"。字符串表就是这种行的连续段。
DB_STRING_RE = re.compile(r'^db\s+("(?:[^"\\]|\\.)*")$')
# 紧跟 db 行的续行：db "<PK><MN> are listed in" / next "regional order.@"
# 两行是**同一条**文本，抽取时要并成一个单位（记成 <NEXT>），编码时续行要被吃掉。
NEXT_STRING_RE = re.compile(r'^next\s+("(?:[^"\\]|\\.)*")$')

# 文本框内宽 18 个 tile；汉字占 2 tile，半角占 1 tile。
LINE_TILE_LIMIT = 18
LINE_TILE_WARN = 16  # 留出右边缘空白，避免贴着边框


class TextIRError(ValueError):
    pass


class UnsupportedCondition(TextIRError):
    pass


# --- 求值 -------------------------------------------------------------------


def eval_condition(expr: str, defines: set) -> bool:
    """求值 RGBDS 的 if 条件，只支持 DEF(X) / ! / && / || / () 。

    出现不支持的形式时抛 UnsupportedCondition，避免静默取错分支。
    """
    e = re.sub(
        r"DEF\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)",
        lambda m: "1" if m.group(1) in defines else "0",
        expr,
    )
    if re.search(r"[A-Za-z_]", e) or re.search(r"[^01!&|()\s]", e):
        raise UnsupportedCondition(expr)
    py = e.replace("&&", " and ").replace("||", " or ").replace("!", " not ")
    try:
        return bool(eval(py, {"__builtins__": {}}, {}))  # noqa: S307 - 已做白名单过滤
    except Exception as exc:  # noqa: BLE001
        raise UnsupportedCondition(f"{expr} ({exc})") from exc


# --- 逐行解析 ---------------------------------------------------------------


def read_lines(path: str) -> list:
    with open(path, encoding="utf-8") as f:
        return f.read().split("\n")


def strip_comment(s: str) -> str:
    """去掉行尾注释；分号在字符串字面量内时不算注释。"""
    out = []
    in_str = False
    i = 0
    while i < len(s):
        c = s[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < len(s):
                out.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif c == ";":
            break
        else:
            out.append(c)
        i += 1
    return "".join(out)


def split_args(s: str) -> list:
    """按顶层逗号切分参数（字符串内的逗号不算分隔符）。"""
    out, cur, in_str, i = [], [], False, 0
    while i < len(s):
        c = s[i]
        if in_str:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            cur.append(c)
        elif c == ",":
            out.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    return [a.strip() for a in out if a.strip() != ""]


def unquote(arg: str) -> str:
    """把 "..." 字面量还原成 Python 字符串；非字面量原样返回。"""
    if len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"':
        body = arg[1:-1]
        return body.replace("\\\"", "\"").replace("\\\\", "\\")
    return arg


def is_string_arg(arg: str) -> bool:
    return len(arg) >= 2 and arg[0] == '"' and arg[-1] == '"'


# --- 抽取 -------------------------------------------------------------------


def extract_blocks(path: str, defines: set) -> list:
    """返回 [{label, body_start, body_end, en, opaque, translatable}]，按出现顺序。"""
    lines = read_lines(path)
    n = len(lines)
    blocks = []
    i = 0
    while i < n:
        line = lines[i]
        m = LABEL_RE.match(line.strip())
        if m and not line[:1].isspace():
            start = i + 1
            j = start
            while j < n:
                s = lines[j]
                if SECTION_RE.match(s):
                    break
                if LABEL_RE.match(s.strip()) and not s[:1].isspace():
                    break
                j += 1
            blocks.append({"label": m.group(1), "body_start": start, "body_end": j})
            i = j
            continue
        i += 1

    for b in blocks:
        _parse_body(b, lines, defines, path)
    return blocks


def _parse_body(block: dict, lines: list, defines: set, path: str) -> None:
    parts: list = []  # ("t", str) 或 ("x", opaque_index)
    opaque: list = []
    stack: list = []  # 条件栈：[{"taken": bool, "done": bool}]
    active = True
    translatable = True

    for idx in range(block["body_start"], block["body_end"]):
        raw = strip_comment(lines[idx]).strip()
        if not raw:
            continue
        m = MACRO_RE.match(raw)
        if not m:
            translatable = False
            break
        name, rest = m.group(1), m.group(2).strip()

        if name in CONDITIONAL_MACROS:
            if name in ("if", "elif"):
                if name == "if":
                    cond = eval_condition(rest, defines)
                    stack.append({"taken": cond, "done": cond})
                else:
                    if not stack:
                        raise TextIRError(f"{path}:{block['label']} 孤立的 elif")
                    top = stack[-1]
                    cond = (not top["done"]) and eval_condition(rest, defines)
                    top["taken"] = cond
                    top["done"] = top["done"] or cond
                active = all(f["taken"] for f in stack)
            elif name == "else":
                if not stack:
                    raise TextIRError(f"{path}:{block['label']} 孤立的 else")
                top = stack[-1]
                top["taken"] = not top["done"]
                top["done"] = True
                active = all(f["taken"] for f in stack)
            else:  # endc
                if not stack:
                    raise TextIRError(f"{path}:{block['label']} 孤立的 endc")
                stack.pop()
                active = all(f["taken"] for f in stack)
            continue

        if not active:
            continue

        if name in SIMPLE_MACROS:
            parts.append(("t", SIMPLE_MACROS[name]))
            for arg in split_args(rest):
                if not is_string_arg(arg):
                    raise TextIRError(
                        f"{path}:{block['label']} {name} 的参数不是字符串字面量：{arg}"
                    )
                parts.append(("t", unquote(arg)))
            continue

        if name in OPAQUE_MACROS:
            parts.append(("x", len(opaque)))
            opaque.append(raw)
            continue

        # 其余指令（db/dw/...）不做翻译，整块交回原始汇编
        translatable = False
        break

    if stack:
        translatable = False

    block["opaque"] = opaque
    block["translatable"] = translatable
    block["en"] = _flatten(parts) if translatable else ""


def _flatten(parts: list) -> str:
    out = []
    for kind, val in parts:
        out.append(f"{{{{{val}}}}}" if kind == "x" else val)
    return "".join(out)


def extract_units(path: str, blocks: list = None, defines: set = None) -> list:
    """抽出字符串表里的逐条 db "..."，返回 [{key, line, en}]（按行号递增）。

    键 = 全局标签[.局部标签]#序号。局部标签只在同一个全局标签内唯一，
    所以键里带上全局标签；同一作用域内重名时靠序号继续区分。
    """
    lines = read_lines(path)
    if blocks is None:
        blocks = extract_blocks(path, defines)
    # 整块会被重写的（text 宏块）不在这里处理，避免和块级替换打架。
    claimed = set()
    for b in blocks:
        if b["translatable"]:
            claimed.update(range(b["body_start"], b["body_end"]))

    units = []
    counters: dict = {}
    cur_global = None
    cur_local = None
    for i, line in enumerate(lines):
        if i in claimed:
            continue
        s = strip_comment(line).strip()
        if not s:
            continue
        if not line[:1].isspace():
            m = LABEL_RE.match(s)
            if m:
                cur_global = m.group(1)
                cur_local = None
                continue
            m = LOCAL_LABEL_RE.match(s)
            if m:
                cur_local = m.group(1).lstrip(".")
                continue
        m = DB_STRING_RE.match(s)
        if not m:
            continue
        en = unquote(m.group(1))
        # 吃掉紧随其后的 next "..." 续行（可能不止一行），并进同一条文本。
        next_lines = []
        j = i + 1
        while j < len(lines) and j not in claimed:
            m2 = NEXT_STRING_RE.match(strip_comment(lines[j]).strip())
            if not m2:
                break
            en += "<NEXT>" + unquote(m2.group(1))
            next_lines.append(j)
            j += 1
        scope = cur_global or ""
        if cur_local:
            scope = f"{scope}.{cur_local}"
        idx = counters.get(scope, 0)
        counters[scope] = idx + 1
        units.append({
            "key": f"{scope}#{idx}", "line": i, "en": en, "next_lines": next_lines,
        })
    return units


def extract_entries(path: str, defines: set) -> list:
    """全部可译条目，按源码顺序：[{key, kind, en, line}]。

    kind = "block"（text 宏块，整块重写）或 "unit"（db 字符串，逐行重写）。
    """
    blocks = extract_blocks(path, defines)
    # 空文本块不收：标签后面没有可译内容（空标签、两个标签共用同一段、movement
    # 数据表标签…）时 en 是 "" 或纯空白，收进 IR 只会永远挂在"未译"里当噪音。
    entries = [
        {"key": b["label"], "kind": "block", "en": b["en"], "line": b["body_start"] - 1}
        for b in blocks
        if b["translatable"] and b["en"].strip()
    ]
    entries += [
        {"key": u["key"], "kind": "unit", "en": u["en"], "line": u["line"]}
        for u in extract_units(path, blocks, defines)
        if u["en"].strip()
    ]
    entries.sort(key=lambda e: e["line"])
    return entries


# --- 重新编码 ---------------------------------------------------------------


def render_block(block: dict, zh: str, charmap: dict, gmap: dict, keys: list,
                 marker: str = None) -> list:
    """把译文渲染成汇编行：db 字节流 + 原样插回的 opaque 命令。

    marker 非空时先写一行 `;@ <键>`：db 字符串条没有自己的标签，
    这行注释让 text-decode 还能按条还原核对。
    """
    out = []
    if marker:
        out.append(f";@ {marker}")
    if block["en"]:
        out.append("\t; 原文: " + block["en"].replace(";", "；"))

    chunks = MARKER_RE.split(zh)
    buf = bytearray()

    def flush():
        for k in range(0, len(buf), 16):
            out.append("\tdb " + ", ".join(f"${b:02x}" for b in buf[k:k + 16]))
        buf.clear()

    for i, chunk in enumerate(chunks):
        if i % 2 == 0:
            if chunk:
                buf.extend(encode_string(chunk, charmap, gmap, keys))
        else:
            n = int(chunk)
            if n >= len(block["opaque"]):
                raise TranslationError(f"占位符 {{{{{n}}}}} 超出范围")
            flush()
            out.append("\t" + block["opaque"][n])
    flush()
    if not out or not any(line.startswith("\tdb") for line in out):
        raise TranslationError("译名为空，至少需要一个终止符（@ 或 <DONE>）")
    return out


def _clean_zh(zh: str) -> str:
    """译文首尾空白：不能一律 strip。

    菜单串靠首尾空格对齐（例如 db " Money@" 的开头空格），一律 strip 会改变版面；
    只有出现换行/制表符时（多半是 JSON 里排版意外）才整体 strip。
    """
    if any(c in zh for c in "\r\n\t"):
        return zh.strip()
    return zh.rstrip()


def build(src_path: str, trans: dict, out_path: str, defines: set,
          charmap: dict = None, keys: list = None, gmap: dict = None) -> tuple:
    """生成替换用的 .asm。返回 (可译条目数, 已替换条目数)。"""
    lines = read_lines(src_path)
    blocks = {b["label"]: b for b in extract_blocks(src_path, defines)}
    units = {u["line"]: u for u in extract_units(src_path, list(blocks.values()), defines)}
    if charmap is None:
        charmap = load_charmap()
    if keys is None:
        keys = charmap_keys_by_length(charmap)
    if gmap is None:
        gmap = _glyph_map()

    out = [
        "; 自动生成，请勿手改（由 tools/zh/zh.py text-encode 产出）",
        f"; 源：{os.path.relpath(src_path, os.path.dirname(out_path))}",
        "; 未翻译的块原样保留原文；已翻译的块输出双字节 db 字节流。",
    ]
    replaced = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m = LABEL_RE.match(line.strip())
        if m and not line[:1].isspace():
            label = m.group(1)
            b = blocks.get(label)
            zh = _clean_zh(trans.get(label) or "")
            if b and b["translatable"] and zh:
                out.append(line)
                try:
                    out.extend(render_block(b, zh, charmap, gmap, keys))
                except TranslationError as e:
                    raise TextIRError(f"{os.path.basename(src_path)}:{label}: {e}") from e
                replaced += 1
                i = b["body_end"]
                continue
        u = units.get(i)
        if u is not None:
            zh = _clean_zh(trans.get(u["key"]) or "")
            if zh:
                try:
                    out.extend(render_block({"en": u["en"], "opaque": []},
                                            zh, charmap, gmap, keys, marker=u["key"]))
                except TranslationError as e:
                    raise TextIRError(f"{os.path.basename(src_path)}:{u['key']}: {e}") from e
                replaced += 1
                # 续行已并进这一条，源文件里的 next "..." 行不能再原样输出
                # （否则生成文件里会残留半句英文）。
                nxt = u.get("next_lines") or []
                i = (nxt[-1] + 1) if nxt else i + 1
                continue
        out.append(line)
        i += 1

    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))
    # 只数可译条目：不可译的宏块（整块是 {{0}} 占位命令）不该计入分母，
    # 否则打印出来的「替换 0/70」会让人误以为漏了大半条目。
    translatable = sum(1 for b in blocks.values() if b["translatable"])
    return translatable + len(units), replaced


def _glyph_map() -> dict:
    from zh import glyph_map  # 延迟导入，避免循环

    return glyph_map()


# --- 校验 -------------------------------------------------------------------


def validate_block(label: str, en: str, zh: str) -> list:
    problems = []
    if not zh.strip():
        return problems

    if MARKER_RE.findall(en) != MARKER_RE.findall(zh):
        problems.append(
            f"[占位符不匹配] {label}\n    en: {' '.join(MARKER_RE.findall(en)) or '(无)'}"
            f"\n    zh: {' '.join(MARKER_RE.findall(zh)) or '(无)'}"
        )

    # 终止符
    tail = MARKER_RE.sub("", zh)
    if not (tail.endswith("@") or "<DONE>" in tail or "<PROMPT>" in tail):
        problems.append(f"[缺少终止符] {label}（应以 @ / <DONE> / <PROMPT> 结尾）")

    # 行长：按 tile 计账（控制码零宽度，名字类占位符按典型宽度估算）
    # 菜单项画在更宽的菜单窗口里（文本区 18 tile），单独取阈值
    is_menu = zh.endswith("@") and not any(
        t in zh for t in ("<LINE>", "<PARA>", "<CONT>", "\\n")
    )
    warn = LINE_TILE_LIMIT if is_menu else LINE_TILE_WARN
    for w in line_tile_widths(zh):
        if w > LINE_TILE_LIMIT:
            problems.append(f"[行长超限] {label} {w} tile > {LINE_TILE_LIMIT} tile")
        elif w > warn:
            problems.append(f"[行长偏长] {label} {w} tile > {LINE_TILE_WARN} tile（会贴右边框）")

    return problems


def load_trans(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v.get("zh", "") for k, v in data.get("blocks", {}).items()}


def dump_trans(path: str, entries: list, old: dict, source: str = None) -> int:
    """写出 IR，保留已有译文；返回新增条数。"""
    payload = {
        "_comment": "由 tools/zh/zh.py text-extract 生成；zh 为空表示未翻译（回退原文）。"
                    "控制码沿用 <LINE>/<DONE>/<PLAYER> 等写法，{{n}} 为不可翻译的占位命令，必须原样保留。",
        "source": source,
        "blocks": {},
    }
    added = 0
    for e in entries:
        prev = old.get(e["key"])
        if prev is None:
            added += 1
        payload["blocks"][e["key"]] = {"en": e["en"], "zh": prev or ""}
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return added
