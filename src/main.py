#!/usr/bin/env python3
"""起卦 — CardputerZero 应用入口。

打开即是数字时钟（NTP 校时 + IP 定位），显示公历、农历与干支四柱；
按 6 键（屏幕【起卦】按钮所在的键位）即以当下时刻起卦，给出卦象与解释。

本地预览：
    python src/main.py --screenshot shots/clock.png --at "2026-11-25 20:44:00" \
        --lat 30.57 --lon 104.07 --tz 8 --cast
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import astro                                        # noqa: E402
import cz                                           # noqa: E402
import gua                                          # noqa: E402
import net                                          # noqa: E402
import ui                                           # noqa: E402

TEXTS_PER_PAGE = 4
TEXT_WIDTH = 56
KEY_POLL = 0.08
LOG_PATH = "/tmp/qigua.log"
__version__ = "0.7.0"

CAST_KEYS = {"6", "enter", " "}
BACK_KEYS = {"esc", "backspace"}
# 方向键只在 FN 层存在；官方文档明确建议「非文本输入界面直接兼容 F/Z/X/C」，
# 所以这里同时收字母与方向码（F=上 Z=左 X=下 C=右）。
LEFT_KEYS = {"left", "up", "pageup", ",", "`", "z", "f", "a"}
RIGHT_KEYS = {"right", "down", "pagedown", "/", ".", "semicolon", "c", "x", "d"}


def local_tz_hours() -> float:
    lt = time.localtime()
    off = time.altzone if lt.tm_isdst else time.timezone
    return -off / 3600.0


class App:
    def __init__(self, lat: float | None = None, lon: float | None = None,
                 tz: float | None = None, offline: bool = False, verbose: bool = False,
                 city: str = ""):
        self.clock = net.Clock()
        self.geo = (net.Geo(lat, lon, city, None, "cli")
                    if lat is not None and lon is not None else None)
        self.tz = tz if tz is not None else local_tz_hours()
        self.offline = offline
        self.verbose = verbose
        self.uix = ui.Ui()

        self.mode = "clock"                 # clock | hex
        self.cast: gua.Cast | None = None
        self.pages: list[list[str]] = []
        self.page = 0
        self.pressed_until = 0.0
        self.calibrating_until = 0.0
        self.flash_until = 0.0
        self.flash_text = ""
        self.status = ""
        self.net_state = {"source": "RTC", "geo_busy": not offline, "refreshing": False}
        self.last_key_at = 0.0
        self._moment: astro.Moment | None = None
        self._moment_key = None
        self._stop = threading.Event()
        self._refresh = threading.Event()

        conf = net.load_config()
        try:
            self.uix.btn_x = int(conf.get("btn_x", ui.DEFAULT_BTN_X))
        except Exception:                   # noqa: BLE001
            self.uix.btn_x = ui.DEFAULT_BTN_X

    # ------------------------------------------------------------ 后台校时定位
    def log(self, msg: str) -> None:
        """始终写 /tmp/qigua.log（便于事后排查），-v 时同时打到 stderr。"""
        if self.verbose:
            try:
                print(msg, file=sys.stderr, flush=True)
            except Exception:                   # noqa: BLE001
                pass
        try:
            if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 65536:
                os.remove(LOG_PATH)
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
        except OSError:
            pass

    def _worker(self) -> None:
        if self.offline:
            return
        first = True
        while not self._stop.is_set():
            forced = self._refresh.is_set()
            self._refresh.clear()
            try:
                if first or forced or self.clock.needs_sync():
                    net.sync_time(self.clock, log=self.log)
                    self.net_state["source"] = self.clock.source
                if first or forced or self.geo is None or self.geo.source == "cache":
                    self.net_state["geo_busy"] = True
                    g = net.geo_lookup(log=self.log, force=forced)
                    if g is not None:
                        self.geo = g
                        if g.tz:
                            self.tz = net.tz_offset_hours(g.tz, self.tz)
                    self.net_state["geo_busy"] = False
                if forced:
                    self._after_refresh()
            except Exception as exc:                    # noqa: BLE001
                self.log(f"worker: {exc}")
            finally:
                self.net_state["refreshing"] = False
            first = False
            self._refresh.wait(120)

    def _after_refresh(self) -> None:
        """手动刷新完成后给一条短回执。"""
        src = self.clock.source.replace("NTP:", "")
        pos = f"{self.geo.city} {self.geo.lat:.2f},{self.geo.lon:.2f}" if self.geo else "无定位"
        self.flash_text = f"已刷新 {src} · {pos}"
        self.flash_until = self.clock.now() + 3.5
        self.log(f"refresh done: {self.flash_text}")

    def request_refresh(self) -> None:
        self.net_state["refreshing"] = True
        self._refresh.set()
        self.log("manual refresh (key 8)")

    def _status_text(self, now: float) -> str:
        if self.net_state.get("refreshing"):
            return "正在重新校时…"
        if now < self.flash_until:
            return self.flash_text
        return ""

    # ------------------------------------------------------------ 时刻
    @property
    def lat(self) -> float:
        return self.geo.lat if self.geo else 30.0

    @property
    def lon(self) -> float:
        return self.geo.lon if self.geo else self.tz * 15.0

    def moment(self, now: float) -> astro.Moment:
        key = (time.strftime("%Y%m%d%H%M", time.gmtime(now)), round(self.lat, 3),
               round(self.lon, 3), self.tz)
        if self._moment is None or self._moment_key != key:
            self._moment = astro.Moment(datetime.fromtimestamp(now, timezone.utc),
                                        self.lat, self.lon, self.tz)
            self._moment_key = key
        return self._moment

    def cast_now(self, now: float) -> None:
        m = astro.Moment(datetime.fromtimestamp(now, timezone.utc), self.lat, self.lon, self.tz)
        self.cast = gua.cast(m)
        self.pages = self.cast.text_pages(per_page=TEXTS_PER_PAGE, width=TEXT_WIDTH)
        self.mode = "hex"
        self.page = 0
        self.log(f"cast {self.cast.name} moving={self.cast.moving} {self.cast.rel}")

    # ------------------------------------------------------------ 画
    @property
    def page_count(self) -> int:
        return 1 + len(self.pages)

    def render(self, now: float):
        pressed = now < self.pressed_until
        if self.mode == "clock":
            return self.uix.clock(now, self.moment(now), self.geo, self.net_state,
                                  pressed=pressed, status=self._status_text(now), tz=self.tz,
                                  calibrating=now < self.calibrating_until,
                                  key_flash=(now - self.last_key_at) < 0.5)
        if self.mode == "about":
            return self.uix.about(__version__, self.clock.source)
        assert self.cast is not None
        if self.page == 0:
            return self.uix.hexagram(self.cast, self.cast.m, self.page, self.page_count)
        return self.uix.text_page(self.cast, self.pages[self.page - 1], self.page - 1,
                                  self.page_count - 1)

    # ------------------------------------------------------------ 键
    def _nudge_button(self, delta: int, now: float) -> None:
        x = max(ui.BTN_X_MIN, min(ui.BTN_X_MAX, self.uix.btn_x + delta))
        if x != self.uix.btn_x:
            self.uix.btn_x = x
            net.save_config({"btn_x": x})
        self.calibrating_until = now + 3.0

    def on_key(self, code: int, ch: str, now: float) -> bool:
        """返回 False 表示退出。"""
        if ch in BACK_KEYS:
            if self.mode == "about":
                self.mode = "clock"
                return True
            if self.mode == "hex":
                if self.page > 0:
                    self.page = 0
                else:
                    self.mode = "clock"
                    self.cast = None
                return True
            return False
        if self.mode == "about":
            if ch in ("h", "6", "enter", " "):
                self.mode = "clock"
            return True
        if self.mode == "clock":
            if ch in CAST_KEYS:
                self.pressed_until = now + 0.25
                self.cast_now(now)
            elif ch == "8":
                self.request_refresh()
            elif ch == "h":
                self.mode = "about"
            elif ch in LEFT_KEYS:
                self._nudge_button(-4, now)
            elif ch in RIGHT_KEYS:
                self._nudge_button(+4, now)
            return True
        # 卦象 / 断语页
        if ch in CAST_KEYS:
            self.cast_now(now)
        elif ch in RIGHT_KEYS:
            self.page = (self.page + 1) % self.page_count
        elif ch in LEFT_KEYS:
            self.page = (self.page - 1) % self.page_count
        return True

    # ------------------------------------------------------------ 主循环
    def run(self) -> int:
        screen = cz.Screen()
        kbd = cz.Keyboard(grab=True)
        self.log(f"start fb={screen.path} ok={screen.ok} kbd={kbd.paths} grab={kbd.grabbed} "
                 f"btn_x={self.uix.btn_x}")

        t = threading.Thread(target=self._worker, daemon=True)
        t.start()
        last_tick = -1
        try:
            while True:
                now = self.clock.now()
                dirty = False
                for code, ch in kbd.read(KEY_POLL):
                    self.last_key_at = now
                    self.log(f"key code={code} ch={ch!r}")
                    if not self.on_key(code, ch, now):
                        return 0
                    dirty = True
                tick = int(now)
                if not kbd.grabbed and tick % 2 == 0:
                    if kbd.retry_grab():
                        self.log("grab ok (retry)")
                if dirty or tick != last_tick or now < self.pressed_until:
                    screen.show(self.render(now))
                    last_tick = tick
        except KeyboardInterrupt:
            return 0
        finally:
            self._stop.set()
            kbd.close()
            try:
                screen.show(cz.blank())          # 走之前把屏擦黑，桌面重绘更干净
            except Exception:                    # noqa: BLE001
                pass
            screen.close()


# ---------------------------------------------------------------- 本地预览

def screenshot(args) -> None:
    if args.at:
        local = datetime.strptime(args.at, "%Y-%m-%d %H:%M:%S")
        ts = local.replace(tzinfo=timezone.utc).timestamp()
    else:
        ts = time.time()
    app = App(lat=args.lat, lon=args.lon, tz=args.tz, offline=True, city=args.city or "")
    app.net_state = {"source": "NTP:aliyun", "geo_busy": False}
    if app.geo is None:
        app.geo = net.Geo(30.5728, 104.0668, "成都", "Asia/Shanghai", "demo")
    app.clock.set(ts, "NTP:demo", 0.002)

    out = args.screenshot
    base = out[:-4] if out.lower().endswith(".png") else out
    app.render(ts).save(out)
    print("wrote", out)
    if args.cast:
        app.cast_now(ts)
        for i in range(app.page_count):
            app.page = i
            p = f"{base}-{i}.png"
            app.render(ts).save(p)
            print("wrote", p)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="起卦")
    ap.add_argument("--screenshot", help="把一帧渲染成 PNG（本地预览）")
    ap.add_argument("--cast", action="store_true", help="预览时顺带渲染卦象页")
    ap.add_argument("--at", help="预览用时刻 'YYYY-MM-DD HH:MM:SS'（按 UTC 解释）")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--tz", type=float, help="时区小时，例如 8")
    ap.add_argument("--city", help="预览时显示的城市名")
    ap.add_argument("--offline", action="store_true", help="不联网")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.screenshot:
        screenshot(args)
        return 0
    try:
        return App(lat=args.lat, lon=args.lon, tz=args.tz, offline=args.offline,
                   verbose=args.verbose, city=args.city or "").run()
    except Exception:                       # noqa: BLE001 — 从桌面启动时没人看 stderr
        import traceback
        try:
            with open("/tmp/qigua.err", "a", encoding="utf-8") as f:
                f.write(time.strftime("%H:%M:%S ") + traceback.format_exc() + "\n")
        except OSError:
            pass
        raise


if __name__ == "__main__":
    sys.exit(main())
