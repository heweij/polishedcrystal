#!/usr/bin/env python3
"""用 PyBoy 在本地验证 polishedcrystal-zh-demo ROM 的汉字显示效果。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROM = ROOT / "polishedcrystal-zh-demo-3.2.3.gbc"

if not ROM.exists():
    print(f"ROM 不存在: {ROM}")
    sys.exit(1)

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
from pyboy import PyBoy

pyboy = PyBoy(str(ROM), window="null", scale=2)
pyboy.set_emulation_speed(0)

# 跳过 boot 阶段
for _ in range(300):
    pyboy.tick()

screen = pyboy.screen
shot = ROOT / "polishedcrystal-zh-demo-shot.png"
screen.image.save(str(shot))
print(f"截图已保存: {shot}")

pyboy.stop()
