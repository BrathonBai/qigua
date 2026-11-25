"""net.py — 校时与定位（纯标准库）。

* 校时：SNTP（UDP 123）为主，HTTP 的 Date 头为备，失败则退回本机 RTC。
* 定位：多家免密钥 IP 地理服务依次尝试，结果缓存 30 分钟；也支持配置文件手填经纬度。

所有网络动作都带超时，且只在后台线程里跑，不阻塞界面。
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import struct
import time
import urllib.request
from datetime import datetime, timezone

APP_ID = "qigua"
CACHE_DIR = os.path.expanduser("~/.cache/" + APP_ID)
CONF_DIR = os.path.expanduser("~/.config/" + APP_ID)
GEO_CACHE = os.path.join(CACHE_DIR, "geo.json")
CONF_FILE = os.path.join(CONF_DIR, "config.json")

NTP_HOSTS = [
    "ntp.aliyun.com",
    "ntp1.aliyun.com",
    "cn.pool.ntp.org",
    "ntp.tencent.com",
    "pool.ntp.org",
    "time.apple.com",
]

DATE_HOSTS = [
    "http://www.baidu.com/",
    "http://connect.rom.miui.com/generate_204",
    "https://www.qq.com/",
]

UA = "qigua/1.0 (CardputerZero)"

# (名称, URL, 取经度的键, 取纬度的键, 城市键, 时区键)
GEO_PROVIDERS = [
    ("ip-api", "http://ip-api.com/json/?lang=zh-CN&fields=status,message,country,regionName,city,lat,lon,timezone,query",
     "lat", "lon", "city", "timezone"),
    ("ipwho.is", "https://ipwho.is/",
     "latitude", "longitude", "city", ("timezone", "id")),
    ("ipapi.co", "https://ipapi.co/json/",
     "latitude", "longitude", "city", "timezone"),
    ("ipinfo", "https://ipinfo.io/json",
     "loc", None, "city", "timezone"),
]


def friendly_host(host: str) -> str:
    """ntp.aliyun.com -> aliyun；cn.pool.ntp.org -> pool。"""
    parts = host.split(".")
    if len(parts) >= 2 and parts[0].lower() in ("ntp", "ntp1", "ntp2", "time", "cn", "0", "1", "2"):
        return parts[1]
    return parts[0]


# ---------------------------------------------------------------- 校时

class Clock:
    """把「一次校时 + 单调时钟」变成持续可用的当前时间。"""

    def __init__(self) -> None:
        self._mono = time.monotonic()
        self._unix = time.time()
        self.source = "RTC"
        self.rtt = 0.0
        self.synced_at = 0.0

    def now(self) -> float:
        return self._unix + (time.monotonic() - self._mono)

    @property
    def offset(self) -> float:
        """当前时钟相对 RTC 的偏差（秒，正数表示 RTC 慢）。"""
        return self._unix - time.time()

    def set(self, unix_ts: float, source: str, rtt: float = 0.0) -> None:
        self._mono = time.monotonic()
        self._unix = unix_ts
        self.source = source
        self.rtt = rtt
        self.synced_at = time.time()

    def needs_sync(self, period: float = 1800.0) -> bool:
        return (time.time() - self.synced_at) > period


def sntp_now(host: str, timeout: float = 1.5) -> tuple[float, float]:
    """返回 (Unix 时间, 往返时延)。"""
    pkt = b"\x1b" + 47 * b"\x00"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t0 = time.time()
        s.sendto(pkt, (host, 123))
        data, _ = s.recvfrom(64)
        t3 = time.time()
    finally:
        s.close()
    if len(data) < 48:
        raise ValueError("short ntp packet")
    secs = struct.unpack("!I", data[40:44])[0]
    frac = struct.unpack("!I", data[44:48])[0]
    if secs == 0:
        raise ValueError("null ntp transmit time")
    ts = secs - 2208988800 + frac / 4294967296.0
    return ts + (t3 - t0) / 2.0, t3 - t0


def http_date_now(url: str, timeout: float = 4.0) -> float:
    """从 HTTP 响应头 Date 取时间（秒级精度）。"""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        date = resp.headers.get("Date")
    if not date:
        raise ValueError("no Date header")
    dt = datetime.strptime(date, "%a, %d %b %Y %H:%M:%S %Z").replace(tzinfo=timezone.utc)
    return dt.timestamp() + 0.5


def sync_time(clock: Clock, log=None) -> bool:
    for host in NTP_HOSTS:
        try:
            ts, rtt = sntp_now(host, timeout=1.5)
            clock.set(ts, "NTP:" + friendly_host(host), rtt)
            if log:
                log(f"ntp ok {host} rtt={rtt*1000:.0f}ms")
            return True
        except Exception as exc:                      # noqa: BLE001
            if log:
                log(f"ntp fail {host}: {exc}")
    for url in DATE_HOSTS:
        try:
            ts = http_date_now(url, timeout=4.0)
            clock.set(ts, "HTTP")
            if log:
                log(f"http date ok {url}")
            return True
        except Exception as exc:                      # noqa: BLE001
            if log:
                log(f"http date fail {url}: {exc}")
    if log:
        log("time sync failed, keep RTC")
    return False


# ---------------------------------------------------------------- 定位

class Geo:
    __slots__ = ("lat", "lon", "city", "tz", "source", "ip")

    def __init__(self, lat: float, lon: float, city: str = "", tz: str | None = None,
                 source: str = "", ip: str = ""):
        self.lat = lat
        self.lon = lon
        self.city = city
        self.tz = tz
        self.source = source
        self.ip = ip

    @property
    def text(self) -> str:
        if self.city:
            return f"{self.city} {self.lat:.2f},{self.lon:.2f}"
        return f"{self.lat:.2f},{self.lon:.2f}"

    def __repr__(self) -> str:
        return f"Geo({self.lat},{self.lon},{self.city},{self.source})"


def _dig(d: dict, key):
    if isinstance(key, tuple):
        for k in key:
            if not isinstance(d, dict):
                return None
            d = d.get(k)
        return d
    return d.get(key) if isinstance(d, dict) else None


def _fetch_json(url: str, timeout: float = 5.0) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def geo_lookup(log=None, ttl: float = 1800.0, force: bool = False) -> Geo | None:
    # 1) 手填优先
    conf = load_config()
    if conf.get("lat") is not None and conf.get("lon") is not None:
        return Geo(float(conf["lat"]), float(conf["lon"]), conf.get("city", ""),
                   conf.get("tz"), "config")

    # 2) 缓存（force=True 时跳过，强制重新问一次）
    if not force:
        cached = _read_geo_cache()
        if cached and (time.time() - cached["ts"]) < ttl:
            return Geo(cached["lat"], cached["lon"], cached.get("city", ""),
                       cached.get("tz"), "cache", cached.get("ip", ""))

    # 3) 逐家尝试
    for name, url, klat, klon, kcity, ktz in GEO_PROVIDERS:
        try:
            data = _fetch_json(url)
            if data.get("status") == "fail" or data.get("success") is False:
                continue
            vlat = _dig(data, klat)
            vlon = _dig(data, klon)
            if klat == "loc":                     # ipinfo 的 "纬度,经度"
                if isinstance(vlat, str) and "," in vlat:
                    vlat, vlon = vlat.split(",", 1)
            lat, lon = float(vlat), float(vlon)   # type: ignore[arg-type]
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                continue
            if abs(lat) < 1e-6 and abs(lon) < 1e-6:
                continue
            g = Geo(lat, lon, str(_dig(data, kcity) or ""), _dig(data, ktz),
                    name, str(data.get("query") or data.get("ip") or ""))
            _write_geo_cache(g)
            if log:
                log(f"geo ok {name} {g.text}")
            return g
        except Exception as exc:                  # noqa: BLE001
            if log:
                log(f"geo fail {name}: {exc}")
    return None


def _read_geo_cache() -> dict | None:
    try:
        with open(GEO_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        if "lat" in d and "lon" in d and "ts" in d:
            return d
    except Exception:                             # noqa: BLE001
        pass
    return None


def _write_geo_cache(g: Geo) -> None:
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        tmp = GEO_CACHE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"lat": g.lat, "lon": g.lon, "city": g.city, "tz": g.tz,
                       "ip": g.ip, "source": g.source, "ts": time.time()}, f)
        os.replace(tmp, GEO_CACHE)
    except Exception:                             # noqa: BLE001
        pass


def load_config() -> dict:
    try:
        with open(CONF_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:                             # noqa: BLE001
        return {}


def save_config(patch: dict) -> None:
    """把若干键合并写回配置文件（失败静默，不影响主流程）。"""
    try:
        cur = load_config()
        cur.update(patch)
        os.makedirs(CONF_DIR, exist_ok=True)
        tmp = CONF_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONF_FILE)
    except Exception:                             # noqa: BLE001
        pass


def tz_offset_hours(tz_name: str | None, default: float = 8.0) -> float:
    """时区名 -> 当前 UTC 偏移（小时）。取不到就用 default。"""
    if not tz_name:
        return default
    try:
        from zoneinfo import ZoneInfo
        off = datetime.now(ZoneInfo(tz_name)).utcoffset()
        if off is not None:
            return off.total_seconds() / 3600.0
    except Exception:                             # noqa: BLE001
        pass
    return default


if __name__ == "__main__":
    c = Clock()
    print("sync:", sync_time(c, log=print))
    print("clock:", datetime.fromtimestamp(c.now(), timezone.utc).isoformat(), c.source)
    t0 = time.time()
    g = geo_lookup(log=print)
    print("geo:", g, f"{time.time()-t0:.2f}s")
    if g:
        print("tz hours:", tz_offset_hours(g.tz))
