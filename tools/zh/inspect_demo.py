#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from pyboy import PyBoy

ROM = Path(r"E:\workplace\polishedcrystal\polishedcrystal-zh-demo-3.2.3.gbc")
pyboy = PyBoy(str(ROM), window="null", scale=2)
pyboy.set_emulation_speed(0)
for _ in range(300):
    pyboy.tick()

# wTilemap = $c440
print("=== wTilemap ($c440) ===")
for y in range(18):
    row = [f"{pyboy.memory[0xc440 + y * 20 + x]:02x}" for x in range(20)]
    print(f"row{y}: {' '.join(row)}")

print("\n=== wTilemap + 18*20 ($c5a8) = attr? ===")
for y in range(18):
    row = [f"{pyboy.memory[0xc5a8 + y * 20 + x]:02x}" for x in range(20)]
    print(f"row{y}: {' '.join(row)}")

pyboy.stop()
