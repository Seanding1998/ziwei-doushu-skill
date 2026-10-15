#!/usr/bin/env python3
"""
ziwei-doushu / scripts/audit_report.py — 报告结构一致性审查器

**为什么需要它**：生成器的 `--validate` 查的是「字段填了没、语言干不干净」，
但查不出**结论与盘面不符**——例如三星（七杀破军贪狼）虽构成三合，却**不在命宫三方四正内**，
严格说不宜称「杀破狼命」，把这三个字摆进格局表标「成立」就会误导读者。
这类错误是可以机械检出的，不该留给人眼。

**第二轮盲审的教训（本版新增检测器的由来）**：上一轮只改了文字，同类错误又出现——
「对宫左辅」（左辅其实与文昌同宫）、「两宫都不在命宫三方四正之内」（辰正是命宫三合）、
「禄被空劫夹」（空劫与禄同宫，不是夹）、格局表填「不成立」而正文说「存疑并列」。
这些都**能落到盘面事实上**，所以本轮把它们做成机器可抓的检测器：

    星曜错位措辞   STAR_MISATTRIBUTED      「对宫 X」「X 与 Y 同宫」「X 与 Y 分居」与落宫不符
    三方四正误判   SURROUND_MISJUDGED      「X 不在三方四正」但盘面在内（或反之）
    夹宫措辞       FLANK_MISJUDGED         「A 夹 B」但 A 的两颗并不同居 B 的两邻（同宫不算夹）
    亮度编造       BRIGHTNESS_FABRICATED   盘面 brightness 为 null 的星被安上庙/旺/得/利/平/不/陷
    格局表 vs 正文 PATTERN_VERDICT_CONFLICT 表列「不成立」而该行正文称存疑并列（或反之）
    计数不一致     COUNT_MISMATCH           「N 项格局」与 patternCandidates 长度不符

**第五轮新增（同类问题第二次出现即代码化）**：

    配对断言错     AUSPICIOUS_PAIRING_MISSTATED
        「六吉／六煞／辅弼／昌曲／魁钺／羊陀／火铃／空劫」被说成
        成对／不成对／同宫／对拱，与 `chart.pairingFacts` 不符。
        第五轮 BLOCKER 即此类：报告四处手写「六吉两两不成对宫」，而盘面三对俱成对拱。
        修法不是改对那句话，而是让配对事实由脚本产出（§二 六吉六煞分布与配对表），正文只许引用。

**第六轮新增（判断层的「尺」也要机器核）**：

    效力层级错     EFFICACY_LEVEL_MISSTATED
        「X宫……属〈同宫／两邻／三合／对宫／回照／遥会〉」与该宫相对命宫的**实际层级**不符；
        或把「效力次一等／次之」用在第 1～2 级（同宫／两邻）的对象上。
        第六轮两条 BLOCKER 的共同根因即此类：同一颗星作煞时算贴身、作吉时算「次一等」。
        尺见 references/interpretation.md §3.2：
        1 同宫（贴身）＞ 2 两邻（夹）＞ 3 三合会照 ＞ 4 对宫照 ＞ 5 遥会。
    破格/救格串味  PATTERN_BREAKSAVE_MISLABELED
        带标签的两段式 `break_save` 里，「破格」栏的正文写了救格的事（救格／救应／折损），
        或「救格／折损」栏的正文写了破格的事。概念误用早在 interpretation.md §4 有明文，
        本轮补上机器判据。

本脚本独立于生成器：**只读 chart 与 report HTML，重新核对结构与措辞的一致性**，
不依赖 AI 的自我声明。退出码 0 = 通过；1 = 有 ERROR；2 = 参数/文件错误。

用法：
    python audit_report.py --chart chart.json --report report.html
    python audit_report.py --chart chart.json --report report.html --json
    python audit_report.py --chart chart.json --analysis analysis.json   # 审 analysis 原文
"""

from __future__ import annotations

import argparse
import html as html_mod
import json
import re
import sys
from pathlib import Path

# ---------- 常量 ----------
BR12 = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
ALL14 = ["紫微", "天机", "太阳", "武曲", "天同", "廉贞", "天府", "太阴",
         "贪狼", "巨门", "天相", "天梁", "七杀", "破军"]
ALL_PALACES = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
               "迁移", "仆役", "交友", "官禄", "田宅", "福德", "父母"]
POSITIVE = {"庙", "旺", "得", "利"}
FIVE_CLASSES = ["水二局", "木三局", "金四局", "土五局", "火六局"]
START_AGE = {"水二局": 2, "木三局": 3, "金四局": 4, "土五局": 5, "火六局": 6}

# 命宫三方四正之外的「大格」名：出现在格局表时，必须显式声明不主导本命
BIG_PATTERNS = ["杀破狼", "紫府朝垣", "府相朝垣", "百官朝拱", "君臣庆会", "火贪格", "铃贪格"]
# 必须落在命宫三方四正才成立的格
MUST_BE_IN_SOUL_SURROUND = {
    "机月同梁", "日月同宫",
}
# 必须**分居命宫两邻**（夹宫）才成立的格。它们不该用三方四正判，
# 而要用夹宫判——星曜本来就在命宫两邻，不在命宫三方四正内，用三方四正判必假阳性。
FLANK_PATTERNS = {
    "魁钺夹命", "昌曲夹命", "羊陀夹命", "空劫夹命", "火铃夹命", "刑忌夹印",
}

# ---------- 新增检测器用到的常量 ----------
# 亮度词，只取**不会跟成别的词**的字面量。
#   「庙/旺/利/平/陷」是亮度专用字；
#   「不得地/不得」是完整的否定亮度词。
# 刻意不收孤立的多义字：「得」在「得贵人／得力／得到」里是动词而非亮度，
# 把它收进来会对「天魁得地」以外的正常句子误报（本轮实测到一次假阳性）。
BRIGHT_WORDS = ["不得地", "不得", "庙", "旺", "利", "平", "陷"]
# 跟在亮度词后、说明它属于另一个词组的字——命中就丢弃这次匹配
BRIGHT_TAIL = set("地级位曜主")
# 窄义亮度：只会作亮度用，不会跟成「不得地」这类词
BRIGHT_NARROW = {"庙", "旺", "利", "平", "陷"}

# 夹宫类简称 → 两邻的两颗星（术语「空劫夹」即 地空 + 地劫）
FLANK_ABBR = {
    "空劫": ("地空", "地劫"),
    "羊陀": ("擎羊", "陀罗"),
    "火铃": ("火星", "铃星"),
    "魁钺": ("天魁", "天钺"),
    "昌曲": ("文昌", "文曲"),
    "辅弼": ("左辅", "右弼"),
}

# 格局表结论列的措辞
VERDICT_ESTABLISHED = ("成立", "成格", "成立（")
VERDICT_DENIED = ("不成立", "不成格", "不成立（")
# 正文里的「并列/存疑」措辞——出现即表示该格不宜单方断言
DISPUTED_MARKS = ("存疑", "并列", "两种口径", "各派口径", "不下单方", "不下断言",
                  "流派分歧", "两说")
# 正文里的**单方断言**措辞——结论列已写「存疑／并列」时，出现这些即自相矛盾。
# ⚠️ 只能收「裁定式」说法，不能收光秃秃的「不成格」：
# 「若采某派口径则不成格」是在陈述两派差别，正是并列该说的话（本轮实测到假阳性）。
# 引号与格名允许夹在中间（「应标为『刑忌夹印』不成立」），故用正则而非字面量。
UNIVOCAL_VERDICTS = (
    r"应标为[^\n]{0,12}?(?:不成立|不成格)",
    r"应判为[^\n]{0,12}?(?:不成立|不成格)",
    r"本报告裁为[^\n]{0,12}?(?:不成立|不成格)",
)
# 「两说并列」类措辞，必须与这些词**相邻**才算「并列两派口径」；
# 单说「两股力量并列」不是在讲流派分歧，不能据此判矛盾。
SCHOOL_MARKS = ("派", "口径", "流派", "存疑", "两说")


def star_index(chart: dict) -> dict[str, str]:
    """星名 → 地支落宫。主星与辅星一起收；同名多落点只取首个（盘面不会重复）。"""
    idx: dict[str, str] = {}
    for p in chart["palaces"]:
        for key in ("major", "minor"):
            for s in (p.get(key) or []):
                idx.setdefault(s["name"], p["branch"])
    return idx


def brightness_map(chart: dict) -> dict[str, str | None]:
    """星名 → brightness 原值（可能是 None）。None 表示盘面没有该星的庙旺值。"""
    out: dict[str, str | None] = {}
    for p in chart["palaces"]:
        for key in ("major", "minor"):
            for s in (p.get(key) or []):
                out.setdefault(s["name"], s.get("brightness"))
    return out


def all_star_names(chart: dict) -> list[str]:
    """盘面出现的全部星名，按长度降序——构造正则时长的在前，避免被短名前缀截断。"""
    names = set(star_index(chart))
    return sorted(names, key=len, reverse=True)


def palace_of(chart: dict, branch: str) -> str:
    for p in chart["palaces"]:
        if p["branch"] == branch:
            return p["name"]
    return ""


def relation_inside(phrase: str) -> bool | None:
    """
    判断一句涉及「三方四正 / 两邻」的话**声称**这些星在集合内还是在集合外。

    返回 True=声称在内，False=声称在外，None=判不出（宁可不判，也不误报）。

    ⚠️ 第四轮修正：旧版按「句尾限定词优先，句中『不在』忽略」处理，于是
      「X 在…之内」（正确）与「X 在…之外」（正确）**两种正确写法都被判成矛盾**，
      只有「X 不在…之内」这一种写法侥幸对。旧夹具恰好只用了那一种，所以一直没暴露。
      汉语里句尾限定词给的是**集合**（之内＝内集，之外＝外集），句中的「不在」是
      **取反**——两者必须相乘，不能只取其一：
        在…之内 → 内 ； 不在…之内 → 外
        在…之外 → 外 ； 不在…之外 → 内（双重否定）
      没有句尾限定词时，才按句中的「不在 / 在」判。
    """
    if phrase.endswith("之外") or phrase.endswith("以外"):
        base: bool | None = False
    elif phrase.endswith("之内") or phrase.endswith("以内"):
        base = True
    else:
        base = None
    neg = ("不在" in phrase) or ("均不在" in phrase) or ("皆不在" in phrase) \
        or ("都不在" in phrase)
    if base is None:
        if neg:
            return False
        return True if "在" in phrase else None
    return (not base) if neg else base


# 排查开关：置 True 时把第 7 组检测器的中间量打到 stdout（默认关，零开销）。
# 留着的理由：这一组正则的假阴性极难发现（规则不报错 ≠ 规则在工作），
# 下一轮若再怀疑某条规则「没生效」，先开这个开关看中间量，别再靠猜。
_TRACE = False


class Finding:
    def __init__(self, level: str, code: str, msg: str, detail: str = "") -> None:
        self.level = level      # BLOCKER / ERROR / WARN
        self.code = code
        self.msg = msg
        self.detail = detail

    def as_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.msg, "detail": self.detail}


def html_to_text(markup: str) -> str:
    s = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    s = re.sub(r"<style\b.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return html_mod.unescape(s)


def audit(chart: dict, analysis: dict | None, report_markup: str | None) -> list[Finding]:
    F: list[Finding] = []
    b = chart["basic"]
    name_of = {p["branch"]: p["name"] for p in chart["palaces"]}

    soul = b["soulPalaceBranch"]
    i = BR12.index(soul)
    surround_branches = {BR12[i], BR12[(i + 4) % 12], BR12[(i + 8) % 12], BR12[(i + 6) % 12]}

    markup = report_markup or ""
    text = html_to_text(markup) if report_markup is not None else ""

    # ── 1 星曜落宫核对：报告若说「某星在某宫」，必须与盘一致 ──
    actual: dict[str, str] = {}
    for p in chart["palaces"]:
        for s in (p.get("major") or []) + (p.get("minor") or []):
            actual.setdefault(s["name"], p["branch"])
    other_stars = "|".join(["左辅", "右弼", "天魁", "天钺", "文昌", "文曲", "禄存", "天马",
                            "擎羊", "陀罗", "火星", "铃星", "地空", "地劫"])
    # 宫名首字，用来把「X在Y宫」里的地支 Y 与宫名区分开
    PALACE_FIRST = "命兄夫子财疾迁仆交官田福父"
    # 必须同时抓住：
    #   「天机在子」            → 子后有「宫」，是宫名首字之前
    #   「天机在丑为庙」        → 丑后是「为」，没有「宫」字
    #   「太阴在辰落陷」        → 辰后是「落」
    # 所以地支后只需排除「紧跟宫名首字」这一种情况。
    pat = re.compile("(" + "|".join(ALL14) + "|" + other_stars + ")"
                     r"(?:星)?(?:在|落|坐|入)([子丑寅卯辰巳午未申酉戌亥])"
                     r"(?![" + PALACE_FIRST + r"])")
    for m in pat.finditer(text):
        star, br = m.group(1), m.group(2)
        if star in actual and actual[star] != br:
            # 第五轮修假阳性：**并列枚举**里的「在」是共享的——
            # 「右弼、文曲在辰之对宫戌宫」「天魁、天钺在丑、未」这两种写法里，
            # 「在」后面跟的是整串的落点，不是紧邻那颗星的落点。
            # 判据：星名前紧邻顿号/和/与，或地支后紧跟顿号 → 属枚举，不判
            #（宁漏不误报：真正的「X在Y」单说不受影响）。
            if text[m.start() - 1: m.start()] in ("、", "和", "与"):
                continue
            if text[m.end(2): m.end(2) + 1] in ("、", "和", "与"):
                continue
            F.append(Finding("BLOCKER", "STAR_MISPLACED",
                             f"报告称「{star}在{br}」，但盘面 {star} 在 {actual[star]}宫",
                             m.group(0)))
    # 宫名表述四种形态都要认：
    #   X宫为/是/即 Y宫、X宫·Y宫        —— 宫名在后
    #   X宫（Y宫）                       —— 括注里就是宫名本身
    #   Y宫X（Z宫）                      —— 宫名在前（如「命宫申（命宫）」）
    # 两种形态放进**一条正则的两个分支**，且带宫名的分支写在前面。
    # 为什么不能拆成两条独立正则：re 找到最左匹配就提交。文本是「命宫申（兄弟）」时，
    # 从位置 2 起的「申宫（兄弟）」能命中第二条，但第一条会先在位置 3 用「未宫」起步、
    # 匹配失败后又**不再回退**到更早的位置，于是整条被漏掉（本轮实测到的假阴性）。
    # 「」只用来画宫名边界，避免「命宫」里的「宫」被当成地支宫。仆役与交友视为同一宫。
    #
    # ⚠️ 第四轮修正两处**假阳性**（都是被新正文触发、实测到的）：
    #   ① 连接词原本是可选的，于是「子宫是命宫的三合宫」被读成「子宫是命宫」；
    #      现在连接词必填，且宫名后不许紧跟「的/之」（那是关系从句，不是命名）。
    #   ② 括号形态原本不要求闭括号，于是「未宫（命宫左邻）」被读成「未宫＝命宫」；
    #      现在要求括号里**只有**那个宫名（可带空白）后立刻闭括号。
    alias = {"交友": "仆役", "仆役": "交友"}
    PAL = "命宫|兄弟|夫妻|子女|财帛|疾厄|迁移|仆役|交友|官禄|田宅|福德|父母"
    BRCLS = "[子丑寅卯辰巳午未申酉戌亥]"
    # 宫名后可以是任何字符或直接结束——「命宫申（命宫），」里宫名后面是右括号，
    # 旧写法要求「后面必须还有一个字符」，会把句末的「（命宫）」漏掉（本轮实测）。
    pat2 = re.compile(
        r"(?:(?P<pre>" + PAL + r")(?P<pbr>" + BRCLS + r")\s*(?:宫)?\s*"
        r"[（(]\s*(?P<pnm>" + PAL + r")\s*[）)]"
        r"|(?P<br>" + BRCLS + r")宫\s*(?:[（(]\s*(?P<bnm>" + PAL + r")\s*[）)]"
        r"|(?:为|是|即|名为|[·・/])\s*(?P<nm>" + PAL + r")"
        # ⚠️ 第四轮排除了「的/之」（关系从句不是命名）；
        # 第五轮再排「宫名＋地支」：「子宫是命宫申的三合宫」里，
        # 「命宫申」是「命宫（地支申）」的定位写法，不是「子宫＝命宫」。
        r"(?![的之]|[子丑寅卯辰巳午未申酉戌亥])))"
    )
    for m in pat2.finditer(text):
        if m.group("pbr"):
            # 「命宫申（兄弟）」形态：pbr 是地支，pnm 是括注里的宫名。
            # 不带括注的「命宫申」不在这里判——那种写法无法与「命宫申宫」区分，
            # 硬判会造出假阳性；宁可漏这一种写法。
            br, nm, pre = m.group("pbr"), m.group("pnm"), m.group("pre")
            real = name_of.get(br)
            if real and alias.get(nm, nm) != real:
                F.append(Finding("BLOCKER", "PALACE_MISNAMED",
                                 f"报告称「{pre}{br}（{nm}）」，但盘面 {br}宫 是 {real}",
                                 m.group(0)))
            continue
        br = m.group("br") or m.group("br")
        nm = m.group("bnm") or m.group("nm")
        if not br or not nm:
            continue
        real = name_of.get(br)
        if real and alias.get(nm) != real and real != nm:
            F.append(Finding("BLOCKER", "PALACE_MISNAMED",
                             f"报告称「{br}宫」为{nm}，但盘面 {br}宫 是 {real}", m.group(0)))

    # ── 2 基本参数一致性 ──
    if b["fiveElementsClass"] and b["fiveElementsClass"] not in text:
        F.append(Finding("WARN", "FIVE_CLASS_MISSING", f"报告未出现五行局「{b['fiveElementsClass']}」"))
    for label, val in (("命宫", b["soulPalaceBranch"]), ("身宫", b["bodyPalaceBranch"])):
        if label in text and val not in text:
            F.append(Finding("WARN", "PALACE_BRANCH_MISSING", f"报告未出现{label}地支「{val}」"))

    exp_age = START_AGE.get(b["fiveElementsClass"])
    if exp_age is not None:
        m = re.search(r"(\d{1,2})\s*岁(?:起运|起)", text)
        if m and int(m.group(1)) != exp_age:
            F.append(Finding("ERROR", "START_AGE_MISMATCH",
                             f"报告称 {m.group(1)} 岁起运，但{b['fiveElementsClass']}应为 {exp_age} 岁",
                             m.group(0)))

    valid_ranges = {tuple(d["ageRange"]) for d in (chart.get("decadal") or [])}
    for m in re.finditer(r"(\d{1,2})\s*[~\-至到]\s*(\d{1,2})\s*(?:虚岁|岁)", text):
        rng = (int(m.group(1)), int(m.group(2)))
        if valid_ranges and rng not in valid_ranges and rng[1] - rng[0] == 9:
            F.append(Finding("ERROR", "DECADAL_RANGE_INVALID",
                             f"报告出现大限区间 {rng[0]}~{rng[1]}，但盘面大限表无此区间", m.group(0)))

    # ── 3 定盘等级 vs 排盘告警 ──
    # 「是否贴近交界」以脚本算出的 minutes_from_hour_boundary 为准（<30 分钟＝贴近，
    # 与 interpretation.md §1 的 A/B/C 分级同一阈值）。旧版只 grep 告警文案，
    # 一旦告警措辞变化就会静默失效。
    warns = " ".join((chart.get("meta") or {}).get("warnings") or [])
    st = chart.get("solarTime") or {}
    near = False
    mfb = st.get("minutes_from_hour_boundary")
    if isinstance(mfb, (int, float)):
        near = mfb < 30
    if re.search(r"距时辰交界", warns) or re.search(r"距时辰交界", " ".join(st.get("warnings") or [])):
        near = True
    lvl = ""
    m = re.search(r"定盘可信度[：:]\s*([ABC])", text) or re.search(r"定盘等级[：:]?\s*([ABC])", text)
    if m:
        lvl = m.group(1)
    if near and lvl == "A":
        F.append(Finding("ERROR", "FIRMING_OVERCLAIM",
                         "排盘提示时辰贴近交界，但定盘等级标为 A 可信——等级与依据不符"))
    if near and not lvl and analysis is not None:
        F.append(Finding("WARN", "FIRMING_UNSTATED", "时辰贴近交界但报告未明示定盘等级"))

    # ── 4 格局：报告表格 vs 脚本条件 vs 宫位 ──
    # 候选表按机器键建索引；报告里显示的是 display_name（可能带「（存疑条目）」这类限定语），
    # 所以两种写法都要能命中——否则会出现「脚本明明判过却报未判定」的假告警。
    cands: dict[str, dict] = {}
    for c in (chart.get("patternCandidates") or []):
        for key in (c.get("pattern"), c.get("name"), c.get("display_name")):
            if key:
                cands.setdefault(key, c)

    def branches_in_evidence(cand: dict) -> list[str]:
        return re.findall(r"@\['([子丑寅卯辰巳午未申酉戌亥]+)'\]", str(cand.get("evidence", "")))

    # ── 格局表结论格的统一定位（末轮抽出为函数）──────────────────────
    # 为什么要抽出来：格局表在末轮加了一列「格性」，而格性也带
    # `<span class="bright">吉</span>` / `<span class="dim">凶</span>` 这类状态样式。
    # 凡是「按 span class 找结论」的写法，加列后都会先抓到格性格——吉格被读成不成立。
    # 结论格的判据因此收紧为：**一个单元格里同时出现三态样式与三态词**
    # （成立／不成立／存疑／存疑并列），全脚本共用这一份判据，不再各写各的。
    _VERDICT_SPAN_RE = re.compile(
        r"<span class=\"(?:bright|dim|pending)\">"
        r"(?P<verdict>存疑／并列|存疑|不成立|成立)</span>")
    _VERDICT_CELL_RE = re.compile(
        r"<td[^>]*>(?:(?!</td>).)*?<span class=\"(?:bright|dim|pending)\">"
        r"(?:存疑／并列|存疑|不成立|成立)</span>(?:(?!</td>).)*?</td>", re.S)

    def verdict_of(row_html: str) -> str | None:
        """返回该行的三态结论（成立／不成立／存疑／并列）；不是格局数据行则返回 None。"""
        cm = _VERDICT_CELL_RE.search(row_html)
        if cm is None:
            return None
        vm = _VERDICT_SPAN_RE.search(cm.group(0))
        return vm.group("verdict") if vm else None

    def check_pattern_claim(nm: str, claimed: bool, where: str, raw: str) -> None:
        if nm not in cands:
            return
        cand = cands[nm]
        # needs_adjudication=False：该格属流派分歧，脚本只提供双方落点，
        # 报告须并列两说，不标「成立/不成立」。
        if cand.get("needs_adjudication") is False:
            F.append(Finding("WARN", "PATTERN_DISPUTED",
                             f"{where}列出存疑格局「{nm}」：该格成立与否取决于流派口径，"
                             f"报告必须并列两说且不得单方断言。脚本依据：{cand.get('evidence')}"))
            return
        if not claimed:
            return
        sb = branches_in_evidence(cand)
        in_soul = any(x in surround_branches for x in sb)
        # 条件不满足却标成立：夹宫类与三方四正类都直接判矛盾
        #（夹宫类的条件本身就是「那两颗星正好分居两邻」，由脚本判定）
        if not cand.get("conditions_met"):
            F.append(Finding("BLOCKER", "PATTERN_CONTRADICTS_SCRIPT",
                             f"{where}称「{nm}」成立，但脚本判定其构成条件不满足",
                             str(cand.get("evidence"))))
            return
        if sb and nm in FLANK_PATTERNS:
            # 夹宫类：星曜必须正好落在命宫两邻（本宫不算），否则「夹命」二字不成立
            i2 = BR12.index(soul)
            flank = {BR12[(i2 - 1) % 12], BR12[(i2 + 1) % 12]}
            if not set(sb) <= flank or len(set(sb)) < 2:
                F.append(Finding("BLOCKER", "FLANK_PATTERN_MISPLACED",
                                 f"{where}称「{nm}」成立，但夹宫类格局要求相关星曜分居命宫"
                                 f"{soul}的两邻 {sorted(flank)}；脚本记录的落点为 {sb}",
                                 f"星曜在 {sb}；命宫{soul}两邻为 {sorted(flank)}"))
            return
        if sb and not in_soul and nm in MUST_BE_IN_SOUL_SURROUND:
            F.append(Finding("BLOCKER", "PATTERN_OUTSIDE_SOUL_SURROUND",
                             f"{where}称「{nm}」成立，但该格必须落在命宫三方四正内；"
                             f"相关星曜在 {sb}，而命宫{soul}三方四正为 {sorted(surround_branches)}"))
        if sb and not in_soul and nm in BIG_PATTERNS:
            # 星曜不在命宫三方四正内，却在「本盘格局」表里标「成立」——无论备注多严谨都会误导。
            # 正确写法：裁定为「不成立（非命宫三方，不主此格）」，或降级为附注而非表格行。
            F.append(Finding("BLOCKER", "BIG_PATTERN_MISLEADING",
                             f"{where}把「{nm}」标为成立，但其星曜（{sb}）不在命宫{soul}的三方四正"
                             f"{sorted(surround_branches)} 内——三星虽成三合，此盘却不以该格为主，"
                             "标「成立」会误导读者。应裁定为「不成立（非命宫三方，不主此格）」或降为附注",
                             f"星曜在 {sb}；命宫三方四正为 {sorted(surround_branches)}"))

    if report_markup is not None:
        # 用命名组，避免以后再调整模式时把捕获组序号挪错（这个坑踩过一次：
        # 加了一个可选组就把 verdict 挤到 group 4，代码仍读 group 3，于是整表被判成「不成立」）。
        # 结论列有**三态**：成立 / 不成立 / 存疑／并列（后者渲染成 class="pending"）。
        # 「存疑」放在 alternation 最前面，否则「不成立」会先命中、把存疑行读成不成立。
        row = re.compile(
            # ⚠️ 只抓「首格＝格局名」与整行内容，**结论列在行内另找**：
            #    末轮给格局表加了一列（格性），若把「结论列紧接第二个 <td>」写死，
            #    加列后整表一行都解析不出，会被报成 AUDIT_PARSE_FAILED（审查自身失效）
            #    ——那是假故障。结论一律走 verdict_of()（见上），日后再加列也不会失效。
            r"<tr><td>(?P<name>[^<]+)</td>(?P<body>.*?)</tr>",
            re.S)
        matched_rows = 0
        for m in row.finditer(markup):
            verdict = verdict_of(m.group(0))
            if not verdict:
                continue  # 不是格局表的数据行（如表头、别的小表），跳过而不计数
            matched_rows += 1
            nm = m.group("name").strip()
            if verdict in ("存疑／并列", "存疑"):
                # 并列两说：本就不该由脚本判「成立/不成立」，交给 PATTERN_DISPUTED 提示口径。
                # 旧版一律按 `verdict == "成立"` 判，存疑行会被当成「不成立」而静默放过。
                if nm not in cands:
                    F.append(Finding("WARN", "PATTERN_UNVERIFIED",
                                     f"报告把格局「{nm}」列为存疑并列，脚本未对该格做条件判定"
                                     "——需人工确认依据"))
                continue
            check_pattern_claim(nm, verdict == "成立", "报告格局表", m.group(0))
            if nm not in cands:
                F.append(Finding("WARN", "PATTERN_UNVERIFIED",
                                 f"报告列出格局「{nm}」，脚本未对该格做条件判定——需人工确认依据"))
        # 防线：报告里有格局表却一行都没解析出来，说明审查本身失效，必须报错而非静默通过
        if matched_rows == 0 and "格局" in html_to_text(markup):
            F.append(Finding("ERROR", "AUDIT_PARSE_FAILED",
                             "未能从报告中解析出任何格局行——审查可能失效，请人工复核"))

    if analysis is not None:
        for pt in (analysis.get("patterns") or []):
            check_pattern_claim(pt.get("name") or "", pt.get("satisfied") is True,
                                "analysis", json.dumps(pt, ensure_ascii=False))
    # ══════════════════════════════════════════════════════════════════
    # 7 措辞 vs 盘面（第二轮盲审暴露的六类错误，全部落到盘面事实上）
    #   这些错误上一轮靠「改文字」修，结果同类错误再犯——所以做成机器可抓的。
    # ══════════════════════════════════════════════════════════════════
    stars = star_index(chart)
    brights = brightness_map(chart)
    names_desc = all_star_names(chart)
    # 星名排序后交替进正则：长名在前，避免「地空」抢掉「地空地劫」之类
    star_alt = "|".join(re.escape(n) for n in names_desc)
    # ⚠️ 星名大列表**必须自带括号**再进任何量词/分组。
    # `star_alt + "(?:…)*"` 这种写法里，量词只作用于列表的**最后一个**候选名，
    # 前面几十个候选名都跑到了组外——「并列星名」这类结构于是永远匹配不到，
    # 整条规则静默失效。本轮实测踩到过一次（假阴性）。
    star_one = "(?:" + star_alt + r")"
    # gap 上限压到 8 字，且**不许跨越顿号/逗号/句读**——顿号是并列分隔符，
    # 「左辅、文昌得」里的「得」属于文昌，不属于左辅；「贪狼受对宫化入忌（向心）命宫」
    # 更是跨了从句。放宽到 16 字会一路吃过去造成假阳性（本轮实测到两处）。
    # 连接词只收「与/和/、」不收「同」：「为命身同宫」里的「同」会被当成连接词，
    # 读成「身同 同宫」，把「同」当成星名（本轮实测到的假阳性）。
    # gap 里**必须把标点全部排除**（逗号、顿号、括号都算）。
    # 否则「对宫天府与它相拱。」里的「，」会被 gap 吃掉，
    # chain_end 的前视断言就再也看不到句读，整条规则静默失效（本轮实测到的坑）。
    gap = r"[^\n。；;，,、（）()：:]{0,8}"
    # 链尾：终止标点或「的/之/两/双」；「相对」「同宫」等关系词本身不算终止，
    # 因为它们正是被核对的措辞（如「两宫相对而非同宫」）。
    # 链尾：终止标点、连接词（与/和/及/同）或「的/之/两/双」。
    # 连接词必须算终止——「对宫天府与它相拱」里天府后面紧跟「与」，
    # 旧写法漏了它，整条规则静默失效（本轮实测到的坑）。
    chain_end = r"(?=[\s，,。、；;：:）)】]|的|之|两|双|与|和|及|同|$)"
    # 按**从句**回看主语，而不是按固定字数：
    #   固定窗口（曾用 12 字）会把上一句末尾的星名误当主语，于是「天府在卯、太阴在辰，
    #   两星并不同宫。对宫天府…」被判成「已有主语」而整条跳过——真错被放过。
    # 从句边界取句读（。；，、）与换行，与 gap 的排除集一致。
    CLAUSE_BREAK = "。；;，,、\n"
    SUBJECT_GAP = r"[^。；;，,、\n]{0,6}"

    def clause_before(idx: int) -> str:
        j = idx
        while j > 0 and text[j - 1] not in CLAUSE_BREAK:
            j -= 1
        return text[j:idx]

    subject_re = re.compile(star_one + r"(?:" + SUBJECT_GAP + r"(?:与|和|、)"
                            + SUBJECT_GAP + star_one + r")?\s*$")
    # 「不能算『三合内五吉』」这类**被否定的引用**不是断言，见 7.4
    NEG_AFTER = ("不能算", "不算", "不能作", "不可作", "非", "不是", "并无", "未有", "没有")

    def star_pos(nm: str) -> str | None:
        return stars.get(nm)

    def need_flanks(br: str, s1: str, s2: str) -> tuple[bool, set[str]]:
        i2 = BR12.index(br)
        nb = {BR12[(i2 - 1) % 12], BR12[(i2 + 1) % 12]}
        return ({star_pos(s1), star_pos(s2)} == nb), nb

    def check_star_relation(star: str, rel: str, target: str, raw: str) -> None:
        at = star_pos(star)
        if not at:
            return
        if rel in ("同宫", "同一宫", "同处一宫", "同坐"):
            if at != target:
                F.append(Finding(
                    "ERROR", "STAR_MISATTRIBUTED",
                    f"报告称「{star}{rel}{target}宫」，但盘面 {star} 在 {at}宫"
                    f"（{palace_of(chart, at)}）、{target}宫是 {palace_of(chart, target)}",
                    raw))
            return
        if rel == "对宫" or rel == "相对":
            if (BR12.index(at) + 6) % 12 != BR12.index(target):
                F.append(Finding(
                    "ERROR", "STAR_MISATTRIBUTED",
                    f"报告称「{star}在对宫{target}」（把 {star} 当作 {target}宫的对宫星），"
                    f"但盘面 {star} 在 {at}宫（{palace_of(chart, at)}），"
                    f"{target}宫的对宫是 {BR12[(BR12.index(target) + 6) % 12]}宫",
                    raw))
            return
        if rel == "分居":
            if at == target:
                F.append(Finding(
                    "ERROR", "STAR_MISATTRIBUTED",
                    f"报告称「{star}与…分居{target}」，但盘面 {star} 就在 {at}宫，与之一同宫",
                    raw))
            return

    def check_same_palace(s1: str, s2: str, raw: str) -> None:
        b1, b2 = star_pos(s1), star_pos(s2)
        if not b1 or not b2 or b1 == b2:
            return
        F.append(Finding(
            "ERROR", "STAR_MISATTRIBUTED",
            f"报告称「{s1}与{s2}同宫」，但盘面 {s1} 在 {b1}宫（{palace_of(chart, b1)}）、"
            f"{s2} 在 {b2}宫（{palace_of(chart, b2)}）——两星并不同宫",
            raw))

    def unclosed_paren(text: str, idx: int, back: int = 8) -> bool:
        """光标之前 8 字内有没有未闭合的「（」——有说明正处在插入语里，不判。"""
        s = text[max(0, idx - back): idx]
        return s.rfind("（") > s.rfind("）") or s.rfind("(") > s.rfind(")")

    def check_flank(flank_group: list[str], rel: str, target: str, raw: str) -> None:
        """「A 夹 B」：A 的两颗必须正好分居 B 的两邻（同宫不算夹）。"""
        if rel == "夹" and len(flank_group) == 1:
            pair = FLANK_ABBR.get(flank_group[0])
        elif rel == "分居" and len(flank_group) == 2:
            pair = (flank_group[0], flank_group[1])
        else:
            return
        if not pair:
            return  # 简称不在表内（如「两颗煞星夹命」）→ 不判，宁可漏也不误报
        s1, s2 = pair
        b1, b2 = star_pos(s1), star_pos(s2)
        if not b1 or not b2:
            return
        ok, nb = need_flanks(target, s1, s2)
        if ok:
            return
        if b1 == b2:
            why = f"两颗同在 {b1}宫（{palace_of(chart, b1)}）——同宫不是夹"
        else:
            why = f"两颗在 {b1}宫、{b2}宫，而 {target}宫的两邻只有 {sorted(nb)}"
        F.append(Finding(
            "ERROR", "FLANK_MISJUDGED",
            f"报告称「{'、'.join(pair)} {rel}{target}」不成立：{why}",
            raw))

    # 7.1 「对宫 X」「X 之对宫」「X 与 Y 同宫」「X、Y 分居 Z」——链式扫描，逐环核对
    chain_tok = ("(?:" + star_one + r"|[子丑寅卯辰巳午未申酉戌亥]宫|对宫|命宫)"
                 r"(?:" + gap + r"(?:与|和|、)" + gap
                 + r"(?:" + star_one + r"|[子丑寅卯辰巳午未申酉戌亥]宫))*")
    # 主语位置不许是关系词或宫名——否则「受对宫化入」会被读成「对宫 与 X」，
    # 「同日主星」也会被当成星名（须是真名，不能用前缀）。
    # 主语可以是「甲、乙」这种顿号并列：不显式支持的话，正则会在「乙」处才起步
    # （re 找最左的**完整**匹配），于是 tok2 永远为空，「分居 X 两邻」整类漏判
    # ——本轮实测到的假阴性。并列里只许接短修饰，不许跨「在/宫/的」。
    # 主语可以是「甲、乙」这种顿号并列。
    # 写法是 `星 (分隔符 星)*`——**分隔符必须紧贴在星名之间**，中间不能再插 `gap_list`：
    # 「太阴、左辅分居…」里 `[^…、]` 这类字符类走不过「、」，一插 gap_list 并列就只能
    # 匹配零次，正则退到第二颗星处才起步、tok2 永远是空（本轮实测到的假阴性）。
    gap_list = r"[^\n。；;，,、（）()：:在宫的地]{0,3}"
    sep = r"(?:、|与|和)"
    head = star_one + r"(?:" + sep + gap_list + star_one + r")*"
    seg_pat = re.compile(
        r"(?<![不无])(?P<tok>" + head + r")"
        + r"(?:(?:" + gap_list + r"与" + gap_list + r")?(?P<tok2>" + star_one + r"))?"
        + gap + r"(?P<rel>同宫|同一宫|同处一宫|同坐|对宫|相对|分居|夹)"
        + gap + r"(?P<tgt>" + chain_tok + r")" + chain_end)
    origin_pat = re.compile(r"(?<![不无])(?P<rel>对宫|相对)(?P<tgt>" + star_one + r")"
                            + chain_end)

    for m in seg_pat.finditer(text):
        seg = m.group(0)
        # 插入语里的「同宫」不构成对主语的断言：「天相两邻为午宫（巨门同宫擎羊）」
        # 说的是巨门与擎羊同宫，不是天相与擎羊同宫。
        if unclosed_paren(text, m.start()):
            continue
        # 说明：这里**不能**用「命中串里出现 `X宫` 就跳过」这种粗防线。
        # 夹宫类措辞的目标本来就写成地支宫（「分居辰宫两邻」），那样会把
        # 全部夹宫描述误杀——假阴性比假阳性更难发现（本轮实测踩过一次）。

        # 关系词之后的部分要按「token 链」拆开：甲与乙同宫丙 里，丙是同宫的另一个宾语
        rest = seg.split(m.group("rel"), 1)[1]
        chain: list[str] = []
        for tm in re.finditer(star_alt + r"|命宫|[子丑寅卯辰巳午未申酉戌亥]宫", rest):
            chain.append(tm.group(0))
        head = m.group("tok")
        tok2 = m.group("tok2")
        rel = m.group("rel")
        # head 可能是「甲、乙、丙」这样的并列串：拆成单星，取前两颗当作夹宫的两邻。
        # 不能直接把 head 当一个名字用——那样 bookkeeping 会把「太阴、左辅」整串当星名。
        head_list = re.findall(star_one, head)

        def resolve(tok: str) -> str | None:
            """
            token → 目标地支。
            先判「命宫」再判星名：**顺序反了会出错**——盘面若有「命宫」这名字的星
            （某些流派把命宫主星也叫命宫）就会先命中星名；更要紧的是「命宫」本来
            就不是星名，必须先映射成命宫地支，否则「分居命宫两邻」会被解析成
            「命宫这颗星在哪一宫」而落在 None 上，真错被放过（本轮实测到的假阴性）。
            """
            if tok == "命宫":
                return soul
            if tok.endswith("宫") and tok[0] in BR12:
                return BR12[BR12.index(tok[0])]
            return star_pos(tok)

        if rel == "夹":
            if not chain:
                continue
            grp = (head_list + ([tok2] if tok2 else []))
            tgt = resolve(chain[0])
            if tgt is None:
                continue
            check_flank(grp, "夹", tgt, seg)
            continue
        if rel == "分居":
            if _TRACE:
                print(f"[trace-flank] seg={seg!r} head={head_list} tok2={tok2!r} chain={chain}")
            if len(head_list) >= 2 and chain:
                # 「太阴、左辅分居辰宫两邻」：并列串自带配对，不需要 tok2
                tgt = resolve(chain[0])
                if tgt:
                    check_flank(head_list[:2], "分居", tgt, seg)
            elif tok2 and chain:
                tgt = resolve(chain[0])
                if tgt:
                    check_flank([head_list[0], tok2], "分居", tgt, seg)
            continue
        # 同宫 / 对宫 / 相对
        for c in chain:
            if c == "对宫":
                continue
            tgt = resolve(c)
            if not tgt:
                continue
            for st in (head_list[:1] + ([tok2] if tok2 else [])):
                check_star_relation(st, rel, tgt, seg)

    # 7.2 「对宫 X」在前（无主语链）
    for m in origin_pat.finditer(text):
        rel = m.group("rel")
        tgt_star = m.group("tgt")
        # 回看**本从句**（不跨句读）里有没有紧邻的主语星名；有就交给 7.1，避免重复。
        if subject_re.search(clause_before(m.start())):
            continue
        br = star_pos(tgt_star)
        if not br:
            continue
        opp = BR12[(BR12.index(br) + 6) % 12]
        F.append(Finding(
            "ERROR", "STAR_MISATTRIBUTED",
            f"报告称「对宫{tgt_star}」（把 {tgt_star} 当作对宫星），但盘面 {tgt_star} "
            f"在 {br}宫（{palace_of(chart, br)}），其对宫是 {opp}宫",
            m.group(0)))

    # 7.3 「X 与 Y 同宫」——两星都在盘面却不同宫
    # ⚠️ 第五轮修假阳性：**被否定的断言不是断言**。「贪狼与铃星成三合遥会而不同宫」
    # 里的「同宫」被「不」否定，旧写法照样报错（本轮实测到的假阳性）。
    # 判据：紧贴在「同宫」前一字是不是否定词。
    _NEG_CHARS = ("不", "非", "未", "无", "没", "难")
    for m in re.finditer(r"(?P<a>" + star_alt + r")" + gap + r"与" + gap
                         + r"(?P<b>" + star_alt + r")" + gap + r"(?P<tong>同宫)", text):
        if text[m.start("tong") - 1: m.start("tong")] in _NEG_CHARS:
            continue
        check_same_palace(m.group("a"), m.group("b"), m.group(0))

    # 7.4 三方四正误判：「X 不在三方四正」但盘面在内（或反之）
    # 星名与「在/不在」之间**只允许极短距离**（含宫名后缀如「太阳在戌」）：
    # 给 8 字余量会让正则跨越从句、把别的句子的星名拉进来，最后用「辰宫」这种
    # 地支+宫 的写法蒙对——于是该报的没报（本轮实测到的假阴性）。
    # 「在/不在」前面要能吃掉「在辰宫，」这半截定位语。
    # 必须一次吃干净：`在` + 地支 + 可选「宫」+ 可选逗号。
    # 只写 `(?:在[地支])?` 会把「宫，」留下，紧跟着的 neg 组只能匹配「在」，
    # 而 gap 不许跨逗号 → 后面接不上「三方四正」，整句被判为不匹配（本轮实测到的假阴性）。
    surround_pat = re.compile(
        r"(?P<stars>(?:" + star_alt + r"[、与和及]?){1,4})"
        r"(?:在[子丑寅卯辰巳午未申酉戌亥]宫?[，,]?)?"
        r"(?P<neg>都不在|均不在|皆不在|不在|均在|都在|皆在|在)"
        + gap + r"(?:命宫" + soul + r"的?)?三方四正"
        + gap + r"(?:[（(][子丑寅卯辰巳午未申酉戌亥\s、,，]*[）)])?"
        + r"[^\n]{0,6}?(?P<tail>之内|以内|之外|以外)?")
    for m in surround_pat.finditer(text):
        found = re.findall(star_alt, m.group("stars"))
        if not found:
            continue
        phrase = m.group(0)
        # 「不能算『三合内五吉』」这类**被否定的引用**不是断言：「在」属于被引用的术语，
        # 后文一句「不能算」就把它推翻了。看后 12 字有没有否定词，有就整句不判。
        after = text[m.end(): m.end() + 12]
        if any(t in after for t in NEG_AFTER):
            continue
        inside = relation_inside(phrase)
        if inside is None:
            continue
        # 与盘面**不符**的那些星：报告声称「在内」而盘面在外（或反之）。
        # 第四轮修正：判据是「声称 == 事实」即**一致**，矛盾的是两者不等。
        # 旧版写成 `==` 并把 relation_inside 的「之内/之外」当结论用，
        # 于是两种正确写法都被报成矛盾（本轮实测：两条正确句各报一条假阳性）。
        wrong = [s for s in found
                 if star_pos(s) and ((star_pos(s) in surround_branches) != inside)]
        if not wrong:
            continue
        det = "；".join(f"{s}@{star_pos(s)}（{palace_of(chart, star_pos(s))}）" for s in wrong)
        F.append(Finding(
            "ERROR", "SURROUND_MISJUDGED",
            f"报告称「{phrase.strip()}」——即认为 {''.join(found)} 在命宫{soul}三方四正"
            f"{'之内' if inside else '之外'}，但盘面：{det}；"
            f"命宫{soul}三方四正为 {sorted(surround_branches)}",
            phrase))

    # 7.5 亮度不得编造：盘面 brightness 为 null 的星，报告不得给庙/旺/利/平/陷
    bright_alt = "|".join(re.escape(w) for w in BRIGHT_WORDS)
    for m in re.finditer(r"(?<![与和及同])(?P<star>" + star_one + r")"
                         r"(?P<gap>[^\n，,、；;]{0,3}?)(?P<br>" + bright_alt + r")", text):
        star, br = m.group("star"), m.group("br")
        if star not in brights or brights[star] is not None:
            continue
        # 「天钺与天同旺」：亮度属于天同，不属于天钺——主语链先拆清楚。
        if re.search(r"(?:与|和|同)$", m.group("gap")):
            continue
        after = text[m.end():m.end() + 1]
        if br == "不" and after in BRIGHT_TAIL:
            continue  # 「不得地」——完整的否定亮度词
        if br in BRIGHT_NARROW and re.match(r"^(?:与|和)", after):
            continue  # 「天钺旺与…」极少见，保守放过
        F.append(Finding(
            "ERROR", "BRIGHTNESS_FABRICATED",
            f"报告给「{star}」标了亮度「{br}」，但盘面该星 brightness 为 null"
            f"（无庙旺表条目，不得替它编一个亮度）",
            m.group(0)))

    # 7.6 格局表结论 vs 正文口径
    if report_markup is not None:
        # 逐行抓**整行 HTML**（不是只抓名字）——只抓名字的话，正文口径根本无从核对。
        # 终止符必须是 `</tr>`：不能用 `<tr`（会跨到下一行）。
        # 格局名可带「（存疑条目）」这类括注，结论列也可能多写一个单元格，
        # 所以用 `(?:(?!</tr>).)*?` 这种「不跨行」的惰性通配兜住任意结构。
        row_pat = re.compile(
            r"<tr><td>(?P<name>[^<]+)</td>(?P<body>(?:(?!</tr>).)*?)</tr>", re.S)
        cell_pat = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
        rows = []
        for m in row_pat.finditer(markup):
            body = m.group("body")
            # ⚠️ 结论一律走 verdict_of()：它只认「同时带三态样式与三态词」的那一格，
            #    所以末轮新增的「格性」列（凶也渲染成 class="dim"）不会被误当结论。
            verdict = verdict_of(m.group(0))
            if not verdict:
                continue
            cells = cell_pat.findall(body)
            rows.append({"name": m.group("name").strip(), "cells": cells,
                         "verdict": verdict, "html": m.group(0)})
        if _TRACE:
            print(f"[trace-verdict] rows={[(r['name'], r['verdict']) for r in rows]}")

        def cand_key(row_name: str) -> str | None:
            if row_name in cands:
                return row_name
            base = re.sub(r"[（(].*?[）)]", "", row_name).strip()
            if base in cands:
                return base
            for k in sorted(cands, key=len, reverse=True):
                if row_name.startswith(k):
                    return k
            return None

        for row in rows:
            row_name = row["name"]
            key = cand_key(row_name)
            if not key:
                continue
            prose = " ".join(row["cells"])
            # 「并列两说」必须与流派词相邻才算真在讲口径分歧；
            # 只是「两股力量并列」不该被当成并列两派。
            marks = [t for t in DISPUTED_MARKS
                     if re.search(re.escape(t) + r"[^\n]{0,24}?(?:" + "|".join(SCHOOL_MARKS) + r")",
                                  prose)
                     or re.search(r"(?:" + "|".join(SCHOOL_MARKS) + r")[^\n]{0,24}?"
                                  + re.escape(t), prose)]
            univocal = [m.group(0) for t in UNIVOCAL_VERDICTS
                        for m in [re.search(t, prose)] if m]
            if row["verdict"] == "存疑／并列":
                # 结论列已并列，但如果正文里仍在单方断言「应标为…不成立／成立」，
                # 同样自相矛盾——这半边最容易被漏（本轮实测到的假阴性）。
                if not univocal:
                    continue
                F.append(Finding(
                    "ERROR", "PATTERN_VERDICT_CONFLICT",
                    f"格局表结论列已写「存疑／并列」，但同一行正文仍在单方断言"
                    f"「{univocal[0]}」——表格与正文口径矛盾；并列两说的格不得在任何位置单方下结论",
                    row["html"][:200]))
                continue
            # 反过来：结论列单方下了结论（成立/不成立），而正文又称并列两说
            if not marks:
                continue
            F.append(Finding(
                "ERROR", "PATTERN_VERDICT_CONFLICT",
                f"格局表把「{row_name}」单方列为{row['verdict']}，"
                f"但同一行正文又称其「{marks[0]}」——表格结论与正文口径矛盾；"
                "凡并列两说的格，结论列须写「存疑／并列」，不得单方下结论",
                row["html"][:200]))

    # 7.7 计数一致性：正文声明的格局项数必须等于盘面候选数
    n_cand = len(chart.get("patternCandidates") or [])
    count_pats = [
        r"共\s*(\d{1,3})\s*项格局", r"格局\s*(\d{1,3})\s*项", r"(\d{1,3})\s*项格局",
        r"共\s*(\d{1,3})\s*项",
    ]
    counted: list[tuple[int, str]] = []
    _CAT_BEFORE = re.compile(r"(?:成立|不成立|存疑|裁为|列为)[^\n。；]{0,8}$")
    for cp in count_pats:
        for m in re.finditer(cp, text):
            # ⚠️ 分类计数（「裁为成立的有 3 项」「存疑并列的共 1 项」）交给 7.19，
            # 这里只管**总数**。旧版不区分，于是 7.19 引入的分类句会把总数规则
            # 触发一遍（「共 17 项…成立 5 项…不成立 11 项」被判成「同时出现 5、11、17 项」）。
            # 分类词可能在数词**前**（「裁为不成立的共 11 项」）也可能在**后**
            # （「共 11 项不成立」），两边都要看。
            if re.match(r"\s*[，,]?\s*(?:格局)?(?:表内)?(?:裁为|列为)?"
                        r"(?:成立|不成立|存疑)", text[m.end():m.end() + 14]):
                continue
            if _CAT_BEFORE.search(text[max(0, m.start() - 12):m.start()]):
                continue
            counted.append((int(m.group(1)), m.group(0)))
    if counted:
        vals = sorted({v for v, _ in counted})
        if len(vals) > 1:
            F.append(Finding("ERROR", "COUNT_MISMATCH",
                             f"报告内格局项数自相矛盾：同时出现 " +
                             "、".join(str(v) for v in vals) + " 项",
                             "；".join(d for _, d in counted[:6])))
        if n_cand and (n_cand in vals) is False:
            F.append(Finding("ERROR", "COUNT_MISMATCH",
                             f"报告称「{counted[0][1]}」，但盘面 patternCandidates 共 {n_cand} 项",
                             "；".join(d for _, d in counted[:6])))
    elif n_cand:
        F.append(Finding("WARN", "COUNT_UNSTATED",
                         f"报告未声明格局候选共 {n_cand} 项——读者无法核对表格是否列全"))

    # ══════════════════════════════════════════════════════════════════
    # 7.8 起的检测器是**第四轮**新增：第三轮盲审的 BLOCKER/MAJOR 全部落在
    #     「散文里的宫位算术」上——对宫写成三合、大限宫名算错、生年科整条漏标。
    #     这一轮把这些事实改由脚本产出（chart.palaceRelations / decadalPalaceMaps），
    #     并在此逐句核对；凡判不出上下文的，一律不判（宁可漏，不误报）。
    # ══════════════════════════════════════════════════════════════════
    PAL_NAMES = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                 "迁移", "仆役", "交友", "官禄", "田宅", "福德", "父母"]
    PAL_GROUP = "|".join(PAL_NAMES)
    BRCLS = "[子丑寅卯辰巳午未申酉戌亥]"
    BR12_CLS = "子丑寅卯辰巳午未申酉戌亥"
    # 宫名前带「大限/本限/流年/小限/本命」时不按本命宫判（那些是限内/流年宫名）
    NO_LIMIT_PREFIX = r"(?<!大限)(?<!本限)(?<!流年)(?<!小限)(?<!本命)"
    name_to_branch: dict[str, str] = {}
    for p in chart["palaces"]:
        name_to_branch.setdefault(p["name"], p["branch"])
    if "仆役" in name_to_branch and "交友" not in name_to_branch:
        name_to_branch["交友"] = name_to_branch["仆役"]
    if "交友" in name_to_branch and "仆役" not in name_to_branch:
        name_to_branch["仆役"] = name_to_branch["交友"]

    def branch_of_palace(nm: str) -> str | None:
        return name_to_branch.get(nm)

    def rel_error(what: str, detail: str, raw: str) -> None:
        F.append(Finding("ERROR", "PALACE_RELATION_MISJUDGED",
                         f"报告称{what}，与盘面宫位关系不符：{detail}", raw))

    if text:
        # (a) 「X宫的对宫是Y宫」/「X宫对宫为Y」——两端都是宫名
        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<a>" + PAL_GROUP + r")"
                             r"(?:宫)?的?对宫(?:是|为|即)?(?P<b>" + PAL_GROUP + r")", text):
            ba, bb = branch_of_palace(m.group("a")), branch_of_palace(m.group("b"))
            if not ba or not bb:
                continue
            want = BR12[(BR12.index(ba) + 6) % 12]
            if want != bb:
                rel_error(
                    f"「{m.group('a')}宫的对宫是{m.group('b')}宫」",
                    f"{m.group('a')}宫在{ba}，其对宫为{want}宫（{palace_of(chart, want)}）；"
                    f"{m.group('b')}宫在{bb}",
                    m.group(0))

        # (b) 「申宫的对宫是子宫」——两端都是地支宫，同样可判
        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<a>" + BRCLS + r")宫(?:的)?对宫"
                             r"(?:是|为|即)?(?P<b>" + BRCLS + r")宫", text):
            a, b = m.group("a"), m.group("b")
            want = BR12[(BR12.index(a) + 6) % 12]
            if want != b:
                rel_error(f"「{a}宫的对宫是{b}宫」",
                          f"{a}宫的对宫是{want}宫（{palace_of(chart, want)}），不是{b}宫",
                          m.group(0))

        # (a2) 反向写法：「子宫是午宫的对宫」「官禄是命宫的对宫」
        def pname(nm: str) -> str:
            return nm if nm.endswith("宫") else nm + "宫"

        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<b>" + BRCLS + r")宫(?:是|为|即)"
                             r"(?P<a>" + BRCLS + r")宫(?:的)?对宫", text):
            a, b = m.group("a"), m.group("b")
            want = BR12[(BR12.index(b) + 6) % 12]
            if want != a:
                rel_error(f"「{b}宫是{a}宫的对宫」",
                          f"{b}宫的对宫是{want}宫（{palace_of(chart, want)}），不是{a}宫",
                          m.group(0))
        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<b>" + PAL_GROUP + r")(?:宫)?(?:是|为|即)"
                             r"(?P<a>" + PAL_GROUP + r")(?:宫)?(?:的)?对宫", text):
            ba, bb = branch_of_palace(m.group("a")), branch_of_palace(m.group("b"))
            if not ba or not bb:
                continue
            want = BR12[(BR12.index(bb) + 6) % 12]
            if want != ba:
                rel_error(f"「{pname(m.group('b'))}是{pname(m.group('a'))}的对宫」",
                          f"{pname(m.group('b'))}的对宫是{want}宫（{palace_of(chart, want)}），"
                          f"不是{pname(m.group('a'))}", m.group(0))

        # (c) 裸「对宫X」——主语回看**本从句**里最近的宫名；从句里没有时，
        #     若从句以「其／该／此」起头，再回看上一从句；仍没有就退到「命宫」
        #     （只在紧邻 60 字内确实提过命宫时）。都判不出就不判。
        #     第三轮 BLOCKER 就是这种写法：
        #     「火铃夹命：火星在未、铃星在酉分居命宫两邻，配合对宫官禄的双忌」
        #     ——从句以「配合」开头，上一从句末尾是「命宫两邻」，故主语是命宫（申），
        #     其对宫是迁移（寅），而官禄在子（三合）。
        CLAUSE_BREAK_PAL = "。；;！？\n，,、：:（）()—…"
        ANCHOR_RE = re.compile(NO_LIMIT_PREFIX + r"(?P<n>" + PAL_GROUP + r")(?:宫)?|"
                               + NO_LIMIT_PREFIX + r"(?P<br>" + BRCLS + r")宫")

        def last_anchor(s: str) -> str | None:
            br = None
            for am in ANCHOR_RE.finditer(s):
                br = branch_of_palace(am.group("n")) if am.group("n") else am.group("br")
            return br

        def clause_bounds(pos: int) -> tuple[int, int]:
            a = pos
            while a > 0 and text[a - 1] not in CLAUSE_BREAK_PAL:
                a -= 1
            b2 = pos
            while b2 < len(text) and text[b2] not in CLAUSE_BREAK_PAL:
                b2 += 1
            return a, b2

        for m in re.finditer(r"对宫(?:是|为|即)?(?P<pal>" + PAL_GROUP + r")(?:宫)?"
                             r"|对宫(?:是|为|即)?(?P<br>" + BRCLS + r")宫", text):
            # 紧跟在「X宫…对宫」之后的，交给 (a)/(a2)/(b) 处理，避免重复报
            if re.search(NO_LIMIT_PREFIX + r"(?:" + PAL_GROUP + r")(?:宫)?的?$"
                         r"|" + NO_LIMIT_PREFIX + r"[" + BR12_CLS + r"]宫的?$",
                         text[:m.start()]):
                continue
            c_start, _c_end = clause_bounds(m.start())
            clause = text[c_start:m.start()]
            anchor_br = last_anchor(clause)
            if anchor_br is None:
                # 本从句没点名宫位：回看**上一从句**的主语。
                # （「分居命宫两邻，配合对宫官禄的双忌」——上一从句的主语是命宫。）
                # 只回看一层且必须真找到宫名，找不到就不判——不用「远近若干字内有命宫」
                # 这种粗兜底：实测中它会把「需借对宫子女宫…」（田宅借对宫，正确）
                # 误判成命宫的对宫（第四轮实测到的假阳性）。
                p_end = c_start
                while p_end > 0 and text[p_end - 1] in CLAUSE_BREAK_PAL:
                    p_end -= 1
                p_start = p_end
                while p_start > 0 and text[p_start - 1] not in CLAUSE_BREAK_PAL:
                    p_start -= 1
                anchor_br = last_anchor(text[p_start:p_end])
            if anchor_br is None:
                continue
            tok = m.group("pal") or (m.group("br") + "宫")
            bb = branch_of_palace(m.group("pal")) if m.group("pal") else m.group("br")
            if not bb:
                continue
            want = BR12[(BR12.index(anchor_br) + 6) % 12]
            if want != bb:
                rel_error(
                    f"「对宫{tok}」（按本句主语 {anchor_br}宫"
                    f"·{palace_of(chart, anchor_br)} 读）",
                    f"{anchor_br}宫的对宫是{want}宫（{palace_of(chart, want)}），"
                    f"不是{tok}（{bb}）",
                    m.group(0))

        # (d) 三合：「A宫与B宫三合」「A宫在B宫三合之内」「A宫即B宫三合之一」
        def check_trine(a: str, b: str, raw: str) -> None:
            ba = branch_of_palace(a) if a in name_to_branch else a
            bb = branch_of_palace(b) if b in name_to_branch else b
            if not ba or not bb or ba == bb:
                return
            if ba not in [BR12[(BR12.index(bb) + o) % 12] for o in (0, 4, 8)]:
                want = [x for x in [BR12[(BR12.index(bb) + o) % 12] for o in (4, 8)]]
                rel_error(
                    f"「{a}与{b}三合」",
                    f"{a}在{ba}宫、{b}在{bb}宫，两宫并不同三合；"
                    f"{bb}宫的三合宫为 {'、'.join(want)}"
                    f"（{ '、'.join(palace_of(chart, x) for x in want) }）",
                    raw)

        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<a>" + PAL_GROUP + r")"
                             r"(?:宫)?(?:与|和|、)(?P<b>" + PAL_GROUP + r")"
                             r"(?:宫)?(?:互为|互成|为|成|是|入)?三合", text):
            check_trine(m.group("a"), m.group("b"), m.group(0))
        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<a>" + PAL_GROUP + r")"
                             r"(?:宫)?(?:即|在|为|是)(?P<b>" + PAL_GROUP + r")"
                             r"(?:宫)?(?:的)?三合", text):
            check_trine(m.group("a"), m.group("b"), m.group(0))
        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<a>" + BRCLS + r")宫?"
                             r"(?:即|在|为|是)(?P<b>" + PAL_GROUP + r")"
                             r"(?:宫)?(?:的)?三合", text):
            check_trine(m.group("a"), m.group("b"), m.group(0))

        # (e) 两个不同本命宫被说成「同宫」——本命十二宫互不同宫（命身同宫另说）
        for m in re.finditer(NO_LIMIT_PREFIX + r"(?P<a>" + PAL_GROUP + r")(?:宫)?"
                             r"(?:与|和)(?P<b>" + PAL_GROUP + r")(?:宫)?同宫", text):
            if m.group("a") == m.group("b"):
                continue
            rel_error(f"「{m.group('a')}宫与{m.group('b')}宫同宫」",
                      "本命十二宫各占一个地支，两宫不可能同宫（命身同宫说的是宫位标签，"
                      "不是两个本命宫重合）", m.group(0))

    # ── 7.9 大限宫名：正文写「大限/本限 X 宫」时必须与脚本映射一致 ──
    dpmaps = chart.get("decadalPalaceMaps") or []
    if text and dpmaps:
        # 先把全文里所有「限标识」（干支 / 岁数区间 / 年份区间）标出位置，
        # 再据此判断某句话在说哪一个限。取最近的、且在窗口内的那个。
        ids: list[tuple[int, int]] = []
        for k, x in enumerate(dpmaps):
            gz = x.get("ganzhi")
            if gz:
                ids += [(mm.start(), k) for mm in re.finditer(re.escape(gz), text)]
            ar = x.get("ageRange") or []
            if len(ar) == 2:
                for mm in re.finditer(rf"(?<!\d){ar[0]}\s*[~\-–—至到]\s*{ar[1]}(?!\d)", text):
                    ids.append((mm.start(), k))
            yr = x.get("yearRange") or []
            if len(yr) == 2:
                for mm in re.finditer(rf"(?<!\d){yr[0]}\s*[~\-–—至到]\s*{yr[1]}(?!\d)", text):
                    ids.append((mm.start(), k))
        ids.sort()

        def resolve_decadal(pos: int) -> dict | None:
            best = None
            for p0, k in ids:
                if p0 < pos and pos - p0 <= 260:
                    best = k
            if best is not None:
                return dpmaps[best]
            for p0, k in ids:
                if p0 >= pos and p0 - pos <= 160:
                    return dpmaps[k]
            return None

        NAT_TOKEN = r"(?P<nat>" + BRCLS + r"宫|" + PAL_GROUP + r")"

        def nat_branch(tok: str) -> str | None:
            if tok.endswith("宫") and tok[0] in BR12:
                return tok[0]
            return branch_of_palace(tok)

        def check_decadal_claim(cur: dict, dec_name: str, actual_br: str | None,
                                raw: str, desc: str) -> None:
            expect = (cur.get("palaceMap") or {}).get(dec_name)
            if not expect or not actual_br or expect == actual_br:
                return
            F.append(Finding(
                "ERROR", "DECADAL_PALACE_MISNAMED",
                f"{desc}：{cur['ageRange'][0]}~{cur['ageRange'][1]} 虚岁（{cur['ganzhi']}）"
                f"的大限{dec_name}宫在 {expect}宫（本命{palace_of(chart, expect)}），"
                f"而不是 {actual_br}宫（本命{palace_of(chart, actual_br)}）",
                raw))

        # A 式：「申宫在本限是福德宫」「福德在本限为…」
        for m in re.finditer(
                NAT_TOKEN + r"(?:宫)?[^\n。；，,宫本限]{0,10}?"
                r"(?:在本大限|在本限|在大限|于本限|于大限)(?:中|里|内)?"
                r"(?:是|为|即|属|落|作)?(?P<dec>" + PAL_GROUP + r")(?:宫|位)?", text):
            cur = resolve_decadal(m.start())
            if not cur:
                continue
            check_decadal_claim(cur, m.group("dec"), nat_branch(m.group("nat")),
                                m.group(0), "「本限／大限宫名」写法")

        # B 式：「大限命宫落本命福德宫」「本限的福德宫在申宫」
        for m in re.finditer(
                r"(?:大限|本限)(?:的)?(?P<dec>" + PAL_GROUP + r")(?:宫|位)?"
                r"(?:落在|落|为|是|即|在|于|定在)(?:本命|本盘)?"
                + NAT_TOKEN + r"(?:宫|位)?", text):
            cur = resolve_decadal(m.start())
            if not cur:
                continue
            check_decadal_claim(cur, m.group("dec"), nat_branch(m.group("nat")),
                                m.group(0), "「大限宫名＋落点」写法")

        # C 式：「申宫是大限夫妻宫」
        for m in re.finditer(
                NAT_TOKEN + r"(?:宫)?[^\n。；，,]{0,4}?"
                r"(?:是大限|是本限|即为大限|即为本限|属大限|属本限)"
                r"(?P<dec>" + PAL_GROUP + r")(?:宫|位)?", text):
            cur = resolve_decadal(m.start())
            if not cur:
                continue
            check_decadal_claim(cur, m.group("dec"), nat_branch(m.group("nat")),
                                m.group(0), "「本命宫＝大限某宫」写法")

    # ── 7.10 生年四化标记完整性：禄/权/科/忌四类都必须在报告里出现 ──
    # 盘面四类齐全而报告只标了三类，等于对读者隐藏了一条四化（第三轮 MINOR：
    # 右弼生年科在命盘全图与十二宫一览表里整条不见，全文「生年科」零命中）。
    if text:
        types_present: list[str] = []
        for bm in (chart.get("birthMutagen") or []):
            t = bm.get("type")
            if t and t not in types_present:
                types_present.append(t)
        missing_marks = [t for t in ("禄", "权", "科", "忌")
                         if t in types_present and f"生年{t}" not in text]
        if missing_marks:
            mark_desc = "、".join(f"{x['star']}化{x['type']}"
                                  for x in (chart.get("birthMutagen") or [])
                                  if x.get("type") in missing_marks)
            F.append(Finding(
                "ERROR", "MUTAGEN_MARK_INCOMPLETE",
                "报告缺少生年四化标记：" + "、".join(f"生年{t}" for t in missing_marks)
                + f"（盘面该类型为 {mark_desc}）——盘面有的四化必须在星曜表/宫位图上标全，"
                  "不能让读者以为这一类不存在",
                "；".join(f"{x['star']}化{x['type']}@{x['branch']}"
                          for x in (chart.get("birthMutagen") or []))))

    # ── 7.11 「同宫 A 与 B」——关系词在前的写法 ──
    # 第三轮 R3-B2：「救格在同宫天钺与天同旺」把未宫的天钺与申宫的天同说成同宫。
    # 旧版只认「A 与 B 同宫」，写法一颠倒就漏。
    if text:
        for m in re.finditer(r"同宫(?P<mid>[^\n。；，,、]{0,4})(?P<a>" + star_alt + r")"
                             r"(?:星)?(?:与|和|、)(?P<b>" + star_alt + r")", text):
            if text[m.start() - 1: m.start()] in ("不", "非", "未", "无", "没", "难"):
                continue
            check_same_palace(m.group("a"), m.group("b"), m.group(0))

    # ── 7.12 「X 被 A、B 所夹」/「X 被 AB 夹」——夹宫术语的被动式 ──
    # 第三轮 MINOR：地空、地劫与贪狼**同宫**（巳），报告写「禄被夹制」。
    # 同宫不是夹；这一式专门抓「被…夹」的写法，主语可以是星名，也可以是
    # 「禄/忌」这类四化简写（按盘面生年四化找到承载它的那颗星所在之宫）。
    if text:
        abbr_alt = "|".join(FLANK_ABBR)
        lu_ji_branch: dict[str, str] = {}
        for bm in (chart.get("birthMutagen") or []):
            if bm.get("type") in ("禄", "忌") and bm["type"] not in lu_ji_branch:
                lu_ji_branch[bm["type"]] = bm["branch"]
            if bm.get("type") in ("禄", "忌"):
                lu_ji_branch.setdefault("生年" + bm["type"], bm["branch"])

        def subject_branch(tok: str) -> str | None:
            if tok in lu_ji_branch:
                return lu_ji_branch[tok]
            return star_pos(tok)

        for m in re.finditer(r"(?P<s>" + star_alt + r"|生年禄|生年忌|化禄|化忌|禄|忌)"
                             r"(?:星)?被(?P<g>" + abbr_alt + r")(?:所)?夹", text):
            pair = FLANK_ABBR.get(m.group("g"))
            if not pair:
                continue
            sb = subject_branch(m.group("s"))
            if not sb:
                continue
            ok, nb = need_flanks(sb, pair[0], pair[1])
            if ok:
                continue
            p1, p2 = star_pos(pair[0]), star_pos(pair[1])
            if p1 and p2 and p1 == p2:
                why = f"{pair[0]}与{pair[1]}同在 {p1}宫（{palace_of(chart, p1)}）——同宫不是夹"
            else:
                why = (f"{pair[0]}在 {p1}宫、{pair[1]}在 {p2}宫，"
                       f"而 {sb}宫（{palace_of(chart, sb)}）的两邻只有 {sorted(nb)}")
            F.append(Finding(
                "ERROR", "FLANK_MISJUDGED",
                f"报告称「{m.group('s')}被{m.group('g')}夹」，但 {why}",
                m.group(0)))


    # ── 7.13 六吉六煞「配对」断言：与脚本 pairingFacts 不符即报错 ──
    # 第五轮 BLOCKER 的根因：这份算术被手写进散文——报告在四处断言
    # 「六吉六颗…两两不成对宫（不是成对相拱）」「吉星无法配对」，并据此推出
    # 「化解力量不成体系」；而盘面三对俱成对拱（左辅文昌同宫@辰、右弼文曲同宫@戌，
    # 辰戌对拱；魁钺丑未对拱）。修法不是把这句话改对，而是让它不用手写：
    # 配对事实由 astro.pairing_facts 产出、渲染器印成表，正文只许引用。
    # 本检测器按同一份事实核对正文里的成对／同宫／对拱断言。
    pf = chart.get("pairingFacts") or {}
    pf_pairs = pf.get("pairs") or []
    if text and pf_pairs:
        GROUP_ALIAS = {
            "六吉": "六吉", "吉星": "六吉", "吉曜": "六吉",
            "六煞": "六煞", "煞星": "六煞", "煞曜": "六煞",
            "辅弼": "辅弼", "昌曲": "昌曲", "魁钺": "魁钺",
            "羊陀": "羊陀", "火铃": "火铃", "空劫": "空劫",
        }
        pair_alt = "|".join(GROUP_ALIAS)
        # 间隔上限压到 8 字：顿号与逗号**放行**（「六吉六颗俱全，两两不成对宫」是自然写法），
        # 但宫位记号（@ · | ／ 数字）与句读/括号一律排除——
        #   · @ · 数字 一排除，渲染出来的配对表就不会被当成断言（自伤假阳性）；
        #   · ／ 一排除，「六吉六煞的成对／同宫／对拱」这种**并列术语**不会被读成断言。
        pgap = r"[^\n。；;：:！？（）()@·|\d／]{0,8}"
        by_abbr = {x.get("abbr"): x for x in pf_pairs}

        def pairs_of(key: str) -> list[dict]:
            if key in ("六吉", "六煞"):
                return [x for x in pf_pairs if x.get("group") == key]
            x = by_abbr.get(key)
            return [x] if x else []

        def pairing_issue(key: str, kind: str, raw: str) -> None:
            # 群组词（六吉／六煞／吉星／煞星）只判「成对／不成对」这两类整体命题。
            # 「六吉同宫」「六吉对拱」在不知道指哪一对时没有确定的真值——
            # 脚本自己渲染的计数句（「六吉三对之中，同宫或对拱者 3/3」）正是这种写法，
            # 硬判就会自伤（干净夹具实测到的假阳性）。成对／不成对则整体可判。
            if kind in ("same", "not_same", "opposite") and key in ("六吉", "六煞"):
                return
            ps = [x for x in pairs_of(key)
                  if x.get("branch1") and x.get("branch2")]
            if not ps:
                return
            paired = [x for x in ps if x.get("paired")]
            same = [x for x in ps if x.get("samePalace")]
            opp = [x for x in ps if x.get("opposite")]
            bad = {
                "not_paired": bool(paired),
                "paired": not paired,
                "same": not same,
                # 「不同宫」是相反方向的断言：只有当那对星确实同宫时才算错
                "not_same": bool(same),
                "opposite": not opp,
            }[kind]
            if not bad:
                return
            fact = "；".join(
                f"{x['star1']}@{x.get('branch1')}·{x.get('palace1')}、"
                f"{x['star2']}@{x.get('branch2')}·{x.get('palace2')} → {x.get('relation')}"
                for x in ps)
            claim = {
                "not_paired": "不成对（无法配对）",
                "paired": "成对",
                "same": "两两同宫",
                "not_same": "不同宫",
                "opposite": "互为对宫（对拱）",
            }[kind]
            F.append(Finding(
                "ERROR", "AUSPICIOUS_PAIRING_MISSTATED",
                f"报告称「{key}」{claim}，与脚本配对事实不符。脚本：{fact}；"
                f"（脚本判据：「成对」＝同宫或互为对宫；三合遥会不算成对相拱）",
                raw))

        def negated_before_group(m) -> bool:
            """短语之前 8 字内有没有否定词——「并非『吉星无法配对』」这类引用式否定不判。"""
            before = text[max(0, m.start("g") - 8): m.start("g")]
            return bool(re.search(r"[不非无未没难]", before))

        NEG1 = ("不", "非", "未", "无", "没", "难")
        for m in re.finditer(r"(?P<g>" + pair_alt + r")" + pgap
                             + r"(?P<c>不成对宫|不成对拱|不成对|无法配对|不能成对|"
                               r"未成对|无法成对|成不了对)", text):
            if negated_before_group(m):
                continue
            pairing_issue(GROUP_ALIAS[m.group("g")], "not_paired", m.group(0))
        for m in re.finditer(r"(?P<g>" + pair_alt + r")" + pgap
                             + r"(?P<c>成对相拱|两两成对|成对拱|成对|配对)", text):
            # 被否定的写法交给上一条（「不成对」会同时命中本条的「成对」）
            before = text[max(0, m.start("c") - 4): m.start("c")]
            if re.search(r"[不无未难]", before) or negated_before_group(m):
                continue
            pairing_issue(GROUP_ALIAS[m.group("g")], "paired", m.group(0))
        # 「不同宫」是与「同宫」相反的一类断言，单独判：只有当那对星**确实同宫**时才是错。
        for m in re.finditer(r"(?P<g>" + pair_alt + r")" + pgap
                             + r"(?P<c>不同宫|并非同宫|没有同宫|不在同宫)", text):
            if negated_before_group(m):
                continue
            pairing_issue(GROUP_ALIAS[m.group("g")], "not_same", m.group(0))
        for m in re.finditer(r"(?P<g>" + pair_alt + r")" + pgap + r"(?P<c>同宫)", text):
            if text[m.start("c") - 1: m.start("c")] in NEG1 or negated_before_group(m):
                continue
            pairing_issue(GROUP_ALIAS[m.group("g")], "same", m.group(0))
        for m in re.finditer(r"(?P<g>" + pair_alt + r")" + pgap
                             + r"(?P<c>互为对宫|对拱|遥相对拱)", text):
            if text[m.start("c") - 1: m.start("c")] in NEG1 or negated_before_group(m):
                continue
            pairing_issue(GROUP_ALIAS[m.group("g")], "opposite", m.group(0))

    # ══════════════════════════════════════════════════════════════════
    # 7.14 星曜对命宫的「效力层级」：与效力层级尺不符即报错（第六轮新增）
    #
    #   第六轮盲审的两条 BLOCKER 是同一个根因：**同一颗星，作煞时算贴身、
    #   作吉时算「次一等」**——未宫天钺被写成「借宫救应、效力次一等」，
    #   而距命宫更远的戌宫（辰之对宫）反被评为更高一档的「对拱回照」。
    #   层次判断没有一把尺，就必然自相矛盾。尺见
    #   references/interpretation.md §3.2（由近及远）：
    #       1 同宫（贴身）＞ 2 两邻（夹）＞ 3 三合会照 ＞ 4 对宫照 ＞ 5 遥会
    #   其中第 4 级含「三合宫的对宫」——辰是命宫申的三合，戌是辰的对宫，
    #   故戌对命宫属第 4 级（这正是盘面里右弼、文曲那一处）。
    #
    #   只判**能回盘面核对**的两类，宁漏不误报：
    #     ① 点名的宫 + 层级词，与该宫相对命宫的实际层级不符；
    #     ② 「效力次一等／次之」用在第 1～2 级（同宫或两邻）的对象上
    #        ——两邻属贴身层、仅次于同宫，不是「次一等」。
    if text:
        i0 = BR12.index(soul)
        lv2 = {BR12[(i0 - 1) % 12], BR12[(i0 + 1) % 12]}
        lv3 = {BR12[(i0 + 4) % 12], BR12[(i0 + 8) % 12]}
        lv4 = {BR12[(i0 + 6) % 12]} | {BR12[(BR12.index(t) + 6) % 12] for t in lv3}
        LV_NAME = {1: "同宫（贴身）", 2: "两邻（夹，属贴身层）", 3: "三合会照",
                   4: "对宫照（含三合宫的对宫）", 5: "遥会（三方四正之外）"}

        def eff_level(br: str) -> int:
            if br == soul:
                return 1
            if br in lv2:
                return 2
            if br in lv3:
                return 3
            if br in lv4:
                return 4
            return 5

        br_of = {}
        for br, nm in name_of.items():
            br_of.setdefault(nm, br)
        br_of.setdefault("交友", br_of.get("仆役"))

        def pal_br(ps: str) -> str | None:
            if len(ps) == 1 and ps in BR12:
                return ps
            if len(ps) == 2 and ps.endswith("宫") and ps[0] in BR12:
                return ps[0]
            return br_of.get(ps)

        # 间隔里**放行顿号**（「右弼、文曲属对拱回照」是自然写法），但排除逗号／句读／
        # 括号／空白与表格记号。两处实测到的假阳性都靠这一条挡掉：
        #   · 「（兄弟），互为对宫」——括号里的**宫位标签**被当成了断言主语；
        #   · 脚本自己渲染的配对表「天钺@未·兄弟 互为对宫」——去标签后的空格把
        #     表格单元格连成了句子（第五轮配对检测器踩过同一个坑，故同样排除 @ · |）。
        _EFF_EXCL = r"\s。；;，,（）()：:！？@·|\d／"
        eff_gap = r"[^" + _EFF_EXCL + r"]{0,8}"
        eff_gap2 = r"[^" + _EFF_EXCL + r"]{0,6}"
        EFF_WORDS = [("同宫", 1), ("两邻", 2), ("邻宫", 2), ("借宫", 2),
                     ("三合", 3), ("会照", 3),
                     ("对宫", 4), ("对拱", 4), ("回照", 4), ("遥会", 5)]
        eff_alt = "|".join(w for w, _ in EFF_WORDS)
        eff_rank = dict(EFF_WORDS)
        claim_re = re.compile(
            r"(?P<pal>" + BRCLS + r"宫|" + PAL + r")" + eff_gap
            + r"(?:属|为|是|归|作)(?P<g2>" + eff_gap2 + r")(?P<eff>" + eff_alt + r")")
        for m in claim_re.finditer(text):
            ps = m.group("pal")
            br = pal_br(ps)
            if not br:
                continue
            # 「A宫与B宫**同属**某三合」说的是两宫彼此的关系，不是 A 相对命宫的层级
            #（实测到的假阳性：「迁移宫寅同属寅午戌三合」被判成「迁移属第 3 级」）。
            if "同属" in m.group(0):
                continue
            # 「X宫是Y宫的对宫」这种句子说的是 X 与 Y 的关系，不是 X 相对命宫的层级；
            # 只要「是」与层级词之间又出现了别的宫位，且那个宫位不是命宫，就不判
            #（关系类断言由 PALACE_RELATION_MISJUDGED 负责，两把尺不互相抢活）。
            inner = re.findall(BRCLS + r"宫|" + PAL, m.group("g2"))
            if inner and not any(x in ("命宫", soul + "宫") for x in inner):
                continue
            want, got = eff_rank[m.group("eff")], eff_level(br)
            if want == got:
                continue
            F.append(Finding(
                "ERROR", "EFFICACY_LEVEL_MISSTATED",
                f"报告称「{ps}……{m.group('eff')}」，但按效力层级尺，{br}宫相对命宫{soul}宫"
                f"的实际层级是第 {got} 级（{LV_NAME[got]}），不是第 {want} 级"
                f"（{LV_NAME[want]}）",
                m.group(0)))
        # ②「效力次一等」不得用在贴身层（第 1～2 级）。
        #    主语取**前 40 字内最后出现的宫位**，但优先取非命宫的那个——
        #    「未宫天钺（借宫救应，不在命宫三方四正内，效力次一等）」里
        #    「命宫」只是括注，真正被判低的是未宫。
        #    若这 40 字里出现「遥会／三合／对拱」，说明说的是两颗星**彼此**的关系
        #    （「两颗成三合遥会，遥会之力次一等」），不是相对命宫的层级，跳过——
        #    这一条是实测出来的假阳性，不跳过会误伤铃贪格那类正确写法。
        for m in re.finditer(r"(?:效力|力量|力)(?:上)?(?:次一等|次一档|次一层|次之|低一等)", text):
            win = text[max(0, m.start() - 40): m.start()]
            # 只看**紧邻的 12 字**：够判「遥会之力次一等」这类把关系词当限定语的写法，
            # 又不会把前一句的三合／对拱误当成限定语（40 字窗口实测会误判）。
            if re.search(r"遥会|三合|对拱", text[max(0, m.start() - 12): m.start()]):
                continue
            # 括注里的宫名只是说明（「（天钺不在命宫三方四正内）」），不是被判低的主语。
            # 但括注常常**跨越**这一处（左括号在前、右括号在后），那样不会被这条规则括掉，
            # 于是下面「优先取非命宫的那个」就是第二道保险。
            win = re.sub(r"[（(][^）)]*[）)]", " ", win)
            # ⚠️ 变量名不能叫 `cands`：外层已有同名的**格局候选字典**（第 4 节），
            # 这里再绑成 list 会把它覆盖掉，后面按格子查裁断时就会
            # `'list' object has no attribute 'get'`（本轮实测到的坑）。
            pal_cands = re.findall(BRCLS + r"宫|" + PAL + r"|(?<=邻宫)" + BRCLS, win)
            if not pal_cands:
                continue
            non_soul = [x for x in pal_cands if x not in ("命宫", soul + "宫")]
            ps = (non_soul or pal_cands)[-1]
            br = pal_br(ps)
            if not br:
                continue
            got = eff_level(br)
            if got <= 2:
                F.append(Finding(
                    "ERROR", "EFFICACY_LEVEL_MISSTATED",
                    f"报告称「{ps}……」的效力「{m.group(0)}」，但按效力层级尺，{br}宫相对"
                    f"命宫{soul}宫是第 {got} 级（{LV_NAME[got]}）——两邻属贴身层、"
                    "仅次于同宫，只有第 3 级（三合）及以后才可称「效力次一等」",
                    text[max(0, m.start() - 40): m.end() + 10]))



    # ══════════════════════════════════════════════════════════════════
    # 7.15 破格／救格栏不许串味（第六轮新增）
    #
    #   「破格」栏只许写**削弱该格的外部条件**；把「救格被削弱」写进破格栏，
    #   或反过来把破格内容写进救格栏，都属概念误用——references/interpretation.md §4
    #   早有明文，但一直没有机器判据，于是第六轮盲审又抓到一次：火铃夹命行把
    #   **本格的形成形态**（亮度只居中上、两邻配置不对称）当破格，又把
    #   「救格折损」并进同一个负面框。
    #
    #   判据只认**带标签的两段式**（`break_save = [{label, text}, …]`，
    #   渲染成 `<b>标签</b>：正文`）：标签说这是哪一栏，正文不得写另一栏的事。
    #   单段式没有标签，无从判定，跳过（宁漏不误报）。
    def breaksave_issue(lab: str, body: str, raw: str) -> None:
        if not lab or not body:
            return
        if "破格" in lab and re.search(r"救格|救应|折损", body):
            F.append(Finding(
                "ERROR", "PATTERN_BREAKSAVE_MISLABELED",
                f"格局栏「{lab}」的正文写的是救格一侧的事（{body[:36]}…）——"
                "破格说本格被什么外部条件削弱，救格被削弱属救格一侧，两栏不得串味",
                raw))
        # 第二类串味：把**本格的形成形态**当破格。亮度的绝对高低（「只居中上」）
        # 与两邻配置的对称性，是这一格「长成什么样」，不是「外部有什么在削弱它」。
        # 真正的外部破格条件指煞冲、空劫、失辉、宫破、逢冲破这一些。
        if "破格" in lab and re.search(r"不对称|不极端|只居中上|配置不", body):
            F.append(Finding(
                "ERROR", "PATTERN_BREAKSAVE_MISLABELED",
                f"格局栏「{lab}」写的是本格的「形成形态」（{body[:36]}…）——"
                "亮度高低与两邻配置是否对称属「层次／效力说明」，"
                "破格只列外部削弱条件（煞冲、空劫、失辉、宫破、逢冲破）",
                raw))
        if re.search(r"救格|折损", lab) and re.search(r"破格", body):
            F.append(Finding(
                "ERROR", "PATTERN_BREAKSAVE_MISLABELED",
                f"格局栏「{lab}」的正文写的是破格一侧的事（{body[:36]}…）——"
                "救格栏不得承载破格内容",
                raw))

    if report_markup:
        for m in re.finditer(r"<b>(?P<lab>[^<>]{1,24})</b>(?P<txt>[^<]{1,600})",
                             report_markup):
            breaksave_issue(m.group("lab"), m.group("txt"), m.group(0)[:200])
    if analysis is not None and not report_markup:
        # 同一判据在 analysis 原文上也走一遍——但**只在没有 HTML 时**：
        # 成品里标签已经渲染出来，两条路径都跑会把同一条问题报两遍。
        for pt in (analysis.get("patterns") or []):
            bs = pt.get("break_save")
            if not isinstance(bs, list):
                continue
            for x in bs:
                if isinstance(x, dict):
                    breaksave_issue(str(x.get("label") or ""), str(x.get("text") or ""),
                                    json.dumps(x, ensure_ascii=False)[:200])

    # ══════════════════════════════════════════════════════════════════
    # 7.16～7.19 第七轮新增（盲审 B2／B3／B4 的共同根因：**否定式断言与计数**
    #   也属事实层，却一直在检测器覆盖之外）
    #
    #   第六轮教训是「判断层里也有可机器核对的算术」；第七轮的教训更直白：
    #   **肯定式断言被核对了，否定式断言没有**——
    #     · 「寅申既不同宫，也不互为对宫或三合」——寅申正是对宫，全篇却零报错；
    #     · 「禄存@申与化禄@辰都在命宫午三方四正之外」被写成「一在内一在外」；
    #     · 格局表内 3 成立／13 不成立／1 存疑，汇总句却写「成立四项」。
    #   这三条都能落到盘面事实上，所以照第四～六轮的办法：搬进脚本。
    # ══════════════════════════════════════════════════════════════════
    # ── 通用：把宫名或地支 token 归一到地支 ──
    _br_of_tok_cache: dict[str, str | None] = {}

    def _br_of_tok(tok: str) -> str | None:
        if tok in _br_of_tok_cache:
            return _br_of_tok_cache[tok]
        v: str | None
        if len(tok) == 1 and tok in BR12:
            v = tok
        elif tok.endswith("宫") and tok[0] in BR12:
            v = tok[0]
        else:
            v = branch_of_palace(tok.rstrip("宫"))
        _br_of_tok_cache[tok] = v
        return v

    _PAL_ALT = "|".join(PAL_NAMES)
    _ANCHOR_RE = re.compile(_PAL_ALT + r"|" + BRCLS + r"宫")
    _CLAUSE_BREAK = "。；;！？\n，,、：:（）()—…"
    # 句边界：**只**在句末标点与换行处截断。逗号、顿号、括注都不算——
    # 「申与寅不同宫，也不互为对宫」里宫位在前一小句，逗号截断会把锚点切掉。
    _SENT_BREAK = "。；;！？\n"

    def _anchors_in(s: str) -> list[str]:
        """按**出现次序**返回这段文字里点到的地支（去重）。

        ⚠️ 必须保序，不能排序：救格栏的句式是「A……属第 N 级；B……属第 M 级」，
        取前两个才是栏里真正点名的两个宫位。按地支排序会让「未」排到「午」前面，
        于是取前两个时反而漏掉真正该报的那一个（本轮实测踩到过）。
        """
        out: list[str] = []
        for am in _ANCHOR_RE.finditer(s):
            b = _br_of_tok(am.group(0))
            if b and b not in out:
                out.append(b)
        return out

    def _rel_kind(a: str, b: str) -> str:
        """两支之间的关系：same / trine / opposite / other。"""
        if not a or not b:
            return "other"
        d = (BR12.index(b) - BR12.index(a)) % 12
        if d == 0:
            return "same"
        if d in (4, 8):
            return "trine"
        if d == 6:
            return "opposite"
        return "other"

    if text:
        # ── 7.16 否定式宫位关系断言（第七轮新增）──
        #   第七轮 BLOCKER 2-①：「禄存在申、天马在寅，既不同宫，也不互为对宫或三合」
        #   ——寅申正是对宫。旧版 PALACE_RELATION_MISJUDGED 只认肯定式，这句一路绿灯。
        #   判据：否定短语 + 其前一小段里出现的两个宫位/地支；两者实际关系与否定相反即报。
        #   只要「否定与事实相反」这一种，判不出就放过（宁漏不误报）。
        # 已被 7.17 按肯定式判过的（A与B互为对宫而其实不是），不要在这里重复报一遍
        _aff_bad: set[int] = set()
        for _m0 in re.finditer(BRCLS + r"(?:宫)?(?:与|和|、)" + BRCLS + r"(?:宫)?"
                               r"(?:两宫)?(?:互为|互成|彼此)?(?:对宫|对拱|相对)(?!照)", text):
            _aff_bad.add(_m0.start())

        # 否定短语 → 被否定的关系类型。
        # ⚠️ 短语互为前缀（「不同宫」与「不互为对宫」共享首字「不」），
        #    所以**逐个短语各自扫描**再把命中位置合并，不能拼成一条 alternation：
        #    同一个起始位置上 alternation 只试一次，短的命中后正则从它末尾继续，
        #    紧跟其后的长短语整段被跳过（实测：「子与午既不同宫，也不互为对宫」
        #    的后半句一直漏检，就是被前面的「不同宫」吃掉了扫描进度）。
        DENY_CLAIMS = [
            ("不互为对宫", "opposite"),
            ("不成对宫", "opposite"),
            ("不对宫", "opposite"),
            ("不是对宫", "opposite"),
            ("非对宫", "opposite"),
            ("不同宫", "same"),
            ("并非同宫", "same"),
            ("不共处一宫", "same"),
            ("不互为三合", "trine"),
            ("不成三合", "trine"),
            ("不属三合", "trine"),
            ("不同三合", "trine"),
        ]
        _hits = []
        for _ph, _kd in DENY_CLAIMS:
            for _mm in re.finditer(re.escape(_ph), text):
                _hits.append((_mm.start(), _ph, _kd))
        _hits.sort()

        # 判据只认**分句**：以句末标点与逗号顿号切分，取含有该否定短语的那一个分句。
        #   ① 不许跨句：跨过句号会把上一句的宫位拉进来 → 正确句子被误报；
        #   ② 必须连着看：「子与午既」与「也不互为对宫」被逗号切成两段，
        #      主语在前一段，所以要看「当前段 + 前一段」。
        _CLAUSE_SPLIT = re.compile(r"[，,、；;。！？\n]")
        # 分句尾部允许的连接词/标点（最多 4 字）：吃「两宫既」这类尾巴，
        # 又不会一路吃到别的分句上去。
        _TAIL4 = (r"(?:(?:两宫|彼此|双方)|[ \t]|"
                  r"(?![不非未无])[都均皆并既也且又，,、]){0,4}$")
        # ⚠️ 尾部这个 `(?![不非未无])` 不能省：没有它，正则会把「既**不同**」
        #    整段当成尾部连接词吃掉，于是「子宫与丑宫既不同宫」被读成一次
        #    「子宫与丑宫同宫」的断言，正确句子反而被判成矛盾（实测踩到过）。
        _PAIR_RE = re.compile(r"(?P<a>" + BRCLS + r")(?:宫)?[ \t]*(?:与|和|、|vs)[ \t]*"
                              r"(?P<b>" + BRCLS + r")(?:宫)?" + _TAIL4)
        _CONN_RE = re.compile(
            r"[也且又都均皆并]*[，,]?[ \t]*"
            r"不(?:互为|为|成|是|同|属|共处|相)?"
            r"(?P<rel>对宫|对拱|相对|同宫|同一宫|同处一宫|三合|同一三合)")

        for _pos, phrase, kind in _hits:
            # 同一处已被 7.17 按肯定式判过，不再重复报
            if _pos in _aff_bad or (_pos - 1) in _aff_bad:
                continue
            _parts = _CLAUSE_SPLIT.split(text[: _pos])
            _clause = _parts[-1]
            if len(_parts) >= 2:
                # 拼回前一段的主语（「子与午既」+「不」）
                _clause = _parts[-2] + _clause
            # 短语之后紧跟的连接词决定这条否定在讲哪种关系：
            #   「不同宫」讲同宫、「不互为对宫」讲对宫、「不成三合」讲三合。
            # 两者不一致时跳过（避免同一条短语被算成两种关系各报一次）。
            _cm = _CONN_RE.match(text[_pos: _pos + 14])
            if _cm:
                _rel = _cm.group("rel")
                _ck = ("opposite" if _rel in ("对宫", "对拱", "相对")
                       else "same" if _rel.startswith("同") else "trine")
                if _ck != kind:
                    continue
            elif kind == "same":
                # 非对称句式里不判「同宫」的否定：「同宫」这个**被否定的名词本身**
                # 含「宫」字，用宽松窗口取锚点会把「子宫与丑宫既不同宫」读成
                # 「子宫与丑宫同宫」而反被判成矛盾。
                continue
            # ⚠️ 配对只能在**否定词之前**的那截文字里找：
            #    拼回前一分句后，片段里会包含「…不同宫也」这样把否定词也带进来的尾巴，
            #    直接 search 会匹配失败（更糟的是若尾部匹配放宽，会把被否定的名词本身
            #    当成断言）。先在否定词处截断，再取**它之前**的那一段找「甲与乙」。
            #    注意 split 要带捕获组：`re.split(r"[不非未无]", s)[-1]` 取到的是
            #    否定词**之后**的残句，正好把要找的主语丢掉（实测踩到过）。
            _head_m = re.match(r"^(?P<h>.*)[不非未无]", _clause, re.S)
            _head = (_head_m.group("h") if _head_m else _clause)
            _pm = _PAIR_RE.search(_head)
            if _pm:
                brs = [_pm.group("a"), _pm.group("b")]
            else:
                brs = _anchors_in(_head)[-2:]
            if len(brs) < 2 or brs[0] == brs[1]:
                continue
            a, b2 = brs[0], brs[1]
            if _rel_kind(a, b2) != kind:
                continue
            want = {"same": "同宫", "trine": "三合", "opposite": "互为对宫（对拱）"}[kind]
            F.append(Finding(
                "ERROR", "PALACE_RELATION_DENIED_WRONGLY",
                f"报告称「…{_clause.strip()}{phrase}…」，即否认 {a}宫与 {b2}宫{want}；"
                f"但盘面 {a}宫（{palace_of(chart, a)}）与 {b2}宫（{palace_of(chart, b2)}）"
                f"正是{want}——否定式断言与宫位关系表矛盾",
                text[max(0, _pos - 40): _pos + len(phrase)]))

        # ── 7.17 否定式／肯定式「互为对宫」两端都是宫位（第七轮新增）──
        #   第七轮 BLOCKER 2-②：「天府在酉、天相在丑，两宫互为对宫」——酉丑实为三合。
        #   旧版只核「X宫的对宫是Y宫」与「A与B三合」，没核「A与B互为对宫/相对」。
        for m in re.finditer(
                r"(?P<a>" + BRCLS + r"|" + _PAL_ALT + r")(?:宫)?(?:与|和|、)"
                r"(?P<b>" + BRCLS + r"|" + _PAL_ALT + r")(?:宫)?"
                r"(?:两宫)?(?:互为|互成|彼此)?(?P<rel>对宫|对拱|相对)(?!照)", text):
            a, b2 = m.group("a"), m.group("b")
            ba, bb = _br_of_tok(a + "宫" if a.endswith("宫") else a), \
                _br_of_tok(b2 + "宫" if b2.endswith("宫") else b2)
            if not ba or not bb:
                continue
            if _rel_kind(ba, bb) == "opposite":
                continue
            rel = _rel_kind(ba, bb)
            F.append(Finding(
                "ERROR", "PALACE_RELATION_MISJUDGED",
                f"报告称「{a}与{b2}互为对宫」，但盘面 {a}（{ba}宫）与 {b2}（{bb}宫）的关系是"
                + {"same": "同宫", "trine": f"三合（{bb}宫的三合宫含 {ba}宫）",
                   "other": "既不同宫、也不三合、也不互为对宫"}[rel]
                + f"——{bb}宫的对宫是 {BR12[(BR12.index(bb) + 6) % 12]}宫"
                  f"（{palace_of(chart, BR12[(BR12.index(bb) + 6) % 12])}）",
                m.group(0)))

        # ── 7.18 「在／不在命宫三方四正（三合）之内／之外」的地支主语写法 ──
        #   第七轮 BLOCKER 4：「两处来路一在命宫三合之外、一在命宫三合之内」
        #   ——禄存@申与化禄@辰都在命宫午的三方四正之外。
        #   旧版 SURROUND_MISJUDGED 只认**星名**主语，遇到「申／辰」这种地支主语就漏。
        _TRI_WORD = r"(?:三方四正|三合)"
        _TAIL = r"(?P<tail>之内|以内|之外|以外)?"
        _PAIREN = (r"(?:[（(][子丑寅卯辰巳午未申酉戌亥\s、,，]*[）)])?")
        # A 式：地支在前——「申宫在命宫午的三方四正之外」「辰在命宫三合之内」
        for m in re.finditer(
                r"(?<!大限)(?<!本限)(?<!流年)(?<!小限)(?<!本命)"
                r"(?P<br>" + BRCLS + r")(?:宫)?"
                r"(?P<neg>都不在|均不在|皆不在|不在|均在|都在|皆在|在)"
                + gap + r"(?:命宫" + soul + r"的?)?" + _TRI_WORD
                + gap + _PAIREN + r"[^\n]{0,4}?" + _TAIL, text):
            after = text[m.end(): m.end() + 12]
            if any(t in after for t in NEG_AFTER):
                continue
            inside = relation_inside(m.group(0))
            if inside is None:
                continue
            br = m.group("br")
            actually = br in surround_branches
            if actually == inside:
                continue
            F.append(Finding(
                "ERROR", "SURROUND_MISJUDGED",
                f"报告称「{m.group(0).strip()}」——即认为 {br}宫 在命宫{soul}三方四正"
                f"{'之内' if inside else '之外'}，但盘面 {br}宫（{palace_of(chart, br)}）"
                f"{'在' if actually else '不在'}其内；命宫{soul}三方四正为 "
                f"{sorted(surround_branches)}",
                m.group(0)))
        # B 式：方位词在前、主语在后——「两处落点都在命宫午的三方四正之外」
        for m in re.finditer(
                r"(?P<neg>都不在|均不在|皆不在|不在|均在|都在|皆在|在)"
                + gap + r"(?:命宫" + soul + r"的?)?" + _TRI_WORD
                + gap + _PAIREN + r"[^\n]{0,4}?(?P<tail>之内|以内|之外|以外)", text):
            after = text[m.end(): m.end() + 12]
            if any(t in after for t in NEG_AFTER):
                continue
            inside = relation_inside(m.group(0))
            if inside is None:
                continue
            # 主语只能向前找**本从句**里的地支（已经点名的落点）
            c_start = m.start()
            while c_start > 0 and text[c_start - 1] not in _CLAUSE_BREAK:
                c_start -= 1
            subj = _anchors_in(text[c_start:m.start()])
            wrong = [x for x in subj if (x in surround_branches) != inside]
            if not wrong:
                continue
            det = "；".join(f"{x}@{x}宫（{palace_of(chart, x)}）" for x in wrong)
            F.append(Finding(
                "ERROR", "SURROUND_MISJUDGED",
                f"报告称「{m.group(0).strip()}」——即认为 {''.join(wrong)} 在命宫{soul}"
                f"三方四正{'之内' if inside else '之外'}，但盘面：{det}；"
                f"命宫{soul}三方四正为 {sorted(surround_branches)}",
                text[c_start:m.end()]))

    # ── 7.19 格局「按裁断类别」的计数一致性（第七轮新增）──
    #   第七轮 BLOCKER 3：表内 3 成立／13 不成立／1 存疑，汇总句写「成立四项」，
    #   第九步写「条件与落点同时成立的…四项」。旧 COUNT_MISMATCH 只对
    #   patternCandidates 的总数，管不了「成立 N 项」这类**分类计数**。
    #   判据：从报告自己的格局表按结论列（span class）分类计数，再核正文里的分类计数。
    if report_markup is not None:
        _row_all = re.compile(r"<tr><td>(?P<name>[^<]+)</td>(?P<body>(?:(?!</tr>).)*?)</tr>",
                              re.S)
        _verdict_span = re.compile(r"<span class=\"(?P<cls>bright|dim|pending)\">"
                                   r"(?P<v>存疑／并列|存疑|成立|不成立)</span>")
        tbl_counts = {"成立": 0, "不成立": 0, "存疑": 0}
        tbl_verdict_of: dict[str, str] = {}
        for m in _row_all.finditer(report_markup):
            v = verdict_of(m.group(0))
            if not v:
                continue
            key = "存疑" if v in ("存疑／并列", "存疑") else v
            tbl_counts[key] += 1
            tbl_verdict_of[m.group("name").strip()] = key
        if sum(tbl_counts.values()):
            if text:
                _CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
                _NUM = r"(?:[0-9]{1,2}|[一二两三四五六七八九十])"
                # 两种语序都要认：
                #   ① 数词在前——「成立 5 项，不成立 11 项」（本报告与夹具的写法）；
                #   ② 结论词在前——「裁为不成立的共 11 项，其余…」（第七轮原文的写法）。
                #   只认一种就会让另一种语序整类漏检（实测踩到过：夹具把数词写在
                #   结论词之后，规则接不上，看起来像规则坏了）。
                _COUNT_RES = [
                    # ⚠️ 前置边界用**零宽负向先行**写，不能用后行断言：
                    #    Python 的 look-behind 要求定宽，而 `_NUM` 是「1~2 位数字
                    #    或一个汉字」的变宽集合，直接编译就报
                    #    `look-behind requires fixed-width pattern`（实测踩到过）。
                    (re.compile(r"(?P<num>" + _NUM + r")\s*项"
                                # 数词与结论词之间只许空白或标点：允许任意间隔会跨小句
                                # 误配（「…共 11 项，**存疑**并列的共 1 项」）。
                                r"\s*[，,]?\s*"
                                r"(?P<verdict>不成立|成立|存疑)"
                                r"(?!并列)"
                                r"(?![的之]?\s*共?\s*" + _NUM + r")"),
                     lambda mm: mm.group("num"),
                     lambda s, st: not (st > 0 and re.match(r"[0-9" + "一二两三四五六七八九十"
                                                            r"]$", s[st - 1]))),
                    (re.compile(r"(?P<verdict>不成立|成立|存疑)"
                                r"(?P<mid>[^\n。；，,]{0,6}?)"
                                r"(?:共|有)?\s*(?P<num>" + _NUM + r")\s*项"),
                     lambda mm: mm.group("num"),
                     lambda s, st: True),
                ]
                _seen_counts: set[tuple[int, str, int]] = set()
                for _rx, _numof, _ok_before in _COUNT_RES:
                    for m in _rx.finditer(text):
                        if not _ok_before(text, m.start()):
                            continue
                        _raw_num = _numof(m)
                        num = int(_raw_num) if _raw_num.isdigit() \
                            else _CN_NUM.get(_raw_num, -1)
                        if num < 0:
                            continue
                        vk = m.group("verdict")
                        vkey = "存疑" if vk == "存疑" else vk
                        # 两种语序可能命中同一处，去重（否则同一条问题报两遍）
                        _sig = (m.start("num"), vkey, num)
                        if _sig in _seen_counts:
                            continue
                        _seen_counts.add(_sig)
                        # 只在该数字确属「格局裁断」语境时判：
                        #   ① 数字后**同一行**内点到格局名；或 ② 该句出现「格局」二字。
                        # 格局名只能在本**行**内找：表格被拍平后，下一行的格局名
                        # 会紧跟在数字后面（「…共 11 项。 格局 是否成立 … 杀破狼」），
                        # 跨行匹配会把不相干的格名算进来（实测到的假阳性）。
                        line_end = text.find("\n", m.start())
                        tail = text[m.start(): (line_end if line_end > 0 else m.start() + 90)]
                        named = [k for k in sorted(cands, key=len, reverse=True)
                                 if re.search(r"(?<![（(])" + re.escape(k), tail)]
                        if not named and "格局" not in text[max(0, m.start() - 30):m.end() + 10]:
                            continue
                        # ⚠️ 格名只有以**穷举词**（即／为／是／如下／分别是／包括）紧接在
                        # 计数之后时，才构成「这两项就是哪两项」的断言；否则行文里随后
                        # 提到的格名只是顺带引用，拿来对比必误报
                        # （实测：同一行先「成立两项（A、B）」、后面又提到 C，
                        #  旧写法把 C 也拉进来，于是正确句被判成矛盾）。
                        after = text[m.end(): m.end() + 90]
                        # 穷举列表只取**本句**（句末标点截断）：跨到下一句就会把别句
                        # 提到的格名也算进来（实测：「成立的两项是 A 与 B；条件满足而
                        # 落点不在位的一项是 C」里的 C 被当成第三项）。
                        _cut = len(after)
                        for _p in "。；;！？\n":
                            _k = after.find(_p)
                            if _k >= 0:
                                _cut = min(_cut, _k)
                        after_sent = after[:_cut]
                        enum_names = []
                        if re.match(r"\s*[（(]?\s*(?:即|为|是|如下|分别是|包括)[^\n。；]{0,4}",
                                    after_sent):
                            enum_names = [k for k in sorted(cands, key=len, reverse=True)
                                          if re.search(r"(?<![（(])" + re.escape(k), after_sent)]
                        if enum_names:
                            if all(tbl_verdict_of.get(n) == vkey for n in enum_names) \
                                    and len(enum_names) == num and tbl_counts[vkey] == num:
                                continue
                            F.append(Finding(
                                "ERROR", "COUNT_MISMATCH",
                                f"报告称「{m.group(0).strip()}」，随后穷举了 {enum_names}，"
                                f"但该表述与报告自己的格局表不符：表里 {vkey} 的有 "
                                f"{tbl_counts[vkey]} 项（成立 {tbl_counts['成立']}／"
                                f"不成立 {tbl_counts['不成立']}／存疑 {tbl_counts['存疑']}）；"
                                f"所列各格在表内的裁断为 "
                                f"{[tbl_verdict_of.get(n) for n in enum_names]}",
                                text[max(0, m.start() - 40):m.end() + 90]))
                            continue
                        if tbl_counts[vkey] == num:
                            continue
                        F.append(Finding(
                            "ERROR", "COUNT_MISMATCH",
                            f"报告称「{m.group(0).strip()}」，但报告自己的格局表里"
                            f"{vkey}的有 {tbl_counts[vkey]} 项"
                            f"（成立 {tbl_counts['成立']}／不成立 {tbl_counts['不成立']}／"
                            f"存疑 {tbl_counts['存疑']}）",
                            text[max(0, m.start() - 40):m.end() + 40]))

    # ── 7.20 格局救格栏的锚点校验（第七轮新增）──
    #   第七轮 BLOCKER 1-②：日月反背的锚点在**辰、戌**，报告给的救应却是
    #   「左辅@午与命宫同宫＝第一级」「天钺@未＝第二级贴身层」——午只是戌的三合宫
    #   （对命宫而言才是同宫），未与辰戌毫无关系。
    #   判据：救格栏里出现的宫位，与**该格的锚点宫**必须有可指认的关系
    #   （同宫／两邻／三合／对宫照）；完全无关系却在救格栏里当救应，即报。
    #   只在栏内出现「救」字时判，避免把「破格栏提到的无关宫位」误伤。
    if report_markup is not None:
        _row2 = re.compile(r"<tr><td>(?P<name>[^<]+)</td>(?P<body>(?:(?!</tr>).)*?)</tr>", re.S)
        _cell = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
        _LABEL = re.compile(r"<b>(?P<lab>[^<>]{1,24})</b>(?P<txt>[^<]{1,600})")
        # 锚点宫取该格 evidence 里记录的地支；夹宫类取「两邻」而不是星曜落点。
        for m in _row2.finditer(report_markup):
            row_name = m.group("name").strip()
            key = row_name if row_name in cands else re.sub(r"[（(].*?[）)]", "", row_name).strip()
            cand = cands.get(key) or cands.get(row_name)
            if not cand:
                continue
            anchors = set(branches_in_evidence(cand))
            if cand.get("pattern") in FLANK_PATTERNS or "两邻" in str(cand.get("evidence") or ""):
                i3 = BR12.index(soul)
                anchors = {BR12[(i3 - 1) % 12], BR12[(i3 + 1) % 12]}
            if not anchors:
                continue
            cell_txt = " ".join(_cell.findall(m.group("body")))
            for lm in _LABEL.finditer(m.group("body")):
                lab, body_txt = lm.group("lab"), lm.group("txt")
                if "救" not in lab and "救" not in body_txt:
                    continue
                if "破格" in lab:
                    continue
                refs = _anchors_in(html_mod.unescape(body_txt))
                stray = [b2 for b2 in refs
                         if b2 not in anchors
                         and not any(_rel_kind(b2, a) != "other" for a in anchors)]
                if not stray:
                    continue
                named = {(nm, br) for nm, br in stars.items() if br in stray}
                F.append(Finding(
                    "ERROR", "BREAKSAVE_ANCHOR_OFF_PATTERN",
                    f"格局「{row_name}」栏「{lab}」把 "
                    + "、".join(
                        (f"{nm}@{x}宫（{palace_of(chart, x)}）" if nm else
                         f"{x}宫（{palace_of(chart, x)}）")
                        for x in stray for nm in ([n for n, b2 in named if b2 == x] or [None]))
                    + f" 当作本格救应，但该格的锚点在 "
                    + "、".join(f"{a}宫（{palace_of(chart, a)}）" for a in sorted(anchors))
                    + "；所列宫位与锚点既不同宫、也不两邻、也不三合、也不互为对宫照"
                      "——救应挂错了锚点",
                    (lab + "：" + body_txt)[:200]))

    # ── 5 徵验声明与免责 ──
    if "徵验率因人而异" not in text and ("疾厄" in text or "健康" in text):
        F.append(Finding("WARN", "NO_SIGNAL_DISCLAIMER", "涉及健康内容但未附「徵验率因人而异」声明"))
    # 必须完整出现免责语，不能只匹配「不构成」二字（正文里「不构成三合」等会误判为已声明）
    if "不构成投资、财务、医疗" not in text and "不构成投资、财务" not in text:
        F.append(Finding("BLOCKER", "NO_DISCLAIMER",
                         "报告缺少完整的免责声明（须含「不构成投资、财务、医疗…专业建议」）"))

    # ── 6 排版完整性 ──
    if report_markup is not None:
        if "技术留痕" not in text:
            F.append(Finding("ERROR", "NO_TECH_TRAIL", "缺少技术留痕折叠区"))
        n_empty = len([p for p in chart["palaces"] if not p.get("major")])
        if n_empty and "（空宫）" not in text:
            F.append(Finding("WARN", "EMPTY_PALACE_UNMARKED", "盘面有空宫但报告未标注「（空宫）」"))
    return F


def text_raw(markup: str) -> str:
    """格局表要按原始 HTML 抓（表格结构即语义）。"""
    return markup


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="audit_report.py",
                                 description="紫微斗数报告结构一致性审查（独立于生成器）")
    ap.add_argument("--chart", required=True, help="chart JSON")
    ap.add_argument("--report", help="已生成的 report.html")
    ap.add_argument("--analysis", help="analysis JSON（可选，审原文）")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args(argv)

    try:
        chart = json.loads(Path(args.chart).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"错误：无法读取 chart — {e}", file=sys.stderr)
        return 2
    if not chart.get("palaces"):
        print("错误：chart 缺 palaces", file=sys.stderr)
        return 2

    analysis = None
    if args.analysis:
        try:
            analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"错误：无法读取 analysis — {e}", file=sys.stderr)
            return 2

    report_text = None
    if args.report:
        try:
            markup = Path(args.report).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            print(f"错误：无法读取 report — {e}", file=sys.stderr)
            return 2
        report_text = markup

    findings = audit(chart, analysis, report_text)
    blockers = [f for f in findings if f.level == "BLOCKER"]
    errors = [f for f in findings if f.level == "ERROR"]
    warns = [f for f in findings if f.level == "WARN"]

    if args.json:
        print(json.dumps({
            "ok": not (blockers or errors),
            "blockers": [f.as_dict() for f in blockers],
            "errors": [f.as_dict() for f in errors],
            "warnings": [f.as_dict() for f in warns],
        }, ensure_ascii=False, indent=1))
    else:
        if blockers:
            print("❌ BLOCKER（结论与盘面矛盾，必须改）")
            for f in blockers:
                print(f"  [{f.code}] {f.msg}")
                if f.detail:
                    print(f"       依据：{f.detail}")
        if errors:
            print("❗ ERROR（结构或措辞不合格）")
            for f in errors:
                print(f"  [{f.code}] {f.msg}")
                if f.detail:
                    print(f"       依据：{f.detail}")
        if warns:
            print("⚠️  WARN（建议核对）")
            for f in warns:
                print(f"  [{f.code}] {f.msg}")
        if not findings:
            print("✅ 结构审查通过：结论与盘面一致，无矛盾")
        else:
            print(f"\n合计：BLOCKER {len(blockers)}　ERROR {len(errors)}　WARN {len(warns)}")

    return 0 if not (blockers or errors) else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
