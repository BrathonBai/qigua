#!/usr/bin/env python3
"""test_astro.py — 历法内核自测（不需要 pytest，直接 python tests/test_astro.py）。

基准样例取自用户实测：2026-11-26 04:44 (CST) 应为
    丙午年 己亥月 甲辰日 丙寅时，农历十月十八
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import astro                                                     # noqa: E402

CST = timezone(timedelta(hours=8))
FAILED: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f"  != {want!r}"))
    if not ok:
        FAILED.append(name)


def approx(name: str, got: float, want: float, tol: float) -> None:
    ok = abs(got - want) <= tol
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got:.3f} (want {want:.3f}±{tol})")
    if not ok:
        FAILED.append(name)


def main() -> int:
    # ---- 四柱：用户给的样例
    m = astro.Moment(datetime(2026, 11, 26, 4, 44, tzinfo=CST), lat=30.5728, lon=104.0668)
    check("年柱", m.year_gz, "丙午")
    check("月柱", m.month_gz, "己亥")
    check("日柱", m.day_gz, "甲辰")
    check("时柱", m.hour_gz, "丙寅")
    check("农历", (m.lunar.month, m.lunar.day), (10, 18))
    check("节气", m.term[0], "小雪")

    # ---- 真太阳时：成都经度 104.07°，比 120° 慢 63.7 分钟；11 月底均时差约 +12.9 分钟
    approx("均时差", m.eot, 12.9, 2.0)
    approx("经度修正", m.lon_corr, (104.0668 - 120) * 4, 0.01)
    solar_min = m.dt_solar.hour * 60 + m.dt_solar.minute
    approx("真太阳时(分钟)", solar_min, 4 * 60 + 44 - 63.73 + 12.9, 3.0)

    # ---- 春节（正月初一）
    for y, mo, d in [(2024, 2, 10), (2025, 1, 29), (2026, 2, 17)]:
        ld = astro.lunar_date(datetime(y, mo, d, 12, tzinfo=CST))
        check(f"{y} 春节", (ld.month, ld.day, ld.leap), (1, 1, False))

    # ---- 闰月：2025 年闰六月（2025-08-01 应为闰六月初）
    ld = astro.lunar_date(datetime(2025, 8, 1, 12, tzinfo=CST))
    check("2025-08-01 闰六月", (ld.month, ld.leap), (6, True))

    # ---- 日柱连续（差一天 = 差一位）
    a = astro.Moment(datetime(2026, 11, 26, 12, tzinfo=CST), 30, 104)
    b = astro.Moment(datetime(2026, 11, 27, 12, tzinfo=CST), 30, 104)
    check("日柱递进", (b.day_idx - a.day_idx) % 60, 1)

    # ---- 均时差极值：11 月初 +16 分钟左右，2 月中旬 −14 分钟左右
    approx("EoT 极大", astro.equation_of_time(astro.jd_from_datetime(
        datetime(2026, 11, 3, 12, tzinfo=timezone.utc))), 16.4, 1.0)
    approx("EoT 极小", astro.equation_of_time(astro.jd_from_datetime(
        datetime(2026, 2, 11, 12, tzinfo=timezone.utc))), -14.2, 1.0)

    # ---- 太阳方位：北京夏至（12:00 CST）太阳应偏南、高度角约 73°
    #      真太阳正午比 12:00 早约 14 分钟，故方位角略偏东（约 167°）
    alt, az = astro.sun_alt_az(astro.jd_from_datetime(datetime(2026, 6, 21, 4, tzinfo=timezone.utc)),
                               39.9, 116.4)
    approx("夏至高度角", alt, 73.4, 1.5)
    check("夏至方位偏南", 160.0 <= az <= 200.0, True)

    # ---- 节气有序：一年 24 个，黄经递增 15°
    terms = astro.solar_terms_around(
        astro.jd_from_datetime(datetime(2026, 6, 1, tzinfo=timezone.utc)), 200)
    degs = [d for _t, d in terms]
    check("节气数量(±200天)", 24 <= len(degs) <= 26, True)
    check("节气黄经步长", all((degs[i + 1] - degs[i]) % 360 == 15 for i in range(len(degs) - 1)), True)

    print()
    if FAILED:
        print("FAILED:", ", ".join(FAILED))
        return 1
    print("all astro tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
