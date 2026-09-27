"""诊断：值列 "510" 的 '5'（tile $e5）显示成 S 状。

逐次 A 按键推进，每次之后检查：
  * tilemap 行7 col14 是否为 $e5（ASCII '5' 的 tile 号）；
  * VRAM tile $e5 是否仍等于当前字体的 '5' 字形；
  * 槽位表里是否出现过覆盖 $e5 的槽位（基址 $e2-$e5）。
找出第一处 "VRAM 内容被改掉" 的时刻，并打印前后槽位表。
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pyboy import PyBoy

from verify_engine import EngineView, Report, Rom, parse_sym, shot, tap

OUT = pathlib.Path("tools/zh/verify_out")
ROM = pathlib.Path("polishedcrystal-zh-3.2.3.gbc")

TARGET = 0xE5  # ASCII '5'
ROW, COL = 7, 14


def main() -> None:
    py = PyBoy(str(ROM), window="null", scale=3)
    py.set_emulation_speed(0)
    rep = Report()
    ev = EngineView(py, Rom(ROM), parse_sym(ROM.with_suffix(".sym")), rep)
    fonts = ev.fonts()
    expect5 = fonts["FontNormal"][(TARGET - 0x80) * 8:(TARGET - 0x80) * 8 + 8]

    def vram_tile(tile: int) -> bytes:
        py.memory[0xFF4F] = 0
        base = 0x8000 + tile * 16
        raw = ev.mem(base, 16)
        # 透明展开的逆：取每行的低位字节（s,s 模式下偶数字节即 1bpp）
        return bytes(raw[i * 2] for i in range(8))

    def slot_bases() -> list[int]:
        return [s[1] for s in ev.slots() if s]

    prev_bases: list[int] = []
    for step in range(1, 171):
        tap(py, "a", wait=4)
        tm = ev.tilemap()
        tile_here = tm[ROW * 20 + COL][0]
        ok5 = vram_tile(TARGET) == expect5
        bases = slot_bases()
        covering = [b for b in bases if b <= TARGET < b + 4]
        new_slot = [b for b in bases if b not in prev_bases]
        gone_slot = [b for b in prev_bases if b not in bases]
        # 菜单出现前（字库未加载 / 不在选项菜单）没有比较意义
        in_menu = 0x80 <= tm[1 * 20 + 2][0] <= 0xF1
        flag = ""
        if in_menu and tile_here == TARGET and not ok5:
            flag += " <== VRAM$e5被改!"
        if covering:
            flag += f" [槽覆盖$e5:基址${covering[0]:02x}]"
        if new_slot or gone_slot:
            flag += f" (新槽{['$%02x' % b for b in new_slot]} 释放{['$%02x' % b for b in gone_slot]})"
        if in_menu and (flag or tile_here == TARGET):
            print(f"step {step:3d}: tilemap(7,14)=${tile_here:02x} "
                  f"VRAM$e5={'OK' if ok5 else 'BAD'}{flag}")
        if in_menu and tile_here == TARGET and not ok5:
            print("\n=== 第一次发现 $e5 被改，槽位表 ===")
            for i, s in enumerate(ev.slots()):
                if s:
                    print(f"  槽{i:2d}: 字形{s[0]} 基址=${s[1]:02x}")
            shot(py, OUT, "diag-e5")
            print("\n实际 VRAM $e5 (1bpp):", vram_tile(TARGET).hex())
            print("期望 '5' 字形      :", expect5.hex())
            break
        prev_bases = bases
    else:
        print("170 步内未发现 $e5 被改")
    py.stop()


if __name__ == "__main__":
    main()
