"""ui.py — 320×170 的两块界面：时钟页与卦象页。

配色：墨底(#0D1017) + 米白(#EDE6D6) + 朱砂(#D1493F) + 暗金(#C9A227)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw

import cz
from cz import H, W, cjk, mono

BG = (13, 16, 23)
PANEL = (22, 27, 36)
INK = (237, 230, 214)
DIM = (138, 133, 120)
FAINT = (74, 78, 88)
ZHU = (209, 73, 63)
GOLD = (201, 162, 39)
TEAL = (91, 140, 133)

# 官方《推荐按键设计》：屏幕正对下方数字键 4~8，
# 五个按键在屏幕上的横坐标为 49 / 105 / 161 / 217 / 273。
# 于是「6」键 = x161（4→49, 5→105, 6→161, 7→217, 8→273）。
KEY_X = {4: 49, 5: 105, 6: 161, 7: 217, 8: 273}
BTN_CX = KEY_X[6]
DEFAULT_BTN_X = BTN_CX
BTN_X_MIN, BTN_X_MAX = 40, 280
BTN_W = 96
WEEKDAY = "一二三四五六日"


class Ui:
    def __init__(self) -> None:
        self.f_title = cjk(14, bold=True)
        self.f_gua = cjk(20, bold=True)
        self.f_sub = cjk(12, bold=True)
        self.f_body = cjk(10)
        self.f_small = cjk(9)
        self.f_tiny = cjk(8)
        self.f_clock = mono(40)
        self.f_btn = cjk(15, bold=True)
        self.btn_x = DEFAULT_BTN_X

    # ------------------------------------------------------------ 时钟页
    def clock(self, clock_now: float, moment, geo, net_state: dict,
              pressed: bool = False, status: str = "", tz: float = 8.0,
              calibrating: bool = False, key_flash: bool = False) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        dt = datetime.fromtimestamp(clock_now, timezone(timedelta(hours=tz)))
        # 顶栏
        d.rectangle([0, 0, W, 16], fill=PANEL)
        cz.text(d, (8, 0), "起卦", self.f_title, ZHU)
        # 校时状态点
        src = net_state.get("source", "RTC")
        dot = TEAL if src.startswith("NTP") else (GOLD if src == "HTTP" else FAINT)
        d.ellipse([W - 88, 6, W - 82, 12], fill=dot)
        cz.text_right(d, W - 8, 1, f"{dt.year}-{dt.month:02d}-{dt.day:02d} 周{WEEKDAY[dt.weekday()]}",
                      self.f_small, INK)

        # 大时钟
        hms = f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
        cz.text_center(d, W // 2, 16, hms, self.f_clock, INK)

        # 干支
        if moment is not None:
            gz = (f"{moment.year_gz}年 {moment.month_gz}月 "
                  f"{moment.day_gz}日 {moment.hour_gz}时")
            cz.text_center(d, W // 2, 62, gz, self.f_sub, GOLD)

            lunar = moment.lunar.text
            solar = moment.dt_solar.strftime("%H:%M")
            day = "昼" if moment.is_day else "夜"
            cz.text_center(d, W // 2, 79, f"农历{lunar} · 真太阳 {solar} · {day}",
                           self.f_body, INK)
        else:
            cz.text_center(d, W // 2, 70, "历法计算中…", self.f_sub, DIM)

        # 定位与校时
        if geo is not None:
            info = f"{geo.city + ' ' if geo.city else ''}{geo.lat:.2f},{geo.lon:.2f}"
        else:
            info = "定位中…" if net_state.get("geo_busy") else "无定位·按标准经线"
        if net_state.get("refreshing"):
            src_txt = "校时中…"
        else:
            src_txt = "本机RTC" if src == "RTC" else src
        cz.text_center(d, W // 2, 93, f"{info} · {src_txt}", self.f_tiny, DIM)

        # 起卦按钮：中心正对「6」键（x=161）
        cx = self.btn_x
        self._button(d, cx, 110, BTN_W, 30, pressed)
        hint = (f"A/D 微调 {cx}px　6 起卦" if calibrating else
                (status or "6 起卦　8 校时　H 关于　ESC 退出"))
        cz.text_center(d, W // 2, 150, hint, self.f_tiny, GOLD if calibrating else FAINT)
        # 按键回执：任何按键都会在左下角闪一下绿灯，便于判断"设备到底有没有收到键"
        if key_flash:
            d.ellipse([4, 160, 10, 166], fill=TEAL)
        return img

    def _button(self, d: ImageDraw.ImageDraw, cx: int, y: int, w: int, h: int, pressed: bool) -> None:
        x0, x1 = cx - w // 2, cx + w // 2
        d.rounded_rectangle([x0, y, x1, y + h], radius=7,
                            fill=ZHU if pressed else (42, 28, 30),
                            outline=(255, 220, 190) if pressed else GOLD, width=1)
        cz.text_center(d, cx, y + 6, "起卦", self.f_btn, INK if pressed else ZHU)

    # ------------------------------------------------------------ 卦象页
    def hexagram(self, cast, moment, page: int = 0, total_pages: int = 1) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        d.rectangle([0, 0, W, 16], fill=PANEL)
        cz.text(d, (8, 0), cast.name, self.f_title, ZHU)
        cz.text_right(d, W - 8, 1, f"{cast.moving}爻动 · {cast.verdict}", self.f_small, GOLD)

        # 六爻图（自上而下为六爻到初爻）
        draw_hexagram(d, 20, 24, cast.lines, cast.moving)

        # 右侧信息
        x = 88
        cz.text(d, (x, 22), f"上{cast.upper.name}{cast.upper.xiang}　下{cast.lower.name}{cast.lower.xiang}",
                self.f_body, INK)
        cz.text(d, (x, 38), f"体 {cast.body.name}{cast.body.wuxing}　用 {cast.use.name}{cast.use.wuxing}",
                self.f_body, INK)
        cz.text(d, (x, 52), f"{cast.rel} · 断为{cast.verdict}", self.f_sub, ZHU)
        cz.text(d, (x, 71), f"互 {cast.hu_name}", self.f_small, DIM)
        cz.text(d, (x, 83), f"变 {cast.bian_name}", self.f_small, DIM)

        # 底部凭据与一句白话
        if moment is not None:
            cred = (f"真太阳 {moment.dt_solar:%H:%M} {'昼' if moment.is_day else '夜'}"
                    f" · 日躔{cast.direction} · 动爻{cast.moving}")
            cz.text_center(d, W // 2, 100, cred, self.f_tiny, DIM)
        cz.text_center(d, W // 2, 114, cast.plain, self.f_body, INK)

        cz.text_center(d, W // 2, 152, f"Z/C 翻页 {page + 1}/{total_pages}　6 再起　ESC 返回",
                       self.f_tiny, FAINT)
        return img

    # ------------------------------------------------------------ 关于页
    def about(self, version: str = "", source: str = "") -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)

        d.rectangle([0, 0, W, 16], fill=PANEL)
        cz.text(d, (8, 0), "关于", self.f_title, ZHU)
        cz.text_right(d, W - 8, 1, f"v{version}", self.f_small, GOLD)

        cz.text_center(d, W // 2, 24, "起 卦", self.f_gua, INK)
        cz.text_center(d, W // 2, 50, "时空梅花 · 数字时钟 · 农历干支", self.f_small, DIM)

        d.line([(40, 68), (W - 40, 68)], fill=(48, 54, 66), width=1)

        cz.text_center(d, W // 2, 76, "出品　fiddling科技工作室", self.f_sub, INK)
        cz.text_center(d, W // 2, 96, "fiddling.work", self.f_body, TEAL)

        cz.text_center(d, W // 2, 118, "历法自算 · NTP 校时 · IP 定位", self.f_small, DIM)
        cz.text_center(d, W // 2, 132, "占断仅供参玩，不作决策依据", self.f_small, FAINT)

        cz.text_center(d, W // 2, 152, "H / ESC 返回", self.f_tiny, FAINT)
        return img

    # ------------------------------------------------------------ 断语页
    def text_page(self, cast, lines: list, page: int, total: int) -> Image.Image:
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, W, 16], fill=PANEL)
        cz.text(d, (8, 0), f"{cast.name} · {cast.rel} {cast.verdict}", self.f_title, ZHU)
        cz.text_right(d, W - 8, 1, f"{page + 1}/{total}", self.f_small, GOLD)

        y = 22
        for ln, is_start in lines:
            if is_start:
                d.rectangle([3, y + 3, 5, y + 13], fill=ZHU)
            cz.text(d, (10, y), ln, self.f_body, INK)
            y += 16

        cz.text_center(d, W // 2, 152, "Z/C 翻页　ESC 返回", self.f_tiny, FAINT)
        return img


# ---------------------------------------------------------------- 六爻图

LINE_H = 8
LINE_GAP = 4
LINE_W = 52
YIN_GAP = 12


def draw_hexagram(d: ImageDraw.ImageDraw, x0: int, y_top: int, lines: list[int],
                  moving: int = 0) -> None:
    """把六爻画在 (x0, y_top)，屏幕上自上而下为第 6 爻到第 1 爻。"""
    for idx in range(6):
        line_no = 6 - idx                       # 屏幕自上而下：6,5,4,3,2,1
        val = lines[line_no - 1]
        y = y_top + idx * (LINE_H + LINE_GAP)
        is_moving = (line_no == moving)
        color = ZHU if is_moving else INK
        if val:
            d.rounded_rectangle([x0, y, x0 + LINE_W, y + LINE_H], radius=2, fill=color)
        else:
            half = (LINE_W - YIN_GAP) // 2
            d.rounded_rectangle([x0, y, x0 + half, y + LINE_H], radius=2, fill=color)
            d.rounded_rectangle([x0 + half + YIN_GAP, y, x0 + LINE_W, y + LINE_H], radius=2, fill=color)
        if is_moving:
            d.ellipse([x0 - 11, y + 1, x0 - 4, y + 8], outline=ZHU, width=1)
