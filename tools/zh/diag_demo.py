#!/usr/bin/env python3
"""诊断 demo ROM：跑若干帧后打印 CPU 状态、关键 WRAM 与水印。"""
from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from pyboy import PyBoy

ROM = Path(r"E:\workplace\polishedcrystal\polishedcrystal-zh-demo-3.2.3.gbc")

pyboy = PyBoy(str(ROM), window="null", scale=2)
pyboy.set_emulation_speed(0)
for _ in range(400):
    pyboy.tick()

print("PC =", hex(pyboy.register_file.PC if hasattr(pyboy, "register_file") else 0))
try:
    rf = pyboy.register_file
    print("AF=%04x BC=%04x DE=%04x HL=%04x SP=%04x" % (rf.A << 8 | rf.F, rf.B << 8 | rf.C, rf.D << 8 | rf.E, rf.H << 8 | rf.L, rf.SP))
except Exception as exc:  # noqa: BLE001
    print("register_file unavailable:", exc)

print("hROMBank =", hex(pyboy.memory[0xffb8]))
print("wZhGlyphSlots @ ...")
# find wZhGlyphSlots from sym
print("LCDC =", hex(pyboy.memory[0xff40]))
print("SVBK =", hex(pyboy.memory[0xff70]))
print("hBGMapMode =", hex(pyboy.memory[0xffe0]) if True else "?")

# wTilemap $c440
print("=== wTilemap ===")
for y in range(18):
    row = [f"{pyboy.memory[0xc440 + y * 20 + x]:02x}" for x in range(20)]
    print(f"r{y:02d}: {' '.join(row)}")

# 非白像素统计：用屏幕缓冲
screen = pyboy.screen.ndarray  # HxWx4
import numpy as np  # noqa: E402

nz = int((screen[:, :, :3] < 200).any(axis=2).sum())
print("non-white pixels:", nz)

pyboy.stop()
