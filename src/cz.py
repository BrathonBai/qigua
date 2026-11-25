"""cz.py — CardputerZero 的屏与键盘（320×170 / RGB565 / evdev），无界面框架依赖。

设计要点：
  * 屏幕不是直驱 SPI，而是 DRM 的 fbdev 兼容层（/dev/fb0）。写完一帧后
    补一次 FBIOPUT_VSCREENINFO + FBIOPAN_DISPLAY，做法与官方 LVGL 模板一致，
    否则面板可能停在旧帧。
  * 键盘是 evdev 设备 cardputer-zero-internal；尽量 EVIOCGRAB 独占，
    抢不到也要能读（从 launcher 启动时它未必一直持有）。
  * 字体优先用系统 Noto CJK（设备上必定存在），可用 CZ_FONT 覆盖。
"""

from __future__ import annotations

import os
import select
import struct
import time

try:                                    # 仅 Linux 有；Windows 上只跑预览
    import fcntl
except ImportError:                     # pragma: no cover
    fcntl = None                        # type: ignore[assignment]

from PIL import Image, ImageDraw, ImageFont

W, H = 320, 170
STRIDE = W * 2

FBIOGET_VSCREENINFO = 0x4600
FBIOPUT_VSCREENINFO = 0x4601
FBIOPAN_DISPLAY = 0x460A
FB_VAR_SIZE = 160

_EV_KEY = 1
_EVENT_FMT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FMT)
_EVIOCGRAB = 0x40044590

KEY_ESC, KEY_1, KEY_6, KEY_0 = 1, 2, 7, 11
KEY_BACKSPACE, KEY_TAB, KEY_ENTER, KEY_SPACE = 14, 15, 28, 57
KEY_LEFT, KEY_RIGHT, KEY_UP, KEY_DOWN = 105, 106, 103, 108
KEY_HOME, KEY_END, KEY_PAGEUP, KEY_PAGEDOWN = 102, 107, 104, 109
KEY_LEFTBRACE, KEY_RIGHTBRACE, KEY_SEMICOLON, KEY_APOSTROPHE = 26, 27, 39, 40
KEY_COMMA, KEY_DOT, KEY_SLASH, KEY_GRAVE = 51, 52, 53, 41
KEY_BACKSLASH, KEY_MINUS, KEY_EQUAL = 43, 12, 13

# Linux 键码是「物理位置顺序」，字母不连续，必须用显式表
_LETTER_ROWS = (
    (16, "qwertyuiop"),          # KEY_Q..KEY_P
    (30, "asdfghjkl"),           # KEY_A..KEY_L
    (44, "zxcvbnm"),             # KEY_Z..KEY_M
)

KEYMAP: dict[int, str] = {
    KEY_ESC: "esc", KEY_TAB: "tab", KEY_ENTER: "enter", KEY_SPACE: " ",
    KEY_BACKSPACE: "backspace", KEY_GRAVE: "`", KEY_MINUS: "-", KEY_EQUAL: "=",
    KEY_LEFTBRACE: "[", KEY_RIGHTBRACE: "]", KEY_BACKSLASH: "\\",
    KEY_SEMICOLON: ";", KEY_APOSTROPHE: "'", KEY_COMMA: ",", KEY_DOT: ".",
    KEY_SLASH: "/",
    KEY_UP: "up", KEY_DOWN: "down", KEY_LEFT: "left", KEY_RIGHT: "right",
    KEY_HOME: "home", KEY_END: "end", KEY_PAGEUP: "pageup", KEY_PAGEDOWN: "pagedown",
}
for _i, _digits in enumerate("1234567890"):
    KEYMAP[2 + _i] = _digits
for _base, _letters in _LETTER_ROWS:
    for _i, _ch in enumerate(_letters):
        KEYMAP[_base + _i] = _ch


# ---------------------------------------------------------------- 屏幕

def find_fb() -> str | None:
    """挑出 320×170、16bpp 的那个 framebuffer。"""
    for idx in range(4):
        base = f"/sys/class/graphics/fb{idx}"
        try:
            with open(base + "/virtual_size") as f:
                size = f.read().strip()
            with open(base + "/bits_per_pixel") as f:
                bpp = int(f.read().strip())
        except OSError:
            continue
        if size == f"{W},{H}" and bpp == 16:
            return f"/dev/fb{idx}"
    return None


class Screen:
    """往 framebuffer 写 RGB565 图像。"""

    def __init__(self, path: str | None = None, pan: bool = True):
        self.path = path or os.environ.get("CZ_FB") or find_fb() or "/dev/fb0"
        self.pan = pan
        self._var: bytes | None = None
        try:
            self.fb = open(self.path, "r+b", buffering=0)
        except OSError:
            self.fb = None
            return
        try:
            buf = bytearray(FB_VAR_SIZE)
            fcntl.ioctl(self.fb.fileno(), FBIOGET_VSCREENINFO, buf)
            self._var = bytes(buf)
            # 触发一次 set_par（官方 LVGL 的 fbdev 驱动也是这么做的）
            fcntl.ioctl(self.fb.fileno(), FBIOPUT_VSCREENINFO, bytearray(self._var))
        except (OSError, AttributeError):
            self._var = None

    @property
    def ok(self) -> bool:
        return self.fb is not None

    def show(self, img: Image.Image) -> None:
        if not self.fb:
            return
        data = to_rgb565(img)
        try:
            self.fb.seek(0)
            self.fb.write(data)
            if self.pan and self._var:
                fcntl.ioctl(self.fb.fileno(), FBIOPAN_DISPLAY, bytearray(self._var))
        except OSError:
            pass

    def close(self) -> None:
        if self.fb:
            try:
                self.fb.close()
            finally:
                self.fb = None


def _numpy_packer():
    try:
        import numpy as np
    except Exception:                             # noqa: BLE001
        return None

    def pack(img: Image.Image) -> bytes:
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        v = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
        return v.astype("<u2").tobytes()
    return pack


def _pure_packer(img: Image.Image) -> bytes:
    src = img.convert("RGB").tobytes()
    out = bytearray(len(src) // 3 * 2)
    j = 0
    for i in range(0, len(src), 3):
        r, g, b = src[i], src[i + 1], src[i + 2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = v & 0xFF
        out[j + 1] = v >> 8
        j += 2
    return bytes(out)


_pack = _numpy_packer() or _pure_packer


def to_rgb565(img: Image.Image) -> bytes:
    return _pack(img)


# ---------------------------------------------------------------- 键盘

def find_kbd() -> str | None:
    dev = "/dev/input/cardputer-zero-internal"
    if os.path.exists(dev):
        return dev
    for idx in range(0, 12):
        p = f"/dev/input/event{idx}"
        try:
            with open(f"/sys/class/input/event{idx}/device/name") as f:
                if "tca8418" in f.read():
                    return p
        except OSError:
            continue
    return None


# 不是键盘的设备（HDMI CEC、红外接收器）要排除，免得遥控器按键串进来
_KBD_EXCLUDE = ("vc4-hdmi", "gpio_ir_recv", "HDMI Jack", "pwr_button", "axp20x")


def _device_name(path: str) -> str:
    idx = os.path.basename(path).replace("event", "")
    try:
        with open(f"/sys/class/input/event{idx}/device/name") as f:
            return f.read().strip()
    except OSError:
        return ""


def _has_key(fd: int, code: int) -> bool:
    """用 EVIOCGBIT(1, ...) 判断设备是否支持某个按键。"""
    if fcntl is None:
        return False
    nbytes = 96
    buf = bytearray(nbytes)
    try:
        fcntl.ioctl(fd, 0x80604521, buf)      # EVIOCGBIT(EV_KEY, 96)
    except OSError:
        return False
    if code >= nbytes * 8:
        return False
    return bool(buf[code // 8] & (1 << (code % 8)))


def find_keyboards() -> list[str]:
    """内部键盘 + 其它真实键盘（USB 外接等）。虚拟注入设备也在这里被发现。"""
    out: list[str] = []
    primary = find_kbd()
    if primary:
        out.append(primary)
    for idx in range(0, 16):
        p = f"/dev/input/event{idx}"
        if not os.path.exists(p) or os.path.realpath(p) == os.path.realpath(primary or ""):
            continue
        name = _device_name(p)
        if any(bad in name for bad in _KBD_EXCLUDE):
            continue
        try:
            fd = os.open(p, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            if _has_key(fd, KEY_ENTER) and _has_key(fd, 30):   # Enter + A
                out.append(p)
        finally:
            os.close(fd)
    return out


class Keyboard:
    """读键盘；可同时监听多个 evdev 设备（CZ_KBD 用冒号分隔，便于注入测试）。"""

    def __init__(self, paths: list[str] | None = None, grab: bool = True):
        if paths is None:
            spec = os.environ.get("CZ_KBD")
            paths = [p for p in spec.split(":") if p] if spec else find_keyboards()
            if not paths:
                paths = ["/dev/input/event3"]
        self.paths = paths
        self.fds: list[int] = []
        self.grabbed = False
        for p in paths:
            try:
                self.fds.append(os.open(p, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                continue
        if grab and self.fds:
            self.grab()

    def grab(self) -> bool:
        if not self.fds:
            return False
        try:
            fcntl.ioctl(self.fds[0], _EVIOCGRAB, 1)
            self.grabbed = True
        except OSError:
            self.grabbed = False
        return self.grabbed

    def retry_grab(self) -> bool:
        """启动时如果被别家占着（launcher 刚放开需要一点时间），后面再试。"""
        if self.grabbed or not self.fds:
            return self.grabbed
        return self.grab()

    def read(self, timeout: float | None = 0.2) -> list[tuple[int, str]]:
        """返回 [(键码, 字符)]，只含按下事件。"""
        if not self.fds:
            time.sleep(timeout or 0)
            return []
        out: list[tuple[int, str]] = []
        try:
            ready, _, _ = select.select(self.fds, [], [], timeout)
        except OSError:
            return out
        for fd in ready:
            try:
                data = os.read(fd, _EVENT_SIZE * 64)
            except OSError:
                continue
            for i in range(0, len(data) - _EVENT_SIZE + 1, _EVENT_SIZE):
                _s, _us, etype, code, value = struct.unpack_from(_EVENT_FMT, data, i)
                if etype == _EV_KEY and value == 1:
                    out.append((code, KEYMAP.get(code, "")))
        return out

    def close(self) -> None:
        if self.fds and self.grabbed:
            try:
                fcntl.ioctl(self.fds[0], _EVIOCGRAB, 0)
            except OSError:
                pass
        for fd in self.fds:
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds = []
        self.grabbed = False


# ---------------------------------------------------------------- 字体

CJK_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/usr/share/APPLaunch/share/font/AlibabaPuHuiTi-3-55-Regular.ttf",
    # 本机预览用（Windows）
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/NotoSansSC-VF.ttf",
    "C:/Windows/Fonts/simhei.ttf",
]
CJK_BOLD_FONTS = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Bold.otf",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
]
MONO_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/consola.ttf",
]

_font_cache: dict[tuple[str, int, bool], ImageFont.FreeTypeFont] = {}


def _load(path: str, size: int) -> ImageFont.FreeTypeFont:
    if path.lower().endswith(".ttc"):
        for index in (2, 0, 1):                  # 2 = SC（简体）字面
            try:
                return ImageFont.truetype(path, size, index=index)
            except Exception:                    # noqa: BLE001
                continue
    return ImageFont.truetype(path, size)


def _first(paths: list[str]) -> str | None:
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def cjk(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = ("cjk", size, bold)
    if key in _font_cache:
        return _font_cache[key]
    path = os.environ.get("CZ_FONT") or (_first(CJK_BOLD_FONTS) if bold else None) or _first(CJK_FONTS)
    if path:
        try:
            f = _load(path, size)
            _font_cache[key] = f
            return f
        except Exception:                        # noqa: BLE001
            pass
    f = ImageFont.load_default()
    _font_cache[key] = f                        # type: ignore[assignment]
    return f                                    # type: ignore[return-value]


def mono(size: int) -> ImageFont.FreeTypeFont:
    key = ("mono", size, True)
    if key in _font_cache:
        return _font_cache[key]
    path = _first(MONO_FONTS)
    f = _load(path, size) if path else ImageFont.load_default()
    _font_cache[key] = f                        # type: ignore[assignment]
    return f                                    # type: ignore[return-value]


# ---------------------------------------------------------------- 小工具

def text(d: ImageDraw.ImageDraw, xy, s: str, font, fill) -> None:
    d.text(xy, s, font=font, fill=fill)


def text_center(d: ImageDraw.ImageDraw, cx: int, y: int, s: str, font, fill) -> None:
    w = d.textlength(s, font=font)
    d.text((cx - w / 2, y), s, font=font, fill=fill)


def text_right(d: ImageDraw.ImageDraw, rx: int, y: int, s: str, font, fill) -> None:
    w = d.textlength(s, font=font)
    d.text((rx - w, y), s, font=font, fill=fill)


def has_glyph(font, ch: str) -> bool:
    """粗判字体是否含某字（渲染为空且非空白 → 视为缺字）。"""
    if ch.strip() == "":
        return True
    try:
        mask = font.getmask(ch)
        return mask.getbbox() is not None
    except Exception:                             # noqa: BLE001
        return False


def blank() -> Image.Image:
    return Image.new("RGB", (W, H), (0, 0, 0))
