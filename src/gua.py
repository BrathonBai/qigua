"""gua.py — 时空梅花：以「时」立上卦、以「日躔方位」立下卦，动爻取时数。

规则（全部可复算，App 里会原样显示算式）：

  年数 = 年支序（子1…亥12）
  月数 = 农历月
  日数 = 农历日
  时数 = 时辰序（子1…亥12，取**真太阳时**，经度与均时差已修正）

  上卦 = (年 + 月 + 日) % 8，0 作 8        ← 时间
  下卦 = 日躔方位所属之后天八卦            ← 空间（由经纬度与时刻算出的太阳方位角）
  动爻 = (年 + 月 + 日 + 时) % 6，0 作 6

断法用梅花易数本门规则：动爻所在之卦为「用」，另一卦为「体」；
以体用五行生克定吉凶，互卦看中间过程，变卦看最终结果。
"""

from __future__ import annotations

import zlib

from astro import Moment

# ---------------------------------------------------------------- 八卦

# (先天数, 名, 五行, 象, 三爻自下而上 1=阳 0=阴)
TRIGRAMS = {
    1: (1, "乾", "金", "天", (1, 1, 1)),
    2: (2, "兑", "金", "泽", (1, 1, 0)),
    3: (3, "离", "火", "火", (1, 0, 1)),
    4: (4, "震", "木", "雷", (1, 0, 0)),
    5: (5, "巽", "木", "风", (0, 1, 1)),
    6: (6, "坎", "水", "水", (0, 1, 0)),
    7: (7, "艮", "土", "山", (0, 0, 1)),
    8: (8, "坤", "土", "地", (0, 0, 0)),
}

_LINES_TO_NUM = {v[4]: k for k, v in TRIGRAMS.items()}

# 后天八卦方位（角度，正北 0°，顺时针）
AZIMUTH_TRIGRAM = [
    (22.5, 6, "北"),      # 坎
    (67.5, 7, "东北"),    # 艮
    (112.5, 4, "东"),     # 震
    (157.5, 5, "东南"),   # 巽
    (202.5, 3, "南"),     # 离
    (247.5, 8, "西南"),   # 坤
    (292.5, 2, "西"),     # 兑
    (337.5, 1, "西北"),   # 乾
    (360.0, 6, "北"),
]

SHENG = {"木": "火", "火": "土", "土": "金", "金": "水", "水": "木"}
KE = {"木": "土", "土": "水", "水": "火", "火": "金", "金": "木"}

# 64 卦：(上卦, 下卦) -> (卦名, 大象辞, 白话)
HEXAGRAMS = {
    # 上乾
    (1, 1): ("乾为天", "天行健，君子以自强不息。", "刚健进取，宜守正持恒。"),
    (1, 2): ("天泽履", "上天下泽，履；君子以辨上下，定民志。", "如履薄冰，谨慎前行。"),
    (1, 3): ("天火同人", "天与火，同人；君子以类族辨物。", "与人同心，合作可成。"),
    (1, 4): ("天雷无妄", "天下雷行，物与无妄；先王以茂对时育万物。", "不妄为则无灾。"),
    (1, 5): ("天风姤", "天下有风，姤；后以施命诰四方。", "不期而遇，慎防小隙。"),
    (1, 6): ("天水讼", "天与水违行，讼；君子以作事谋始。", "争端将起，宜退让谋始。"),
    (1, 7): ("天山遁", "天下有山，遁；君子以远小人，不恶而严。", "退避为上，守静待时。"),
    (1, 8): ("天地否", "天地不交，否；君子以俭德辟难，不可荣以禄。", "上下不交，闭塞难通。"),
    # 上兑
    (2, 1): ("泽天夬", "泽上于天，夬；君子以施禄及下，居德则忌。", "决断去疑，防其躁进。"),
    (2, 2): ("兑为泽", "丽泽，兑；君子以朋友讲习。", "喜悦和顺，宜交流切磋。"),
    (2, 3): ("泽火革", "泽中有火，革；君子以治历明时。", "变革之时，顺天应人。"),
    (2, 4): ("泽雷随", "泽中有雷，随；君子以向晦入宴息。", "随时而动，不宜固执。"),
    (2, 5): ("泽风大过", "泽灭木，大过；君子以独立不惧，遁世无闷。", "负荷过重，宜减担。"),
    (2, 6): ("泽水困", "泽无水，困；君子以致命遂志。", "困顿受制，守正待援。"),
    (2, 7): ("泽山咸", "山上有泽，咸；君子以虚受人。", "感应相通，虚心纳人。"),
    (2, 8): ("泽地萃", "泽上于地，萃；君子以除戎器，戒不虞。", "聚集之时，备患防危。"),
    # 上离
    (3, 1): ("火天大有", "火在天上，大有；君子以遏恶扬善，顺天休命。", "丰盛有成，宜扬善去恶。"),
    (3, 2): ("火泽睽", "上火下泽，睽；君子以同而异。", "意见相违，求同存异。"),
    (3, 3): ("离为火", "明两作，离；大人以继明照于四方。", "光明附丽，宜守中不躁。"),
    (3, 4): ("火雷噬嗑", "雷电，噬嗑；先王以明罚敕法。", "去梗除碍，明断是非。"),
    (3, 5): ("火风鼎", "木上有火，鼎；君子以正位凝命。", "鼎新致养，位正则成。"),
    (3, 6): ("火水未济", "火在水上，未济；君子以慎辨物居方。", "未成之际，审慎图成。"),
    (3, 7): ("火山旅", "山上有火，旅；君子以明慎用刑而不留狱。", "漂泊在外，谨慎自守。"),
    (3, 8): ("火地晋", "明出地上，晋；君子以自昭明德。", "晋升有望，进德修业。"),
    # 上震
    (4, 1): ("雷天大壮", "雷在天上，大壮；君子以非礼弗履。", "势盛宜正，不可恃强。"),
    (4, 2): ("雷泽归妹", "泽上有雷，归妹；君子以永终知敝。", "急于求合，终始宜慎。"),
    (4, 3): ("雷火丰", "雷电皆至，丰；君子以折狱致刑。", "盛大盈满，宜防日中则昃。"),
    (4, 4): ("震为雷", "洊雷，震；君子以恐惧修省。", "震动警醒，修省免祸。"),
    (4, 5): ("雷风恒", "雷风，恒；君子以立不易方。", "恒久守常，不宜骤变。"),
    (4, 6): ("雷水解", "雷雨作，解；君子以赦过宥罪。", "解冻释难，宜宽缓。"),
    (4, 7): ("雷山小过", "山上有雷，小过；君子以行过乎恭，丧过乎哀，用过乎俭。", "小有过越，宜守分寸。"),
    (4, 8): ("雷地豫", "雷出地奋，豫；先王以作乐崇德，殷荐之上帝，以配祖考。", "安乐和顺，戒其怠惰。"),
    # 上巽
    (5, 1): ("风天小畜", "风行天上，小畜；君子以懿文德。", "小有积蓄，宜养德待时。"),
    (5, 2): ("风泽中孚", "泽上有风，中孚；君子以议狱缓死。", "诚信感通，宜以诚待人。"),
    (5, 3): ("风火家人", "风自火出，家人；君子以言有物而行有恒。", "家道和理，宜正内守常。"),
    (5, 4): ("风雷益", "风雷，益；君子以见善则迁，有过则改。", "增益有利，宜迁善改过。"),
    (5, 5): ("巽为风", "随风，巽；君子以申命行事。", "柔顺而入，宜循理而行。"),
    (5, 6): ("风水涣", "风行水上，涣；先王以享于帝立庙。", "涣散宜聚，收心归本。"),
    (5, 7): ("风山渐", "山上有木，渐；君子以居贤德善俗。", "循序渐进，不可躁进。"),
    (5, 8): ("风地观", "风行地上，观；先王以省方观民设教。", "观望审时，先察后动。"),
    # 上坎
    (6, 1): ("水天需", "云上于天，需；君子以饮食宴乐。", "等待时机，蓄养待发。"),
    (6, 2): ("水泽节", "泽上有水，节；君子以制数度，议德行。", "节制有度，量入为出。"),
    (6, 3): ("水火既济", "水在火上，既济；君子以思患而预防之。", "事已成，防患未然。"),
    (6, 4): ("水雷屯", "云雷屯，君子以经纶。", "初生艰难，宜经营筹划。"),
    (6, 5): ("水风井", "木上有水，井；君子以劳民劝相。", "井养不穷，守常施惠。"),
    (6, 6): ("坎为水", "水洊至，习坎；君子以常德行，习教事。", "重险相叠，守信涉险。"),
    (6, 7): ("水山蹇", "山上有水，蹇；君子以反身修德。", "前路艰难，反求诸己。"),
    (6, 8): ("水地比", "地上有水，比；先王以建万国，亲诸侯。", "亲比相辅，宜结善缘。"),
    # 上艮
    (7, 1): ("山天大畜", "天在山中，大畜；君子以多识前言往行，以畜其德。", "大有蓄积，厚积薄发。"),
    (7, 2): ("山泽损", "山下有泽，损；君子以惩忿窒欲。", "损下益上，舍小得大。"),
    (7, 3): ("山火贲", "山下有火，贲；君子以明庶政，无敢折狱。", "文饰之美，重质勿华。"),
    (7, 4): ("山雷颐", "山下有雷，颐；君子以慎言语，节饮食。", "颐养之道，慎言节食。"),
    (7, 5): ("山风蛊", "山下有风，蛊；君子以振民育德。", "积弊待治，除旧布新。"),
    (7, 6): ("山水蒙", "山下出泉，蒙；君子以果行育德。", "蒙昧待启，宜请教于人。"),
    (7, 7): ("艮为山", "兼山，艮；君子以思不出其位。", "止而不动，安分守位。"),
    (7, 8): ("山地剥", "山附于地，剥；上以厚下安宅。", "剥落衰退，宜固本待时。"),
    # 上坤
    (8, 1): ("地天泰", "天地交，泰；后以财成天地之道，辅相天地之宜，以左右民。", "通泰顺利，上下相交。"),
    (8, 2): ("地泽临", "泽上有地，临；君子以教思无穷，容保民无疆。", "居高临下，宜宽厚待人。"),
    (8, 3): ("地火明夷", "明入地中，明夷；君子以莅众，用晦而明。", "明伤于暗，宜韬光养晦。"),
    (8, 4): ("地雷复", "雷在地中，复；先王以至日闭关，商旅不行，后不省方。", "一阳来复，宜静养蓄势。"),
    (8, 5): ("地风升", "地中生木，升；君子以顺德，积小以高大。", "上升渐进，积小成大。"),
    (8, 6): ("地水师", "地中有水，师；君子以容民畜众。", "众力可用，宜有纪律。"),
    (8, 7): ("地山谦", "地中有山，谦；君子以裒多益寡，称物平施。", "谦逊受益，持盈保泰。"),
    (8, 8): ("坤为地", "地势坤，君子以厚德载物。", "柔顺承载，厚德致远。"),
}

# 体用关系 -> (断语, 说明)
REL_TEXT = {
    "用生体": ("吉", "用卦生体卦，外来之力相助，事有凭借，所求多能应手。"),
    "比和": ("吉", "体用同气，内外相合，事顺势而少阻，宜守成不宜更张。"),
    "体克用": ("小吉", "体卦制用卦，事在掌握之中，须费心力，终可有成。"),
    "体生用": ("耗", "体卦生用卦，我耗于外，付出多而收获迟，宜节力省事。"),
    "用克体": ("凶", "用卦克体卦，外力相逼，事多阻隔，宜避锋守静。"),
}

REL_ADVICE = {
    "用生体": ["借势而行，趁有人相助时把事定下来。", "贵人方向在{dir}，宜向彼处求之。", "不必另起炉灶，守着现有凭据即可。"],
    "比和": ["维持现状即是上策，改动反而生波。", "同类相求，宜与同道共事。", "稳中有进，不必急求速成。"],
    "体克用": ["事可为，但要一件一件去办，不可贪多。", "主动者胜，宜先发而不宜后应。", "阻力可化，关键在舍得用力。"],
    "体生用": ["先算清代价再答应，莫作无底之付。", "所耗者精力与时间，宜留三分余地。", "与其外求，不如收回力气照看自己。"],
    "用克体": ["此际不宜强争，退一步反得其全。", "所遇之阻来自外部，非己之过，静待其变。", "守成勿动，动则受制。"],
    "凶": ["宜止不宜行，宜守不宜攻。"],
}

# ---------------------------------------------------------------- 卦体


class Trigram:
    __slots__ = ("num", "name", "wuxing", "xiang", "lines")

    def __init__(self, num: int):
        self.num, self.name, self.wuxing, self.xiang, self.lines = TRIGRAMS[num]

    def __repr__(self) -> str:
        return f"{self.name}{self.wuxing}"


class Cast:
    """一次起卦的全部结果。"""

    def __init__(self, moment: Moment):
        self.m = moment
        self.year_num = moment.year_idx % 12 + 1          # 地支序 子=1
        self.month_num = moment.lunar.month
        self.day_num = moment.lunar.day
        self.hour_num = moment.hour_idx % 12 + 1          # 时辰序 子=1

        total_t = self.year_num + self.month_num + self.day_num
        self.upper = Trigram((total_t - 1) % 8 + 1)

        self.azimuth = moment.sun_az
        self.lower, self.direction = _azimuth_trigram(self.azimuth)

        total_all = total_t + self.hour_num
        self.moving = (total_all - 1) % 6 + 1            # 1..6，自下而上
        self.total_t, self.total_all = total_t, total_all

        # 六爻（自下而上）：下卦三爻 + 上卦三爻
        self.lines = list(self.lower.lines) + list(self.upper.lines)
        self.name, self.xiangci, self.plain = HEXAGRAMS[(self.upper.num, self.lower.num)]

        # 体用
        if self.moving <= 3:
            self.use, self.body = self.lower, self.upper
            self.body_pos = "上"
        else:
            self.use, self.body = self.upper, self.lower
            self.body_pos = "下"

        # 互卦：二三四爻为下互，三四五爻为上互
        self.hu_lower = Trigram(_LINES_TO_NUM[tuple(self.lines[1:4])])
        self.hu_upper = Trigram(_LINES_TO_NUM[tuple(self.lines[2:5])])
        self.hu_name = HEXAGRAMS[(self.hu_upper.num, self.hu_lower.num)][0]

        # 变卦：动爻反转
        bian = list(self.lines)
        bian[self.moving - 1] ^= 1
        self.bian_lower = Trigram(_LINES_TO_NUM[tuple(bian[0:3])])
        self.bian_upper = Trigram(_LINES_TO_NUM[tuple(bian[3:6])])
        self.bian_name = HEXAGRAMS[(self.bian_upper.num, self.bian_lower.num)][0]

        # 体用只论「用」那一侧的变化：互卦、变卦各取与「用」同侧之卦
        upper_is_body = self.body is self.upper
        self.hu_use = self.hu_lower if upper_is_body else self.hu_upper
        self.hu_body = self.hu_upper if upper_is_body else self.hu_lower
        self.bian_use = self.bian_lower if upper_is_body else self.bian_upper

        self.rel = _relation(self.body.wuxing, self.use.wuxing)
        self.verdict, self.rel_text = REL_TEXT[self.rel]
        self.hu_rel = _relation(self.body.wuxing, self.hu_use.wuxing)
        self.bian_rel = _relation(self.body.wuxing, self.bian_use.wuxing)

        self.pages = self._compose()

    # -- 六爻文本（自下而上第 n 爻）
    def line_symbol(self) -> list[str]:
        out = []
        for i, v in enumerate(self.lines):
            bar = "▬▬▬" if v else "▬ ▬"
            mark = "○" if (i + 1) == self.moving else " "
            out.append(f"{bar}{mark}")
        return out  # index 0 = 初爻

    def _seed(self, salt: str) -> int:
        return zlib.crc32(f"{self.name}{self.moving}{salt}".encode("utf-8"))

    def _pick(self, pool: list[str], salt: str) -> str:
        return pool[self._seed(salt) % len(pool)]

    def _compose(self) -> list[str]:
        m = self.m
        p1 = (
            f"{m.year_gz}年{m.month_gz}月{m.day_gz}日{m.hour_gz}时，农历{m.lunar.text}。"
            f"年{self.year_num}+月{self.month_num}+日{self.day_num}={self.total_t}，"
            f"八除得{self.upper.name}；日躔{self.direction}方取{self.lower.name}；"
            f"加时{self.hour_num}共{self.total_all}，六除动爻{self.moving}。"
        )
        p2 = f"{self.name}：{self.xiangci}{self.plain}"
        p3 = (f"体{self.body.name}{self.body.wuxing}，用{self.use.name}{self.use.wuxing}，"
              f"{self.rel}——{self.rel_text}")

        hu_txt = {
            "用生体": "中途有人相助。",
            "比和": "过程平顺无波。",
            "体克用": "中段须得用力。",
            "体生用": "中途耗费颇多。",
            "用克体": "中段阻力最重。",
        }[self.hu_rel]
        bian_txt = {
            "用生体": "结局得助，先难后易。",
            "比和": "结局与初心相合。",
            "体克用": "结局由我作主，虽迟必定。",
            "体生用": "所得不偿所费。",
            "用克体": "结局仍受制，宜早打算。",
        }[self.bian_rel]
        shichen = m.hour_gz[-1]
        advice = self._pick(REL_ADVICE.get(self.rel, ["宜守时待变。"]), "advice").format(dir=self.direction)
        p4 = (
            f"互{self.hu_name}，{hu_txt}变{self.bian_name}，{bian_txt}"
            f"时在{shichen}时，真太阳 {m.dt_solar:%H:%M}，"
            + ("日在地平上，气属阳，宜进取。" if m.is_day else "日未出地，气属阴，宜静守。")
            + f"断为{self.verdict}：{advice}"
        )
        return [p1, p2, p3, p4]

    def text_lines(self, width: int = 56) -> list[tuple[str, bool]]:
        """所有断语按显示宽度折行；第二项标记「这是本段第一行」。"""
        out: list[tuple[str, bool]] = []
        for para in self.pages:
            for i, ln in enumerate(_wrap(para, width)):
                out.append((ln, i == 0))
        return out

    def text_pages(self, per_page: int = 5, width: int = 56) -> list[list[tuple[str, bool]]]:
        lines = self.text_lines(width)
        return [lines[i:i + per_page] for i in range(0, len(lines), per_page)]


NO_LINE_START = "，。；：、！？）」』】…·,.;:!?)]}%"


def _wrap(text: str, width: int) -> list[str]:
    """按显示宽度折行（中文 2 格、ASCII 1 格），并做简单的避头尾。"""
    out: list[str] = []
    cur, w = "", 0
    for ch in text:
        cw = 1 if ord(ch) < 0x2E80 else 2
        if w + cw > width and cur:
            # 标点不放行首：把它留在本行末尾（允许轻微超宽）
            if ch in NO_LINE_START:
                cur += ch
                w += cw
                continue
            out.append(cur)
            cur, w = "", 0
        cur += ch
        w += cw
    if cur:
        out.append(cur)
    return out


def _azimuth_trigram(az: float) -> tuple[Trigram, str]:
    az = az % 360.0
    for limit, num, name in AZIMUTH_TRIGRAM:
        if az < limit:
            return Trigram(num), name
    return Trigram(6), "北"


def _relation(body_wx: str, use_wx: str) -> str:
    if body_wx == use_wx:
        return "比和"
    if SHENG[use_wx] == body_wx:
        return "用生体"
    if SHENG[body_wx] == use_wx:
        return "体生用"
    if KE[use_wx] == body_wx:
        return "用克体"
    return "体克用"


def cast(moment: Moment) -> Cast:
    return Cast(moment)


if __name__ == "__main__":
    from datetime import datetime, timedelta, timezone
    CST = timezone(timedelta(hours=8))
    m = Moment(datetime(2026, 11, 26, 4, 44, tzinfo=CST), lat=30.5728, lon=104.0668)
    c = cast(m)
    print(c.name, "动爻", c.moving, "体", c.body, "用", c.use, c.rel, c.verdict)
    for i, line in enumerate(c.line_symbol(), 1):
        print(f"  {i} {line}")
    for p in c.pages:
        print("-", p)
