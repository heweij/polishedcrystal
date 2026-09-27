"""诊断：下滚后槽 0 出现"字形 594(亲) 无基址"的持久不一致。

逐帧细粒度观察 wZhGlyphSlots[0..1] 与 wZhSlotTile[0..1] 的变化，
并找出 亲 在 tilemap 里引用的 tile 号。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pyboy import PyBoy

from verify_engine import EngineView, Report, Rom, parse_sym, shot, tap

OUT = pathlib.Path("tools/zh/verify_out")
ROM = pathlib.Path("polishedcrystal-zh-3.2.3.gbc")


def in_menu(ev) -> bool:
    tm = ev.tilemap()
    return tm[0][0] == 0x01 and tm[19][0] == 0x02 and 0x80 <= tm[22][0] <= 0xF1


def main() -> None:
    py = PyBoy(str(ROM), window="null", scale=3)
    py.set_emulation_speed(0)
    ev = EngineView(py, Rom(ROM), parse_sym(ROM.with_suffix(".sym")), Report())
    idx = ev.glyph_index_to_char()

    for _ in range(200):
        tap(py, "a", wait=4)
        if in_menu(ev):
            break
    # 光标先推到第 5 项，再长按 DOWN 触发滚动
    for _ in range(4):
        tap(py, "down", hold=8, wait=8)

    prev = None
    for i in range(1, 7):
        print(f"\n--- 长按DOWN #{i} ---")
        # 按住期间逐帧观察
        py.button_press("down")
        for f in range(30):
            py.tick()
            raw = ev.wram("wZhGlyphSlots", 4)
            tiles = ev.wram("wZhSlotTile", 2)
            state = (raw[0] | raw[1] << 8, tiles[0], raw[2] | raw[3] << 8, tiles[1])
            if state != prev:
                s0 = "无" if state[0] == 0xFFFF else f"字形{state[0]}({idx.get(state[0],'?')})基址${state[1]:02x}"
                s1 = "无" if state[2] == 0xFFFF else f"字形{state[2]}({idx.get(state[2],'?')})基址${state[3]:02x}"
                print(f"  帧{f:2d}: 槽0={s0}  槽1={s1}")
                prev = state
        py.button_release("down")
        for _ in range(10):
            py.tick()

        # 亲 在 tilemap 哪里、引用什么 tile
        slots = ev.slots()
        by_glyph = {}
        for n, s in enumerate(slots):
            if s:
                by_glyph.setdefault(s[0], []).append((n, s[1]))
        tm = ev.tilemap()
        for r in range(12):
            for c in range(20):
                t = tm[r * 20 + c][0]
                if 0x80 <= t <= 0xF1:
                    for n, s in enumerate(slots):
                        if s and s[1] <= t < s[1] + 4:
                            g = s[0]
                            if idx.get(g) == "亲":
                                print(f"  亲的格子: ({r},{c}) tile=${t:02x} -> 槽{n} 基址${s[1]:02x}")
        bad = [n for n, s in enumerate(slots) if s and s[1] == 0xFF]
        if bad:
            print(f"  !! 无基址槽位: {bad}")
    shot(py, OUT, "diag-slot0")
    py.stop()


if __name__ == "__main__":
    main()
