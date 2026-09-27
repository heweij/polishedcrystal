#!/usr/bin/env python3
"""跟踪槽位分配的每一步，定位 文/演 为何返回 carry。"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from pyboy import PyBoy

ROM = Path(r"E:\workplace\polishedcrystal\polishedcrystal-zh-demo-3.2.3.gbc")
pyboy = PyBoy(str(ROM), window="null", scale=2)
pyboy.set_emulation_speed(0)
rf = pyboy.register_file

log: list[str] = []
started = {"v": False}


def mk(name):
    def hook(_ctx):
        if len(log) < 300:
            f = rf.F
            de = (rf.D << 8) | rf.E
            log.append("%-34s a=%02x hl=%04x de=%04x F=%02x" % (name, rf.A, rf.HL, de, f))
    return hook


B = 0x45
for name, addr in [
    ("PlaceNextCharCJK", 0x67e8),
    ("GetOrLoadSlot", 0x6720),
    ("  .scan", 0x6735),
    ("  .free", 0x6749),
    ("  .out_of_range", 0x6763),
    ("  .found", 0x6765),
    ("UploadGlyphToSlot", 0x6768),
    ("  .upd.out_of_range", 0x67c4),
    ("DrawGlyph", 0x67ca),
]:
    pyboy.hook_register(B, addr, mk(name), None)

for _ in range(400):
    pyboy.tick()

print("\n".join(log))
pyboy.stop()
