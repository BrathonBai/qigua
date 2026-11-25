#!/usr/bin/env python3
"""make_store_assets.py — 生成商店要用的图标与截图。

* 图标：正方形 PNG 256×256，**不带圆角、不带描边**（商店规范明确不建议），
  商店会自动圆角/缩放。
* 截图：正好 320×170 的 PNG，取四个真实界面状态（时钟 / 卦象 / 断语 / 关于）。

用法： python tools/make_store_assets.py
输出： assets/images/icon.png、assets/images/screenshot0{1..4}.png
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(APP, "src"))
sys.path.insert(0, HERE)

from PIL import Image, ImageDraw                          # noqa: E402

import main as app_main                                   # noqa: E402
import net                                                # noqa: E402
import ui                                                 # noqa: E402

OUT = os.path.join(APP, "assets", "images")
INK = (13, 16, 23)
CREAM = (237, 230, 214)
ZHU = (209, 73, 63)
GOLD = (201, 162, 39)


def flat_icon(size: int = 256) -> Image.Image:
    """商店图标：满幅墨底 + 六爻，无圆角无边框。"""
    s = size
    img = Image.new("RGB", (s, s), INK)
    d = ImageDraw.Draw(img)
    pattern = [1, 0, 1, 0, 1, 0]
    bar_w = int(s * 0.56)
    bar_h = max(2, int(s * 0.078))
    gap = max(2, int(s * 0.052))
    total = len(pattern) * bar_h + (len(pattern) - 1) * gap
    y = (s - total) // 2
    x0 = (s - bar_w) // 2
    yin_gap = int(bar_w * 0.22)
    for i, v in enumerate(pattern):
        color = ZHU if i in (1, 4) else CREAM
        if v:
            d.rectangle([x0, y, x0 + bar_w, y + bar_h], fill=color)
        else:
            half = (bar_w - yin_gap) // 2
            d.rectangle([x0, y, x0 + half, y + bar_h], fill=color)
            d.rectangle([x0 + half + yin_gap, y, x0 + bar_w, y + bar_h], fill=color)
        y += bar_h + gap
    return img


def main() -> int:
    os.makedirs(OUT, exist_ok=True)

    icon = flat_icon(256)
    icon.save(os.path.join(OUT, "icon.png"))
    print("icon.png", icon.size)

    # 固定一个展示用的时刻：2026-11-26 05:43 CST，沈阳
    ts = datetime(2026, 11, 25, 21, 43, 0, tzinfo=timezone.utc).timestamp()
    app = app_main.App(lat=41.8357, lon=123.429, tz=8.0, offline=True, city="沈阳市")
    app.net_state = {"source": "NTP:aliyun", "geo_busy": False}
    app.clock.set(ts, "NTP:aliyun", 0.04)

    shots = []
    shots.append(("screenshot01", app.render(ts)))            # 时钟页
    app.cast_now(ts)
    app.page = 0
    shots.append(("screenshot02", app.render(ts)))            # 卦象页
    app.page = 1
    shots.append(("screenshot03", app.render(ts)))            # 断语页
    app.mode = "about"
    shots.append(("screenshot04", app.render(ts)))            # 关于页

    for name, img in shots:
        assert img.size == (320, 170), img.size
        img.convert("RGB").save(os.path.join(OUT, f"{name}.png"))
        print(name, img.size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
