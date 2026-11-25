#!/usr/bin/env python3
"""test_gua.py — 术法内核自测（python tests/test_gua.py）。"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import astro                                                     # noqa: E402
import gua                                                       # noqa: E402

CST = timezone(timedelta(hours=8))
FAILED: list[str] = []


def check(name: str, got, want) -> None:
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {got!r}" + ("" if ok else f"  != {want!r}"))
    if not ok:
        FAILED.append(name)


def ok(name: str, cond: bool, detail: str = "") -> None:
    print(f"{'ok  ' if cond else 'FAIL'} {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILED.append(name)


def moment(dt: datetime) -> astro.Moment:
    return astro.Moment(dt, lat=30.5728, lon=104.0668)


def main() -> int:
    # ---- 基础数据完整
    check("六十四卦数量", len(gua.HEXAGRAMS), 64)
    check("卦名唯一", len({v[0] for v in gua.HEXAGRAMS.values()}), 64)
    check("八卦齐全", len(gua.TRIGRAMS), 8)

    # ---- 八卦三爻与卦象一致（先天卦序）
    expect = {1: (1, 1, 1), 2: (1, 1, 0), 3: (1, 0, 1), 4: (1, 0, 0),
              5: (0, 1, 1), 6: (0, 1, 0), 7: (0, 0, 1), 8: (0, 0, 0)}
    check("三爻编码", {k: v[4] for k, v in gua.TRIGRAMS.items()}, expect)

    # ---- 五行生克
    check("金克木", gua.KE["金"], "木")
    check("水生木", gua.SHENG["水"], "木")
    check("用生体", gua._relation("火", "木"), "用生体")     # 木生火
    check("体生用", gua._relation("火", "土"), "体生用")
    check("用克体", gua._relation("火", "水"), "用克体")
    check("体克用", gua._relation("金", "木"), "体克用")
    check("比和", gua._relation("火", "火"), "比和")

    # ---- 取卦：样例时刻
    c = gua.cast(moment(datetime(2026, 11, 26, 4, 44, tzinfo=CST)))
    check("年数(午=7)", c.year_num, 7)
    check("月数", c.month_num, 10)
    check("日数", c.day_num, 18)
    check("时数(寅=3)", c.hour_num, 3)
    check("上卦总数", c.total_t, 35)
    check("上卦 35%8=3 离", c.upper.name, "离")
    check("总数为 38", c.total_all, 38)
    check("动爻 38%6=2", c.moving, 2)
    check("卦名", c.name, "火雷噬嗑")
    check("体用", (c.body.name, c.use.name), ("离", "震"))

    # ---- 六爻：下卦在前，动爻反转即变卦
    check("六爻长度", len(c.lines), 6)
    flipped = list(c.lines)
    flipped[c.moving - 1] ^= 1
    check("变卦正确", (c.bian_upper.lines + c.bian_lower.lines) ==
          (tuple(flipped[3:6]) + tuple(flipped[0:3])), True)

    # ---- 互卦：二三四爻 / 三四五爻
    check("互卦下", c.hu_lower.lines, tuple(c.lines[1:4]))
    check("互卦上", c.hu_upper.lines, tuple(c.lines[2:5]))

    # ---- 体用位置：动爻所在之卦为用
    ok("动爻在下卦时用取之", c.moving <= 3 and c.use is c.lower and c.body is c.upper)
    ok("体用互异", c.use.num != c.body.num or c.upper.num == c.lower.num)

    # 遍历若干时刻，确认体用永远覆盖上下两卦且动爻归属正确
    bad_dir = []
    for h in range(0, 24):
        cc = gua.cast(moment(datetime(2026, 11, 26, h, tzinfo=CST)))
        want_use = cc.lower if cc.moving <= 3 else cc.upper
        if cc.use is not want_use:
            bad_dir.append((h, cc.name, cc.moving))
    check("体用归属", bad_dir[:3], [])

    # ---- 确定性：同一时刻必得同一卦与同一段解释
    c2 = gua.cast(moment(datetime(2026, 11, 26, 4, 44, tzinfo=CST)))
    check("确定性-卦", (c2.name, c2.moving), (c.name, c.moving))
    check("确定性-文本", c2.pages, c.pages)

    # ---- 不同时刻应能得到不同卦（覆盖多种动爻）
    movings = set()
    for h in range(0, 24, 2):
        cc = gua.cast(moment(datetime(2026, 11, 26, h, 0, tzinfo=CST)))
        movings.add(cc.moving)
    ok("动爻分布", len(movings) >= 4, f"{sorted(movings)}")

    # ---- 文本分页：每行不超宽、页面数合理
    pages = c.text_pages(per_page=5, width=56)
    ok("分页非空", len(pages) >= 1, f"{len(pages)} 页")
    widest = max(len(ln) for pg in pages for ln, _ in pg)
    ok("行宽合理", widest <= 40, f"最长 {widest} 字")
    ok("避头尾", all(not ln.startswith(("，", "。", "；", "：")) for pg in pages for ln, _ in pg))

    # ---- 全部 64 卦的文本都能生成（防止缺数据）
    missing = []
    for dt in [datetime(2026, 1, 1, tzinfo=CST) + timedelta(hours=3 * i) for i in range(0, 200)]:
        cc = gua.cast(moment(dt))
        if not cc.pages or any(not p for p in cc.pages):
            missing.append(cc.name)
    check("64 卦文本齐备", missing[:3], [])

    print()
    if FAILED:
        print("FAILED:", ", ".join(FAILED))
        return 1
    print("all gua tests passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
