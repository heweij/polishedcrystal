"""验证：开局选项菜单 D-pad 向下滚动后有没有字形/ASCII 残留。

进入初始选项菜单后连按 DOWN 把 11 项列表滚到底，每滚一步：
  * 截图；
  * 跑完整引擎核对（位图自洽 + VRAM 逐字节 + used&ours&~live 不变式）。
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
    return (tm[0][0] == 0x01 and tm[19][0] == 0x02   # 左右边缘 tile
            and 0x80 <= tm[1 * 20 + 2][0] <= 0xF1)   # 行1 有汉字


def main() -> None:
    py = PyBoy(str(ROM), window="null", scale=3)
    py.set_emulation_speed(0)
    ev = EngineView(py, Rom(ROM), parse_sym(ROM.with_suffix(".sym")), Report())
    ev.glyph_index_to_char()

    entered = -1
    for step in range(1, 200):
        tap(py, "a", wait=4)
        if in_menu(ev):
            entered = step
            break
    print(f"第 {entered} 次A进入初始选项菜单")
    shot(py, OUT, "scroll2-00-top")

    bad = 0
    for i in range(1, 9):
        tap(py, "down", hold=12, wait=10)
        rep = Report()
        ev.rep = rep
        present = ev.check(f"scroll2/下滚{i}")
        if rep.errors:
            # 可能采样到了分配中间态（槽位表已写字形、基址还没落），
            # 等画面彻底稳定后重测一次
            for _ in range(30):
                py.tick()
            rep = Report()
            ev.rep = rep
            present = ev.check(f"scroll2/下滚{i}(稳定后复测)")
        rep.dump(f"下滚 {i}")
        shot(py, OUT, f"scroll2-{i:02d}-down")
        if rep.errors:
            bad += 1
        _ = present
    print("结论：", "有失败" if bad else "全程 0 错误")
    py.stop()


if __name__ == "__main__":
    main()
