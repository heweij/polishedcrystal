"""诊断：选项菜单滚动后值列出现多余字符（截图显示 ":S10"）。

推进 50 次 tap a 复现 probe-05 的画面，然后：
  1. dump 整屏 tilemap（tile 号 + 该格是否命中汉字槽位）；
  2. dump 引擎位图（used/pinned/live/ours）与槽位表；
  3. 找出 ":S10" 的 'S' 到底是哪个 tile、来自哪个字形。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pyboy import PyBoy

from verify_engine import EngineView, Report, Rom, parse_sym, tap, shot

OUT = pathlib.Path("tools/zh/verify_out")
ROM = pathlib.Path("polishedcrystal-zh-3.2.3.gbc")

POOL_FIRST = 0x80
POOL_LAST = 0xF1


def main() -> None:
    py = PyBoy(str(ROM), window="null", scale=3)
    py.set_emulation_speed(0)
    rep = Report()
    ev = EngineView(py, Rom(ROM), parse_sym(ROM.with_suffix(".sym")), rep)
    idx = ev.glyph_index_to_char()

    for _ in range(120):
        tap(py, "a", wait=4)
    for _ in range(50):
        tap(py, "a", wait=4)
    shot(py, OUT, "dump-05")
    n = ev.check("dump/50次A")
    print(f"[50次A] 屏幕汉字={''.join(idx.get(g, '#%d' % g) for g in sorted(n))}")

    # 1) 整屏 tilemap
    print("\n=== tilemap（tile 号；$80-$F1 加注槽位/字形）===")
    tm = ev.tilemap()
    slots = ev.slots()
    # slot_by_tile: 瓦片基址 -> (槽位号, 字形号)
    by_tile = {}
    for i, ent in enumerate(slots):
        if ent and ent[1] != 0xFFFF:
            by_tile.setdefault(ent[1], []).append(i)
    for row in range(18):
        line = []
        for col in range(20):
            tile, _attr = tm[row * 20 + col]
            note = ""
            if POOL_FIRST <= tile <= POOL_LAST:
                owners = by_tile.get(tile)
                if owners:
                    g = slots[owners[0]][0]
                    note = f"[槽{owners[0]}字形{idx.get(g, g)}]"
                else:
                    note = "[?]"
            line.append(f"${tile:02x}{note}")
        print(f"  行{row:2d}: " + " ".join(line))

    # 2) 引擎位图
    print("\n=== 位图 ===")
    for name in ("used", "pinned", "live", "ours"):
        bits = ev.bitmap(f"wZhTile{name[0].upper() + name[1:]}" if name != "ours" else "wZhTileOurs")
        ones = [i + POOL_FIRST for i, b in enumerate(bits) if b]
        print(f"  {name:6s}: {len(ones)} 格 {['$%02x' % t for t in ones]}")

    # 3) 槽位表
    print("\n=== 槽位表 ===")
    for i, ent in enumerate(slots):
        if ent and ent[0] != 0xFFFF:
            g, base = ent
            print(f"  槽{i:2d}: 字形{g}({idx.get(g, '?')}) 基址=${base:02x}")

    rep.dump("dump")
    py.stop()


if __name__ == "__main__":
    main()
