"""hook 追踪：捕获"字形条目在、基址 $ff"出现瞬间的完整事件序列。"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from pyboy import PyBoy

from verify_engine import EngineView, Report, Rom, parse_sym, tap

ROM = pathlib.Path("polishedcrystal-zh-3.2.3.gbc")
BANK = 0x3E
ADDR = {
    "FreeSlot": 0x60F8,
    "Rebuild": 0x607B,
    "ReleaseHidden": 0x621A,
    "ResetSlots": 0x61FF,
    "CheckResync": 0x612B,
    "have_slot": 0x62DC,
    "alloc成功": 0x62EC,
    "写基址": 0x62F3,
    "release": 0x631B,
    "found": 0x6323,
}
log: list[str] = []
hit = {"done": False}


def main() -> None:
    py = PyBoy(str(ROM), window="null", scale=3)
    py.set_emulation_speed(0)
    ev = EngineView(py, Rom(ROM), parse_sym(ROM.with_suffix(".sym")), Report())
    idx = ev.glyph_index_to_char()

    def snap(tag):
        if hit["done"]:
            return
        a = py.register_file.A
        raw = ev.wram("wZhGlyphSlots", 48)
        tiles = ev.wram("wZhSlotTile", 24)
        parts = []
        for n in range(24):
            e = raw[n * 2] | raw[n * 2 + 1] << 8
            if e != 0xFFFF:
                parts.append(f"槽{n}={idx.get(e, '?')}({e}),${tiles[n]:02x}")
        log.append(f"{tag}(a=${a:02x}): " + " ".join(parts))

    for name, addr in ADDR.items():
        py.hook_register(BANK, addr, (lambda n: lambda *a: snap(n))(name), None)

    def corrupted() -> list[int]:
        raw = ev.wram("wZhGlyphSlots", 48)
        tiles = ev.wram("wZhSlotTile", 24)
        out = []
        for n in range(24):
            e = raw[n * 2] | raw[n * 2 + 1] << 8
            if e != 0xFFFF and tiles[n] == 0xFF:
                out.append(n)
        return out

    def in_menu():
        tm = ev.tilemap()
        return tm[0][0] == 0x01 and tm[19][0] == 0x02 and 0x80 <= tm[22][0] <= 0xF1

    for _ in range(200):
        tap(py, "a", wait=4)
        if in_menu():
            break
    print("进入菜单，开始 6 次长按 DOWN")

    for i in range(1, 7):
        py.button_press("down")
        for f in range(30):
            py.tick()
            bad = corrupted()
            if bad and not hit["done"]:
                hit["done"] = True
                print(f"\n!!! DOWN#{i} 帧{f}: 出现无基址槽位 {bad}")
                for line in log[-60:]:
                    print("  ", line)
                log.clear()
                hit["done"] = False  # 继续观察
        py.button_release("down")
        for _ in range(10):
            py.tick()
        bad = corrupted()
        print(f"DOWN#{i} 结束: 无基址槽位 {bad}")
    py.stop()


if __name__ == "__main__":
    main()
