"""astro.py — 天文与历法内核（纯标准库，无第三方依赖）。

提供：
  * 太阳视黄经、赤经赤纬、均时差、太阳高度角/方位角
  * 二十四节气时刻（按太阳黄经反解，误差 < 1 分钟）
  * 朔（新月）时刻（Meeus 第 49 章低精度公式，误差约 1 分钟）
  * 中国农历（定朔定气、无中气置闰）
  * 干支历：年柱（立春换年）、月柱（节气换月）、日柱（23 时换日）、时柱（五鼠遁）

时间约定：
  内部一律用 UTC 儒略日（JD, UT）；农历的“日”以 UTC+8 的日期为准（中国标准）。

用法：
  from astro import Moment
  m = Moment.from_datetime(datetime.now(timezone.utc), lat=30.6, lon=104.0, tz=8)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------- 常量

J2000 = 2451545.0
CST = timezone(timedelta(hours=8))      # 中国标准时：农历日界与节气定月的官方基准
SYNODIC = 29.530588861          # 朔望月
TROPICAL = 365.242189           # 回归年

GAN = "甲乙丙丁戊己庚辛壬癸"
ZHI = "子丑寅卯辰巳午未申酉戌亥"
SHENGXIAO = "鼠牛虎兔龙蛇马羊猴鸡狗猪"

# 二十四节气：黄经 -> 名。用「节」定月柱，「中气」定农历闰月。
TERM_NAMES = {
    0: "春分", 15: "清明", 30: "谷雨", 45: "立夏", 60: "小满", 75: "芒种",
    90: "夏至", 105: "小暑", 120: "大暑", 135: "立秋", 150: "处暑", 165: "白露",
    180: "秋分", 195: "寒露", 210: "霜降", 225: "立冬", 240: "小雪", 255: "大雪",
    270: "冬至", 285: "小寒", 300: "大寒", 315: "立春", 330: "雨水", 345: "惊蛰",
}

# 十二「节」的黄经 -> 月支序号（0=子）。立春起寅月。
JIE_TO_ZHI = {
    315: 2,   # 立春 -> 寅
    345: 3,   # 惊蛰 -> 卯
    15: 4,    # 清明 -> 辰
    45: 5,    # 立夏 -> 巳
    75: 6,    # 芒种 -> 午
    105: 7,   # 小暑 -> 未
    135: 8,   # 立秋 -> 申
    165: 9,   # 白露 -> 酉
    195: 10,  # 寒露 -> 戌
    225: 11,  # 立冬 -> 亥
    255: 0,   # 大雪 -> 子
    285: 1,   # 小寒 -> 丑
}

LUNAR_MONTH_CN = ["正", "二", "三", "四", "五", "六", "七", "八", "九", "十", "冬", "腊"]
LUNAR_DAY_CN = [
    "初一", "初二", "初三", "初四", "初五", "初六", "初七", "初八", "初九", "初十",
    "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十",
    "廿一", "廿二", "廿三", "廿四", "廿五", "廿六", "廿七", "廿八", "廿九", "三十",
]


def _wrap180(x: float) -> float:
    return (x + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------- 儒略日

def jd_from_datetime(dt: datetime) -> float:
    """datetime -> 儒略日（UT）。naive 视为 UTC。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    y, m = dt.year, dt.month
    d = (dt.day + (dt.hour + (dt.minute + (dt.second + dt.microsecond / 1e6) / 60.0) / 60.0) / 24.0)
    if m <= 2:
        y -= 1
        m += 12
    a = y // 100
    b = 2 - a + a // 4
    return math.floor(365.25 * (y + 4716)) + math.floor(30.6001 * (m + 1)) + d + b - 1524.5


def datetime_from_jd(jd: float) -> datetime:
    """儒略日 -> UTC datetime（Meeus 7.1 反算）。"""
    z = math.floor(jd + 0.5)
    f = jd + 0.5 - z
    if z < 2299161:
        a = z
    else:
        alpha = math.floor((z - 1867216.25) / 36524.25)
        a = z + 1 + alpha - math.floor(alpha / 4)
    b = a + 1524
    c = math.floor((b - 122.1) / 365.25)
    d = math.floor(365.25 * c)
    e = math.floor((b - d) / 30.6001)
    day = b - d - math.floor(30.6001 * e) + f
    month = e - 1 if e < 14 else e - 13
    year = c - 4716 if month > 2 else c - 4715
    di = int(math.floor(day))
    frac = (day - di) * 24.0
    hh = int(math.floor(frac))
    mm_f = (frac - hh) * 60.0
    mm = int(math.floor(mm_f))
    ss = (mm_f - mm) * 60.0
    if ss >= 59.9995:
        ss = 0.0
        mm += 1
    if mm >= 60:
        mm -= 60
        hh += 1
    if hh >= 24:
        hh -= 24
        di += 1
    try:
        return datetime(year, month, 1, tzinfo=timezone.utc) + timedelta(
            days=di - 1, hours=hh, minutes=mm, seconds=ss
        )
    except ValueError:            # 跨月边界（极罕见）
        return datetime(year, month, 1, tzinfo=timezone.utc) + timedelta(days=di - 1)


# ---------------------------------------------------------------- 太阳

def sun_apparent_longitude(jd: float) -> float:
    """太阳视黄经（度，0–360）。低精度公式，1800–2200 内误差 < 0.01°。"""
    t = (jd - J2000) / 36525.0
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    mr = math.radians(m % 360.0)
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(mr)
         + (0.019993 - 0.000101 * t) * math.sin(2 * mr)
         + 0.000289 * math.sin(3 * mr))
    true_long = l0 + c
    omega = 125.04 - 1934.136 * t
    return (true_long - 0.00569 - 0.00478 * math.sin(math.radians(omega))) % 360.0


def _obliquity(jd: float) -> float:
    t = (jd - J2000) / 36525.0
    eps0 = 23.0 + 26.0 / 60.0 + 21.448 / 3600.0 - (46.8150 * t + 0.00059 * t * t - 0.001813 * t ** 3) / 3600.0
    omega = 125.04 - 1934.136 * t
    return eps0 + 0.00256 * math.cos(math.radians(omega))


def sun_ra_dec(jd: float) -> tuple[float, float]:
    """太阳视赤经、赤纬（度）。"""
    lam = math.radians(sun_apparent_longitude(jd))
    eps = math.radians(_obliquity(jd))
    ra = math.degrees(math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))) % 360.0
    dec = math.degrees(math.asin(math.sin(eps) * math.sin(lam)))
    return ra, dec


def equation_of_time(jd: float) -> float:
    """均时差（分钟）：真太阳时 − 平太阳时。全年约 −14…+16 分钟。"""
    t = (jd - J2000) / 36525.0
    l0 = (280.4664567 + 36000.76982779 * t + 0.03032028 * t ** 2
          + t ** 3 / 49931.0 - t ** 4 / 15300.0 - t ** 5 / 2000000.0) % 360.0
    ra, _dec = sun_ra_dec(jd)
    omega = 125.04 - 1934.136 * t
    dpsi = -0.00478 * math.sin(math.radians(omega))      # 章动近似（度）
    eps = _obliquity(jd)
    e = l0 - 0.0057183 - ra + dpsi * math.cos(math.radians(eps))
    return _wrap180(e) * 4.0


def gmst(jd: float) -> float:
    """格林尼治平恒星时（度）。"""
    t = (jd - J2000) / 36525.0
    return (280.46061837 + 360.98564736629 * (jd - J2000)
            + 0.000387933 * t * t - t ** 3 / 38710000.0) % 360.0


def sun_alt_az(jd: float, lat: float, lon: float) -> tuple[float, float]:
    """太阳高度角与方位角（度）。方位角自正北起顺时针：东 90、南 180、西 270。"""
    ra, dec = sun_ra_dec(jd)
    lst = (gmst(jd) + lon) % 360.0
    h = _wrap180(lst - ra)                       # 时角（度），西正
    latr, decr, hr = math.radians(lat), math.radians(dec), math.radians(h)
    sin_alt = math.sin(latr) * math.sin(decr) + math.cos(latr) * math.cos(decr) * math.cos(hr)
    alt = math.degrees(math.asin(max(-1.0, min(1.0, sin_alt))))
    az = math.degrees(math.atan2(
        -math.sin(hr) * math.cos(decr),
        math.cos(latr) * math.sin(decr) - math.sin(latr) * math.cos(decr) * math.cos(hr),
    )) % 360.0
    return alt, az


# ---------------------------------------------------------------- 节气 / 朔

def _bisect_crossing(jd_guess: float, target_deg: float, span: float = 12.0) -> float:
    """在 jd_guess ± span 天内二分定位太阳视黄经 = target_deg 的时刻。"""
    def f(jd: float) -> float:
        return _wrap180(sun_apparent_longitude(jd) - target_deg)

    lo, hi = jd_guess - span, jd_guess + span
    n = 0
    while f(lo) > 0 and n < 40:
        lo -= 2.0
        n += 1
    n = 0
    while f(hi) < 0 and n < 40:
        hi += 2.0
        n += 1
    for _ in range(48):
        mid = 0.5 * (lo + hi)
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def solar_terms_around(jd: float, span_days: float = 200.0) -> list[tuple[float, int]]:
    """返回 [jd, 黄经度数] 列表，覆盖 jd ± span_days 内的所有节气（一年 24 个）。"""
    out: list[tuple[float, int]] = []
    step = 4.0
    j = jd - span_days
    prev = sun_apparent_longitude(j)
    acc = prev                                  # 展开后的单调黄经（跨 360° 不回绕）
    while j < jd + span_days:
        nxt = sun_apparent_longitude(j + step)
        d = (nxt - prev) % 360.0
        if d <= 0:
            d = 0.01
        acc_next = acc + d
        k0 = math.floor(acc / 15.0)
        k1 = math.floor(acc_next / 15.0)
        for k in range(k0 + 1, k1 + 1):
            target = (k * 15.0) % 360.0
            frac = (k * 15.0 - acc) / d
            guess = j + step * max(0.02, min(0.98, frac))
            t = _bisect_crossing(guess, target)
            out.append((t, int(round(target)) % 360))
        acc = acc_next
        prev = nxt
        j += step
    out.sort()
    # 去重（相邻步可能定位到同一个交点）
    dedup: list[tuple[float, int]] = []
    for t, deg in out:
        if dedup and abs(t - dedup[-1][0]) < 0.02:
            continue
        dedup.append((t, deg))
    return dedup


def new_moon_jd(k: float) -> float:
    """第 k 个朔的时刻（JD, UT）。k=0 约为 2000-01-06。"""
    t = k / 1236.85
    jde = (2451550.09766 + SYNODIC * k + 0.00015437 * t ** 2
           - 0.000000150 * t ** 3 + 0.00000000073 * t ** 4)
    e = 1.0 - 0.002516 * t - 0.0000074 * t ** 2
    m = math.radians((2.5534 + 29.10535670 * k - 0.0000014 * t ** 2 - 0.00000011 * t ** 3) % 360)
    mp = math.radians((201.5643 + 385.81693528 * k + 0.0107582 * t ** 2
                       + 0.00001238 * t ** 3 - 0.000000058 * t ** 4) % 360)
    f = math.radians((160.7108 + 390.67050284 * k - 0.0016118 * t ** 2
                      - 0.00000227 * t ** 3 + 0.000000011 * t ** 4) % 360)
    om = math.radians((124.7746 - 1.56375588 * k + 0.0020672 * t ** 2 + 0.00000215 * t ** 3) % 360)
    corr = (-0.40720 * math.sin(mp)
            + 0.17241 * e * math.sin(m)
            + 0.01608 * math.sin(2 * mp)
            + 0.01039 * math.sin(2 * f)
            + 0.00739 * e * math.sin(mp - m)
            - 0.00514 * e * math.sin(mp + m)
            + 0.00208 * e * e * math.sin(2 * m)
            - 0.00111 * math.sin(mp - 2 * f)
            - 0.00057 * math.sin(mp + 2 * f)
            + 0.00056 * e * math.sin(2 * mp + m)
            - 0.00042 * math.sin(3 * mp)
            + 0.00042 * e * math.sin(m + 2 * f)
            + 0.00038 * e * math.sin(m - 2 * f)
            - 0.00024 * e * math.sin(2 * mp - m)
            - 0.00017 * math.sin(om)
            - 0.00007 * math.sin(mp + 2 * m)
            + 0.00004 * math.sin(2 * mp - 2 * f)
            + 0.00004 * math.sin(3 * m)
            + 0.00003 * math.sin(mp + m - 2 * f)
            + 0.00003 * math.sin(2 * mp + 2 * f)
            - 0.00003 * math.sin(mp + m + 2 * f)
            + 0.00003 * math.sin(mp - m + 2 * f)
            - 0.00002 * math.sin(mp - m - 2 * f)
            - 0.00002 * math.sin(3 * mp + m)
            + 0.00002 * math.sin(4 * mp))
    return jde + corr - _delta_t_days(jde)


def _delta_t_days(jd: float) -> float:
    """TT − UT（天）。2020 年代约 69 秒；这里用分段近似，误差 < 2 秒。"""
    y = 2000.0 + (jd - J2000) / 365.25
    if y < 1950:
        sec = 24.0
    elif y < 2000:
        sec = 24.0 + (y - 1950) * 0.9
    elif y < 2050:
        sec = 63.8 + (y - 2000) * 0.21
    else:
        sec = 74.3 + (y - 2050) * 0.1
    return sec / 86400.0


def _cst_wall(jd: float) -> datetime:
    """JD(UT) -> 中国标准时的墙上时间（naive）。农历的日界按 UTC+8。"""
    return datetime_from_jd(jd).astimezone(CST).replace(tzinfo=None)


def _cst_midnight(jd: float) -> datetime:
    return _cst_wall(jd).replace(hour=0, minute=0, second=0, microsecond=0)


def new_moons_around(jd: float, span_days: float = 200.0) -> list[datetime]:
    """返回 jd ± span_days 内的朔日（CST 墙上日期 00:00）。"""
    k0 = int(math.floor((jd - span_days - 2451550.09766) / SYNODIC)) - 1
    k1 = int(math.ceil((jd + span_days - 2451550.09766) / SYNODIC)) + 1
    out = []
    for k in range(k0, k1 + 1):
        d = _cst_midnight(new_moon_jd(k))
        if d not in out:
            out.append(d)
    out.sort()
    return out


# ---------------------------------------------------------------- 农历

class LunarDate:
    __slots__ = ("year", "month", "day", "leap", "month_name", "day_name", "text")

    def __init__(self, year: int, month: int, day: int, leap: bool, day_name: str | None = None):
        self.year = year
        self.month = month
        self.day = day
        self.leap = leap
        base = LUNAR_MONTH_CN[(month - 1) % 12]
        self.month_name = ("闰" if leap else "") + base + "月"
        self.day_name = day_name or LUNAR_DAY_CN[min(day, 30) - 1]
        self.text = f"{self.month_name}{self.day_name}"

    def __repr__(self) -> str:
        return f"LunarDate({self.year}, {self.month}, {self.day}, leap={self.leap})"


def lunar_date(dt_cst: datetime) -> LunarDate:
    """公历（CST）-> 中国农历（定朔定气、无中气置闰）。

    规则：以冬至所在月为十一月；两冬至间有 13 个月时，第一个无中气的月为闰月。
    农历日界按 UTC+8，与 1929 年以后的中国历书一致。
    """
    wall = dt_cst.astimezone(CST).replace(tzinfo=None) if dt_cst.tzinfo else dt_cst
    date_now = wall.replace(hour=0, minute=0, second=0, microsecond=0)
    jd = jd_from_datetime(wall.replace(tzinfo=CST))
    if jd < J2000 - 1:
        pass

    terms = solar_terms_around(jd, 460.0)
    moons = new_moons_around(jd, 460.0)
    wss = sorted(t for t, deg in terms if deg == 270)
    if len(wss) < 2:
        raise ValueError("not enough winter solstices in window")

    # 选出让 date_now 落在 [月11, 下一个月11) 的那个「岁」
    choice = None
    for i in range(len(wss) - 1):
        ws, nxt_ws = wss[i], wss[i + 1]
        m11 = max(m for m in moons if m <= _cst_midnight(ws))
        m11_next = max(m for m in moons if m <= _cst_midnight(nxt_ws))
        if m11 <= date_now < m11_next:
            choice = (ws, nxt_ws, m11, m11_next)
            break
    if choice is None:            # 兜底（理论上不会走到）
        ws, nxt_ws = wss[-2], wss[-1]
        m11 = max(m for m in moons if m <= _cst_midnight(ws))
        m11_next = max(m for m in moons if m <= _cst_midnight(nxt_ws))
        choice = (ws, nxt_ws, m11, m11_next)
    ws, nxt_ws, m11, m11_next = choice

    months = sorted({m for m in moons if m11 <= m < m11_next})

    # 无中气置闰
    leap_index = -1
    if len(months) >= 13:
        for i in range(1, len(months)):
            end = months[i + 1] if i + 1 < len(months) else m11_next
            has_zhongqi = any(
                months[i] <= _cst_wall(t) < end
                for t, deg in terms if deg % 30 == 0
            )
            if not has_zhongqi:
                leap_index = i
                break

    # 月序：十一月起，闰月沿用上一个月的月名
    seq: list[tuple[datetime, int, bool]] = []
    num = 11
    for i, start in enumerate(months):
        if i == leap_index and seq:
            seq.append((start, seq[-1][1], True))
            continue
        seq.append((start, num, False))
        num = num % 12 + 1

    start, month_no, leap = seq[-1]
    for i, (s, no, lp) in enumerate(seq):
        end = seq[i + 1][0] if i + 1 < len(seq) else m11_next
        if s <= date_now < end:
            start, month_no, leap = s, no, lp
            break

    day = (date_now - start).days + 1
    lunar_year = _cst_wall(ws).year if month_no >= 11 else _cst_wall(ws).year + 1
    return LunarDate(lunar_year, month_no, day, leap)


# ---------------------------------------------------------------- 干支

def ganzhi(index: int) -> str:
    return GAN[index % 10] + ZHI[index % 12]


def _day_index(jd: float) -> int:
    """日柱序号（以 UT 儒略日为准，调用方负责把『日』换成目标时区的日期）。"""
    return (math.floor(jd + 0.5) + 49) % 60


class Moment:
    """一个「时刻」的全部历法信息：公历、农历、干支四柱、真太阳时、太阳方位。"""

    def __init__(self, dt_utc: datetime, lat: float, lon: float, tz_hours: float = 8.0):
        self.dt_utc = dt_utc.astimezone(timezone.utc)
        self.jd = jd_from_datetime(self.dt_utc)
        self.lat = lat
        self.lon = lon
        self.tz_hours = tz_hours
        self.tz = timezone(timedelta(hours=tz_hours))
        self.dt_local = self.dt_utc.astimezone(self.tz)

        # 真太阳时：平太阳时（经度修正）+ 均时差
        self.eot = equation_of_time(self.jd)                      # 分钟
        self.lon_corr = (lon - tz_hours * 15.0) * 4.0              # 分钟
        self.dt_solar = self.dt_local + timedelta(minutes=self.lon_corr + self.eot)

        # 太阳位置
        self.sun_alt, self.sun_az = sun_alt_az(self.jd, lat, lon)

        # 农历：按 UTC+8 的日界（中国历法基准）
        self.lunar = lunar_date(self.dt_utc + timedelta(hours=8))

        # 四柱
        self.year_gz, self.year_idx = self._year_pillar()
        self.month_gz, self.month_idx = self._month_pillar()
        self.day_gz, self.day_idx = self._day_pillar()
        self.hour_gz, self.hour_idx = self._hour_pillar()

        self.term = self._current_term()
        self.solar_hour_zhi = _hour_zhi(self.dt_solar)
        self.is_day = self.sun_alt > 0.0

    # -- 年柱：立春换年
    def _year_pillar(self) -> tuple[str, int]:
        terms = solar_terms_around(self.jd, 400.0)
        lichun = [t for t, deg in terms if deg == 315 and t <= self.jd + 1e-6]
        y = self.dt_local.year if lichun else self.dt_local.year - 1
        idx = (y - 4) % 60
        return ganzhi(idx), idx

    # -- 月柱：节气（节）换月，五虎遁定干
    def _month_pillar(self) -> tuple[str, int]:
        terms = solar_terms_around(self.jd, 200.0)
        jie = [(t, deg) for t, deg in terms if deg in JIE_TO_ZHI and t <= self.jd + 1e-6]
        if not jie:
            raise ValueError("no jie in window")
        _t, deg = jie[-1]
        zhi = JIE_TO_ZHI[deg]
        gan = (self.year_gan * 2 + zhi) % 10
        idx = _gz_index(gan, zhi)
        return ganzhi(idx), idx

    # -- 日柱：23 时（子正）换日
    def _day_pillar(self) -> tuple[str, int]:
        d = self.dt_local
        # 真太阳时过了 23 点，按次日算（晚子时归明日）
        if self.dt_solar.hour >= 23:
            d = d + timedelta(days=1)
        jd0 = jd_from_datetime(datetime(d.year, d.month, d.day, 12, tzinfo=self.tz))
        idx = _day_index(jd0)
        return ganzhi(idx), idx

    # -- 时柱：五鼠遁
    def _hour_pillar(self) -> tuple[str, int]:
        zhi = _hour_zhi(self.dt_solar)
        gan = (self.day_gan * 2 + zhi) % 10
        idx = _gz_index(gan, zhi)
        return ganzhi(idx), idx

    def _current_term(self) -> tuple[str, datetime]:
        terms = solar_terms_around(self.jd, 200.0)
        past = [t for t in terms if t[0] <= self.jd + 1e-6]
        t, deg = past[-1]
        return TERM_NAMES[deg], datetime_from_jd(t).astimezone(self.tz)

    # -- 便捷属性
    @property
    def year_gan(self) -> int:
        return self.year_idx % 10

    @property
    def day_gan(self) -> int:
        return self.day_idx % 10

    def __repr__(self) -> str:
        return (f"Moment({self.dt_local:%Y-%m-%d %H:%M:%S} "
                f"{self.year_gz}年{self.month_gz}月{self.day_gz}日{self.hour_gz}时 "
                f"solar={self.dt_solar:%H:%M:%S} alt={self.sun_alt:.1f} az={self.sun_az:.1f})")


def _hour_zhi(dt: datetime) -> int:
    """时辰序号：子=0（23:00–00:59）。"""
    return ((dt.hour + 1) // 2) % 12


def _gz_index(gan: int, zhi: int) -> int:
    """由干、支求六十甲子序号。"""
    for i in range(60):
        if i % 10 == gan % 10 and i % 12 == zhi % 12:
            return i
    raise ValueError("invalid ganzhi")


# ---------------------------------------------------------------- 自测
if __name__ == "__main__":
    from datetime import datetime as _dt

    m = Moment(_dt(2026, 11, 25, 20, 44, tzinfo=timezone.utc), lat=30.6, lon=104.0)
    print(m)
    print("农历", m.lunar, "节气", m.term[0], "昼" if m.is_day else "夜")
    for y in (2024, 2025, 2026):
        ms = new_moons_around(jd_from_datetime(_dt(y, 2, 20, 12, tzinfo=timezone.utc)), 60)
        print(y, "春节前后朔日", [f"{d:%m-%d}" for d in ms][:3])
