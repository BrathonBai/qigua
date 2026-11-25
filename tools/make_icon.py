#!/usr/bin/env python3
"""make_icon.py — 生成 256×256 应用图标：墨底 + 朱砂六爻 + 暗金细边。

用法： python tools/make_icon.py [out.png]
"""

from __future__ import annotations

import os
import sys

from PIL import Image, ImageDraw

SIZE = 256
INK = (13, 16, 23)
PANEL = (26, 30, 40)
ZHU = (209, 73, 63)
GOLD = (201, 162, 39)
CREAM = (237, 230, 214)


def build(size: int = SIZE) -> Image.Image:
    s = size
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角底
    r = int(s * 0.22)
    d.rounded_rectangle([0, 0, s - 1, s - 1], radius=r, fill=INK)
    d.rounded_rectangle([int(s * 0.035), int(s * 0.035), s - int(s * 0.035), s - int(s * 0.035)],
                        radius=int(r * 0.85), outline=GOLD, width=max(1, s // 128))

    # 六爻：自上而下 阳、阴、阳、阴、阳、阴
    pattern = [1, 0, 1, 0, 1, 0]
    bar_w = int(s * 0.52)
    bar_h = int(s * 0.075)
    gap = int(s * 0.055)
    total = len(pattern) * bar_h + (len(pattern) - 1) * gap
    y = (s - total) // 2
    x0 = (s - bar_w) // 2
    yin_gap = int(bar_w * 0.22)
    for i, v in enumerate(pattern):
        color = ZHU if i in (1, 4) else CREAM
        if v:
            d.rounded_rectangle([x0, y, x0 + bar_w, y + bar_h], radius=bar_h // 2, fill=color)
        else:
            half = (bar_w - yin_gap) // 2
            d.rounded_rectangle([x0, y, x0 + half, y + bar_h], radius=bar_h // 2, fill=color)
            d.rounded_rectangle([x0 + half + yin_gap, y, x0 + bar_w, y + bar_h],
                                radius=bar_h // 2, fill=color)
        y += bar_h + gap
    return img


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "assets/icon.png"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img = build()
    img.save(out)
    # launcher 按尺寸取图：<name>.png / <name>_100.png / <name>_80.png / <name>_256.png
    base = os.path.splitext(out)[0]
    for suffix, size in (("_100", 100), ("_80", 80), ("_256", 256)):
        img.resize((size, size), Image.LANCZOS).save(f"{base}{suffix}.png")
    print("wrote", out, "+ _100/_80/_256")
    return 0


if __name__ == "__main__":
    sys.exit(main())
