#!/usr/bin/env python3
"""Polished Crystal 简体中文化工具链。

子命令：
  gen-font      从点阵/矢量字体渲染 12x12 汉字，输出 1bpp 字形数据与索引
  encode        把翻译目录（JSON）转成双字节 .asm 文本
  validate      校验译文：控制码序列、行长、未翻译项、字形缺失
  chars         汇总字形集（术语表 + 翻译文本中出现的中文）
  text-extract  把 data/text/*.asm 的文本块抽成翻译 IR（保留已有译文）
  text-encode   把翻译 IR 重新编码成 data/text/zh/*.asm（未翻译块原样保留）
  text-validate 校验真实文本译文：占位符/终止符/行长/可编码性
  text-decode   反向还原生成的字节流，核对「Z80 会画出哪些字」及字形数据是否齐备
  title-logo    渲染中文标题美术字（144x56 四色阶 PNG，供 rgbgfx -c dmg 转 2bpp）
  capacity      容量预算：实测压缩比 + 字集外推，回答「全集中文化装不装得下」

编码约定（与 constants/charmap.asm、home/text.asm 一致）：
  一个汉字 = 双字节：lead(0x0A..0x4C) + trail(0x00..0xFF)
  字形序号 i = (lead - ZH_LEAD_START) * 128 + (trail & 0x7F) ; 0..8575
              （trail 只用低 7 位，且必须落在 0x80-0xFF，见 validate 里的尾字节检查）
  字形数据分 bank 存放：chunk = i / ZH_GLYPHS_PER_BANK（512 个/块，32 字节/字形），
  由 ZhGlyphChunkPtrs[chunk] / ZhGlyphChunkBanks[chunk] 定位。

关键点：半角字符**不能**直接用 ASCII 码值。本作 charmap 中 'A'=$80、'0'=$e0、
' '=$7f、'#'=$4d 等，均由 constants/charmap.asm 定义。本工具在运行期解析该文件，
避免手工维护两份表格产生漂移。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

# --- 编码常量（必须与 constants/charmap.asm 保持同步）---------------------
ZH_LEAD_START = 0x0A
ZH_LEAD_END = 0x4C
ZH_LEAD_COUNT = ZH_LEAD_END - ZH_LEAD_START + 1  # 67

# 尾字节固定落在 $80-$ff（原字库的 "字面字形" 区），只携带低 7 位。
# 这样尾字节永远不可能等于控制码($00-$09)、终止符($52-$54)、n-gram($4d-$51)
# 或特殊码($55-$5e)，于是所有「逐字节找 @/<DONE>/<PROMPT>」的既有代码
# （DoTextUntilTerminator、CheckTerminatorChar、DecompressStringToRAM、
#   名字/邮件/电台/告示牌等字符串扫描）都不必改造也不会误判。
ZH_GLYPH_PAGE_SHIFT = 7
ZH_GLYPHS_PER_PAGE = 1 << ZH_GLYPH_PAGE_SHIFT  # 128
ZH_TRAIL_START = 0x80
ZH_TRAIL_END = 0xFF
ZH_TRAIL_MASK = ZH_GLYPHS_PER_PAGE - 1  # $7f

# 字形序号 i -> lead = ZH_LEAD_START + (i >> 7)，trail = $80 | (i & $7f)
ZH_MAX_GLYPHS = ZH_LEAD_COUNT * ZH_GLYPHS_PER_PAGE  # 8576

GLYPH_W = 12
GLYPH_H = 12
TILE = 8
GLYPH_TILES = 4  # 2x2 个 8x8 tile -> 16x16 单元
GLYPH_SIZE = GLYPH_TILES * (TILE * TILE // 8)  # 32 字节

# 字形数据分 bank 存放：每块 ≤ 0x4000 字节（一个 ROMX bank 的上限）。
# 512 * 32 = 16384 = 0x4000，因此每块正好 512 个字形。
GLYPHS_PER_BANK = 512
GLYPH_BANK_SHIFT = GLYPHS_PER_BANK.bit_length() - 1  # 9
GLYPH_CHUNK_BYTES = GLYPHS_PER_BANK * GLYPH_SIZE

# 排版权限：对话框内宽 18 个 tile。汉字占 2 tile（12x12 画在 16x16 单元里），
# 半角走原字库占 1 tile。安全线留 2 tile 边距，避免贴右边框。
LINE_TILE_LIMIT = 18
LINE_TILE_WARN = 16
CJK_TILES = 2
ASCII_TILES = 1

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def repo_path(*parts: str) -> str:
    return os.path.join(ROOT, *parts)


# --- 字符集 -----------------------------------------------------------------
# 需要生成 12x12 字形的字符：汉字 + 全角标点。半角 ASCII 走原 charmap，不进字库。
CJK_RE = re.compile(
    "[\u2014\u2015"                            # 破折号（TRANSLATION_STYLE 要求用——）
    "\u2018\u2019\u201c\u201d\u2026"           # 全角引号、省略号
    "\u3000-\u303f"                            # CJK 标点
    "\u3400-\u4dbf\u4e00-\u9fff"               # 汉字（含扩展 A）
    "\uff01-\uff5e"                            # 全角 ASCII 变体
    "]"
)


def is_cjk(ch: str) -> bool:
    return bool(CJK_RE.match(ch))


def char_tiles(ch: str) -> int:
    return CJK_TILES if is_cjk(ch) else ASCII_TILES


# 控制码标记是零宽度指令，本身不占 tile；但其中几个会在运行期展开成名字/数值，
# 按典型长度估一个保守宽度，避免漏判行长。
CONTROL_TOKEN_RE = re.compile(r"<[A-Za-z0-9_]+>")
VAR_TILE_WIDTH = {
    "<PLAYER>": 7, "<RIVAL>": 7,
    "<USER>": 10, "<TARGET>": 10, "<ENEMY>": 10,
    "<PHONE>": 8, "<TRENDY>": 8, "<BALL>": 8, "<DAY>": 8,
    "<NUM>": 3, "<LV>": 3, "<ID>": 3,
    "<PK>": 1, "<PO>": 1,
}
LINEBREAK_SPLIT_RE = re.compile(r"<LINE>|<NEXT>|<PARA>|<CONT>|<LNBRK>")
# 分页：只有这两种会清对话框（触发 ClearSpeechBox -> Zh_ResetSlots），
# 槽位计数按页算才符合运行时行为。
PAGE_SPLIT_RE = re.compile(r"<PARA>|<PAGE>")


def seg_tiles(seg: str) -> int:
    """一个显示行（已不含换行指令）占用的 tile 数。"""
    seg = re.sub(r"\{\{\d+\}\}", "", seg)
    # asm 数值占位符 {d:SYMBOL} 在汇编期展开成数字，按最宽 4 位估一个保守宽度
    seg = re.sub(r"\{d:[^}]*\}", "0000", seg)
    seg = seg[:-1] if seg.endswith("@") else seg  # @ 是终止符，不占 tile
    total = 0
    pos = 0
    for m in CONTROL_TOKEN_RE.finditer(seg):
        total += sum(char_tiles(c) for c in seg[pos:m.start()])
        total += VAR_TILE_WIDTH.get(m.group(0), 0)
        pos = m.end()
    total += sum(char_tiles(c) for c in seg[pos:])
    return total


def line_tile_widths(zh: str) -> list[int]:
    """按显示行拆开，返回每行的 tile 宽度。"""
    return [seg_tiles(seg) for seg in LINEBREAK_SPLIT_RE.split(zh)]


# --- charmap 解析 -----------------------------------------------------------
_CHARMAP_CACHE: dict[str, int] | None = None

_CHARMAP_RE = re.compile(
    r'charmap\s+"((?:[^"\\]|\\.)*)"\s*,\s*(\$[0-9a-fA-F]+|[0-9]+)'
)


def load_charmap(path: str | None = None) -> dict[str, int]:
    """解析 constants/charmap.asm，返回 default charmap 的 字符 -> 字节 映射。

    default 继承 compressing 继承 no_ngrams，因此把三个 section 的 charmap
    定义按出现顺序合并即可（后者覆盖前者）。
    """
    global _CHARMAP_CACHE
    if _CHARMAP_CACHE is not None:
        return _CHARMAP_CACHE

    path = path or repo_path("constants", "charmap.asm")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到字符表：{path}")

    cm: dict[str, int] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            # charmap 的键不含 ';'，按注释符截断是安全的
            code = line.split(";", 1)[0].strip()
            if not code:
                continue
            m = _CHARMAP_RE.match(code)
            if not m:
                continue
            key = m.group(1).replace('\\"', '"').replace("\\\\", "\\")
            raw = m.group(2)
            cm[key] = int(raw[1:], 16) if raw.startswith("$") else int(raw)

    _CHARMAP_CACHE = cm
    return cm


def charmap_keys_by_length(charmap: dict[str, int]) -> list[str]:
    """按长度降序排列的键，用于最长匹配（保证 "#mon"、"<PLAYER>" 不被切碎）。"""
    return sorted(charmap, key=len, reverse=True)


# --- 字形集 -----------------------------------------------------------------
def iter_glossary(path: str):
    with open(path, encoding="utf-8") as f:
        header = f.readline()
        assert header.split("\t")[0] == "category", "glossary.tsv 首行必须是表头"
        for line in f:
            line = line.rstrip("\n")
            if not line:
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            yield parts[0], parts[1], parts[2]


def translation_paths() -> list[str]:
    tdir = repo_path("tools", "zh", "translations")
    if not os.path.isdir(tdir):
        return []
    return [os.path.join(tdir, n) for n in sorted(os.listdir(tdir)) if n.endswith(".json")]


# --- 真实游戏文本（data/text/*.asm）-----------------------------------------
def text_ir_dir() -> str:
    return repo_path("tools", "zh", "translations", "text")


def text_source_paths() -> list[str]:
    """data/text/ 下的顶层 .asm（生成的 data/text/zh/ 不在其中）。

    已被 include_sources.txt 收录的文件要排除：这类文件没有自己的目标文件
    （被 main.asm 之类 INCLUDE 进别的 SECTION），对象替换换不进去，留在本通路
    只会生成一份永远不生效的 IR —— text-decode 核对时会报「还原不一致」。
    典型例子：data/text/std_text.asm。
    """
    tdir = repo_path("data", "text")
    if not os.path.isdir(tdir):
        return []
    try:
        taken = {os.path.normcase(os.path.normpath(p)) for p in include_source_paths()}
    except Exception:
        taken = set()
    return [
        os.path.join(tdir, n)
        for n in sorted(os.listdir(tdir))
        if n.endswith(".asm")
        and os.path.isfile(os.path.join(tdir, n))
        and os.path.normcase(os.path.normpath(os.path.join(tdir, n))) not in taken
    ]


def map_ir_dir() -> str:
    return repo_path("tools", "zh", "translations", "maps")


def map_scripts_out() -> str:
    """换入用的「剧本清单」生成物（data/maps/scripts.asm 的中文版副本）。"""
    return repo_path("data", "text", "zh_maps", "scripts.asm")


def map_source_paths() -> list[str]:
    """tools/zh/map_sources.txt 声明的地图脚本（第三类通路）。

    与 data/text/*.asm 不同，地图脚本**没有自己的目标文件**：654 个 maps/*.asm 全被
    data/maps/scripts.asm 收在同一个 data/maps/map_data.o 里，所以既不能整对象
    替换，也不适合逐个改 INCLUDE 点（要改 654 处、且 map_data.asm 只写一行）。

    做法是把 scripts.asm 本身也变成生成物：逐行复制原文件，只把收录了的地图的
    INCLUDE 换成生成副本（data/text/zh_maps/<stem>.asm），其余保持原样。

    清单格式与 include_sources.txt 相同：纯路径清单，每行一个，不能写注释
    （Makefile 也读它）。
    """
    path = repo_path("tools", "zh", "map_sources.txt")
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            s = raw.strip()
            if not s:
                continue
            if not s.endswith(".asm") or "#" in s or " " in s:
                raise TranslationError(
                    f"tools/zh/map_sources.txt 第 {lineno} 行不是纯路径：{s!r}"
                    f"（该文件由 Makefile 直接读取，不能写注释）")
            out.append(repo_path(*s.replace("\\", "/").split("/")))
    return out


def include_ir_dir() -> str:
    return repo_path("tools", "zh", "translations", "include")


def include_source_paths() -> list[str]:
    """tools/zh/include_sources.txt 声明的源文件（条件 INCLUDE 换入的那批）。

    这些文件被别的 .asm INCLUDE 进同一个目标文件（engine/*.asm 进 main.o，
    data/options/*.asm 进 options_menu.o），没法像 data/text/*.asm 那样整对象替换，
    中文构建改在 INCLUDE 点用 IF DEF(LANG_ZH) 换入生成的中文版。

    清单文件同时被 Makefile 用 $(file <) 读取，所以必须是**纯路径清单**：
    每行一个路径，不接受注释或其它内容（说明见 docs/TRANSLATION_TOOLING.md）。
    """
    path = repo_path("tools", "zh", "include_sources.txt")
    if not os.path.isfile(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            s = raw.strip()
            if not s:
                continue
            if not s.endswith(".asm") or "#" in s or " " in s:
                raise TranslationError(
                    f"tools/zh/include_sources.txt 第 {lineno} 行不是纯路径：{s!r}"
                    f"（该文件由 Makefile 直接读取，不能写注释）")
            out.append(repo_path(*s.replace("\\", "/").split("/")))
    return out


def text_source_specs() -> list[dict]:
    """所有可翻译源文件，元素为 {stem, src, kind, ir_dir, out}。

    kind = "text"   ：产物与英文版同名 section/label，构建时整对象替换；
    kind = "include"：编在宿主目标文件里，只能靠 INCLUDE 点的条件 INCLUDE 换入；
    kind = "maps"   ：产物同上，但换入点是**生成物** data/text/zh_maps/scripts.asm
                      （data/maps/scripts.asm 的副本，只换收录地图的 INCLUDE）。
    """
    specs = []
    for src in text_source_paths():
        stem = os.path.basename(src)[:-4]
        specs.append({
            "stem": stem,
            "src": src,
            "kind": "text",
            "ir_dir": text_ir_dir(),
            "out": repo_path("data", "text", "zh", stem + ".asm"),
        })
    for src in include_source_paths():
        stem = os.path.basename(src)[:-4]
        if not os.path.isfile(src):
            raise TranslationError(f"include_sources.txt 里的文件不存在：{src}")
        clash = next((s for s in specs if s["stem"] == stem), None)
        if clash:
            raise TranslationError(
                f"stem 冲突：{src} 与 {clash['src']} 都会写成 {stem}"
                f"（IR / 产物 / --only 都按这个 stem 找）")
        specs.append({
            "stem": stem,
            "src": src,
            "kind": "include",
            "ir_dir": include_ir_dir(),
            "out": repo_path("data", "text", "zh_include", stem + ".asm"),
        })
    for src in map_source_paths():
        stem = os.path.basename(src)[:-4]
        if not os.path.isfile(src):
            raise TranslationError(f"map_sources.txt 里的文件不存在：{src}")
        clash = next((s for s in specs if s["stem"] == stem), None)
        if clash:
            raise TranslationError(
                f"stem 冲突：{src} 与 {clash['src']} 都会写成 {stem}"
                f"（IR / 产物 / --only 都按这个 stem 找）")
        specs.append({
            "stem": stem,
            "src": src,
            "kind": "maps",
            "ir_dir": map_ir_dir(),
            "out": repo_path("data", "text", "zh_maps", stem + ".asm"),
        })
    return specs


def _text_ir():
    """延迟导入 tools/zh/text_ir.py（避免与 text_ir -> zh 形成循环导入）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    import text_ir  # noqa: PLC0415

    return text_ir


def _ir_translations(tdir: str) -> dict:
    """{stem: {label: zh}}，扫描一个 IR 目录下的全部 .json。"""
    out: dict[str, dict] = {}
    if not os.path.isdir(tdir):
        return out
    ti = _text_ir()
    for n in sorted(os.listdir(tdir)):
        if n.endswith(".json"):
            out[n[:-5]] = ti.load_trans(os.path.join(tdir, n))
    return out


def text_translations() -> dict:
    """{stem: {label: zh}}，来自 tools/zh/translations/text/*.json。"""
    return _ir_translations(text_ir_dir())


def include_translations() -> dict:
    """{stem: {label: zh}}，来自 tools/zh/translations/include/*.json。"""
    return _ir_translations(include_ir_dir())


def map_translations() -> dict:
    """{stem: {label: zh}}，来自 tools/zh/translations/maps/*.json。"""
    return _ir_translations(map_ir_dir())


def collect_chars(extra_texts=None, *, include_glossary: bool = False) -> list[str]:
    """收集需要生成字形的字符。

    默认只收「真正出现在译文里」的字，避免术语表中暂未使用的字白占 ROM
    （每个字形 32 字节，多一个就多一份 16x16 图腾）。需要把术语表里的译名
    也一并刻进字库时，显式传 include_glossary=True。
    """
    chars: set[str] = set()
    gloss = repo_path("tools", "zh", "glossary", "glossary.tsv")
    if include_glossary:
        for _cat, _en, zh in iter_glossary(gloss):
            for ch in zh:
                if is_cjk(ch):
                    chars.add(ch)
    for t in extra_texts or []:
        for ch in t:
            if is_cjk(ch):
                chars.add(ch)
    return sorted(chars)


def glyph_map() -> dict[str, int]:
    chars = collect_chars(_flatten_translations())
    return {ch: i for i, ch in enumerate(chars)}


# --- 字形渲染 ---------------------------------------------------------------
def _render_glyph(ch, font) -> list[list[int]]:
    """把单个字符渲染成 16x16 的 0/1 位图（汉字画在左上 12x12 内）。"""
    from PIL import Image, ImageDraw

    img = Image.new("L", (16, 16), 0)
    d = ImageDraw.Draw(img)
    d.text((0, 0), ch, fill=255, font=font)
    px = img.load()
    return [[1 if px[x, y] > 127 else 0 for x in range(16)] for y in range(16)]


def _bitmap_to_tiles(bits: list[list[int]]) -> bytes:
    """16x16 位图 -> 4 个 8x8 1bpp tile（TL, TR, BL, BR），每个 8 字节。"""
    out = bytearray()
    for ty in (0, 8):
        for tx in (0, 8):
            for y in range(8):
                b = 0
                for x in range(8):
                    if bits[ty + y][tx + x]:
                        b |= 0x80 >> x
                out.append(b)
    return bytes(out)


def _write(path: str, lines) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(line + "\n")


def cmd_gen_font(args) -> int:
    from PIL import ImageFont

    chars = collect_chars(_flatten_translations(),
                          include_glossary=getattr(args, "with_glossary", False))
    if not chars:
        print("错误：未收集到任何汉字（术语表为空？）", file=sys.stderr)
        return 2
    if len(chars) > ZH_MAX_GLYPHS:
        print(f"错误：字形数 {len(chars)} 超过双字节编码容量 {ZH_MAX_GLYPHS}", file=sys.stderr)
        return 2

    try:
        font = ImageFont.truetype(args.font, size=args.size)
    except OSError as e:
        print(f"错误：无法加载字体 {args.font}：{e}", file=sys.stderr)
        return 2

    glyphs = bytearray()
    for ch in chars:
        glyphs += _bitmap_to_tiles(_render_glyph(ch, font))

    gdir = repo_path("gfx", "font")
    os.makedirs(gdir, exist_ok=True)
    os.makedirs(repo_path("data", "font"), exist_ok=True)

    # 字形数可能变少，先清掉上一次的分块文件，避免残留被扫进依赖
    for name in os.listdir(gdir):
        if re.fullmatch(r"zh\.\d+\.1bpp", name):
            os.remove(os.path.join(gdir, name))

    chunks = [glyphs[k:k + GLYPH_CHUNK_BYTES]
              for k in range(0, len(glyphs), GLYPH_CHUNK_BYTES)]
    for k, blob in enumerate(chunks):
        with open(os.path.join(gdir, f"zh.{k}.1bpp"), "wb") as f:
            f.write(blob)

    # 分块数据 + 每块的基址/所在 bank。渲染代码按 chunk = i / ZH_GLYPHS_PER_BANK 寻址。
    _write(repo_path("data", "font", "zh_glyphs.asm"), [
        "; 自动生成，请勿手改（由 tools/zh/zh.py gen-font 产出）",
        f"; {len(chars)} 个字形分 {len(chunks)} 块存放，每块至多 {GLYPHS_PER_BANK} 个字形"
        f"（{GLYPHS_PER_BANK} x {GLYPH_SIZE} = {GLYPH_CHUNK_BYTES} 字节 = 0x{GLYPH_CHUNK_BYTES:x}）。",
        "IF DEF(LANG_ZH)",
    ] + [
        line
        for k in range(len(chunks))
        for line in (
            "",
            f'SECTION "Chinese Glyph GFX {k}", ROMX',
            f"ZhGlyphGFX{k}::",
            f'INCBIN "gfx/font/zh.{k}.1bpp"',
            f"ZhGlyphGFX{k}End::",
        )
    ] + ["", "ENDC"])

    _write(repo_path("data", "font", "zh_chunk_ptrs.asm"), [
        "; 自动生成，请勿手改。分块基址表，索引 = 字形序号 / ZH_GLYPHS_PER_BANK。",
        "; 被 engine/gfx/cjk_text.asm 的 ZhGlyphChunkPtrs 引用（必须与渲染代码同 bank）。",
    ] + [f"\tdw ZhGlyphGFX{k}" for k in range(len(chunks))])

    _write(repo_path("data", "font", "zh_chunk_banks.asm"), [
        "; 自动生成，请勿手改。分块所在的 ROM bank，索引 = 字形序号 / ZH_GLYPHS_PER_BANK。",
        "; 被 engine/gfx/cjk_text.asm 的 ZhGlyphChunkBanks 引用（必须与渲染代码同 bank）。",
    ] + [f"\tdb BANK(ZhGlyphGFX{k})" for k in range(len(chunks))])

    # 码点表：仅作调试用的反查清单，不编入 ROM（运行时用的是码流里的字形序号）。
    _write(repo_path("data", "font", "zh_index.asm"), [
        "; 自动生成，请勿手改（由 tools/zh/zh.py gen-font 产出）。",
        "; 字形序号 -> Unicode 码点；仅供排查用，**不编入 ROM**。",
    ] + [f"\tdb ${(ord(ch) >> 8) & 0xff:02x}, ${ord(ch) & 0xff:02x}" for ch in chars])

    _write(repo_path("constants", "zh_font.asm"), [
        "; 自动生成，请勿手改（由 tools/zh/zh.py gen-font 产出）",
        f"DEF ZH_GLYPH_COUNT     EQU {len(chars)}",
        f"DEF ZH_GLYPH_SIZE      EQU {GLYPH_SIZE}",
        f"DEF ZH_GLYPHS_PER_BANK EQU {GLYPHS_PER_BANK}",
        f"DEF ZH_GLYPH_BANK_COUNT EQU {len(chunks)}",
        f"DEF ZH_GLYPH_BANK_SHIFT EQU {GLYPH_BANK_SHIFT}",
        "",
        f"IF ZH_GLYPH_COUNT > {ZH_MAX_GLYPHS}",
        '\tFAIL "字形数超过双字节编码容量"',
        "ENDC",
        "ASSERT ZH_GLYPH_SIZE * ZH_GLYPHS_PER_BANK == $4000",
        "ASSERT 1 << ZH_GLYPH_BANK_SHIFT == ZH_GLYPHS_PER_BANK",
    ])

    print(f"gen-font: {len(chars)} 个字形 -> {len(chunks)} 个分块"
          f"（每块 <= {GLYPHS_PER_BANK} 字形 / {GLYPH_CHUNK_BYTES} 字节）")
    print(f"gen-font: 分块数据 {len(glyphs)} 字节 -> gfx/font/zh.<n>.1bpp")
    print(f"gen-font: 索引与常量 -> data/font/zh_glyphs.asm, constants/zh_font.asm")
    return 0


# --- 文本转码 ---------------------------------------------------------------
class TranslationError(ValueError):
    pass


def encode_string(s: str, charmap: dict[str, int], gmap: dict[str, int],
                  keys_by_len: list[str]) -> bytes:
    """把一段译文（含控制码标记）编码为游戏的字节流。

    匹配顺序：汉字字形 > 最长 charmap 键 > 报错。
    """
    out = bytearray()
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]

        # 0) {{0}} 与 {d:SYMBOL} 这类占位符由组装/汇编期展开，不参与编码
        if ch == "{":
            m = re.match(r"\{+[^{}]*\}+", s[i:])
            if m:
                i += m.end()
                continue

        # 1) 汉字/全角标点：优先走 12x12 字库
        if ch in gmap:
            g = gmap[ch]
            out.append(ZH_LEAD_START + (g >> ZH_GLYPH_PAGE_SHIFT))
            out.append(ZH_TRAIL_START | (g & ZH_TRAIL_MASK))
            i += 1
            continue

        # 2) 最长匹配 charmap（涵盖 "<PLAYER>"、"#mon"、"'s" 等多字符键）
        for key in keys_by_len:
            if s.startswith(key, i):
                out.append(charmap[key])
                i += len(key)
                break
        else:
            raise TranslationError(
                f"无法编码的字符 {ch!r}（U+{ord(ch):04X}）；"
                f"若为汉字请先加入术语表/译文的字形集再跑 gen-font"
            )
    return bytes(out)


def _flatten(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, list):
        r = []
        for v in obj:
            r.extend(_flatten(v))
        return r
    if isinstance(obj, dict):
        r = []
        for v in obj.values():
            r.extend(_flatten(v))
        return r
    return []


def _flatten_translations() -> list[str]:
    texts: list[str] = []
    for path in translation_paths():
        with open(path, encoding="utf-8") as f:
            texts.extend(_flatten(json.load(f)))
    for trans in text_translations().values():
        texts.extend(v for v in trans.values() if v)
    for trans in include_translations().values():
        texts.extend(v for v in trans.values() if v)
    for trans in map_translations().values():
        texts.extend(v for v in trans.values() if v)
    return texts


def load_translation(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是对象：{{label: {{en, zh}}}}")
    return data


def cmd_encode(args) -> int:
    charmap = load_charmap()
    keys = charmap_keys_by_length(charmap)
    gmap = glyph_map()
    odir = repo_path("data", "text", "zh")
    os.makedirs(odir, exist_ok=True)

    total = 0
    for path in translation_paths():
        stem = os.path.basename(path)[:-5]
        data = load_translation(path)
        out_asm = os.path.join(odir, stem + ".asm")
        try:
            with open(out_asm, "w", encoding="utf-8") as f:
                f.write("; 自动生成，请勿手改（由 tools/zh/zh.py encode 产出）\n")
                f.write(f"; 源：tools/zh/translations/{stem}.json\n")
                for label, entry in data.items():
                    text = entry["zh"] if isinstance(entry, dict) else entry
                    blob = encode_string(text, charmap, gmap, keys)
                    # 允许在 JSON 条目里用 "section" 指定段名，并可选 "fragment": true
                    # 与同段引擎放在同一 bank。
                    if isinstance(entry, dict) and "section" in entry:
                        section = entry["section"]
                        fragment = entry.get("fragment", False)
                    else:
                        section = f"zh_{stem}_{label}"
                        fragment = False
                    # FRAGMENT 段语法：SECTION FRAGMENT "name", ROMX
                    if fragment:
                        f.write(f'\nSECTION FRAGMENT "{section}", ROMX\n')
                    else:
                        f.write(f'\nSECTION "{section}", ROMX\n')
                    f.write(f"{label}::\n")
                    if not blob:
                        f.write("\tdb $53 ; @ (空串)\n")
                    for k in range(0, len(blob), 16):
                        chunk = blob[k:k + 16]
                        f.write("\tdb " + ", ".join(f"${b:02x}" for b in chunk) + "\n")
                    total += 1
        except TranslationError as e:
            print(f"[编码失败] {stem}:{label}: {e}", file=sys.stderr)
            return 1
        print(f"encode: {out_asm}（{len(data)} 条）")

    print(f"encode: 共 {total} 条文本")
    return 0


# --- 反向解码：验证「运行期会画出哪个字」------------------------------------
# 编码是「译者意图 -> 字节」，解码是「字节 -> Z80 实际会画出的字」。
# 二者在 item 级别比对，才能证明双字节公式、分块公式与渲染代码一致。
LABEL_ONLY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)::?$")
# 逐条替换的 db 字符串（菜单/界面文本）没有自己的标签，靠 text_ir 写出的
# `;@ 作用域#序号` 注释行标记分组，这样解码校验也能按条核对。
UNIT_MARK_RE = re.compile(r"^;@\s*([A-Za-z_][A-Za-z0-9_.]*#\d+)\s*$")
DB_LINE_RE = re.compile(r"^db\s+(.+)$")
DEF_RE = re.compile(r"^DEF\s+([A-Za-z_][A-Za-z0-9_]*)\s+EQU\s+(.+?)\s*$")

# 12x12 字形按 TL, TR, BL, BR 四个 8x8 tile 存储（见 zh._bitmap_to_tiles）。
TILE_ORIGINS = ((0, 0), (TILE, 0), (0, TILE), (TILE, TILE))


def parse_db_asm(path: str) -> dict:
    """把生成的文本 .asm 解析成 {label: [(kind, value), ...]}。

    kind 为 "db"（value 是 bytes）或 "op"（value 是原样保留的命令行）。
    逐条替换的 db 字符串没有自己的标签，靠 text_ir 写出的 `;@ 键` 注释行分组；
    条目的 db 段一结束（出现非 db 行）就退出分组，否则后面的代码会被算进来。
    """
    out: dict[str, list] = {}
    label = None
    unit = None
    with open(path, encoding="utf-8") as f:
        for raw in f.read().split("\n"):
            mark = UNIT_MARK_RE.match(raw.strip())
            if mark:
                unit = mark.group(1)
                out.setdefault(unit, [])
                continue
            line = raw.split(";", 1)[0].strip()
            if not line:
                continue
            if unit is not None:
                m = DB_LINE_RE.match(line)
                if m:
                    _append_db(out, unit, m.group(1))
                    continue
                unit = None  # 字符串条目的字节段结束，本行交回下面的常规逻辑
            m = LABEL_ONLY_RE.match(line)
            if m:
                label = m.group(1)
                out[label] = []
                continue
            if line.startswith("SECTION"):
                continue
            if label is None:
                continue
            m = DB_LINE_RE.match(line)
            if m:
                _append_db(out, label, m.group(1))
                continue
            out[label].append(("op", line))
    return out


def _eval_db_byte(tok: str):
    """求值一个 db 字节项：$hex / %bin / 十进制，以及它们用 | 拼出的位组合。

    含符号或表达式的项返回 None —— 解码只面向字面量数据，遇到符号跳过即可。
    """
    val = 0
    for part in tok.split("|"):
        p = part.strip()
        if not p:
            return None
        try:
            if p.startswith("$"):
                n = int(p[1:], 16)
            elif p.startswith("%"):
                n = int(p[1:], 2)
            else:
                n = int(p, 10)
        except ValueError:
            return None
        val |= n
    return val if 0 <= val <= 255 else None


def _append_db(out: dict, key: str, items: str) -> None:
    """把一行 db 的字节并进 key 的字节段。

    只认字面量；字符串字面量（如 db "Money@"）与含符号的项会被跳过，
    跳过比整行丢弃更有利于后续字节对齐。
    """
    blob = bytearray()
    for tok in items.split(","):
        v = _eval_db_byte(tok.strip())
        if v is not None:
            blob.append(v)
    # 汇编器把连续的 db 行拼成一段字节流，汉字可能被切在两行之间，
    # 因此这里也要合并，否则会把一对 lead+trail 拆开解析。
    if out.get(key) and out[key][-1][0] == "db":
        out[key][-1] = ("db", out[key][-1][1] + bytes(blob))
    else:
        out.setdefault(key, []).append(("db", bytes(blob)))


def gen_constants() -> dict:
    """读回 constants/zh_font.asm 与 constants/zh_text.asm 里的 DEF 常量。

    作为「汇编侧以为的字库规模/槽位数」的独立来源，用来和工具算出的值对照。
    只接受纯字面量，含表达式的行（如 `1 << 7`）会被跳过。
    """
    vals: dict[str, int] = {}
    for name in ("zh_font.asm", "zh_text.asm"):
        path = repo_path("constants", name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for line in f:
                m = DEF_RE.match(line.split(";", 1)[0].strip())
                if m:
                    try:
                        vals[m.group(1)] = int(m.group(2), 0)
                    except ValueError:
                        pass
    return vals


def decode_bytes(blob: bytes, rev: dict, chars: list) -> tuple:
    """按 home/text.asm + engine/gfx/cjk_text.asm 的规则还原字节流。

    汉字：lead($0a-$4c) + trail，字形序号 = (lead - ZH_LEAD_START) * 256 + trail。
    返回 (文本, 问题列表, 引用到的字形序号列表)。
    """
    out, problems, glyphs = [], [], []
    i = 0
    while i < len(blob):
        b = blob[i]
        if ZH_LEAD_START <= b <= ZH_LEAD_END:
            if i + 1 >= len(blob):
                problems.append(f"末尾孤立的汉字前导字节 ${b:02x}")
                break
            trail = blob[i + 1]
            g = ((b - ZH_LEAD_START) << ZH_GLYPH_PAGE_SHIFT) | (trail & ZH_TRAIL_MASK)
            glyphs.append(g)
            if not ZH_TRAIL_START <= trail <= ZH_TRAIL_END:
                problems.append(
                    f"前导字节 ${b:02x} 的尾字节 ${trail:02x} 不在 ${ZH_TRAIL_START:02x}-"
                    f"${ZH_TRAIL_END:02x}，可能被误判成控制码/终止符"
                )
            if g >= len(chars):
                problems.append(f"字形序号 {g} 超出字库（共 {len(chars)} 个）")
                out.append("\ufffd")
            else:
                out.append(chars[g])
            i += 2
            continue
        if b in rev:
            out.append(rev[b])
        else:
            problems.append(f"未知字节 ${b:02x}：既不是汉字前导码，也不在 charmap 中")
            out.append(f"<${b:02x}>")
        i += 1
    return "".join(out), problems, glyphs


def canonical_text(s: str, charmap: dict, keys: list, rev: dict, gmap: dict) -> str:
    """把文本里的 charmap 键统一成 rev 的规范拼写，便于与解码结果直接比较。

    例如 JSON 写 "#mon"、charmap 另有等价的 "<PKMN>"，两侧都被归一成同一种写法。
    """
    # {d:SYMBOL} 与 {{n}} 由 asm/运行期展开，不进 ROM 字节，比对时两边都去掉
    s = re.sub(r"\{+[^{}]*\}+", "", s)
    out, i = [], 0
    while i < len(s):
        if s[i] in gmap:
            out.append(s[i])
            i += 1
            continue
        for key in keys:
            if s.startswith(key, i):
                out.append(rev.get(charmap[key], key))
                i += len(key)
                break
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def audit_glyphs(chars: list, used: set) -> list:
    """逐个核对被引用的字形：分块文件存在、偏移落在块内、点阵非空。"""
    problems = []
    gdir = repo_path("gfx", "font")
    consts = gen_constants()
    expect_banks = consts.get("ZH_GLYPH_BANK_COUNT")
    actual_chunks = len([n for n in os.listdir(gdir)
                         if re.fullmatch(r"zh\.\d+\.1bpp", n)]) if os.path.isdir(gdir) else 0
    if expect_banks is not None and expect_banks != actual_chunks:
        problems.append(
            f"constants/zh_font.asm 声明 {expect_banks} 个分块，磁盘上有 {actual_chunks} 个"
            f"（需要重跑 gen-font）"
        )
    if consts.get("ZH_GLYPH_COUNT") not in (None, len(chars)):
        problems.append(
            f"constants/zh_font.asm 声明 {consts['ZH_GLYPH_COUNT']} 个字形，"
            f"当前字形集是 {len(chars)} 个（需要重跑 gen-font）"
        )

    for i in sorted(used):
        chunk, off = divmod(i, GLYPHS_PER_BANK)
        path = os.path.join(gdir, f"zh.{chunk}.1bpp")
        if not os.path.isfile(path):
            problems.append(f"字形 {i}（{chars[i]}）缺少分块文件 zh.{chunk}.1bpp")
            continue
        with open(path, "rb") as f:
            blob = f.read()
        if (off + 1) * GLYPH_SIZE > len(blob):
            problems.append(f"字形 {i}（{chars[i]}）偏移超出 zh.{chunk}.1bpp（{len(blob)} 字节）")
            continue
        data = blob[off * GLYPH_SIZE:(off + 1) * GLYPH_SIZE]
        if not any(data):
            problems.append(f"字形 {i}（{chars[i]}）的点阵是空的")
    return problems


def cmd_text_decode(args) -> int:
    ti = _text_ir()
    defines = set(args.define or DEFAULT_DEFINES)
    charmap = load_charmap()
    keys = charmap_keys_by_length(charmap)
    chars = collect_chars(_flatten_translations())
    gmap = {ch: i for i, ch in enumerate(chars)}
    rev: dict[int, str] = {}
    for k in sorted(charmap, key=len):
        rev.setdefault(charmap[k], k)

    used: set[int] = set()
    def page_glyph_need(text: str) -> int:
        """单页用到的不同字形数。

        引擎在 <PARA> 换页时 call ClearSpeechBox -> Zh_ResetSlots，槽位是**按页**
        重置的，所以判据应该是「每页不同字形 <= ZH_SLOT_COUNT」，按整条累计会把
        长对话全部误报成槽位不足。占位命令（<PLAYER>/<RIVAL>/{{n}}）由引擎自己画，
        不占字形槽。
        """
        worst_page = 0
        for page in text.split("<PARA>"):
            stripped = re.sub(r"<[A-Za-z_]+>|\{\{\d+\}\}", "", page)
            worst_page = max(worst_page, len({c for c in stripped if ord(c) > 0x2000}))
        return worst_page

    slots = gen_constants().get("ZH_SLOT_COUNT", 0)
    worst = 0
    problems = 0
    for spec in _selected_sources(args.only):
        stem = spec["stem"]
        asm_path = spec["out"]
        if not os.path.isfile(asm_path):
            continue
        trans = ti.load_trans(os.path.join(spec["ir_dir"], stem + ".json"))
        blocks = {b["label"]: b for b in ti.extract_blocks(spec["src"], defines)}
        parsed = parse_db_asm(asm_path)

        checked = 0
        for label, zh in trans.items():
            if not zh.strip() or label not in parsed:
                continue
            block = blocks.get(label)
            opaque = list(block["opaque"]) if block else []
            checked += 1

            # 还原成与 JSON 同构的文本：db 段解码，opaque 行换成 {{n}}
            parts: list[str] = []
            block_glyphs: set[int] = set()
            for kind, val in parsed[label]:
                if kind == "db":
                    text, probs, glyphs = decode_bytes(val, rev, chars)
                    parts.append(text)
                    block_glyphs.update(glyphs)
                    used.update(glyphs)
                    for p in probs:
                        print(f"[解码异常] {stem}:{label}: {p}")
                        problems += 1
                else:
                    try:
                        parts.append("{{%d}}" % opaque.index(val))
                    except ValueError:
                        print(f"[未知命令] {stem}:{label}: {val}")
                        problems += 1

            got = canonical_text("".join(parts), charmap, keys, rev, gmap)
            want = canonical_text(zh, charmap, keys, rev, gmap)

            # 字形缓存槽位数有限（constants/zh_text.asm），一页内用到的不同字形
            # 超过槽位数时会显示空白格。按页统计（见 page_glyph_need 的说明）。
            page_need = page_glyph_need(zh)
            if slots and page_need > slots:
                print(f"[槽位不足] {stem}:{label} 单页需要 {page_need} 个不同字形 > "
                      f"ZH_SLOT_COUNT={slots}（超出的会显示成空白格）")
                problems += 1
            worst = max(worst, page_need)

            if got != want:
                print(f"[还原不一致] {stem}:{label}")
                print(f"    译文: {want}")
                print(f"    字节: {got}")
                problems += 1
            elif args.show:
                print(f"{label}: {got}")

        if checked:
            print(f"text-decode: {stem}: 核对 {checked} 条")

    print(f"text-decode: 引用 {len(used)} 个字形；单条最多 {worst} 个"
          f"（ZH_SLOT_COUNT={slots}）")
    for msg in audit_glyphs(chars, used):
        print(f"[字形异常] {msg}")
        problems += 1
    print(f"text-decode: {problems} 处问题")
    return 1 if problems else 0


# --- 校验 -------------------------------------------------------------------
TOKEN_RE = re.compile(r"<[A-Za-z0-9_]+>|@")
LINEBREAK_TOKENS = {"<NEXT>", "<LINE>", "<PARA>", "<CONT>", "<LNBRK>"}


def token_sequence(s: str) -> list[str]:
    return TOKEN_RE.findall(s)


def cmd_validate(args) -> int:
    charmap = load_charmap()
    keys = charmap_keys_by_length(charmap)
    gmap = glyph_map()
    problems = 0

    for path in translation_paths():
        stem = os.path.basename(path)[:-5]
        data = load_translation(path)
        for label, entry in data.items():
            if not isinstance(entry, dict) or "zh" not in entry:
                print(f"[格式错误] {stem}:{label} 需为 {{\"en\": .., \"zh\": ..}}")
                problems += 1
                continue
            en = entry.get("en", "")
            zh = entry["zh"]

            # 1) 控制码序列必须与原文完全一致（顺序 + 个数）
            if en and token_sequence(en) != token_sequence(zh):
                print(f"[控制码不匹配] {stem}:{label}")
                print(f"    en: {' '.join(token_sequence(en)) or '(无)'}")
                print(f"    zh: {' '.join(token_sequence(zh)) or '(无)'}")
                problems += 1

            # 2) 终止符
            if not zh.endswith("@") and "<DONE>" not in zh:
                print(f"[缺少终止符] {stem}:{label}（应以 @ 或 <DONE> 结尾）")
                problems += 1

            # 3) 行长（按 tile 计账：汉字占 2 tile，半角占 1 tile）
            #    菜单项画在更宽的菜单窗口里（文本区 18 tile），单独取阈值
            is_menu = zh.endswith("@") and not any(
                t in zh for t in ("<LINE>", "<PARA>", "<CONT>", "\\n")
            )
            warn = LINE_TILE_LIMIT if is_menu else LINE_TILE_WARN
            for w in line_tile_widths(zh):
                if w > LINE_TILE_LIMIT:
                    print(f"[行长超限] {stem}:{label} {w} tile > {LINE_TILE_LIMIT} tile")
                    problems += 1
                elif w > warn:
                    print(f"[行长偏长] {stem}:{label} {w} tile > {LINE_TILE_WARN} tile（会贴右边框）")

            # 4) 逐字符可编码性 + 是否混入未翻译英文
            try:
                encode_string(zh, charmap, gmap, keys)
            except TranslationError as e:
                print(f"[无法编码] {stem}:{label}: {e}")
                problems += 1

    print(f"validate: {problems} 处问题")
    return 1 if problems else 0


# --- 字形集汇总 -------------------------------------------------------------
def cmd_chars(args) -> int:
    chars = collect_chars(_flatten_translations())
    print(f"chars: 共 {len(chars)} 个汉字（上限 {ZH_MAX_GLYPHS}）")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("".join(chars))
        print(f"已写入 {args.out}")
    return 0


# --- 真实游戏文本：抽取 / 编码 / 校验 ---------------------------------------
DEFAULT_DEFINES = ["LANG_ZH", "ZH_DEMO"]


def _selected_sources(only) -> list[dict]:
    return [s for s in text_source_specs() if not only or s["stem"] in only]


def cmd_text_extract(args) -> int:
    ti = _text_ir()
    defines = set(args.define or DEFAULT_DEFINES)
    os.makedirs(text_ir_dir(), exist_ok=True)
    os.makedirs(include_ir_dir(), exist_ok=True)
    os.makedirs(map_ir_dir(), exist_ok=True)

    total = added_all = 0
    for spec in _selected_sources(args.only):
        stem = spec["stem"]
        out_json = os.path.join(spec["ir_dir"], stem + ".json")
        old = ti.load_trans(out_json)
        try:
            entries = ti.extract_entries(spec["src"], defines)
        except ti.TextIRError as e:
            print(f"[跳过] {stem}: {e}", file=sys.stderr)
            continue
        added = ti.dump_trans(out_json, entries, old, source=os.path.relpath(spec["src"], ROOT))
        done = sum(1 for e in entries if old.get(e["key"]))
        print(f"text-extract: {stem}: 可译 {len(entries)} 条（已译 {done}，本次新增 {added}）")
        total += len(entries)
        added_all += added

    print(f"text-extract: 共 {total} 条可译文本，本次新增 {added_all} 条待译")
    return 0


def cmd_text_encode(args) -> int:
    ti = _text_ir()
    defines = set(args.define or DEFAULT_DEFINES)
    charmap = load_charmap()
    keys = charmap_keys_by_length(charmap)
    gmap = glyph_map()

    total = replaced_all = 0
    for spec in _selected_sources(args.only):
        stem = spec["stem"]
        ir_path = os.path.join(spec["ir_dir"], stem + ".json")
        if not os.path.isfile(ir_path):
            continue
        trans = ti.load_trans(ir_path)
        out_asm = spec["out"]
        os.makedirs(os.path.dirname(out_asm), exist_ok=True)
        try:
            n, replaced = ti.build(spec["src"], trans, out_asm, defines,
                                   charmap=charmap, keys=keys, gmap=gmap)
        except ti.TextIRError as e:
            print(f"[编码失败] {e}", file=sys.stderr)
            return 1
        print(f"text-encode: {stem}: 替换 {replaced}/{n} 条 -> {out_asm}")
        total += n
        replaced_all += replaced

    print(f"text-encode: 共替换 {replaced_all}/{total} 条")
    return 0


def cmd_map_scripts(args) -> int:
    """生成 data/text/zh_maps/scripts.asm：data/maps/scripts.asm 的副本。

    只有 tools/zh/map_sources.txt 收录、且已产出中文副本的地图会被换掉；
    其余 INCLUDE 原样保留，所以「零翻译」时生成物与英文原文件等价。
    """
    src = repo_path("data", "maps", "scripts.asm")
    out_path = map_scripts_out()
    out_dir = os.path.dirname(out_path)
    specs = [s for s in text_source_specs() if s["kind"] == "maps"]
    swapped: dict[str, str] = {}
    skipped: list[str] = []

    for spec in specs:
        gen = spec["out"]
        if not os.path.isfile(gen):
            continue
        # 地图脚本里若有自己的 INCLUDE，副本换了目录会解析不到（rgbasm 的相对路径
        # 基准尚未在这里定死），宁可不换也不生成一个编不过的文件。
        with open(spec["src"], encoding="utf-8") as f:
            for line in f:
                if re.match(r'\s*INCLUDE\b', line):
                    skipped.append(spec["stem"])
                    break
            else:
                swapped[spec["stem"]] = gen

    with open(src, encoding="utf-8") as f:
        lines = f.read().split("\n")

    INC_RE = re.compile(r'^(\s*INCLUDE\s+")maps/([^"/]+)\.asm"(\s*(?:;.*)?)$')
    out = [
        "; 自动生成，请勿手改（由 tools/zh/zh.py map-scripts 产出）",
        "; 源：data/maps/scripts.asm；只有 map_sources.txt 收录的地图会被换成中文副本。",
    ]
    n_swap = 0
    for line in lines:
        m = INC_RE.match(line)
        if m and m.group(2) in swapped:
            # 路径必须写成**仓库根相对**：tools/scan_includes 不做「相对当前文件」
            # 的解析，写成同级相对名会让 map_data.o 的依赖变成一个不存在的裸文件名
            # （实测 No rule to make target 'NewBarkTown.asm'）。rgbasm 会依次尝试
            # 相对当前文件与相对 cwd，写成根相对两种情况下都能找到。
            gen = os.path.relpath(swapped[m.group(2)], ROOT).replace("\\", "/")
            out.append(f'{m.group(1)}{gen}"{m.group(3)}')
            n_swap += 1
            continue
        out.append(line)

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))

    print(f"map-scripts: 收录 {len(specs)} 个地图，本次换入 {n_swap} 个 -> {out_path}")
    if skipped:
        print(f"map-scripts: 忽略 {len(skipped)} 个含内部 INCLUDE 的地图："
              f"{', '.join(sorted(skipped))}")
    return 0


def cmd_text_validate(args) -> int:
    ti = _text_ir()
    defines = set(args.define or DEFAULT_DEFINES)
    charmap = load_charmap()
    keys = charmap_keys_by_length(charmap)
    gmap = glyph_map()
    problems = 0
    # 文本引擎一次只能在 VRAM 里放这么多字形槽位（见 constants/zh_text.asm 的
    # ZH_SLOT_COUNT）。槽位表在 ClearSpeechBox 里被重置（Zh_ResetSlots），所以限制
    # 不是"整条文本"，而是**每一页 <PARA>**：跨页的长对话不受影响，同页超了才会画白块。
    slot_limit = gen_constants().get("ZH_SLOT_COUNT", 0)

    for spec in _selected_sources(args.only):
        stem = spec["stem"]
        ir_path = os.path.join(spec["ir_dir"], stem + ".json")
        if not os.path.isfile(ir_path):
            continue
        trans = ti.load_trans(ir_path)
        try:
            entries = {e["key"]: e for e in ti.extract_entries(spec["src"], defines)}
        except ti.TextIRError as e:
            print(f"[跳过] {stem}: {e}", file=sys.stderr)
            continue

        done = 0
        for key, zh in trans.items():
            if not zh.strip():
                continue
            done += 1
            entry = entries.get(key)
            if entry is None:
                print(f"[未知标签] {stem}:{key}")
                problems += 1
                continue
            for msg in ti.validate_block(key, entry["en"], zh):
                print(f"{stem}: {msg}")
                problems += 1
            # 逐段可编码性（占位符之间才是真正要编码的文本）
            try:
                for part in re.split(r"\{\{\d+\}\}", zh):
                    encode_string(part, charmap, gmap, keys)
            except TranslationError as e:
                print(f"[无法编码] {stem}:{key}: {e}")
                problems += 1
            if slot_limit:
                worst = max(
                    (len({c for c in page if c in gmap}) for page in PAGE_SPLIT_RE.split(zh)),
                    default=0,
                )
                if worst > slot_limit:
                    print(f"[字形槽位超限] {stem}:{key}: 单页用了 {worst} 个不同汉字"
                          f"（ZH_SLOT_COUNT={slot_limit}），这一页会画不出字，"
                          f"要在 vt2 插 <PARA> 分到两页")
                    problems += 1
        if done:
            print(f"text-validate: {stem}: 已译 {done} 条")

    print(f"text-validate: {problems} 处问题")
    return 1 if problems else 0


# --- 标题美术字 --------------------------------------------------------------
# 标题画面（engine/movie/title.asm）把 TitleLogoGFX 解压进 vTiles1，再按
# 「每行 18 个 tile、共 7 行」写进 BGMap，起点是 (1, 3)。这张图因此**必须**
# 恰好 144x56 像素：尺寸一变，tile 数量与引擎写死的 18x7 布局就对不上，
# 标题会撕裂或盖住别的元素。
# 转换走 Makefile 的通用规则 `%.2bpp: %.png`，命令是 `rgbgfx -c dmg`，
# 只认 DMG 的四个色阶 #ffffff / #aaaaaa / #555555 / #000000，故按这四个值绘制。

TITLE_LOGO_W = 144
TITLE_LOGO_H = 56
TITLE_LOGO_SS = 8  # 超采样倍数：高分辨率渲染后缩小，描边宽度才稳定为整数像素

DMG_WHITE = 0xFF
DMG_LIGHT = 0xAA
DMG_DARK = 0x55
DMG_BLACK = 0x00


def _text_mask(text: str, size: int, center_y: int, font) -> "Image.Image":
    """把一行文字画成 144x56 的二值掩码：水平居中、纵向按 center_y 居中。"""
    from PIL import Image, ImageDraw

    big = Image.new("L", (TITLE_LOGO_W * TITLE_LOGO_SS, TITLE_LOGO_H * TITLE_LOGO_SS), 0)
    d = ImageDraw.Draw(big)
    left, top, right, bottom = d.textbbox((0, 0), text, font=font)
    x = (TITLE_LOGO_W * TITLE_LOGO_SS - (right - left)) // 2 - left
    y = int(center_y * TITLE_LOGO_SS) - (bottom - top) // 2 - top
    d.text((x, y), text, font=font, fill=255)
    # 缩小后阈值化：抗锯齿的灰边要么归入笔画，要么丢掉，不留在 4 色阶图里。
    small = big.resize((TITLE_LOGO_W, TITLE_LOGO_H), Image.LANCZOS)
    return small.point(lambda v: 255 if v >= 128 else 0)


def render_title_logo(lines, font_path: str, outline: int = 1):
    """渲染标题美术字，返回 144x56 的 L 模式图（取值仅 DMG 四个色阶）。

    lines 为 (文本, 字号, 纵向中心 y) 列表。
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

    masks = []
    for text, size, center_y in lines:
        font = ImageFont.truetype(font_path, size=size * TITLE_LOGO_SS)
        masks.append(_text_mask(text, size, center_y, font))

    ink = Image.new("L", (TITLE_LOGO_W, TITLE_LOGO_H), 0)
    for m in masks:
        ink = ImageChops.lighter(ink, m)

    # 描边必须完整落在画布内，否则被裁掉的一侧会缺少黑边，字看起来会「缺角」。
    box = ink.getbbox()
    if box is None:
        raise ValueError("渲染结果为空：文本或字号设置有问题")
    if box[0] < outline or box[1] < outline or box[2] > TITLE_LOGO_W - outline or box[3] > TITLE_LOGO_H - outline:
        raise ValueError(
            f"文字外框 {box} 距画布边缘不足 {outline} 像素，描边会被裁切；"
            f"请调小字号或纵向位置"
        )

    grown = ink
    for _ in range(outline):
        grown = grown.filter(ImageFilter.MaxFilter(3))
    ring = ImageChops.subtract(grown, ink)

    # 标题画面给 logo 所在的第 3-9 行挂的是 title.pal 的调色板 2/3：
    #   索引 0 = 黑（背景）、索引 1 = 淡黄、索引 2 = 金、索引 3 = 暗蓝。
    # 而 rgbgfx -c dmg 的映射是 白->0、浅灰->1、深灰->2、黑->3，
    # 也就是说这张图里的「白色」在屏幕上画成黑色背景 —— 笔画必须用灰色系，
    # 一旦把字面填成白色，字就直接消失在黑底里。
    # 上半浅灰（淡黄）、下半深灰（金）正好还原原作的金色金属渐变。
    grad = Image.new("L", (TITLE_LOGO_W, TITLE_LOGO_H), DMG_LIGHT)
    gd = ImageDraw.Draw(grad)
    for m in masks:
        band = m.getbbox()
        if band is None:
            continue
        top, bottom = band[1], band[3]
        split = top + (bottom - top) * 0.55
        gd.rectangle([0, split, TITLE_LOGO_W, bottom], fill=DMG_DARK)

    img = Image.new("L", (TITLE_LOGO_W, TITLE_LOGO_H), DMG_WHITE)
    img.paste(DMG_BLACK, (0, 0), ring)
    return Image.composite(grad, img, ink)


def cmd_title_logo(args) -> int:
    from PIL import Image

    lines = []
    for text, size, center_y in ((args.line1, args.size1, args.y1), (args.line2, args.size2, args.y2)):
        if text:
            lines.append((text, size, center_y))
    if not lines:
        print("错误：至少需要一行标题文字", file=sys.stderr)
        return 2

    try:
        img = render_title_logo(lines, args.font, args.outline)
    except (OSError, ValueError) as e:
        print(f"错误：{e}", file=sys.stderr)
        return 2

    out = args.out if os.path.isabs(args.out) else repo_path(*args.out.split("/"))
    os.makedirs(os.path.dirname(out), exist_ok=True)

    # 严格限制成四个色阶，并断言输出确实只有这四种取值，避免混进中间灰
    # 让 rgbgfx 的 DMG 映射凭空多出一档。
    shades = sorted(set(img.tobytes()))
    if not set(shades) <= {DMG_WHITE, DMG_LIGHT, DMG_DARK, DMG_BLACK}:
        print(f"错误：输出含非 DMG 色阶 {shades}", file=sys.stderr)
        return 2
    img.save(out)
    print(f"title-logo: {out} {img.size[0]}x{img.size[1]} 色阶 {shades}")
    return 0


# --- 容量预算 ---------------------------------------------------------------
# 可用 bank 数由调用方给（默认 255）：Bankswitch 只写 [rROMB] 低 8 位，
# 所以 bank $01-$ff（约 4MB）都可用，超过 $ff 才需要写 $3000 第 9 位。


def _romx_used(map_path: str):
    """读 rgblink .map 的 SUMMARY：返回 (ROMX 已用字节, bank 数)。"""
    try:
        with open(map_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"\s*ROMX:\s*(\d+)\s*bytes used\s*/\s*(\d+)\s*free in (\d+)\s*banks", line)
                if m:
                    return int(m.group(1)), int(m.group(3))
    except OSError:
        pass
    return None, None


def _glyph_count() -> int:
    """当前字库字数：gfx/font/zh.*.1bpp 总字节 / 每字 32 字节。"""
    gdir = repo_path("gfx", "font")
    total = 0
    if os.path.isdir(gdir):
        for name in os.listdir(gdir):
            if re.fullmatch(r"zh\.\d+\.1bpp", name):
                total += os.path.getsize(os.path.join(gdir, name))
    return total // GLYPH_SIZE if GLYPH_SIZE else 0


def _heaps_fit(points):
    """Heaps 定律 N = K * x^beta 的最小二乘拟合（双对数线性）。"""
    import math

    xs, ys = [], []
    for x, n in points:
        if x > 0 and n > 0:
            xs.append(math.log(x))
            ys.append(math.log(n))
    if len(xs) < 2:
        return None, None
    m = len(xs)
    mx = sum(xs) / m
    my = sum(ys) / m
    sxx = sum((v - mx) ** 2 for v in xs)
    if sxx == 0:
        return None, None
    beta = sum((a - mx) * (b - my) for a, b in zip(xs, ys)) / sxx
    return math.exp(my - beta * mx), beta


def _growth_segments(curve, step: int = 1000):
    """把 (累计汉字数, 字数) 曲线切成等长段，返回每段的 [起点, 新增字数]。

    汉字是封闭字符集，字数增长应当逐级放缓（饱和）；若这里显示的是匀速增长，
    说明专有名词（宝可梦名/地名/技能名）在不断灌入新字，饱和点会更晚到来。
    """
    if not curve:
        return []
    segs = []
    last_x, last_n = 0, 0
    for x, n in curve:
        while x - last_x >= step:
            nxt = last_x + step
            segs.append([nxt, n - last_n])
            last_x, last_n = nxt, n
    if curve[-1][0] - last_x > step / 2:
        segs.append([curve[-1][0], curve[-1][1] - last_n])
    return segs


def cmd_gate(args) -> int:
    """构建门禁：把「空间不够」从链接期的 unknown ROM bank $80 提前成一句人话。

    --min-free 0（默认）只报警不失败；设成正数（如 16384）即硬门禁。
    超过可用 bank 数则**总是**失败——那是真错误，不是余量问题。
    """
    used, banks = _romx_used(repo_path(*args.map.split("/")))
    if used is None:
        print(f"gate: 读不到 {args.map} 的 ROMX SUMMARY，跳过", file=sys.stderr)
        return 0
    cap = args.banks * 0x4000
    free = cap - used
    if banks > args.banks:
        print(f"gate: 失败 —— ROMX 用了 {banks} 个 bank，超过可用 {args.banks} 个"
              f"（{used}/{cap} 字节）。上限来自 bankends 的 BANKS；"
              f"若要扩容，Bankswitch 只写 [rROMB] 低 8 位，故最大 $ff。", file=sys.stderr)
        return 1
    if free < args.min_free:
        print(f"gate: 失败 —— ROMX 仅剩 {free} 字节，低于阈值 {args.min_free}"
              f"（已用 {used}/{cap}）。跑 `zh.py capacity` 看字库与文本的账。", file=sys.stderr)
        return 1
    tag = "通过" if free >= args.min_free else "警告"
    # 与 tools/bankends 的 -q 输出同形：中文构建用它替代编不动的 bankends.exe
    print(f"Free space: {free}/{cap} ({100.0 * free / cap:.2f}%)")
    print(f"gate: ROMX 余量 {free} 字节 / {cap}（阈值 {args.min_free}）{tag}")
    if free < 16384:
        print(f"gate: 提醒 —— 余量不足 16 KB，下一批译文很可能撑爆（见 zh.py capacity）")
    return 0


def cmd_capacity(args) -> int:
    """回答「全集中文化装不装得下」：实测压缩比 + 字集外推 + 空间预算。

    三条实测输入：
      1) 压缩比 r = 已译条目的中文编码字节 / 英文编码字节（比值越小越省）；
      2) 全量英文文本字节（含 654 张地图、UI 文本、以及尚未接入管线的电话文本）；
      3) 字集增长曲线按 Heaps 定律 N = K·x^β 外推到全量文本。
    空间账：剩余 = 当前剩余 + 未译文本换成中文省下的 - 字库增量（每字 32 字节）。
    """
    ti = _text_ir()
    defines = set(DEFAULT_DEFINES)
    charmap = load_charmap()
    keys = charmap_keys_by_length(charmap)
    gmap = glyph_map()

    def enc(s, gm):
        try:
            return len(encode_string(s, charmap, gm, keys))
        except TranslationError:
            return None

    # 1) 已译条目：分桶压缩比（地图长句与 UI 短句省得多寡不同）+ 字频
    import collections
    import math

    done = {"maps": [0, 0, 0], "ui": [0, 0, 0]}   # tag -> [en_bytes, zh_bytes, 条数]
    curve, curve_h, seen, acc = [], [], set(), 0
    freq = collections.Counter()
    hanzi = acc_hanzi = 0
    for d, tag in ((map_ir_dir(), "maps"), (text_ir_dir(), "ui"), (include_ir_dir(), "ui")):
        for p in sorted(pathlib.Path(d).glob("*.json")):
            ir = json.loads(p.read_text(encoding="utf-8"))
            for b in ir.get("blocks", {}).values():
                if not (b.get("zh") and b.get("en")):
                    continue
                e, z = enc(b["en"], {}), enc(b["zh"], gmap)
                if e is None or z is None:
                    continue
                done[tag][0] += e
                done[tag][1] += z
                done[tag][2] += 1
                freq.update(c for c in b["zh"] if is_cjk(c))
                nh = sum(1 for c in b["zh"] if is_cjk(c))
                hanzi += nh
                acc_hanzi += nh
                acc += len(b["en"])
                seen |= {c for c in b["zh"] if is_cjk(c)}
                curve.append((acc, len(seen)))
                curve_h.append((acc_hanzi, len(seen)))
    if not freq:
        print("错误：还没有任何已译条目，无法估压缩比", file=sys.stderr)
        return 2
    ratio = {t: (v[1] / v[0] if v[0] else 1.0) for t, v in done.items()}
    en_done = sum(v[0] for v in done.values())
    zh_done = sum(v[1] for v in done.values())
    K, beta = _heaps_fit(curve)

    # 2) 全量英文文本
    def scan(paths):
        total = chars = ents = skipped = 0
        for src in paths:
            try:
                entries = ti.extract_entries(src, defines)
            except Exception:
                continue
            for e in entries:
                n = enc(e["en"], {})
                if n is None:
                    skipped += 1
                    continue
                total += n
                chars += len(e["en"])
                ents += 1
        return total, chars, ents, skipped

    maps_paths = sorted(str(p) for p in pathlib.Path(repo_path("maps")).glob("*.asm"))
    ui_paths = list(text_source_paths()) + list(include_source_paths())
    phone_dir = repo_path("data", "phone", "text")
    phone_paths = sorted(str(p) for p in pathlib.Path(phone_dir).glob("*.asm")) \
        if os.path.isdir(phone_dir) else []

    m_b, m_c, m_n, m_s = scan(maps_paths)
    u_b, u_c, u_n, u_s = scan(ui_paths)
    p_b, p_c, p_n, p_s = scan(phone_paths)

    all_bytes = m_b + u_b
    all_chars = m_c + u_c

    # 3) 空间账
    used_zh, banks = _romx_used(repo_path(*args.map.split("/")))
    glyph_now = _glyph_count()
    if used_zh is None:
        print(f"错误：读不到 {args.map} 的 ROMX SUMMARY（先构建一次中文 ROM）", file=sys.stderr)
        return 2
    cap = args.banks * 0x4000
    free_now = cap - used_zh
    # 分桶：地图长句与 UI 短句各用自己的压缩比
    remaining = {"maps": max(m_b - done["maps"][0], 0), "ui": max(u_b - done["ui"][0], 0)}
    remaining_en = sum(remaining.values())
    save = sum(v * (1 - ratio[t]) for t, v in remaining.items())

    # 全量译完后大约多少汉字：总中文字节 / 每汉字平均字节（控制码摊薄在内）
    per_hanzi = zh_done / hanzi if hanzi else 2.0
    zh_total_bytes = m_b * ratio["maps"] + u_b * ratio["ui"]
    hanzi_total = zh_total_bytes / per_hanzi
    n_hat = int(K * all_chars ** beta) if K else 0
    segs = _growth_segments(curve_h)
    # 临界字数：再多一个字就挤爆 ROM（= 现有余量 + 文本节省，除以每字字节）
    n_crit = int((free_now + save) / GLYPH_SIZE + glyph_now)
    n_crit16 = int((free_now + save) / (GLYPH_SIZE // 2) + glyph_now)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("=== 中文化容量预算 ===")
    print(f"构建产物      : {args.map}（ROMX 已用 {used_zh} / {banks} bank）")
    print(f"可用空间      : {cap} 字节（bank $01-${args.banks - 1:02x}），当前剩余 {free_now}")
    print()
    print("--- 文本量 ---")
    print(f"地图脚本      : {len(maps_paths)} 个文件，{m_n} 条，{m_b} 字节（{m_c} 字符）")
    print(f"UI / include  : {len(ui_paths)} 个文件，{u_n} 条，{u_b} 字节（{u_c} 字符）")
    print(f"电话文本(未接入): {len(phone_paths)} 个文件，{p_n} 条，{p_b} 字节 —— 尚未接入翻译管线")
    print(f"合计待译      : {all_bytes} 字节（{all_chars} 字符）")
    print(f"已译          : {en_done} 字节 -> 中文 {zh_done} 字节")
    print()
    print("--- 实测压缩比 ---")
    for t, label in (("maps", "地图长句"), ("ui", "UI 短句")):
        v = done[t]
        if v[0]:
            print(f"{label:<12} : {ratio[t]:.3f}（{v[2]} 条，{v[0]} -> {v[1]} 字节）")
    print(f"已译部分{'省下' if zh_done < en_done else '反而增大'} {abs(zh_done - en_done)} 字节")
    print(f"剩余未译      : {remaining_en} 字节 -> 预计省下 {save:.0f} 字节")
    print()
    print("--- 字集外推 ---")
    print(f"样本          : {len(freq)} 字 / {hanzi} 汉字 / {acc} 英文字符")
    print(f"全量预估      : {hanzi_total:.0f} 汉字（当前样本的 {hanzi_total / hanzi:.1f} 倍）")
    if segs:
        print("分段增长      : 每 1000 汉字新增字数 " +
              " ".join(f"{x // 1000}k:+{d}" for x, d in segs))
    if K:
        print(f"Heaps 幂律    : K={K:.2f} β={beta:.3f} -> {n_hat} 字（无饱和项，作为上界）")
    print(f"临界字数      : {n_crit} 字（32 字节/字）；字模减半后 {n_crit16} 字")
    if n_crit > ZH_MAX_GLYPHS:
        print(f"瓶颈提醒      : 空间能装 {n_crit} 字，但双字节编码只到 {ZH_MAX_GLYPHS} 字"
              f"——真正的天花板是字形编码，不是 ROM")
    print()
    print("--- 敏感性：最终需要的字数 vs 剩余空间 ---")
    print(f"{'字数':>6} {'字库增量':>10} {'文本节省':>10} {'剩余空间':>10}  判定")
    marks = {n_crit: "  <== 临界字数"}
    if n_hat:
        marks.setdefault(n_hat, "  <== Heaps 上界")
    for n in sorted(set([1200, 1600, 2000, 2400, 2800, 3200, 4000] + list(marks))):
        dg = (n - glyph_now) * GLYPH_SIZE
        free = free_now + save - dg
        verdict = "装得下" if free >= 0 else f"溢出 {-free:.0f} 字节"
        print(f"{n:>6} {dg:>10} {save:>10.0f} {free:>10.0f}  {verdict}{marks.get(n, '')}")
    print()
    for n in (2000, 3000, 4000):
        dg16 = (n - glyph_now) * (GLYPH_SIZE // 2)
        free16 = free_now + save - dg16
        print(f"方案 A（字模 32 -> 16 字节/字）按 {n} 字算：字库增量 {dg16} 字节"
              f" -> 剩余 {free16:.0f} 字节（{'装得下' if free16 >= 0 else '仍然溢出'}）")
    print()
    print("提示：剩余空间低于 16 KB 就该治理（见 Makefile 里的容量门禁）。")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Polished Crystal 中文化工具链")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gen-font", help="生成 12x12 汉字 1bpp 字形")
    g.add_argument("--font", default=r"C:\Windows\Fonts\simsun.ttc",
                   help="TTF/TTC 路径（建议换成开源点阵字体；当前默认用系统宋体 12px 点阵）")
    g.add_argument("--size", type=int, default=12, help="字号（像素）")
    g.add_argument("--with-glossary", action="store_true",
                   help="把术语表译名里的字也刻进字库（每字 32 字节，ROM 富余时才划算）")
    g.set_defaults(func=cmd_gen_font)

    e = sub.add_parser("encode", help="译文 JSON -> 双字节 .asm")
    e.set_defaults(func=cmd_encode)

    v = sub.add_parser("validate", help="校验控制码/行长/终止符/可编码性")
    v.set_defaults(func=cmd_validate)

    c = sub.add_parser("chars", help="汇总汉字字形集")
    c.add_argument("--out", help="输出字符集文件")
    c.set_defaults(func=cmd_chars)

    tx = sub.add_parser("text-extract", help="data/text/*.asm -> 翻译 IR（保留已有译文）")
    tx.add_argument("--only", nargs="*", help="只处理指定文件名（不含 .asm）")
    tx.add_argument("--define", nargs="*", help=f"构建宏，默认 {DEFAULT_DEFINES}")
    tx.set_defaults(func=cmd_text_extract)

    te = sub.add_parser("text-encode", help="翻译 IR -> data/text/zh/*.asm（未译块保留原文）")
    te.add_argument("--only", nargs="*", help="只处理指定文件名（不含 .asm）")
    te.add_argument("--define", nargs="*", help=f"构建宏，默认 {DEFAULT_DEFINES}")
    te.set_defaults(func=cmd_text_encode)

    tv = sub.add_parser("text-validate", help="校验真实文本译文：占位符/终止符/行长/可编码性")
    tv.add_argument("--only", nargs="*", help="只处理指定文件名（不含 .asm）")
    tv.add_argument("--define", nargs="*", help=f"构建宏，默认 {DEFAULT_DEFINES}")
    tv.set_defaults(func=cmd_text_validate)

    td = sub.add_parser("text-decode", help="反向还原生成的 .asm 字节流，验证渲染结果与字形数据")
    td.add_argument("--only", nargs="*", help="只处理指定文件名（不含 .asm）")
    td.add_argument("--define", nargs="*", help=f"构建宏，默认 {DEFAULT_DEFINES}")
    td.add_argument("--show", action="store_true", help="打印每条译文还原后的文本")
    td.set_defaults(func=cmd_text_decode)

    ms = sub.add_parser("map-scripts", help="生成 data/text/zh_maps/scripts.asm（地图剧本换入清单）")
    ms.set_defaults(func=cmd_map_scripts)

    cp = sub.add_parser("capacity", help="容量预算：全集中文化装不装得下")
    cp.add_argument("--map", default="polishedcrystal-zh-3.2.3.map",
                    help="中文 ROM 的 .map（读 ROMX 已用字节）")
    cp.add_argument("--banks", type=int, default=255,
                    help="可用 ROMX bank 数（默认 255 = 4MB，Bankswitch 只写 [rROMB] 低 8 位）")
    cp.set_defaults(func=cmd_capacity)

    gt = sub.add_parser("gate", help="容量门禁：链接后检查 ROMX 余量")
    gt.add_argument("--map", default="polishedcrystal-zh-3.2.3.map", help="中文 ROM 的 .map")
    gt.add_argument("--banks", type=int, default=255, help="可用 ROMX bank 数（bank $01 起）")
    gt.add_argument("--min-free", type=int, default=0, help="余量下限；超过即失败（0 = 只报警）")
    gt.set_defaults(func=cmd_gate)

    tl = sub.add_parser("title-logo", help="渲染中文标题美术字（144x56 四色阶 PNG）")
    tl.add_argument("--out", default="gfx/title/zh/logo.png", help="输出 PNG 路径")
    tl.add_argument("--font", default=r"C:\Windows\Fonts\simhei.ttf",
                    help="标题用粗黑体 TTF；商用发布请换成可再分发的开源字体")
    tl.add_argument("--line1", default="宝可梦", help="第一行文字")
    tl.add_argument("--size1", type=int, default=28, help="第一行字号")
    tl.add_argument("--y1", type=int, default=15, help="第一行纵向中心")
    tl.add_argument("--line2", default="水晶版", help="第二行文字（留空则只画一行）")
    tl.add_argument("--size2", type=int, default=21, help="第二行字号")
    tl.add_argument("--y2", type=int, default=42, help="第二行纵向中心")
    tl.add_argument("--outline", type=int, default=1, help="黑色描边宽度（像素）")
    tl.set_defaults(func=cmd_title_logo)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
