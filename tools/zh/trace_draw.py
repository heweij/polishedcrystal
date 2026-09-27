#!/usr/bin/env python3
"""Hook Zh_PlaceNextCharCJK / Zh_DrawGlyphToTilemap 记录每次调用的 de/hl。"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from pyboy import PyBoy

ROM = Path(r"E:\workplace\polishedcrystal\polishedcrystal-zh-demo-3.2.3.gbc")
pyboy = PyBoy(str(ROM), window="null", scale=2)
pyboy.set_emulation_speed(0)

rf = pyboy.register_file
print("register_file attrs:", [a for a in dir(rf) if not a.startswith("_")])

log: list[str] = []


def at_place(_ctx):
    if len(log) < 200:
        de = (rf.D << 8) | rf.E
        log.append("PlaceNextChar de=%04x hl=%04x a=%02x" % (de, rf.HL, rf.A))


def at_draw(_ctx):
    if len(log) < 200:
        log.append("  DrawGlyph   a=%02x hl=%04x" % (rf.A, rf.HL))


def at_blank(_ctx):
    if len(log) < 200:
        de = (rf.D << 8) | rf.E
        log.append("  BLANK!      de=%04x hl=%04x a=%02x" % (de, rf.HL, rf.A))


pyboy.hook_register(0x45, 0x67e8, at_place, None)
pyboy.hook_register(0x45, 0x67ca, at_draw, None)
pyboy.hook_register(0x45, 0x680c, at_blank, None)

for _ in range(400):
    pyboy.tick()

print("\n".join(log))
pyboy.stop()
