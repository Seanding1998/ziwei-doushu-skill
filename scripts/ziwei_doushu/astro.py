"""
ziwei_doushu/astro.py — 确定性天文与历法计算（纯 Python，无外部依赖）

职责边界：凡「可计算」的都在这里算完，绝不交给 AI 心算。
  · 真太阳时校正（经度项 + 均时差）
  · 时辰序号判定（含早晚子时、跨日位移）
  · 干支 / 六十甲子 / 纳音
  · 十干四化、自化（离心/向心）
  · 三方四正、夹宫
  · 大限 / 小限 / 流年命宫 岁数表
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

# ---------- 基本常量 ----------
# iztro 的时序号：0=早子 1=丑 … 11=亥 12=晚子
TIME_NAMES = ["早子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "晚子"]

# 地支两套顺序
BR12 = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]  # 子起
BR_YIN = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]  # 寅起（iztro 宫序）

STEMS = "甲乙丙丁戊己庚辛壬癸"
BRANCHES = "子丑寅卯辰巳午未申酉戌亥"
# 阳干：大限行向要用（阳男阴女顺行／阴男阳女逆行）
_YANG_STEMS = frozenset("甲丙戊庚壬")

# 十干四化（通行本）。中州派差异在戊·庚·壬的「科」，见 references/interpretation.md §6
MUTAGEN_TABLE: dict[str, list[str]] = {
    "甲": ["廉贞", "破军", "武曲", "太阳"],
    "乙": ["天机", "天梁", "紫微", "太阴"],
    "丙": ["天同", "天机", "文昌", "廉贞"],
    "丁": ["太阴", "天同", "天机", "巨门"],
    "戊": ["贪狼", "太阴", "右弼", "天机"],
    "己": ["武曲", "贪狼", "天梁", "文曲"],
    "庚": ["太阳", "武曲", "太阴", "天同"],
    "辛": ["巨门", "太阳", "文曲", "文昌"],
    "壬": ["天梁", "紫微", "左辅", "武曲"],
    "癸": ["破军", "巨门", "太阴", "贪狼"],
}
MUTAGEN_NAMES = ["禄", "权", "科", "忌"]

# 纳音五行（六十甲子）——只留判定五行局所需
_NAYIN_WUXING = {
    "水": "水二局", "木": "木三局", "金": "金四局", "土": "土五局", "火": "火六局",
}
# 六十甲子纳音表（30 组，每组管两个干支）
_NAYIN_60 = [
    ("甲子", "乙丑", "海中金", "金"), ("丙寅", "丁卯", "炉中火", "火"),
    ("戊辰", "己巳", "大林木", "木"), ("庚午", "辛未", "路旁土", "土"),
    ("壬申", "癸酉", "剑锋金", "金"), ("甲戌", "乙亥", "山头火", "火"),
    ("丙子", "丁丑", "涧下水", "水"), ("戊寅", "己卯", "城头土", "土"),
    ("庚辰", "辛巳", "白蜡金", "金"), ("壬午", "癸未", "杨柳木", "木"),
    ("甲申", "乙酉", "泉中水", "水"), ("丙戌", "丁亥", "屋上土", "土"),
    ("戊子", "己丑", "霹雳火", "火"), ("庚寅", "辛卯", "松柏木", "木"),
    ("壬辰", "癸巳", "长流水", "水"), ("甲午", "乙未", "砂中金", "金"),
    ("丙申", "丁酉", "山下火", "火"), ("戊戌", "己亥", "平地木", "木"),
    ("庚子", "辛丑", "壁上土", "土"), ("壬寅", "癸卯", "金箔金", "金"),
    ("甲辰", "乙巳", "覆灯火", "火"), ("丙午", "丁未", "天河水", "水"),
    ("戊申", "己酉", "大驿土", "土"), ("庚戌", "辛亥", "钗钏金", "金"),
    ("壬子", "癸丑", "桑柘木", "木"), ("甲寅", "乙卯", "大溪水", "水"),
    ("丙辰", "丁巳", "沙中土", "土"), ("戊午", "己未", "天上火", "火"),
    ("庚申", "辛酉", "石榴木", "木"), ("壬戌", "癸亥", "大海水", "水"),
]
_NAYIN_MAP = {}
for _a, _b, _name, _wx in _NAYIN_60:
    _NAYIN_MAP[_a] = (_name, _wx)
    _NAYIN_MAP[_b] = (_name, _wx)

# 长生生十二神顺序（用于复核，不解读）
CHANGSHENG = ["长生", "沐浴", "冠带", "临官", "帝旺", "衰", "病", "死", "墓", "绝", "胎", "养"]

# 十二宫名的固定次序。命宫起，其后每一宫比前一宫**地支减一**：
#   命宫申 → 兄弟未 → 夫妻午 → 子女巳 → 财帛辰 → 疾厄卯 → 迁移寅 →
#   仆役丑 → 官禄子 → 田宅亥 → 福德戌 → 父母酉
# 本盘宫序与大限宫序用的是**同一条规则**（大限只是换一个「命宫地支」起点），
# 故第四轮把这条映射做成可计算函数，禁止 AI 手算——第三轮盲审的 BLOCKER
# 正是手算这一步时把「大限福德宫」算成了申宫（实为子·本命官禄宫）。
PALACE_ORDER = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                "迁移", "仆役", "官禄", "田宅", "福德", "父母"]


# ---------- 干支 ----------
def ganzhi(year: int) -> str:
    """公历年 → 干支（以 1984 甲子为基准，仅用于立春口径对照）。"""
    k = (year - 1984) % 60
    return STEMS[k % 10] + BRANCHES[k % 12]


def nayin(ganzhi_str: str) -> tuple[str, str] | None:
    """干支 → (纳音名, 五行)。"""
    return _NAYIN_MAP.get(ganzhi_str)


def five_elements_from_palace_stem(palace_ganzhi: str) -> str | None:
    """命宫干支（如 '戊寅'）→ 五行局名（如 '土五局'）。"""
    ny = nayin(palace_ganzhi)
    return _NAYIN_WUXING.get(ny[1]) if ny else None


# ---------- 真太阳时 ----------
def _day_of_year(d: _dt.date) -> int:
    return d.timetuple().tm_yday


def equation_of_time_minutes(d: _dt.date, hour_local: float) -> float:
    """均时差（分钟）。近似公式，精度 ±20 秒级，足够定时辰。"""
    import math

    n = _day_of_year(d)
    gamma = (2 * math.pi / 365.0) * (n - 1 + (hour_local - 12) / 24.0)
    return 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )


def hour_index_from_clock(hh: int, mm: int = 0) -> int:
    """钟点 → iztro 时序号。23 点整属子时（晚子=12），0 点属早子（=0）。"""
    if hh == 23:
        return 12
    if hh == 0:
        return 0
    return (hh + 1) // 2


def _period_bounds(time_index: int) -> tuple[int, int]:
    """
    时序号 → (本时辰起点, 终点) 的分钟数（钟表口径）。

    iztro 的时辰区间按钟表**奇数整点**切：早子 00:00–00:59、丑 01:00–03:00、
    寅 03:00–05:00 … 巳 09:00–11:00、午 11:00–13:00、未 13:00–15:00 … 亥 21:00–23:00、
    晚子 23:00–23:59。`basic.timeRange`（午时 11:00~13:00）就是这套口径，本函数与它一致。
    """
    if time_index == 0:                      # 早子
        return 0, 60
    if time_index == 12:                     # 晚子
        return 23 * 60, 24 * 60
    start = 2 * time_index - 1               # n=1→01:00, n=6→11:00, n=11→21:00
    return start * 60, (start + 2) * 60


def nearest_hour_boundary(minutes_of_day: int, time_index: int | None = None
                          ) -> tuple[int, int, int, str]:
    """
    距最近的**时辰交界**有多远、误差会把你带到哪个时辰。

    ⚠️ 这是本 skill 踩过的坑：`min(t % 120, 120 - t % 120)` 算的是「距最近的**整点**」，
    而时辰交界只在**奇数整点**（01、03…11、13…23）。用它会在偶数整点上多出四个
    并不存在的交界，把 午时 11:41 误报成「距交界 19 分钟（12:00）」；
    实际它距 11:00 有 41 分钟、距 13:00 有 79 分钟，最近交界是 **11:00**，
    风险方向是**退入巳时**——与盘面 `basic.timeRange`（午时 11:00~13:00）相符。

    返回 (最近交界分钟数, 偏出后进入的时序号, 距离分钟, 方向说明)。
    """
    t = minutes_of_day % 1440
    if time_index is None:
        # 由钟点反推时序号（0=早子，12=晚子），与 hour_index_from_clock 同源
        hh = t // 60
        time_index = hour_index_from_clock(hh, t % 60)
    start, end = _period_bounds(time_index)
    d_start, d_end = t - start, end - t
    if d_start <= d_end:
        boundary, dist, raw_idx = start, d_start, time_index - 1
    else:
        boundary, dist, raw_idx = end, d_end, time_index + 1
    if raw_idx < 0:
        raw_idx = 12                        # 早子之前 = 晚子（23:00–23:59）
    elif raw_idx > 12:
        raw_idx = 0                         # 晚子之后 = 早子（00:00–00:59）
    direction = (f"退入{TIME_NAMES[raw_idx]}时" if d_start <= d_end
                 else f"进入{TIME_NAMES[raw_idx]}时")
    return boundary, raw_idx, dist, direction


def apparent_solar_time(
    birth: _dt.date,
    hh: int,
    mm: int,
    longitude: float | None,
    standard_meridian: float = 120.0,
) -> dict[str, Any]:
    """
    真太阳时校正。

    Returns dict with:
      corrected / clock / apparent / delta_minutes / equation_of_time /
      longitude_term / day_shift / time_index / time_name /
      minutes_from_hour_boundary / nearest_hour_boundary /
      nearest_boundary_minutes / boundary_shift_direction / warnings

    `timeRange`（本时辰区间的钟表起讫）取自 iztro 的 basic，与本函数算出的
    time_index 必须一致；本函数只负责「距交界多远、往哪边偏」。
    """
    warnings: list[str] = []
    clock = f"{hh:02d}:{mm:02d}"
    clock_idx = hour_index_from_clock(hh, mm)

    if longitude is None:
        warnings.append(
            "未提供经度，无法做真太阳时校正。若出生时间距时辰交界 30 分钟内，"
            "结果可能整体错位——必须补经度后复核。"
        )
        # 未校正时两条轴重合，钟点轴字段照给，保证报告模板不用分支处理
        cb_min, _cb_idx, cb_edge, cb_dir = nearest_hour_boundary(hh * 60 + mm, clock_idx)
        return {
            "corrected": False,
            "clock": clock,
            "apparent": None,
            "delta_minutes": 0.0,
            "equation_of_time": None,
            "longitude_term": None,
            "day_shift": 0,
            "time_index": clock_idx,
            "time_name": TIME_NAMES[clock_idx],
            "minutes_from_hour_boundary": cb_edge,
            "nearest_hour_boundary": f"{cb_min // 60:02d}:{cb_min % 60:02d}",
            "nearest_boundary_minutes": cb_min,
            "boundary_shift_direction": cb_dir,
            "clock_minutes_from_hour_boundary": cb_edge,
            "clock_nearest_hour_boundary": f"{cb_min // 60:02d}:{cb_min % 60:02d}",
            "clock_boundary_shift_direction": cb_dir,
            "warnings": warnings,
        }

    eot = equation_of_time_minutes(birth, hh + mm / 60.0)
    lon_term = (longitude - standard_meridian) * 4.0
    delta = lon_term + eot

    total = hh * 60 + mm + round(delta)
    day_shift = 0
    while total < 0:
        total += 1440
        day_shift -= 1
    while total >= 1440:
        total -= 1440
        day_shift += 1
    ah, am = divmod(total, 60)

    idx = hour_index_from_clock(ah, am)
    # 距最近**时辰交界**的分钟数：交界在奇数整点（午时 11:00~13:00），不是整点取模
    boundary_min, boundary_idx, edge, direction = nearest_hour_boundary(total, idx)
    # 钟点轴的同两项——报告必须**两条轴都写清楚**（第三轮盲审 MINOR）：
    # 真太阳时 11:41 距 11:00 只有 41 分钟、方向是「退入巳时」；
    # 钟表 12:07 距 13:00 只有 53 分钟、方向是「进入未时」。两轴最近交界与方向相反，
    # 只写一条轴就会让读者以为「反正都不贴近」而忽略方向相反这件事。
    clock_total = hh * 60 + mm
    cb_min, cb_idx, cb_edge, cb_dir = nearest_hour_boundary(clock_total, clock_idx)

    if idx != clock_idx:
        warnings.append(
            f"真太阳时校正改变了时辰：钟表 {clock} 属{TIME_NAMES[clock_idx]}时，"
            f"校正后 {ah:02d}:{am:02d} 属{TIME_NAMES[idx]}时。"
        )
    if edge < 30:
        warnings.append(
            f"真太阳时 {ah:02d}:{am:02d} 距时辰交界仅 {edge} 分钟"
            f"（最近的交界是 {boundary_min // 60:02d}:{boundary_min % 60:02d}，"
            f"风险方向为{direction}）；出生时间若有一刻钟误差即会换时辰。"
            "请提供三至五件已知重大事件的年份，用流年与大限四化逐件反推核对后，再采信大限流年结论。"
        )
    if day_shift:
        warnings.append(f"真太阳时跨日（位移 {day_shift} 天），晚子时日柱口径需确认。")

    return {
        "corrected": True,
        "clock": clock,
        "apparent": f"{ah:02d}:{am:02d}",
        "delta_minutes": round(delta, 2),
        "equation_of_time": round(eot, 2),
        "longitude_term": round(lon_term, 2),
        "day_shift": day_shift,
        "time_index": idx,
        "time_name": TIME_NAMES[idx],
        "minutes_from_hour_boundary": edge,
        "nearest_hour_boundary": f"{boundary_min // 60:02d}:{boundary_min % 60:02d}",
        "nearest_boundary_minutes": boundary_min,
        "boundary_shift_direction": direction,
        # 钟点轴（未校正）的同两项——与真太阳时轴并列，报告须两轴都写
        "clock_minutes_from_hour_boundary": cb_edge,
        "clock_nearest_hour_boundary": f"{cb_min // 60:02d}:{cb_min % 60:02d}",
        "clock_boundary_shift_direction": cb_dir,
        "warnings": warnings,
    }


# ---------- 四化 / 自化 ----------
def birth_mutagen(year_stem: str) -> list[dict[str, str]]:
    """生年四化：年干 → [{star, type, index}]"""
    stars = MUTAGEN_TABLE.get(year_stem, [])
    return [{"star": s, "type": MUTAGEN_NAMES[i]} for i, s in enumerate(stars)]


def mutagen_of_stem(stem: str) -> dict[str, str]:
    """宫干 → {星名: 四化类型}，用于自化判定。"""
    return {s: MUTAGEN_NAMES[i] for i, s in enumerate(MUTAGEN_TABLE.get(stem, []))}


def self_mutagen(palaces: list[dict[str, Any]]) -> list[dict[str, str]]:
    """
    自化计算（两向）：
      ↓ 离心自化：本宫宫干化入本宫内的星
      ↑ 向心自化：对宫宫干化入本宫内的星
    """
    by_branch = {p["branch"]: p for p in palaces}

    def star_names(p: dict[str, Any]) -> set[str]:
        out = set()
        for key in ("major", "minor", "adjective"):
            for s in p.get(key) or []:
                out.add(s["name"] if isinstance(s, dict) else s)
        return out

    out: list[dict[str, str]] = []
    for p in palaces:
        names = star_names(p)
        # 离心
        for star, typ in mutagen_of_stem(p["stem"]).items():
            if star in names:
                out.append({
                    "direction": "离心", "symbol": "↓",
                    "branch": p["branch"], "palace": p["name"], "from_branch": p["branch"],
                    "from_stem": p["stem"], "star": star, "type": typ,
                })
        # 向心（对宫）
        opp_b = BR12[(BR12.index(p["branch"]) + 6) % 12]
        opp = by_branch.get(opp_b)
        if opp:
            for star, typ in mutagen_of_stem(opp["stem"]).items():
                if star in names:
                    out.append({
                        "direction": "向心", "symbol": "↑",
                        "branch": p["branch"], "palace": p["name"], "from_branch": opp_b,
                        "from_stem": opp["stem"], "star": star, "type": typ,
                    })
    return out


# ---------- 宫位关系 ----------
def trine_of(branch: str) -> list[str]:
    """三合三宫（含本宫）。"""
    i = BR12.index(branch)
    return [BR12[(i + o) % 12] for o in (0, 4, 8)]


def opposite_of(branch: str) -> str:
    return BR12[(BR12.index(branch) + 6) % 12]


def surrounded(palaces: list[dict[str, Any]], branch: str) -> dict[str, Any]:
    """三方四正：本宫 + 对宫 + 两个三合宫。"""
    by_branch = {p["branch"]: p for p in palaces}
    tri = trine_of(branch)
    return {
        "self": by_branch.get(branch),
        "opposite": by_branch.get(opposite_of(branch)),
        "trine1": by_branch.get(tri[1]),
        "trine2": by_branch.get(tri[2]),
    }


def flanking(palaces: list[dict[str, Any]], branch: str) -> dict[str, Any]:
    """夹宫：命宫的左右两邻。"""
    by_branch = {p["branch"]: p for p in palaces}
    i = BR12.index(branch)
    left = BR12[(i - 1) % 12]
    right = BR12[(i + 1) % 12]
    return {"left": by_branch.get(left), "right": by_branch.get(right)}


SOFT6 = ["左辅", "右弼", "天魁", "天钺", "文昌", "文曲"]
TOUGH6 = ["擎羊", "陀罗", "火星", "铃星", "地空", "地劫"]


def flanking_kinds(palace: dict[str, Any] | None) -> list[dict[str, str]]:
    """夹宫性质：吉 / 煞。"""
    if not palace:
        return []
    out = []
    for key in ("major", "minor"):
        for s in palace.get(key) or []:
            nm = s["name"] if isinstance(s, dict) else s
            if nm in SOFT6:
                out.append({"star": nm, "kind": "吉"})
            elif nm in TOUGH6:
                out.append({"star": nm, "kind": "煞"})
    return out


def flanked_stars(palaces: list[dict[str, Any]], branch: str,
                  stars: tuple[str, str]) -> tuple[bool, str]:
    """
    通用夹宫判定：`stars` 两颗是否**分居** branch 的两邻（一左一右，本宫不算）。

    为什么做成通用函数：夹宫类格局不止「夹命宫」这一种——巳宫禄存被午·擎羊与
    辰·陀罗夹、某宫被空劫夹等，同样属于「两颗正好落在两邻」的结构。
    旧版只写死了「夹命宫」四个格，导致巳宫羊陀夹这类结构在候选表里根本不存在，
    审查者只能靠人工发现（第二轮盲审 MAJOR）。

    返回 (是否成夹, 说明文字)。同宫不算夹——两颗同位时本函数判定为否，
    报告若写成「X 夹 Y」即与盘面矛盾（审查器的 FLANK_MISJUDGED 抓这一类）。
    """
    s1, s2 = stars
    by_branch = {p["branch"]: p for p in palaces}

    def where(name: str) -> list[str]:
        out = []
        for p in palaces:
            names = [s["name"] for s in (p.get("major") or []) + (p.get("minor") or [])]
            if name in names:
                out.append(p["branch"])
        return out

    b1, b2 = where(s1), where(s2)
    i = BR12.index(branch)
    nb = {BR12[(i - 1) % 12], BR12[(i + 1) % 12]}
    ok = bool(b1 and b2 and b1[0] != b2[0] and {b1[0], b2[0]} == nb)
    same = bool(b1 and b2 and b1[0] == b2[0])
    note = "（两颗同宫，不构成夹）" if same else ""
    return ok, f"{s1}@{b1} {s2}@{b2}；{branch}宫两邻={sorted(nb)}{note}"


# ---------- 六吉六煞配对事实（第五轮新增） ----------
# 为什么要有这一段：第五轮盲审的 BLOCKER 是「六吉不成对」——
# 报告在四处手写「六吉六颗…两两不成对宫（不是成对相拱）」「吉星无法配对」，
# 并据此推出「化解力量不成体系」。而盘面事实恰恰相反：
#   左辅@辰、文昌@辰（同宫）；右弼@戌、文曲@戌（同宫）；辰戌互为对宫；
#   天魁@丑、天钺@未互为对宫。
# 也就是说，**「成不成对、同不同宫、对不对拱」是纯算术**，不该由 AI 手写。
# 这里一次算清，渲染器印成表，正文只许引用；审查器按同一份事实逐句核对。
#
# 判据（术语口径，写死在脚本里，避免各处各说）：
#   · 「成对」= 两颗**同宫**或**互为对宫（对拱）**；
#   · 三合遥会单列为「三合」，**不算「成对相拱」**；
#   · 既不同宫也不对拱三合 → 「分居」。
PAIR_SPECS: list[tuple[str, str, str, str]] = [
    ("六吉", "左辅", "右弼", "辅弼"),
    ("六吉", "文昌", "文曲", "昌曲"),
    ("六吉", "天魁", "天钺", "魁钺"),
    ("六煞", "擎羊", "陀罗", "羊陀"),
    ("六煞", "火星", "铃星", "火铃"),
    ("六煞", "地空", "地劫", "空劫"),
]


def _star_places(palaces: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """星名 → {branch, palace, brightness}。同名多落点只取首个（盘面不会重复）。"""
    out: dict[str, dict[str, str]] = {}
    for p in palaces:
        for key in ("major", "minor"):
            for s in p.get(key) or []:
                nm = s["name"] if isinstance(s, dict) else s
                out.setdefault(nm, {
                    "branch": p["branch"],
                    "palace": p["name"],
                    "brightness": (s.get("brightness") if isinstance(s, dict) else None),
                })
    return out


def pairing_facts(palaces: list[dict[str, Any]], soul_branch: str) -> dict[str, Any]:
    """
    六吉六煞的**分布与配对事实**：每颗落在哪一宫、哪些同宫、哪些互为对宫（对拱）、
    哪些落在命宫三方四正内。全部由脚本算，AI 正文只能引用，不得自行断言成对/不成对。
    """
    places = _star_places(palaces)
    surround = set(trine_of(soul_branch)) | {opposite_of(soul_branch)}

    pairs: list[dict[str, Any]] = []
    for group, s1, s2, abbr in PAIR_SPECS:
        p1, p2 = places.get(s1), places.get(s2)
        b1, b2 = (p1 or {}).get("branch"), (p2 or {}).get("branch")
        entry: dict[str, Any] = {
            "group": group, "abbr": abbr, "star1": s1, "star2": s2,
            "branch1": b1, "branch2": b2,
            "palace1": (p1 or {}).get("palace"), "palace2": (p2 or {}).get("palace"),
            "brightness1": (p1 or {}).get("brightness"),
            "brightness2": (p2 or {}).get("brightness"),
        }
        if not b1 or not b2:
            entry.update({"samePalace": False, "opposite": False, "trine": False,
                          "relation": "缺（有星不在盘面）", "paired": False,
                          "inSurround": [], "bothInSurround": False})
        else:
            same = b1 == b2
            opp = opposite_of(b1) == b2
            tri = b2 in trine_of(b1)
            entry.update({
                "samePalace": same,
                "opposite": opp,
                "trine": tri,
                "relation": ("同宫" if same else "对拱" if opp else "三合遥会" if tri else "分居"),
                "paired": bool(same or opp),
                "inSurround": [s for s, b in ((s1, b1), (s2, b2)) if b in surround],
                "bothInSurround": bool(b1 in surround and b2 in surround),
            })
        pairs.append(entry)

    def group_summary(group: str) -> dict[str, Any]:
        ps = [x for x in pairs if x["group"] == group]
        in_sur: list[str] = []
        stars = []
        for group_name, s1, s2, abbr in PAIR_SPECS:
            if group_name != group:
                continue
            for s in (s1, s2):
                pl = places.get(s)
                stars.append({
                    "star": s, "branch": (pl or {}).get("branch"),
                    "palace": (pl or {}).get("palace"),
                    "inSurround": bool(pl and pl["branch"] in surround),
                })
                if pl and pl["branch"] in surround:
                    in_sur.append(s)
        return {
            "pairs": ps,
            "stars": stars,
            "allPresent": all(x["branch1"] and x["branch2"] for x in ps),
            "totalPairs": len(ps),
            "pairedCount": sum(1 for x in ps if x["paired"]),
            "samePalaceCount": sum(1 for x in ps if x["samePalace"]),
            "oppositeCount": sum(1 for x in ps if x["opposite"]),
            "trineCount": sum(1 for x in ps if x["trine"] and not x["paired"]),
            "inSurround": in_sur,
            "surroundCount": len(in_sur),
        }

    return {
        "soulBranch": soul_branch,
        "surroundBranches": [soul_branch, opposite_of(soul_branch)]
                            + [b for b in trine_of(soul_branch) if b != soul_branch],
        "pairs": pairs,
        "soft": group_summary("六吉"),
        "tough": group_summary("六煞"),
        "places": places,
    }


# ---------- 十二宫名映射（本命 / 大限同一规则，但**方向随盘而变**） ----------
def decadal_direction(year_stem: str, gender: str) -> int:
    """
    大限行向：**阳男阴女顺行（+1）／阴男阳女逆行（-1）**。

    口径见 `references/glossary.md`「大限方向」软件通行口径。
    该方向是「生年干阴阳 × 性别」的函数，不是常数——旧版把它写死成逆行，
    于是所有阳男／阴女盘的大限十二宫映射整体错位（第七轮脚本疑似失效 1）。
    """
    yang = year_stem in _YANG_STEMS
    forward = (gender == "男" and yang) or (gender == "女" and not yang)
    return 1 if forward else -1


def palace_name_map(soul_branch: str) -> dict[str, str]:
    """
    「大限命宫地支」→ 十二宫名到地支的映射（宫名 → 地支）。

    规则：大限十二宫名与本命**同向排布**——宫名按 `PALACE_ORDER` 逐位**逆行**，
    **与阳男阴女的行向无关**。行向（`decadal_direction`）只决定「哪一支是大限命宫」，
    不改变限内宫名的排法。

    依据（本地实测 iztro 2.6.2 `horoscope().decadal.palaceNames`，4 张盘 × 2 个限）：
      · 1990-06-07 男（阳男，限命戌 23–32）→ 戌命 / 酉兄弟 / 申夫妻 / 未子女 / 午财帛 /
        巳疾厄 / 辰迁移 / 卯仆役 / 寅官禄 / 丑田宅 / 子福德 / 亥父母
      · 2000-04-22 女（阳女，限命午 3–12）→ 午命 / 巳兄弟 / 辰夫妻 / 卯子女 / 寅财帛 /
        丑疾厄 / 子迁移 / 亥仆役 / 戌官禄 / 酉田宅 / 申福德 / 未父母
    两例都是**逆行**排布；把行向套到这张表上（顺行盘改成正排）是把对改成错，
    故本函数**不再接受 direction 参数**。
    """
    i = BR12.index(soul_branch)
    return {nm: BR12[(i - k) % 12] for k, nm in enumerate(PALACE_ORDER)}


def palace_branch_map(soul_branch: str) -> dict[str, str]:
    """上式的反向表：地支 → 宫名。"""
    return {v: k for k, v in palace_name_map(soul_branch).items()}


def relations_of(palaces: list[dict[str, Any]], branch: str) -> dict[str, Any]:
    """
    任意一宫的**完整相对关系**：对宫、三合两宫、左右两邻，以及三方四正四支。

    为什么要做成结构化字段：报告里「对宫 X」「X 在 Y 三合」「X、Y 分居 Z 两邻」
    这类话，第三轮之前全靠 AI 手写，实测必错（把三合宫写成对宫、把同宫写成夹）。
    现在关系表由脚本产出并渲染进报告，AI 只能引用，不能自算。
    """
    by_branch = {p["branch"]: p for p in palaces}
    i = BR12.index(branch)
    left_b, right_b = BR12[(i - 1) % 12], BR12[(i + 1) % 12]
    opp_b = opposite_of(branch)
    tri = trine_of(branch)

    def brief(b: str) -> dict[str, Any]:
        p = by_branch.get(b) or {}
        return {"branch": b, "palace": p.get("name"), "stem": p.get("stem")}

    return {
        "branch": branch,
        "palace": (by_branch.get(branch) or {}).get("name"),
        "opposite": brief(opp_b),
        # 三合三宫含本宫，这里只给另外两宫
        "trine": [brief(tri[1]), brief(tri[2])],
        "left": brief(left_b),
        "right": brief(right_b),
        "surroundBranches": [branch, opp_b, tri[1], tri[2]],
    }


# ---------- 岁数表 ----------
_SOUL_START = {  # 小限起点：生年支三合局 → 起宫
    "寅": "辰", "午": "辰", "戌": "辰",
    "申": "戌", "子": "戌", "辰": "戌",
    "巳": "未", "酉": "未", "丑": "未",
    "亥": "丑", "卯": "丑", "未": "丑",
}


# ---------- 流年辅星（流禄 / 流羊 / 流陀 / 流马） ----------
# 第七轮新增：报告要写「某年流羊落本命命宫」这类事实，就必须由脚本给，不能手算。
# 出处是通行的流年安星法：
#   流年禄存：由**流年天干**定（起法与本命禄存同表）
#   流年擎羊：流年禄存**前一宫**；流年陀罗：流年禄存**后一宫**
#     （禄前擎羊、禄后陀罗——与本命羊陀同一规则，故不必另立表）
#   流年天马：由**流年地支**三合局定（寅午戌→申、申子辰→寅、巳酉丑→亥、亥卯未→巳）
_LU_BY_STEM = {
    "甲": "寅", "乙": "卯", "丙": "巳", "丁": "午", "戊": "巳",
    "己": "午", "庚": "申", "辛": "酉", "壬": "亥", "癸": "子",
}
_MA_BY_BRANCH = {
    "寅": "申", "午": "申", "戌": "申",
    "申": "寅", "子": "寅", "辰": "寅",
    "巳": "亥", "酉": "亥", "丑": "亥",
    "亥": "巳", "卯": "巳", "未": "巳",
}


def yearly_aux_stars(year_stem: str, year_branch: str) -> dict[str, str]:
    """某一流年的 流禄／流羊／流陀／流马 落在哪个地支。"""
    lu = _LU_BY_STEM.get(year_stem)
    if not lu:
        return {}
    i = BR12.index(lu)
    return {
        "流禄": lu,
        "流羊": BR12[(i + 1) % 12],
        "流陀": BR12[(i - 1) % 12],
        "流马": _MA_BY_BRANCH.get(year_branch, ""),
    }


def soul_limit_ages(branch: str, year_branch: str, gender: str) -> list[int]:
    """小限：男顺女逆，起点由生年支三合局定。"""
    start = BR12.index(_SOUL_START.get(year_branch, "辰"))
    direction = 1 if gender == "男" else -1
    return [
        age for age in range(1, 121)
        if BR12[(start + direction * (age - 1)) % 12] == branch
    ]


def yearly_ages(branch: str, year_branch: str) -> list[int]:
    """流年命宫：自生年支起，顺行一宫一年。"""
    start = BR12.index(year_branch)
    return [age for age in range(1, 121) if BR12[(start + age - 1) % 12] == branch]


# ---------- 格局候选（只判条件，不下断语） ----------
def _major_at(palaces: list[dict[str, Any]]) -> dict[str, list[str]]:
    return {p["branch"]: [s["name"] for s in (p.get("major") or [])] for p in palaces}


def _stars_at(palaces: list[dict[str, Any]], key: str) -> dict[str, list[str]]:
    return {p["branch"]: [s["name"] for s in (p.get(key) or [])] for p in palaces}


def _in_trine(target: str, branch: str) -> bool:
    return branch in trine_of(target)


def pattern_candidates(palaces: list[dict[str, Any]], soul_branch: str) -> list[dict[str, Any]]:
    """
    常见格局的**条件判定**（可计算的那一半）。

    这里只回答「古法所说的构成条件是否满足」，**不回答「此格是否成立、如何断」**——
    那要按全盘裁断（亮度、辅煞、四化、层次），属 AI 的活。
    因此产物里 `satisfied` 一律为 None，只给 conditions_met 供 AI 复核。
    """
    maj = _major_at(palaces)
    mino = _stars_at(palaces, "minor")
    all_names = {p["branch"]: set(maj.get(p["branch"], []) + mino.get(p["branch"], [])) for p in palaces}

    def where_major(name: str) -> list[str]:
        return [b for b, lst in maj.items() if name in lst]

    def where_any(name: str) -> list[str]:
        return [b for b, s in all_names.items() if name in s]

    out: list[dict[str, Any]] = []

    def add(name: str, ok: bool, evidence: str, display: str | None = None,
            adjudicate: bool = True) -> None:
        out.append({
            "pattern": name,                      # 机器键：审查器按此名匹配
            "display_name": display or name,      # 报告显示名：可带限定语
            "conditions_met": bool(ok),
            "evidence": evidence,
            "satisfied": None,          # 由 AI 裁断
            "break_save": None,         # 由 AI 填
            "needs_adjudication": adjudicate,
        })

    # 杀破狼（三星分处三合）
    spn = [where_major(n) for n in ("七杀", "破军", "贪狼")]
    ok = (all(spn) and len({b for lst in spn for b in lst}) == 3
          and all(any(_in_trine(spn[0][0], b) for b in lst) for lst in spn))
    add("杀破狼", ok, f"七杀@{spn[0]} 破军@{spn[1]} 贪狼@{spn[2]}")

    # 日月
    sun, moon = where_major("太阳"), where_major("太阴")
    add("日月同宫", bool(sun and moon and sun[0] == moon[0]), f"太阳@{sun} 太阴@{moon}")
    add("日月反背",
        bool(sun and moon and BR12.index(sun[0]) == (BR12.index(moon[0]) + 6) % 12),
        f"太阳@{sun} 太阴@{moon}（相对而居；是否成格须再查双方亮度）")

    # 火贪 / 铃贪
    tan = where_major("贪狼")
    for star, label in (("火星", "火贪格"), ("铃星", "铃贪格")):
        hb = where_any(star)
        same = bool(tan and hb and any(b == tan[0] for b in hb))
        tri = bool(tan and hb and any(_in_trine(tan[0], b) for b in hb))
        add(label, same or tri,
            f"贪狼@{tan} {star}@{hb}"
            + ("（同宫）" if same else "（三合遥会，力量次之）" if tri else ""))

    # 机月同梁
    tri_set = set(trine_of(soul_branch)) | {soul_branch, opposite_of(soul_branch)}
    found = {n: any(n in maj.get(b, []) for b in tri_set) for n in ("天机", "太阴", "天同", "天梁")}
    add("机月同梁", all(found.values()),
        f"命宫{soul_branch}三方四正内 " + "、".join(f"{k}{'见' if v else '未见'}" for k, v in found.items()))

    # 紫府
    zi, fu, xiang = where_major("紫微"), where_major("天府"), where_major("天相")
    add("紫府同宫", bool(zi and fu and zi[0] == fu[0]), f"紫微@{zi} 天府@{fu}")

    # 夹宫两邻（夹宫类格局与府相朝垣的「夹命」分支都要用，先算）
    fl = flanking(palaces, soul_branch)
    lb = fl["left"]["branch"] if fl.get("left") else None
    rb = fl["right"]["branch"] if fl.get("right") else None

    # 府相朝垣：条件看的是**天府、天相**是否进入命宫三方四正（或夹命宫），
    # 与紫微落在哪一宫无关——旧版写成「紫微需子或午」，那是紫府同宫/紫微朝垣的条件，
    # 挂在府相朝垣这一行属依据用错（审查已指出）。
    fu_in = bool(fu) and fu[0] in tri_set
    xiang_in = bool(xiang) and xiang[0] in tri_set
    fu_flank = bool(fu) and fu[0] in (lb, rb)
    xiang_flank = bool(xiang) and xiang[0] in (lb, rb)
    add("府相朝垣", bool((fu_in or fu_flank) and (xiang_in or xiang_flank)),
        f"天府@{fu} 天相@{xiang}；命宫{soul_branch}三方四正={'/'.join(sorted(tri_set))}"
        f"；命宫两邻={lb}/{rb}")

    # 火铃夹命：火星与铃星分居命宫两邻
    hb_huo, hb_ling = where_any("火星"), where_any("铃星")
    add("火铃夹命",
        bool(hb_huo and hb_ling and {hb_huo[0], hb_ling[0]} == {lb, rb}),
        f"火星@{hb_huo} 铃星@{hb_ling}；命宫两邻={lb}/{rb}")
    for stars, label in ((("天魁", "天钺"), "魁钺夹命"),
                         (("文昌", "文曲"), "昌曲夹命"),
                         (("擎羊", "陀罗"), "羊陀夹命"),
                         (("地空", "地劫"), "空劫夹命")):
        s1, s2 = stars
        b1, b2 = where_any(s1), where_any(s2)
        ok = bool(b1 and b2 and {b1[0], b2[0]} == {lb, rb})
        add(label, ok, f"{s1}@{b1} {s2}@{b2}；命宫两邻={lb}/{rb}")

    # 禄马交驰
    lu, ma = where_any("禄存"), where_any("天马")
    add("禄马交驰", bool(lu and ma and any(l == m or _in_trine(l, m) for l in lu for m in ma)),
        f"禄存@{lu} 天马@{ma}")

    # 刑忌夹印：印星（天相）两侧一为刑（擎羊）、一为忌。
    #
    # 第五轮修正（MAJOR 3「何种忌计入夹」标准不一致）：
    #   旧版把「自化忌是否算忌」当成本格唯一的争点，于是在羊陀夹禄忌单方采信飞忌的同时，
    #   本格却因此列「存疑／并列」——同一份报告对同一个问题用了两套口径，属双标。
    #   现在**统一口径**（见 references/interpretation.md §3.1）：
    #     「夹」类格局的「忌」一律计入全部化忌（生年忌／离心自化忌／向心飞化忌）。
    #   在这个统一口径下，本盘「忌」的条件按脚本判定是否满足；
    #   本格若仍须并列，理由换成一个**真正独立**的分歧：
    #     刑忌夹印传统以「命宫天相被刑、忌两邻所夹」为准，
    #     是否放宽到任意宫位的印星被夹，各派不一。本盘天相在未·兄弟宫（非命宫），
    #     故该分歧对本盘有实际影响——脚本只摆落点，不替流派下结论。
    xiang_b = xiang[0] if xiang else None
    if xiang_b:
        i3 = BR12.index(xiang_b)
        nb_a, nb_b = BR12[(i3 - 1) % 12], BR12[(i3 + 1) % 12]
        selfs = self_mutagen(palaces)
        nbrs = []
        xing_sides: list[str] = []
        ji_sides: list[str] = []
        for nb in (nb_a, nb_b):
            pal = next((x for x in palaces if x["branch"] == nb), None)
            if not pal:
                continue
            names = [s["name"] for s in (pal.get("major") or []) + (pal.get("minor") or [])]
            has_xing = "擎羊" in names
            has_birth_ji = any(
                isinstance(s, dict) and s.get("mutagen") == "忌"
                for s in (pal.get("major") or []) + (pal.get("minor") or [])
            )
            side_self = [m for m in selfs if m["branch"] == nb and m["type"] == "忌"]
            has_ji = bool(has_birth_ji or side_self)
            if has_xing:
                xing_sides.append(nb)
            if has_ji:
                ji_sides.append(nb)
            marks = []
            if has_xing:
                marks.append("擎羊＝刑")
            if has_birth_ji:
                marks.append("生年忌")
            for m in side_self:
                marks.append(f"{m['star']}{m['direction']}化忌")
            nbrs.append(f"{nb}宫（{'、'.join(names) or '空宫'}"
                        + (f"；{'、'.join(marks)}" if marks else "") + "）")
        # 刑与忌须分处两邻才算夹（同在一邻不构成「夹」）
        ji_ok = bool(xing_sides and ji_sides) and not (
            len(xing_sides) == 1 and xing_sides == ji_sides)
        add("刑忌夹印", False,
            f"天相@{[xiang_b]}（本命"
            f"{next((p['name'] for p in palaces if p['branch'] == xiang_b), '')}宫），"
            f"两邻={'／'.join(nbrs)}。"
            # 第六轮：本条曾出现「字段与文本互相矛盾」——conditions_met=false，
            # 而同一行 evidence 写「刑与忌分处两邻的条件已满足」。查明结论：
            #   **字段是对的，文本用词含混。** 本条的 conditions_met 记录的是该格
            #   「完整古法条件」（传统以「命宫天相被刑、忌两邻所夹」为准）是否满足；
            #   而「刑与忌分处两邻」只是完整条件里的**落点子条件**。
            #   本盘天相在兄弟宫、不在命宫，故按该派的完整条件不满足（false 正确）；
            #   落点子条件则确实满足。两者不矛盾，但必须把子条件与完整条件分开写，
            #   否则读者（与审查器）看到的就是「字段说否、文本说是」。
            f"按统一口径（凡化忌——生年／离心自化／向心飞化——皆计入夹），"
            f"本格的落点子条件（刑与忌分处两邻）{'已满足' if ji_ok else '未满足'}；"
            f"而本条的构成条件判定取的是「完整古法条件」——该格传统以「命宫天相被刑、忌两邻所夹」"
            f"为准，本盘天相在"
            f"{next((p['name'] for p in palaces if p['branch'] == xiang_b), '')}宫"
            f"{'（确为命宫）' if xiang_b == soul_branch else '（不在命宫）'}"
            f"，故按该派的完整条件{'满足' if xiang_b == soul_branch else '不满足'}。"
            "是否放宽到任意宫位的印星被夹，各派不一；两说对本盘有实际差别，"
            "故此处只摆落点，不替流派下结论",
            display="刑忌夹印（存疑条目）", adjudicate=False)
    else:
        add("刑忌夹印", False, "盘面无天相，不适用", display="刑忌夹印（存疑条目）",
            adjudicate=False)

    # 双禄交流（禄存 + 化禄 同宫或三合）
    #
    # 第五轮修正（MAJOR 4）：旧版只给落点，未交代「交流」的判定口径——
    # 报告于是照抄落点直接标成立，与该报告对夹宫类「同宫不算夹」的严格度不一致。
    # 现在把口径写进 evidence：**本脚本按「同宫或三合」计入（同宫即算交流）**；
    # 若采「要求分居相会」的更严口径，须注明并重判（详见 astro.pattern_candidates 的 evidence）。口径本身在 references/interpretation.md §3.1 固化。
    hl = [p["branch"] for p in palaces
          if any(s.get("mutagen") == "禄" for s in (p.get("major") or []) + (p.get("minor") or []))]
    add("双禄交流", bool(lu and hl and any(l == h or _in_trine(l, h) for l in lu for h in hl)),
        f"禄存@{lu} 化禄@{hl}；本报告口径：禄存与化禄同宫或三合皆计入（同宫即算「交流」）；"
        "若采「要求分居相会」之口径，结论需重判")

    # 羊陀夹禄忌：被夹之宫须同时坐禄存与「忌」（生年忌或向心化忌），
    # 且擎羊、陀罗正好分居其两邻——即「禄与忌同宫、外受羊陀所夹」。
    # 第二轮盲审指出：报告在定盘自检里提过「禄存与擎羊陀罗相邻」，
    # 但格局表全表未提此结构，属漏项。
    # 注意：这里刻意不登记「只要有禄存就算成立」的裸羊陀夹——禄存与擎羊陀罗的
    # 位置关系是固定的（禄前擎羊、禄后陀罗），裸格恒真、无鉴别力，列进表只会灌水。
    lu_ji_branches = [
        p["branch"] for p in palaces
        if any((s["name"] if isinstance(s, dict) else s) == "禄存"
               for s in (p.get("minor") or []))
        and any(s.get("mutagen") == "忌"
                for s in (p.get("major") or []) + (p.get("minor") or []))
    ]
    # 向心化忌（对宫宫干化忌入本宫）同样计入「忌」
    for m in self_mutagen(palaces):
        if m["type"] == "忌" and m["branch"] not in lu_ji_branches:
            pb = m["branch"]
            has_lu = any(
                any((s["name"] if isinstance(s, dict) else s) == "禄存"
                    for s in (p.get("minor") or []))
                for p in palaces if p["branch"] == pb
            )
            if has_lu:
                lu_ji_branches.append(pb)
    hit = False
    # 口径声明（第五轮统一，避免与刑忌夹印条双标）：
    #   「夹」类格局的「忌」一律计入全部化忌——生年忌／离心自化忌／向心飞化忌。
    #   本格据此计入向心飞忌。另须说明：**「羊陀夹禄」本身是禄存位置的结构性必然**
    #   （禄前擎羊、禄后陀罗，禄存落在哪一宫，羊陀就必在其两邻），
    #   裸格恒真、无鉴别力；本格的鉴别力全部来自「禄存与忌同宫」这一半。
    LU_JI_VERDICT = ("本格口径：凡化忌（生年／离心自化／向心飞化）皆计入夹。"
                     "另一点：「羊陀夹禄」是禄存位置的结构性必然（禄前擎羊、禄后陀罗），"
                     "单独看无鉴别力；本格的鉴别力来自「禄存与忌同宫」这一半。")
    for lu_b in lu_ji_branches:
        ok, ev = flanked_stars(palaces, lu_b, ("擎羊", "陀罗"))
        if ok:
            add("羊陀夹禄忌", True, f"禄存与忌同在{lu_b}宫；{ev}。{LU_JI_VERDICT}")
            hit = True
            break
    if not hit:
        add("羊陀夹禄忌", False,
            f"禄存与忌同宫之宫={lu_ji_branches or '无'}；未取得羊陀分居两邻的落点。{LU_JI_VERDICT}")

    return out


# ---------- 格式校验 ----------
def validate_palaces(palaces: list[dict[str, Any]]) -> list[str]:
    """机器自检：抓整盘级排盘错误（不能替代往事定盘）。"""
    problems: list[str] = []
    names = [p["name"] for p in palaces]
    if len(names) != 12:
        problems.append(f"宫位数不是 12（实为 {len(names)}）")
    if len(set(names)) != len(names):
        dup = [n for n in set(names) if names.count(n) > 1]
        problems.append(f"宫名重复：{dup}")

    all14 = ["紫微", "天机", "太阳", "武曲", "天同", "廉贞",
             "天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"]
    found = []
    for p in palaces:
        for s in p.get("major") or []:
            nm = s["name"] if isinstance(s, dict) else s
            if nm in all14:
                found.append(nm)
    if sorted(found) != sorted(all14):
        missing = set(all14) - set(found)
        extra = [x for x in found if found.count(x) > 1]
        problems.append(f"十四主星不齐：缺 {sorted(missing)}，重复 {sorted(set(extra))}")

    # 禄存与羊陀相邻
    lu = next((p["branch"] for p in palaces
               if any((s["name"] if isinstance(s, dict) else s) == "禄存"
                      for s in (p.get("minor") or []))), None)
    if lu:
        i = BR12.index(lu)
        prev_b, next_b = BR12[(i - 1) % 12], BR12[(i + 1) % 12]
        def has(pb, nm):
            p = next((x for x in palaces if x["branch"] == pb), None)
            return bool(p) and any(
                (s["name"] if isinstance(s, dict) else s) == nm
                for k in ("major", "minor") for s in (p.get(k) or [])
            )
        if not (has(prev_b, "擎羊") or has(next_b, "擎羊")):
            problems.append("未找到擎羊与禄存相邻（排盘可能有误）")
    return problems
