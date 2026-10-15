#!/usr/bin/env python3
"""
ziwei-doushu / scripts/test_audit.py — 审查器自测（回归防线）

**为什么需要它**：审查器自己失效比被审对象出错更危险——它会让人误以为「已通过」。
本脚本用**构造夹具**验证每一类错误都能被检出，并验证干净报告不会误报。

用法：
    python test_audit.py --chart chart.json [--report 修正后的report.html]
    python test_audit.py --chart chart.json --report report.html --keep   # 保留夹具供人工查看

退出码：0 = 自测全过；1 = 有失败项；2 = 参数/输入错误。

覆盖的错误类别（对应 audit_report.py 的检查；夹具数见文件末「合计 N/N」）：

  A 结构类（第一轮）
    1 STAR_MISPLACED            报告把某星说到错误的地支
    2 PALACE_MISNAMED           报告把某地支宫说成别的宫名
    3 FIRMING_OVERCLAIM         时辰贴近交界却标 A 可信（用合成盘夹具，恒被测到）
    4 DECADAL_RANGE_INVALID     出现盘面大限表里没有的区间
    5 START_AGE_MISMATCH        起运岁数与五行局不符
    6 NO_DISCLAIMER             缺免责声明
  B 措辞 vs 盘面（第二轮：只改文字的修法失效，故改为机器可抓）
    7 STAR_MISATTRIBUTED(同宫)  「X 与 Y 同宫」但两星并不同宫
    8 STAR_MISATTRIBUTED(对宫)  「对宫 X」但 X 并不在该宫的对宫
    9 SURROUND_MISJUDGED        「X 不在命宫三方四正」但盘面在内
   10 FLANK_MISJUDGED           「A、B 分居 C 两邻」但两星同宫（同宫不是夹）
   11 BRIGHTNESS_FABRICATED     盘面 brightness 为 null 的星被标上亮度词
   12 PATTERN_VERDICT_CONFLICT  格局表结论列与表内正文口径矛盾（不成立 vs 存疑）
   13 COUNT_MISMATCH            「N 项格局」与盘面 patternCandidates 长度不符
  C 宫位算术（第四轮：散文里的宫位关系全部改由脚本产出，这里逐类钉死）
   14 DECADAL_PALACE_MISNAMED   正文写「本限／大限 X 宫在 Y 宫」，与脚本映射不符
   15 PALACE_RELATION_MISJUDGED 「X宫的对宫是Y宫」与盘面相反
   16 PALACE_RELATION_MISJUDGED 裸「对宫X宫」——上一从句主语的对宫并不是 X（第三轮 BLOCKER 原型）
   17 MUTAGEN_MARK_INCOMPLETE   盘面有生年科，全文却一个「生年科」也没有
   18 FLANK_MISJUDGED           「X 被 AB 夹」但 A、B 与 X 同宫或不在 X 两邻
  D 六吉六煞配对（第五轮：配对算术改由脚本产出，正文只许引用）
   19 AUSPICIOUS_PAIRING_MISSTATED  群组级：「六吉两两不成对宫」而盘面至少一对真成对
   20 AUSPICIOUS_PAIRING_MISSTATED  对级：某对星并不同宫，却被写成「同宫」

> 第 3 类的夹具是**合成**的：把临时 chart 副本的交界距离改成 10 分钟再验收。
> 因为「是否贴近交界」是盘面事实——距交界 41 分钟的盘上标 A 并不算高估，
> 拿它当夹具等于把闸门改错。规则本身仍然每一轮都被实测。
>
> 其余每一条夹具都是**从盘面事实出发构造的**：先问盘面「X 到底在哪一宫」，
> 再把报告改成与盘面相反的说法。这样换个盘、换个时辰，夹具仍然成立——
> 不会像写死字面量那样在别的盘上变成「干净夹具本身违规」。
> 大限十二宫映射在夹具里**独立重算一遍**（不读 chart 里的新字段），
> 否则字段本身算错时自测也会跟着通过。
> 同理，D 组的配对判据（同宫／对拱）也在本文件里**独立重算**，不读 `chart.pairingFacts`。
>
> 本文件只测 `audit_report.py`；**祈使式元指令**那条规则的夹具与反例在
> `check_report_language.py --self-test`（22 例应检出 + 20 例不得误报）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
AUDIT = HERE / "audit_report.py"

BR12 = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
PALACE_NAMES = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                "迁移", "仆役", "交友", "官禄", "田宅", "福德", "父母"]
# 十二宫名的固定次序。这里**独立重写一遍**（不 import astro），
# 好让「大限宫名映射」这件事在自测里被第二套实现复核。
PALACE_ORDER = ["命宫", "兄弟", "夫妻", "子女", "财帛", "疾厄",
                "迁移", "仆役", "官禄", "田宅", "福德", "父母"]
# 夹宫简称 → 两颗星（与 audit_report.FLANK_ABBR 同源，自测里按盘面事实用）
FLANK_ABBR = {
    "空劫": ("地空", "地劫"),
    "羊陀": ("擎羊", "陀罗"),
    "火铃": ("火星", "铃星"),
    "魁钺": ("天魁", "天钺"),
    "昌曲": ("文昌", "文曲"),
    "辅弼": ("左辅", "右弼"),
}
# 六吉六煞对星（第五轮新增）：夹具里**独立重写一遍**配对判据，不 import astro，
# 好让「哪一对成对、哪一对不成」这件事在自测里被第二套实现复核。
PAIR_SPECS = [
    ("六吉", "辅弼", "左辅", "右弼"),
    ("六吉", "昌曲", "文昌", "文曲"),
    ("六吉", "魁钺", "天魁", "天钺"),
    ("六煞", "羊陀", "擎羊", "陀罗"),
    ("六煞", "火铃", "火星", "铃星"),
    ("六煞", "空劫", "地空", "地劫"),
]


def decadal_map(soul_branch: str) -> dict[str, str]:
    """大限命宫地支 → 十二宫名到地支的映射（第二套实现，用于独立复核）。"""
    i = BR12.index(soul_branch)
    return {nm: BR12[(i - k) % 12] for k, nm in enumerate(PALACE_ORDER)}


def opp(br: str) -> str:
    return BR12[(BR12.index(br) + 6) % 12]


def build_clean_report(chart: dict) -> str:
    """
    构造一份「干净」的伪报告——只含审查器会读的结构，不追求排版。

    关键：**每一句涉及星曜位置的话都由盘面推导**，不写死字面量。
    这样审查器的每个检测器都至少有一段「应当通过」的真实输入，
    而不是「因为报告里根本没提，所以没报错」。
    """
    b = chart["basic"]
    soul = b["soulPalaceBranch"]
    i = BR12.index(soul)
    left, right = BR12[(i - 1) % 12], BR12[(i + 1) % 12]
    surround = {soul, BR12[(i + 4) % 12], BR12[(i + 8) % 12], opp(soul)}

    # 星名 → 地支
    at: dict[str, str] = {}
    for p in chart["palaces"]:
        for key in ("major", "minor"):
            for s in (p.get(key) or []):
                at.setdefault(s["name"], p["branch"])
    name_of = {p["branch"]: p["name"] for p in chart["palaces"]}

    all_stars = sorted(at, key=len, reverse=True)
    # 找一对**不同宫**的星 → 用于「A 与 B 不同宫」的正确表述
    pair = None
    for a in all_stars:
        for c in all_stars:
            if a != c and at[a] != at[c]:
                pair = (a, c)
                break
        if pair:
            break
    # 找一颗在**命宫三方四正之外**的星 → 用于「在…之外」的正确表述
    outside_star = next((s for s in all_stars if at[s] not in surround), None)
    # 找一颗**亮度为 null** 的星 → 用于亮度不得编造的干净用例
    null_star, null_br = None, None
    for p in chart["palaces"]:
        for key in ("major", "minor"):
            for s in (p.get(key) or []):
                if s.get("brightness") is None:
                    null_star, null_br = s["name"], p["branch"]
                    break
            if null_star:
                break
        if null_star:
            break

    d0 = (chart.get("decadal") or [{}])[0]
    rng = d0.get("ageRange", [3, 12])
    start_age = {"水二局": 2, "木三局": 3, "金四局": 4, "土五局": 5, "火六局": 6}.get(
        b["fiveElementsClass"], 3)

    cands = chart.get("patternCandidates") or []
    # 夹具自己的「本盘不以此格为主」清单——**独立重写**，不 import 审查器常量。
    # 这些格即使古法构成条件满足，只要星曜不在命宫三方四正内，本盘就不标成立。
    NOT_MAIN_HERE = {"杀破狼", "紫府朝垣", "府相朝垣", "百官朝拱", "君臣庆会",
                     "火贪格", "铃贪格"}
    # 另一类：**判断层的成格要件**——这些格要求相关星曜落在命宫三方四正之内才成立。
    # 构成条件与成格要件是两层：古法条件满足（conditions_met=true）不等于本盘成此格。
    # 缺了这一类时，若盘面把该格的两曜放在三方四正之外，干净夹具会自己把该格标成
    # 「成立」而被审查器判 BLOCKER——那是夹具与审查器规则不同步，不是被审报告的问题。
    MUST_BE_INSIDE = {"机月同梁", "日月同宫", "日月反背"}

    def _fixture_verdict(c: dict) -> str:
        """夹具自己的裁断规则——**独立重写**，不 import audit/generate。

        与生成器同源：需要并列两说的格（needs_adjudication=False）写「存疑／并列」；
        条件不满足、或条件虽满足但本盘不以此格为主的，写「不成立」；其余写「成立」。
        """
        if c.get("needs_adjudication") is False:
            return "存疑"
        if not c.get("conditions_met"):
            return "不成立"
        if c.get("pattern") in NOT_MAIN_HERE:
            return "不成立"
        if c.get("pattern") in MUST_BE_INSIDE:
            # 成格要件：该格涉及的星曜落点须**全部**在命宫三方四正之内。
            brs = re.findall(r"@\['([子丑寅卯辰巳午未申酉戌亥]+)'\]",
                             str(c.get("evidence") or ""))
            if not brs or not all(x in surround for x in brs):
                return "不成立"
        return "成立"

    # 末轮新增：格局表多一列「格性」（吉／凶／中性），取值见模块级 NATURE_FIXTURE。
    # 干净夹具必须带上这一列——否则「加列之后审查器还读不读得出结论」这件事
    # 在夹具里根本无法复现（末轮就是加了这一列才发现旧解析器整表读不出）。
    # ⚠️ 取值必须写成**无 span 的裸文本**（`<td>吉</td>`）：G30 夹具靠精确替换它来验证
    #    「结论列判定不会被格性列的样式带偏」，带 span 就替换不中、夹具静默失效。
    rows = []
    for c in cands:
        disp = c.get("display_name") or c["pattern"]
        # 结论列三态：需并列两说的格写「存疑／并列」，其余按条件写成立／不成立。
        # 干净夹具必须给出这三态——否则「存疑行被渲染成不成立」这个错没法在夹具里复现。
        v = _fixture_verdict(c)
        cls = {"存疑": "pending", "成立": "bright", "不成立": "dim"}[v]
        mark = f'<span class="{cls}">{v}</span>'
        nat = NATURE_FIXTURE.get(c["pattern"], "吉")
        # 第六轮：破格／救格栏给**带标签的两段式**（渲染成 <b>标签</b>：正文），
        # 这样 PATTERN_BREAKSAVE_MISLABELED 有一段「应当通过」的真实输入——
        # 否则「没报错」可能只是「报告里没有带标签的栏」。
        rows.append(f'<tr><td>{disp}</td><td>{nat}</td><td>{mark}</td>'
                    f'<td>依据与落点已回盘面核对</td>'
                    f'<td><b>破格</b>：被夹之宫无吉曜入宫，力量被耗掉一层。'
                    f'<br><b>救格折损</b>：救应只剩一半。</td></tr>')

    # 格局表里第一个「存疑条目」（needs_adjudication=False）的机器键，用于第 12 类夹具
    disputed = next((c.get("pattern") for c in cands
                     if c.get("needs_adjudication") is False), None)

    # 正确的关系句（每句都能被审查器核对，且都应通过）
    rel_same = (f"{pair[0]}在{at[pair[0]]}、{pair[1]}在{at[pair[1]]}，"
                f"两星并不同宫。" if pair else "盘面星曜不足，略。")
    rel_opp = ""
    for s in all_stars:
        if s not in (soul,):
            rel_opp = (f"{s}在{at[s]}宫，其对宫是{opp(at[s])}宫。")
            break
    rel_surround = (
        f"{outside_star}在{at[outside_star]}宫，不在命宫{soul}的三方四正"
        f"（{''.join(sorted(surround))}）之内。"
        if outside_star else "盘面无可举之例，略。")
    rel_bright = (
        f"{null_star}在{null_br}宫，盘面没有它的庙旺值，故不标亮度。" if null_star else "")

    # ── 第四轮新增：干净夹具也要给「宫位关系 / 大限宫名 / 生年四化标记 / 被夹」
    #    四类检测器各自一段**应当通过**的真实输入。每句都由盘面推导，不写死字面量。
    d0 = (chart.get("decadal") or [{}])[0]
    ar0 = d0.get("ageRange") or [3, 12]
    yr0 = d0.get("yearRange") or [2000, 2009]
    dsoul = d0.get("branch") or soul
    dmap = decadal_map(dsoul)
    dec_ok = (f"现行大限为 {ar0[0]}~{ar0[1]} 虚岁（{yr0[0]}~{yr0[1]}），"
              f"{d0.get('ganzhi') or ''}，大限命宫落本命{name_of.get(dsoul, '')}"
              f"（{dsoul}宫），本限福德宫在{dmap['福德']}宫；"
              f"{dmap['夫妻']}宫在本限是夫妻宫。")
    # 正确的关系句：地支形式的对宫 + 宫名形式的对宫 + 三合
    opp_soul = opp(soul)
    rel_pair = (f"{soul}宫的对宫是{opp_soul}宫；{opp_soul}宫是{soul}宫的对宫；"
                f"{BR12[(i + 4) % 12]}宫是{soul}宫的三合宫。")
    # 生年四化标记：四类齐全（盘面有哪几类就标哪几类）
    bm_types = []
    for bm in (chart.get("birthMutagen") or []):
        if bm.get("type") and bm["type"] not in bm_types:
            bm_types.append(bm["type"])
    mut_marks = "生年四化标记：" + "、".join(f"生年{t}" for t in bm_types) + "。"

    # 六吉六煞配对（第五轮新增）：每个新检测器都要有一段**应当通过**的真实输入，
    # 否则「没报错」可能只是「报告里根本没提」。判据独立重算（不读 chart.pairingFacts）。
    pair_bits, soft_paired, soft_total = [], 0, 0
    for group, abbr, s1, s2 in PAIR_SPECS:
        b1, b2 = at.get(s1), at.get(s2)
        if not b1 or not b2:
            continue
        same, oppo = b1 == b2, b2 == opp(b1)
        rel = "同宫" if same else ("互为对拱" if oppo else "分居")
        if group == "六吉":
            soft_total += 1
            if same or oppo:
                soft_paired += 1
        pair_bits.append(f"{abbr}：{s1}在{b1}、{s2}在{b2}，{rel}")
    pair_ok = "；".join(pair_bits) + "。"
    if soft_total:
        # ⚠️ 「同宫」前必须留标点：审查器的配对断言规则只认紧邻的写法，
        # 写成「六吉三对之中同宫…」会被（正确地）读成一句断言而在干净夹具上误报。
        pair_ok += f"六吉三对之中，同宫或对拱者 {soft_paired}/{soft_total}。"
    # 「X 被 AB 夹」的正确写法：找一颗真的被某个对星组合夹住的星
    flank_ok = ""
    for p in chart["palaces"]:
        nb = {BR12[(BR12.index(p["branch"]) - 1) % 12], BR12[(BR12.index(p["branch"]) + 1) % 12]}
        here = [nm for nm, br in at.items() if br == p["branch"]]
        if not here:
            continue
        for abbr, pair in FLANK_ABBR.items():
            if {at.get(pair[0]), at.get(pair[1])} == nb:
                flank_ok = f"{here[0]}被{abbr}夹。"
                break
        if flank_ok:
            break

    # ── 第六轮新增：效力层级尺（EFFICACY_LEVEL_MISSTATED）与破格/救格串味
    #    （PATTERN_BREAKSAVE_MISLABELED）各自一段**应当通过**的输入。
    #    四层层级由本文件独立重算（不读 audit_report 的内部常量）：
    #      1 同宫 ＞ 2 两邻 ＞ 3 三合 ＞ 4 对宫照（含三合宫的对宫）＞ 5 遥会
    tr1, tr2 = BR12[(i + 4) % 12], BR12[(i + 8) % 12]
    lv4_set = {opp(soul), opp(tr1), opp(tr2)}
    lv5_br = next(x for x in BR12
                  if x not in ({soul, left, right, tr1, tr2} | lv4_set))
    eff_ok = (f"{left}宫属两邻。{tr1}宫属三合会照。"
              f"{opp(tr1)}宫属对拱回照。{lv5_br}宫属遥会。")

    # ── 第七轮新增：否定式关系断言 / 地支主语的三方四正 / 分类计数 /
    #    救格锚点，四类检测器各自一段**应当通过**的输入。
    #    ⚠️ 这几句不只是「凑数」——它们是**不得误报的反例**：审查器若把
    #    「A、B 两宫并不互为对宫」这种正确否定也报出来，干净夹具立刻变红。
    _F = fixture_facts(chart)
    deny_ok, anti_ok = _F["deny_ok"], _F["anti_ok"]
    tri_word_ok, count_ok = _F["tri_word_ok"], _F["count_ok"]
    anchor_row = _F["anchor_row"]
    _body_rows = list(rows[:-1]) + ([anchor_row] if anchor_row else rows[-1:])
    table_tag = ('<table><tr><th>格局</th><th>格性</th><th>是否成立</th>'
                 '<th>条件</th><th>破格／加重条件与救格</th></tr>'
                 + "".join(_body_rows) + "</table>")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>
<h2>一、定盘与口径声明</h2>
<div>定盘可信度：B 基本可信。五行局{b['fiveElementsClass']}。真太阳时校正后见下。</div>
<h2>二、命盘全图</h2>
<div>命宫{soul}（{name_of.get(soul, '')}），命宫两邻为{left}宫与{right}宫。</div>
<div>{rel_same}{rel_opp}</div>
<div>{rel_pair}{mut_marks}</div>
<h2>三、命格骨架</h2><p>骨架文字</p>
<h2>四、星系结构</h2><p>{rel_surround}{rel_bright}{pair_ok}</p>
<div>{flank_ok}{eff_ok}{deny_ok}{anti_ok}{tri_word_ok}</div>
<h2>六、格局认定</h2>
<p>本盘格局候选共 {len(cands)} 项，逐项裁断如下。{count_ok}</p>
{table_tag}
<h2>九、大限趋势</h2><p>{rng[0]}~{rng[1]} 虚岁。虚岁 {start_age} 岁起运。</p>
<p>{dec_ok}</p>
<h2>十一、风险与边界</h2>
<div class="disclaimer">本文为命理与自我观察内容，仅供参考，需与实际条件交叉判读；不构成投资、财务、医疗、诊断、法律或其他专业建议，必要时请咨询合格专业人士。
文中徵验讯号属陆斌斌—王亭之体系收录，实际徵验率因人而异。</div>
<details class="tech-trail"><summary>🔧 技术留痕</summary>记录</details>
</body></html>"""


def run_audit(chart_path: Path, report_path: Path) -> tuple[int, str]:
    r = subprocess.run([sys.executable, str(AUDIT), "--chart", str(chart_path),
                        "--report", str(report_path)],
                       capture_output=True, text=True, encoding="utf-8")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def near_boundary(chart: dict) -> bool:
    """
    「时辰贴近交界」是**盘面事实**，不是常量：由 chart 的真太阳时距最近时辰交界
    的分钟数决定（<30 分钟＝贴近，与 interpretation.md §1 的定盘分级同阈值）。

    自测夹具必须照它生成——不能假定任何一张盘都贴近交界，否则换个时间就会
    「干净夹具本身违规」而报假失败。
    """
    st = chart.get("solarTime") or {}
    mfb = st.get("minutes_from_hour_boundary")
    if isinstance(mfb, (int, float)):
        return mfb < 30
    blob = " ".join((chart.get("meta") or {}).get("warnings") or []) \
        + " " + " ".join(st.get("warnings") or [])
    return bool(re.search(r"距时辰交界", blob))


def _star_index(chart: dict) -> dict[str, str]:
    at: dict[str, str] = {}
    for p in chart["palaces"]:
        for key in ("major", "minor"):
            for s in (p.get(key) or []):
                at.setdefault(s["name"], p["branch"])
    return at


# 夹具自己的「本盘不以此格为主」清单——**独立重写**，不 import 审查器常量。
# 这些格即使古法构成条件满足，只要星曜不在命宫三方四正内，本盘就不标成立。
NOT_MAIN_HERE = {"杀破狼", "紫府朝垣", "府相朝垣", "百官朝拱", "君臣庆会",
                 "火贪格", "铃贪格"}
# 另一类：**判断层的成格要件**——审查器要求相关星曜落在命宫三方四正之内才算成立。
# 构成条件与成格要件是两层：古法条件满足（conditions_met=true）不等于本盘成此格。
# 缺了这一类时，若盘面把该格的两曜放在三方四正之外，干净夹具会自己把该格标成
# 「成立」而被审查器判 BLOCKER——那是夹具与审查器规则不同步，不是被审报告的问题。
MUST_BE_INSIDE = {"机月同梁", "日月同宫", "日月反背"}
# 本盘三方四正落点（由 fixture_facts 写入），供 _fixture_verdict 复核落点用。
_FIXTURE_SURROUND: set[str] | None = None

# 夹具自己的「格性」表——**独立重写**，不 import 生成器/审查器常量。
# 末轮给格局表加了这一列；夹具必须带上它，否则「加列之后审查器还读不读得出」
# 这件事在夹具里无法复现。取值写成无 span 的裸文本，方便夹具做精确替换。
NATURE_FIXTURE = {
    "日月反背": "凶", "火铃夹命": "凶", "羊陀夹命": "凶",
    "空劫夹命": "凶", "刑忌夹印": "凶", "羊陀夹禄忌": "凶",
    "杀破狼": "中性", "日月同宫": "中性", "禄马交驰": "中性",
}


def _fixture_verdict(c: dict) -> str:
    """夹具自己的裁断规则——**独立重写**，不 import audit/generate。

    与生成器同源：需要并列两说的格（needs_adjudication=False）写「存疑／并列」；
    条件不满足、或条件虽满足但本盘不以此格为主的，写「不成立」；其余写「成立」。

    「必须落在命宫三方四正之内」的那一类，按调用方塞进候选条目的 `_surround`
    复核落点；调用方没塞时按不成立处理——夹具的立场是**宁可不标成立**，
    否则干净夹具自己会撞上 PATTERN_OUTSIDE_SOUL_SURROUND（实测踩到过）。
    """
    if c.get("needs_adjudication") is False:
        return "存疑"
    if not c.get("conditions_met"):
        return "不成立"
    if c.get("pattern") in NOT_MAIN_HERE:
        return "不成立"
    if c.get("pattern") in MUST_BE_INSIDE:
        # 成格要件：该格涉及的星曜落点须**全部**在命宫三方四正之内
        # （日月反背、日月同宫都要两颗在内；机月同梁要四颗在内），只进一颗不算成格。
        brs = re.findall(r"@\['([子丑寅卯辰巳午未申酉戌亥]+)'\]",
                         str(c.get("evidence") or ""))
        sur = _FIXTURE_SURROUND
        if not brs or not sur or not all(x in sur for x in brs):
            return "不成立"
    return "成立"


def fixture_facts(chart: dict) -> dict:
    """
    第七轮新增的**共享夹具事实**：干净夹具与变异夹具都要用同一批字符串
    （分类计数的正确写法、否定式关系的正确写法、地支主语三方四正的正确写法、
    救格锚点的正确写法）。放在这里算一次，两处引用，避免两套实现漂移。
    """
    b = chart["basic"]
    soul = b["soulPalaceBranch"]
    i = BR12.index(soul)
    surround = {soul, BR12[(i + 4) % 12], BR12[(i + 8) % 12], opp(soul)}
    cands = chart.get("patternCandidates") or []
    # 把本盘的三方四正落点交给 _fixture_verdict 复核「必须落在其内」的格。
    # ⚠️ 不能挂在候选条目的字典上：调用方会把 chart 深拷贝后 json.dumps 成临时盘，
    #    set 不是可序列化类型，合成盘夹具会直接构造失败（实测踩到过）。
    global _FIXTURE_SURROUND
    _FIXTURE_SURROUND = surround

    verdicts = {"成立": 0, "不成立": 0, "存疑": 0}
    for c in cands:
        verdicts[_fixture_verdict(c)] += 1
    # ⚠️ 句式必须把**数词写在结论词之前**（「成立 5 项，不成立 11 项，存疑 1 项」）：
    #    审查器的分类计数规则认的就是「N 项 + 结论词」这个紧邻组合。
    #    写成「裁为成立的共 5 项」时数词在结论词**之后**，规则接不上——
    #    夹具会因为句式不合规而「不被检出」，看起来像规则坏了，其实是夹具没照口径写。
    count_ok = (f"格局表统计：成立 {verdicts['成立']} 项，"
                f"不成立 {verdicts['不成立']} 项，"
                f"存疑 {verdicts['存疑']} 项。")

    asym = None
    for x in BR12:
        for y2 in BR12:
            if (BR12.index(y2) - BR12.index(x)) % 12 not in (0, 4, 6, 8):
                asym = (x, y2)
                break
        if asym:
            break
    deny_ok = f"{asym[0]}宫与{asym[1]}宫既不同宫，也不互为对宫。" if asym else ""

    anti = None
    for x in BR12:
        for y2 in BR12:
            if (BR12.index(y2) - BR12.index(x)) % 12 not in (0, 6):
                anti = (x, y2)
                break
        if anti:
            break
    anti_ok = f"{anti[0]}与{anti[1]}并不互为对宫。" if anti else ""

    in_br = next((x for x in BR12 if x in surround), soul)
    out_br = next((x for x in BR12 if x not in surround), soul)
    tri_word_ok = (f"{in_br}宫在命宫{soul}的三方四正之内；"
                   f"{out_br}宫在命宫{soul}的三方四正之外。")

    anchor_row, anchor_demo, anchor_disp = "", None, None
    for c in cands:
        ev = str(c.get("evidence") or "")
        # 锚点必须取「星名@['地支']」里**星名紧跟**的落点：先把 `@` 之前的自由文字切掉，
        # 再从**最后一个** `@['…']` 里取地支。
        ab = re.findall(r"@\['([子丑寅卯辰巳午未申酉戌亥]+)'\]", ev.rsplit("@", 1)[-1])
        if not ab:
            continue
        anchor_demo = ab[-1]
        anchor_disp = c.get("display_name") or c["pattern"]
        _cls = {"存疑": "pending", "成立": "bright", "不成立": "dim"}[_fixture_verdict(c)]
        anchor_row = (f'<tr><td>{anchor_disp}</td>'
                      f'<td>{NATURE_FIXTURE.get(c["pattern"], "吉")}</td>'
                      f'<td><span class="{_cls}">{_fixture_verdict(c)}</span></td>'
                      f'<td>依据已核</td>'
                      f'<td><b>救格</b>：{anchor_demo}宫所载之星即本格锚点，'
                      f'救应落在锚点之内。</td></tr>')
    return {"verdicts": verdicts, "count_ok": count_ok, "asym": asym, "deny_ok": deny_ok,
            "anti": anti, "anti_ok": anti_ok, "in_br": in_br, "out_br": out_br,
            "tri_word_ok": tri_word_ok, "anchor_row": anchor_row,
            "anchor_demo": anchor_demo, "anchor_disp": anchor_disp}


def plant(clean: str, chart: dict) -> list[tuple[str, str, str]]:
    """返回 [(用例名, 变异后 HTML, 期望命中的错误码)]"""
    b = chart["basic"]
    soul = b["soulPalaceBranch"]
    i = BR12.index(soul)
    left, right = BR12[(i - 1) % 12], BR12[(i + 1) % 12]
    surround = {soul, BR12[(i + 4) % 12], BR12[(i + 8) % 12], opp(soul)}
    name_of = {p["branch"]: p["name"] for p in chart["palaces"]}
    at = _star_index(chart)
    all_stars = sorted(at, key=len, reverse=True)
    cases: list[tuple[str, str, str]] = []

    # ── A1 星曜落宫错：把正确地支换成另一个 ──
    for p in chart["palaces"]:
        for s in (p.get("major") or []):
            wrong = next(x for x in BR12 if x != p["branch"])
            cases.append(("星曜落宫错", clean.replace(f'{s["name"]}在{p["branch"]}', f'{s["name"]}在{wrong}'),
                          "STAR_MISPLACED"))
            break
        else:
            continue
        break

    # ── A2 宫名错 ──
    real = name_of.get(soul)
    wrong_name = next(n for n in PALACE_NAMES if n != real and n != "交友")
    cases.append(("宫名错", clean.replace(f"命宫{soul}（{real}）", f"命宫{soul}（{wrong_name}）"),
                  "PALACE_MISNAMED"))

    # ── A3 定盘等级与告警不符 —— **只在盘面确实贴近交界时才有这个夹具** ──
    if near_boundary(chart):
        cases.append(("定盘等级不实", clean.replace("定盘可信度：B", "定盘可信度：A"),
                      "FIRMING_OVERCLAIM"))

    # ── A4 大限区间不在表内 ──
    d0 = (chart.get("decadal") or [{}])[0]
    rng = d0.get("ageRange", [3, 12])
    cases.append(("大限区间错", clean.replace(f"{rng[0]}~{rng[1]} 虚岁", "47~56 虚岁"),
                  "DECADAL_RANGE_INVALID"))

    # ── A5 起运岁数错 ──
    sa = {"水二局": 2, "木三局": 3, "金四局": 4, "土五局": 5, "火六局": 6}.get(
        b["fiveElementsClass"], 3)
    cases.append(("起运岁数错", clean.replace(f"虚岁 {sa} 岁起运", f"虚岁 {sa + 2} 岁起运"),
                  "START_AGE_MISMATCH"))

    # ── A6 免责缺失 ──
    cases.append(("免责缺失",
                  clean.replace("不构成投资、财务、医疗、诊断、法律或其他专业建议", "仅供娱乐参考"),
                  "NO_DISCLAIMER"))

    # ── B7 「X 与 Y 同宫」但两星并不同宫 ──
    sp = None
    for a in all_stars:
        for c in all_stars:
            if a != c and at[a] != at[c]:
                sp = (a, c)
                break
        if sp:
            break
    if sp:
        cases.append((f"同宫措辞错（{sp[0]}与{sp[1]}）",
                      clean.replace(f"{sp[0]}在{at[sp[0]]}、{sp[1]}在{at[sp[1]]}，两星并不同宫",
                                    f"{sp[0]}与{sp[1]}同宫"),
                      "STAR_MISATTRIBUTED"))

    # ── B8 「对宫 X」但 X 不在该宫的对宫 ──
    anchor = next((s for s in all_stars if s != soul), None)
    if anchor:
        cases.append((f"对宫措辞错（对宫{anchor}）",
                      clean.replace(f"{anchor}在{at[anchor]}宫，其对宫是{opp(at[anchor])}宫。",
                                    f"对宫{anchor}与它相拱。"),
                      "STAR_MISATTRIBUTED"))

    # ── B9 三方四正误判：盘面在命宫三方四正内，报告说不在 ──
    inside_star = next((s for s in all_stars if at[s] in surround and s != soul), None)
    if inside_star:
        cases.append((f"三方四正误判（{inside_star}）",
                      clean.replace(f"命宫两邻为{left}宫与{right}宫。",
                                    f"命宫两邻为{left}宫与{right}宫。"
                                    f"{inside_star}在{at[inside_star]}宫，"
                                    f"不在命宫{soul}的三方四正之内。"),
                      "SURROUND_MISJUDGED"))

    # ── B10 夹宫措辞：两颗星其实同宫，却称「分居某宫两邻」 ──
    # 被夹的目标必须写成**地支宫**（不是「命宫」）：审查器对「X宫两邻」这种
    # 说明性插入语有意不判（它是在解释两邻，不是断言夹宫），所以夹具要用
    # 「分居巳宫两邻」这类真正可核的写法。
    # 目标宫取**第一个有 ≥2 星的宫**——「太阴、左辅」落在命宫两邻那种写法不行，
    # 因为那两颗星本来就在命宫两邻附近，判不出错。
    same_pair = None
    by_branch: dict[str, list[str]] = {}
    for nm, br in at.items():
        by_branch.setdefault(br, []).append(nm)
    for br, nms in by_branch.items():
        if len(nms) >= 2:
            same_pair = (nms[0], nms[1], br)
            break
    if same_pair:
        s1, s2, br = same_pair
        cases.append((f"夹宫措辞错（{s1}、{s2} 同在{br}）",
                      clean.replace("六、格局认定",
                                    f"六、格局认定 {s1}、{s2}分居{br}宫两邻。"),
                      "FLANK_MISJUDGED"))

    # ── B11 亮度编造：给 brightness=null 的星标上亮度 ──
    null_star = next((s for s in all_stars
                      if any((x.get("brightness") is None)
                             for p in chart["palaces"]
                             for key in ("major", "minor")
                             for x in (p.get(key) or []) if x["name"] == s)), None)
    if null_star:
        cases.append((f"亮度编造（{null_star}）",
                      clean.replace("盘面没有它的庙旺值，故不标亮度。",
                                    f"{null_star}在{at[null_star]}为庙。"),
                      "BRIGHTNESS_FABRICATED"))

    # ── B12 格局表结论与正文口径矛盾（两个方向都要覆盖）──
    # ① 存疑行被渲染成单选结论（把结论列的 pending 换成 dim「不成立」，正文仍说并列）
    # ② 结论列已写「存疑／并列」，正文却仍在单方断言「应标为…不成立」
    # 干净夹具对 needs_adjudication=False 的格已经输出 pending，所以 ① 必须**显式改回**
    # 单选结论，否则两个夹具会变成同一个变异体、只测到一条分支（本轮实测踩到过）。
    disputed = next((c for c in (chart.get("patternCandidates") or [])
                     if c.get("needs_adjudication") is False), None)
    if disputed and disputed.get("display_name"):
        disp = disputed["display_name"]
        # 干净夹具的结论列是三态（成立／不成立／存疑／并列），这里只按**显示名 +
        # 格性格 + pending 单元格**定位，不把「存疑／并列」这四个字写死——否则改动
        # 渲染措辞时夹具会先于被审对象失效。末轮加了「格性」列，故中间多一格（`<td>凶</td>`）。
        pending_cell = f'<td>{disp}</td><td>凶</td><td><span class="pending">'
        assert pending_cell in clean, "干净夹具未输出 pending 结论列"
        # ⚠️ 正文里引用的格名要用**机器键**（不带「（存疑条目）」这类显示限定语）：
        # 单方断言的正则只容忍 12 字以内的引用，带上限定语会把夹具本身变成
        # 「引用太长所以不匹配」，测的就不是规则了（本轮实测踩到过）。
        pname = disputed.get("pattern") or disp
        cases.append((f"格局表结论与正文矛盾·存疑行被单方断言（{disputed.get('pattern')}）",
                      clean.replace(pending_cell,
                                    f'<td>{disp}</td>'
                                    f'<td>该格存疑，两派口径并列，'
                                    f'按一派口径应标为「{pname}」不成立</td>'
                                    f'<td><span class="dim">不成立</span></td><td'),
                      "PATTERN_VERDICT_CONFLICT"))
        cases.append((f"格局表结论与正文矛盾·结论存疑但正文单方断言（{disputed.get('pattern')}）",
                      # ⚠️ 存疑结论的 `<span class="pending">存疑</span>` 必须**原样保留**：
                      # 审查器只认这一种写法来判「结论列＝存疑」。把单方断言插进 span 里，
                      # 结论格就成了 `<span class="pending">按一派口径…不成立</span>`，
                      # 审查器连结论列都读不出来，夹具考的就不再是「正文单方断言」（实测踩到过）。
                      # 所以单方断言要落在**同一行的别的格子**里（依据格）。
                      clean.replace(
                          f'<td><span class="pending">存疑</span></td>'
                          f'<td>依据与落点已回盘面核对</td>',
                          f'<td><span class="pending">存疑</span></td>'
                          f'<td>两派落点并列；按一派口径应标为「{pname}」不成立</td>'),
                      "PATTERN_VERDICT_CONFLICT"))

    # ── B13 计数不一致：正文声明的项数与盘面候选数不符 ──
    n = len(chart.get("patternCandidates") or [])
    if n:
        cases.append(("格局计数不一致", clean.replace("逐项裁断如下。", f"逐项裁断如下。脚本判定 {n + 3} 项格局。"),
                      "COUNT_MISMATCH"))

    # ══════════════════════════════════════════════════════════════
    # C 宫位算术（第四轮）：散文里的宫位关系全部改由脚本产出，
    #   这一组夹具钉住「机器确实抓得到」——每一条都从盘面事实反着写。
    # ══════════════════════════════════════════════════════════════

    # ── C14 大限宫名错：把「本限福德宫在X宫」换成另一个地支 ──
    d0 = (chart.get("decadal") or [{}])[0]
    dsoul = d0.get("branch") or soul
    dmap = decadal_map(dsoul)
    ok_claim = f"本限福德宫在{dmap['福德']}宫"
    wrong_br = next(x for x in BR12 if x != dmap["福德"])
    if ok_claim in clean:
        cases.append((f"大限宫名错（本限福德宫在{wrong_br}）",
                      clean.replace(ok_claim, f"本限福德宫在{wrong_br}宫"),
                      "DECADAL_PALACE_MISNAMED"))
    else:
        cases.append(("大限宫名错", clean, "DECADAL_PALACE_MISNAMED"))

    # ── C15 「X宫的对宫是Y宫」方向错（地支形式） ──
    ok_opp = f"{soul}宫的对宫是{opp(soul)}宫"
    bad_opp = next(x for x in BR12 if x != opp(soul))
    if ok_opp in clean:
        cases.append((f"对宫关系错（{soul}宫的对宫写成{bad_opp}）",
                      clean.replace(ok_opp, f"{soul}宫的对宫是{bad_opp}宫"),
                      "PALACE_RELATION_MISJUDGED"))

    # ── C16 裸「对宫X宫」：上一从句主语是命宫，X 却不是命宫的对宫 ──
    #    （第三轮 BLOCKER 原型：「…分居命宫两邻，配合对宫官禄的双忌」）
    anchor = f"命宫两邻为{left}宫与{right}宫。"
    bad_pal = next(nm for nm in PALACE_NAMES
                   if nm not in ("命宫", "交友") and nm != name_of.get(opp(soul)))
    if anchor in clean:
        cases.append((f"裸对宫关系错（对宫{bad_pal}，主语命宫）",
                      clean.replace(anchor, anchor + f"配合对宫{bad_pal}的双忌，"),
                      "PALACE_RELATION_MISJUDGED"))

    # ── C17 生年四化标记不完整：抹掉其中一类 ──
    bm_types = []
    for bm in (chart.get("birthMutagen") or []):
        if bm.get("type") and bm["type"] not in bm_types:
            bm_types.append(bm["type"])
    if bm_types:
        drop = bm_types[0]
        if f"生年{drop}" in clean:
            cases.append((f"生年四化标记缺失（抹掉生年{drop}）",
                          clean.replace(f"生年{drop}", "（标记被抹）"),
                          "MUTAGEN_MARK_INCOMPLETE"))

    # ── C18 「X 被 AB 夹」：主语星与那一对并不构成夹 ──
    #    干净夹具里给了一句真成立的「X 被AB夹」，这里把主语换成别宫的星。
    flank_ok = ""
    flank_demo = None
    for p in chart["palaces"]:
        nb = {BR12[(BR12.index(p["branch"]) - 1) % 12], BR12[(BR12.index(p["branch"]) + 1) % 12]}
        here = [nm for nm, br in at.items() if br == p["branch"]]
        if not here:
            continue
        for abbr, pair in FLANK_ABBR.items():
            if {at.get(pair[0]), at.get(pair[1])} == nb:
                flank_ok = f"{here[0]}被{abbr}夹。"
                flank_demo = (here[0], abbr)
                break
        if flank_ok:
            break
    if flank_demo:
        f_star, f_abbr = flank_demo
        wrong_star = next((nm for nm in all_stars if at[nm] != at.get(f_star)), None)
        if wrong_star:
            cases.append((f"被夹措辞错（{wrong_star}被{f_abbr}夹）",
                          clean.replace(f"{f_star}被{f_abbr}夹", f"{wrong_star}被{f_abbr}夹"),
                          "FLANK_MISJUDGED"))

    # ══════════════════════════════════════════════════════════════
    # D 六吉六煞配对（第五轮）：配对算术一律由脚本产出，
    #   这一组夹具钉住「正文自称成对/不成对/同宫时，机器确实核得到」。
    #   夹具同样由盘面事实反着写，不写死字面量。
    # ══════════════════════════════════════════════════════════════
    pat_anchor = "<h2>六、格局认定</h2>"
    soft_pair_rows = []
    for group, abbr, s1, s2 in PAIR_SPECS:
        if group != "六吉":
            continue
        b1, b2 = at.get(s1), at.get(s2)
        if b1 and b2:
            soft_pair_rows.append((abbr, b1, b2, (b1 == b2) or (b2 == opp(b1))))

    # ── D19 群组级：「六吉两两不成对宫」——盘面却至少有一对真成对（第五轮 BLOCKER 原型）──
    if any(r[3] for r in soft_pair_rows) and pat_anchor in clean:
        cases.append(("六吉配对断言错（称两两不成对宫）",
                      clean.replace(pat_anchor,
                                    pat_anchor + "<p>六吉六颗两两不成对宫，吉星无法配对。</p>"),
                      "AUSPICIOUS_PAIRING_MISSTATED"))

    # ── D20 对级：某对星并不同宫，却被写成「同宫」 ──
    for group, abbr, s1, s2 in PAIR_SPECS:
        b1, b2 = at.get(s1), at.get(s2)
        if b1 and b2 and b1 != b2 and pat_anchor in clean:
            cases.append((f"对星同宫断言错（{abbr}同宫，实为{b1}／{b2}）",
                          clean.replace(pat_anchor, pat_anchor + f"<p>{abbr}同宫。</p>"),
                          "AUSPICIOUS_PAIRING_MISSTATED"))
            break

    # ══════════════════════════════════════════════════════════════
    # E 效力层级尺与破格／救格分栏（第六轮）
    #   第六轮两条 BLOCKER 的共同根因：同一颗星作煞时算贴身、作吉时算「次一等」，
    #   而更远的戌宫反被评为更高一档——层次判断没有一把尺。这一组把它钉死。
    # ══════════════════════════════════════════════════════════════

    # ── E21 层级词与盘面路径层级不符：两邻（第 2 级）被写成对拱回照（第 4 级） ──
    ok_lv2 = f"{left}宫属两邻。"
    if ok_lv2 in clean:
        cases.append((f"效力层级错（{left}宫写成对拱回照）",
                      clean.replace(ok_lv2, f"{left}宫属对拱回照。"),
                      "EFFICACY_LEVEL_MISSTATED"))

    # ── E22 「效力次一等」用在贴身层（两邻） ──
    if ok_lv2 in clean:
        cases.append((f"效力次一等用在贴身层（{left}宫）",
                      clean.replace(ok_lv2, f"{left}宫属借宫救应、效力次一等。"),
                      "EFFICACY_LEVEL_MISSTATED"))

    # ── E23 破格栏写救格的事（破格／救格串味） ──
    ok_break = "被夹之宫无吉曜入宫，力量被耗掉一层。"
    if ok_break in clean:
        cases.append(("破格栏串味（写救应）",
                      clean.replace(ok_break, "被夹之宫无吉曜入宫，故无救应。"),
                      "PATTERN_BREAKSAVE_MISLABELED"))

    # ══════════════════════════════════════════════════════════════
    # F 否定式断言 · 地支主语的三方四正 · 分类计数 · 救格锚点（第七轮）
    #   第七轮的教训：**肯定式断言被核对了，否定式断言没有**——
    #   报告写「寅申既不同宫，也不互为对宫」，而寅申正是对宫，全篇零报错。
    #   这一组把「否认一个真实存在的关系」也钉死。
    # ══════════════════════════════════════════════════════════════
    _F = fixture_facts(chart)
    asym, anti = _F["asym"], _F["anti"]
    tri_word_ok, count_ok = _F["tri_word_ok"], _F["count_ok"]
    anchor_row, anchor_demo = _F["anchor_row"], _F["anchor_demo"]
    in_br, out_br = _F["in_br"], _F["out_br"]

    # ── F24 否认一个真实存在的对宫关系（第七轮 BLOCKER 2-① 原型）──
    if asym:
        x, y2 = asym
        true_opp = next(((a2, opp(a2)) for a2 in BR12), None)
        if true_opp and f"{x}宫与{y2}宫既不同宫，也不互为对宫。" in clean:
            cases.append((f"否定式对宫断言错（称{true_opp[0]}{true_opp[1]}不互为对宫）",
                          clean.replace(f"{x}宫与{y2}宫既不同宫，也不互为对宫。",
                                        f"{true_opp[0]}宫与{true_opp[1]}宫既不同宫，"
                                        f"也不互为对宫。"),
                          "PALACE_RELATION_DENIED_WRONGLY"))

    # ── F25 肯定式「互为对宫」写错（第七轮 BLOCKER 2-② 原型：酉丑实为三合）──
    if anti:
        a2, b3 = anti
        if f"{a2}与{b3}并不互为对宫。" in clean:
            cases.append((f"互为对宫写错（{a2}与{b3}实非对宫）",
                          clean.replace(f"{a2}与{b3}并不互为对宫。",
                                        f"{a2}与{b3}两宫互为对宫。"),
                          "PALACE_RELATION_MISJUDGED"))

    # ── F26 地支主语的「在/不在命宫三方四正之内」写反（第七轮 BLOCKER 4 原型）──
    if tri_word_ok in clean:
        cases.append((f"地支主语三方四正写反（{out_br}写成在内）",
                      clean.replace(tri_word_ok,
                                    f"{in_br}宫在命宫{soul}的三方四正之内；"
                                    f"{out_br}宫在命宫{soul}的三方四正之内。"),
                      "SURROUND_MISJUDGED"))

    # ── F27 分类计数与格局表不符（第七轮 BLOCKER 3 原型）──
    #     两个方向都钉：结论词在后（「成立 5 项」）与结论词在前（「不成立 11 项」）。
    n_bad = _F["verdicts"]["不成立"]
    if count_ok in clean and n_bad >= 2:
        cases.append((f"分类计数错·结论词在后（不成立 {n_bad} 项写成 {n_bad - 1} 项）",
                      clean.replace(count_ok,
                                    f"格局表统计：成立 {_F['verdicts']['成立']} 项，"
                                    f"不成立 {n_bad - 1} 项，"
                                    f"存疑 {_F['verdicts']['存疑']} 项。"),
                      "COUNT_MISMATCH"))
        cases.append((f"分类计数错·结论词在前（不成立 {n_bad} 项写成 {n_bad - 1} 项）",
                      clean.replace(count_ok,
                                    f"本次判定共 {len(chart.get('patternCandidates') or [])} 项格局，"
                                    f"裁为不成立的共 {n_bad - 1} 项，其余成立或存疑。"),
                      "COUNT_MISMATCH"))

    # ── F28 救格挂在与锚点无关的宫上（第七轮 BLOCKER 1-② 原型）──
    #     找一支与锚点既不同宫、也不两邻、也不三合、也不对宫的宫。
    stray_br = next((x for x in BR12
                     if anchor_demo
                     and (BR12.index(x) - BR12.index(anchor_demo)) % 12
                     not in (0, 1, 4, 6, 8, 11)), None)
    if anchor_row and stray_br:
        cases.append((f"救格锚点错（挂在{stray_br}宫，锚点在{anchor_demo}宫）",
                      clean.replace(anchor_row,
                                    anchor_row.replace(f"{anchor_demo}宫所载之星即本格锚点",
                                                       f"{stray_br}宫所载之星即本格锚点")),
                      "BREAKSAVE_ANCHOR_OFF_PATTERN"))

    # ══════════════════════════════════════════════════════════════
    # G 格局表加列后的解析健壮性（末轮）
    #   末轮给格局表加了「格性」列（吉／凶／中性）。旧解析器把结论列写死成
    #   「紧跟首格的第二个 <td>」，加列后整表一行都读不出，报成 AUDIT_PARSE_FAILED
    #   ——假故障。这一组用**两行**夹具把两面都钉住：
    #     G29 多一列也要照常读出结论（旁路行不得把整表带崩）；
    #     G30 结论列的判定只许看结论那一格（不能按整行里出现 dim 就判不成立）。
    # ══════════════════════════════════════════════════════════════
    real_names = [c.get("pattern") or c.get("display_name") or ""
                  for c in (chart.get("patternCandidates") or [])]
    fake_name = next(("格局" + x for x in "甲乙丙丁戊己庚辛壬癸"
                      if ("格局" + x) not in real_names), "格局旁")
    extra_row = (f'<tr><td>{fake_name}</td>'
                 f'<td><span class="dim">凶</span></td>'
                 f'<td><span class="dim">不成立</span></td>'
                 f'<td>旁路行：用于验证多一列时的行解析。</td>'
                 f'<td>—</td></tr>')
    # ⚠️ 旁路行要插在**首行之后**，不能用 `replace("<tr><td>", …)`：
    #    那种写法改的是表结构本身，一旦表头写法定型就会漂。这里用「取一行真实数据行、
    #    在它后面插旁路行」的锚定写法（`plant()` 里拿不到 `build_clean_report` 的局部
    #    变量，所以数据行由盘面现场重算一遍）。
    # ⚠️ 旁路行要插在第一张表的数据行**之后**：用整行 HTML 做替换锚点，
    #    改的是数据行本身，表头与表尾都不动（`plant()` 里拿不到渲染器的局部变量，
    #    所以数据行的写法在这里按同一模板重算一遍）。
    _vcls = {"存疑": "pending", "成立": "bright", "不成立": "dim"}
    body_row0 = ""
    for c in (chart.get("patternCandidates") or []):
        v0 = _fixture_verdict(c)
        body_row0 = (f'<tr><td>{c.get("display_name") or c["pattern"]}</td>'
                     f'<td>{NATURE_FIXTURE.get(c["pattern"], "吉")}</td>'
                     f'<td><span class="{_vcls[v0]}">{v0}</span></td>'
                     f'<td>依据与落点已回盘面核对</td>'
                     f'<td><b>破格</b>：被夹之宫无吉曜入宫，力量被耗掉一层。'
                     f'<br><b>救格折损</b>：救应只剩一半。</td></tr>')
        break
    if body_row0 and body_row0 in clean:
        cases.append(("格局表多一列（格性）后仍能逐行解析",
                      clean.replace(body_row0, body_row0 + extra_row, 1),
                      "PATTERN_UNVERIFIED"))
    # G30 已上移到 main()（它属「不应误报」类，需要自己跑一次审查，而不是构造变异体）。
    return cases


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="test_audit.py", description="审查器自测")
    ap.add_argument("--chart", required=True)
    ap.add_argument("--report", help="另用真实报告做一次干净用例（可选）")
    ap.add_argument("--keep", action="store_true", help="保留夹具目录")
    args = ap.parse_args(argv)

    chart_path = Path(args.chart)
    if not chart_path.exists():
        print(f"错误：找不到 {chart_path}", file=sys.stderr)
        return 2
    chart = json.loads(chart_path.read_text(encoding="utf-8"))
    if not chart.get("palaces"):
        print("错误：chart 缺 palaces", file=sys.stderr)
        return 2

    # 注意：不要用 tempfile.mkdtemp()——某些受限环境会拒绝写入它创建的目录（ACL 问题），
    # 而同一父目录下自建的子目录可以正常写。这里用确定性名字 + mkdir(exist_ok=True)。
    base = Path(tempfile.gettempdir()) / "ziwei-audit-test"
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / "run"
    if tmp.exists():
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    results: list[tuple[str, bool, str]] = []

    # 干净用例：应 PASS
    clean = build_clean_report(chart)
    clean_f = tmp / "clean.html"
    clean_f.write_text(clean, encoding="utf-8")
    code, out = run_audit(chart_path, clean_f)
    results.append(("干净夹具（不应误报）", code == 0, out.strip().splitlines()[0] if out.strip() else ""))

    # G30：结论列判定不得被「格性」列的样式带偏（**不应误报**类夹具）。
    #     判据：拿一行本来就是「凶 ＋ 不成立」的格，把它的格性写成
    #     `<span class="dim">凶</span>`——吉凶与结论用的是同一套状态样式。
    #     若结论格的定位只按 class 找，就会先抓到格性格，把这一行读成「成立」，
    #     立刻误报 PATTERN_CONTRADICTS_SCRIPT。所以这一条的正确表现是 **exit=0**。
    #     ⚠️ 只替换单元格的**内容**，不替换 `<td>…</td>`：整个换掉会多出一个 `<td>`，
    #        表格结构错位、结论格读不出来，夹具就成了「结构坏了所以没报错」（实测踩到过）。
    if re.search(r"<td>凶</td>", clean):
        styled = clean.replace("<td>凶</td>",
                               '<td><span class="dim">凶</span></td>', 1)
        f30 = tmp / "clean-nature-dim.html"
        f30.write_text(styled, encoding="utf-8")
        code, out = run_audit(chart_path, f30)
        results.append(("干净用例·格性列也用 dim 样式时结论列不被带偏（不应误报）",
                        code == 0,
                        "结论列仍读作不成立" if code == 0 else
                        f"误报（exit={code}）："
                        + (out.strip().splitlines()[0] if out.strip() else "（空）")))
    else:
        results.append(("干净用例·格性列也用 dim 样式时结论列不被带偏（不应误报）",
                        False, "夹具未生效：干净夹具里找不到「凶 ＋ 不成立」的行"))

    # 定盘高估规则的**合成**夹具：把盘面参数改成「距交界 10 分钟」，
    # 逼出带告警的报告，再把等级写成 A 应当被拦。用临时 chart 副本，
    # 不改动入参盘面本身——这条规则必须始终被测到，不能靠「本盘的时辰刚好贴近交界」。
    try:
        near_chart = json.loads(json.dumps(chart))
        st = near_chart.setdefault("solarTime", {})
        st["corrected"] = True
        st["minutes_from_hour_boundary"] = 10
        st["nearest_hour_boundary"] = "11:00"
        st["boundary_shift_direction"] = "退入巳时"
        st["warnings"] = ["真太阳时距时辰交界仅 10 分钟；出生时间若有一刻钟误差即会换时辰。"]
        near_chart.setdefault("meta", {})["warnings"] = list(st["warnings"])
        near_path = tmp / "near-chart.json"
        near_path.write_text(json.dumps(near_chart, ensure_ascii=False), encoding="utf-8")
        near_report = tmp / "near-overclaim.html"
        near_report.write_text(clean.replace("定盘可信度：B", "定盘可信度：A"), encoding="utf-8")
        code, out = run_audit(near_path, near_report)
        ok = (code == 1) and ("FIRMING_OVERCLAIM" in out)
        results.append(("应检出：贴近交界却标高等级（合成盘）", ok,
                        "命中 FIRMING_OVERCLAIM" if ok else
                        f"未命中（exit={code}）；实际输出首行："
                        + (out.strip().splitlines()[0] if out.strip() else "（空）")))
    except Exception as e:  # noqa: BLE001
        results.append(("应检出：贴近交界却标高等级（合成盘）", False, f"夹具构造失败：{e}"))

    if args.report:
        code, out = run_audit(chart_path, Path(args.report))
        results.append((f"真实报告 {Path(args.report).name}（应 PASS）", code == 0,
                        out.strip().splitlines()[0] if out.strip() else ""))

    # 各类变异：应 FAIL 且命中期望错误码
    # 文件名必须带**用例序号**而不是「错误码 + 已见错误码个数」——
    # 同一错误码有多个夹具时（如 STAR_MISATTRIBUTED 的同宫/对宫两例）后者会覆盖前者，
    # 于是前一个夹具实际验的是后一个变异体，测试结果静默失真（本轮实测到的坑）。
    cases = plant(clean, chart)
    for idx, (name, mutated, expect) in enumerate(cases):
        f = tmp / f"{idx:02d}-{expect}.html"
        f.write_text(mutated, encoding="utf-8")
        code, out = run_audit(chart_path, f)
        ok = (code == 1) and (expect in out)
        detail = "命中 " + expect if ok else (
            f"未命中（exit={code}）期望 {expect}；实际输出首行：" +
            (out.strip().splitlines()[0] if out.strip() else "（空）"))
        results.append((f"应检出：{name}", ok, detail))

    print("=== 审查器自测 ===")
    failed = 0
    for name, ok, detail in results:
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            print(f"      {detail}")
            failed += 1
    print(f"\n合计 {len(results) - failed}/{len(results)} 通过")
    if not args.keep:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"夹具保留于：{tmp}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
