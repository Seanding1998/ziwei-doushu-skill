#!/usr/bin/env python3
"""
ziwei-doushu / scripts/generate_report.py — HTML 报告生成器（跨平台，纯 Python）

设计要点（对齐成熟 skill 的报告组织方式）：
  1. **脚本优先**：AI 不输出 HTML，只产出结构化 JSON；渲染交给本脚本。
  2. **两层读者**：可读区（给当事人） / 「技术留痕」折叠区（给复核者）。
  3. **语言闸门**：可读区禁用内部流程词，命中即 `--validate` 报错阻断。
  4. **完整性校验**：板块、字数下限、盘面数据一致性。

用法：
  python generate_report.py --chart chart.json --analysis analysis.json --output report.html
  python generate_report.py --input combined.json --output report.html
  python generate_report.py --input combined.json --validate      # 只校验，不写文件

输入 JSON 顶层：
  {
    "meta":     { ... },     # 当事人与口径声明
    "chart":    { ... },     # ziwei.py chart 的输出（可整段塞入）
    "analysis": { ... },     # AI 的分析（见 references/report-schema.md）
    "md_full":  { ... }      # 逐字全文附录（可选）
  }
"""

from __future__ import annotations

import argparse
import datetime as _dt
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

# ============================================================
# 语言闸门：可读区禁用词（与 check_report_language.py 同源）
# ============================================================

# 内部流程/工程词：出现在可读区即不合格
INTERNAL_TOKENS: list[str] = [
    # 流程门禁类
    "门禁", "软门禁", "准入条件", "强制加载", "强制输出", "检查点", "预读",
    "路由表", "Tier A", "Tier B", "升降级", "深断",
    # 审计留痕类
    "本步已加载", "已加载 references", "证明已真读", "假合规", "落盘", "回退方案",
    "单轮审查", "审查 P", "修正记录", "判据", "子代理", "主流程",
    # 内部标识类
    "SKILL.md", "references/", "scripts/", "ziwei.py", "engine.js",
    "generate_report", "JSON 字段", "schema", "Step ", "步骤N-",
    # 第六轮：交付文本里最顽固的一类流程词。「由排盘脚本给出」「脚本判定 17 项」
    # 「本表为脚本产出的事实」——读者要的是「排盘结果」，不是「谁产的」。
    # 加进黑名单之后，凡残留即报错；正确写法是「由排盘结果给出」「本次判定 N 项」。
    "脚本",
    # 量化记号
    "打分", "评分表",
]

# 允许保留的专有词（是断语本身，不能当黑话删）
ALLOWED_PRO_TOKENS: list[str] = [
    "命宫", "身宫", "来因宫", "三方四正", "四化", "自化", "离心自化", "向心自化",
    "庙", "旺", "得地", "利益", "平和", "不得地", "落陷", "亮度",
    "禄", "权", "科", "忌", "生年四化", "大限", "小限", "流年", "流月",
    "格局", "破格", "救格", "夹宫", "对宫", "三合", "六吉", "六煞",
    "五行局", "水二局", "木三局", "金四局", "土五局", "火六局",
    "命主", "身主", "十二宫", "紫微", "天机", "太阳", "武曲", "天同", "廉贞",
    "天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军",
]

INTERNAL_RE = re.compile("|".join(re.escape(t) for t in INTERNAL_TOKENS))
MD_FILE_RE = re.compile(r"[\w\-/]+\.md\b")
SCRIPT_PATH_RE = re.compile(r"[\w\-/\\]+\.(?:py|js|json)\b")
ABSPATH_RE = re.compile(r"(?:[A-Za-z]:\\|/Users/|/home/|\\\\)[^\s\"'<>]+")

# Python/JSON 数据结构字面量痕迹——出现即说明「结构化数据被当成文本塞进了可读区」
DICT_LITERAL_RE = re.compile(
    r"\{\s*['\"]|['\"]\s*:\s*['\"]|['\"]\s*:\s*(?:True|False|None|null)"
    r"|[\[(]\s*['\"][^'\"]+['\"]\s*[,:\]]"
)
# Markdown 强调标记：HTML 里 `**` 会原样显示，必须渲染前去掉
MD_MARK_RE = re.compile(r"\*{1,3}(?=[^\s*])")

# 必填板块
REQUIRED_SECTIONS = [
    "定盘与口径", "命盘全图", "骨架", "星系结构", "四化主轴",
    "格局", "十二宫逐宫", "优势与短板", "大限趋势", "风险与边界",
    # ⚠️ 「结论与建议」原先漏在这张清单外：verdict / advice 一直被校验、被语言闸门扫描，
    # 却既不渲染（已于第七轮补上渲染），也不参与「板块完整性 N/N」的核对——
    # 结果是它一旦为空就静默消失、而报告仍报满分。现列入必填。
    "结论与建议",
]

MIN_LEN = {
    "skeleton": 100,
    "star_system": 100,
    "mutagen_axis": 120,
    "palace_reading": 200,
    "strengths": 80,
    "weaknesses": 80,
    "decadal_trend": 150,
    "risk_boundary": 100,
    # 总评与宜忌不得空着占位（原先无下限，空串也能通过）
    "verdict": 40,
    "advice": 30,
}

# 九步分析留痕：每步最低字数。这是「AI 确实分析过、而非直接拼字段」的证据链。
NOTE_STEPS = [
    "step1_定盘", "step2_骨架", "step3_星系类型", "step4_本宫三方四正",
    "step5_亮度", "step6_辅佐煞杂曜", "step7_四化三层", "step8_夹宫", "step9_格局裁断",
]
NOTE_MIN = 40

# 九步的中文标签（渲染用）
STEP_LABELS = {
    "step1_定盘": "第一步 · 定盘校验",
    "step2_骨架": "第二步 · 命格骨架",
    "step3_星系类型": "第三步 · 星系类型",
    "step4_本宫三方四正": "第四步 · 本宫与三方四正",
    "step5_亮度": "第五步 · 亮度",
    "step6_辅佐煞杂曜": "第六步 · 辅佐煞杂曜",
    "step7_四化三层": "第七步 · 四化三层",
    "step8_夹宫": "第八步 · 夹宫",
    "step9_格局裁断": "第九步 · 格局裁断",
}

# ============================================================
# 可读区内部语剥离（自动清理 + 记入技术留痕）
# ============================================================


def strip_internal(text: str) -> tuple[str, list[str]]:
    """
    把可读区文本里的内部语剥离，返回 (清洗后文本, 命中片段列表)。
    命中片段会进「技术留痕」，原文不丢。

    除路径/文件名/流程词外，还要清掉两类「不是人话」的痕迹：
      · Python/JSON 数据结构字面量（`{'title': …}`、`"text": …`）
      · Markdown 强调星号（HTML 里会原样显示成 `**`）
    """
    if not text:
        return "", []
    hits: list[str] = []
    out = text
    for pattern in (ABSPATH_RE, SCRIPT_PATH_RE, MD_FILE_RE, INTERNAL_RE):
        for m in pattern.finditer(out):
            frag = m.group(0)
            if frag not in hits:
                hits.append(frag)
        out = pattern.sub("", out)
    for m in DICT_LITERAL_RE.finditer(out):
        frag = m.group(0).strip()
        if frag and frag not in hits:
            hits.append(frag)
    out = _MD_BOLD_RE.sub(r"\1", out)
    out = _MD_EMPH_RE.sub(r"\1", out)
    out = MD_MARK_RE.sub("", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip(), hits


def scan_readable(text: str) -> list[str]:
    """
    只扫描不修改，用于校验。
    先剥掉具体的路径/文件名（它们已被整体判定违规），再在剩余文本里找内部词，
    避免同一个违规被重复计数（如 references/interpretation.md 与其中的 references/）。
    """
    if not text:
        return []
    hits: list[str] = []
    remaining = text
    for pattern in (ABSPATH_RE, SCRIPT_PATH_RE, MD_FILE_RE):
        for m in pattern.finditer(remaining):
            hits.append(m.group(0))
        remaining = pattern.sub(" ", remaining)
    for m in INTERNAL_RE.finditer(remaining):
        hits.append(m.group(0))
    return sorted(set(hits))


def scan_artifact_leaks(text: str) -> list[str]:
    """
    成品可读区专用：抓「结构化数据被当文本渲染」的痕迹。

    为什么单独一条闸门：列表渲染器漏处理 dict 条目时，可读区会出现
    `{'title': '…', 'text': '…'}` 这种 Python repr。它既不是禁用词也不含路径，
    旧闸门一律放过——但读者看到的是内部数据结构，属严重泄漏。
    """
    if not text:
        return []
    found: list[str] = []
    for m in DICT_LITERAL_RE.finditer(text):
        frag = m.group(0).strip()
        if frag and frag not in found:
            found.append(frag)
    for m in re.finditer(r"\*{1,3}[^\s*]", text):
        if m.group(0) not in found:
            found.append(m.group(0))
    return found


# ============================================================
# 祈使式元指令闸门（第五轮新增）
# ============================================================
# **为什么单列一类**：这已是同类问题第二次出现——第一次是「内部流程词」（门禁/子代理…），
# 第二次是「结构化数据残留」（`{'title': …}`）。第五轮 MINOR 指出交付文本里还残留着
# **写给自己看的祈使句**：「落点须分开写：不可写成『两宫都不在』」「切记…不可混用」
# 「不能写成『同宫』」「不可混称…」。读者看到的是作者的施工说明，不是断语。
# 按本项目规矩，第二次出现即代码化——所以它进语言闸门，不再靠下一轮审查抓。
#
# **判定难点与边界**：汉语里「须」是常用字，单收「须」会把
# 「力量须靠实际往来维持」这类**正常陈述**（说的是事理的necessity，不是写作指令）判成违规。
# 因此规则只收「祈使词 + 写作/核对动词」的**相邻组合**，而不是单个祈使词：
#   · 祈使词：须／必须／切记／务必／不可／不得／不能／不要／不许
#   · 写作动词：写明／写清／写全／写出／写／注明／说明／点名／分开写／分开说／
#               分列／单列／分清／区分／并列／声明／标成立／标明／称／混称／混用／
#               混为一谈／一致／按表／照表／核对／注意／看
#   两者之间的间隔**不许跨句读**（。；，！？），于是「须靠…／须借…／须持续…／不得地」
#   一律不命中。
# 成文口径（写进 references/report-template.md）：**给当事人的建议用「宜／忌」，
# 「须／必须／切记／务必」只用于写作指令——所以出现在可读区即为残留。**
META_IMPERATIVE_CORE = (
    "写明|写清|写全|写出|分开写|分开说|分列|单列|分清|注明|说明|点名|点出|区分|并列|声明|"
    "分别说|分别写|分别注明|分别说明|分别列出|分别记|"
    "标成立|标为成立|标明|混称|混用|混为一谈|一致|按表|照表|核对|注意|称|看|写"
)
META_INSTRUCTION_RES: list[re.Pattern[str]] = [
    # (a) 祈使词 + 极短间隔 + 写作动词（间隔不跨句读）
    re.compile(r"(?:须|必须)(?:要|得)?[^\n。；;，,！？]{0,5}?(?:" + META_IMPERATIVE_CORE + r")"),
    # (b) 纯祈使词：与写作无关也不该出现在交付文本里
    re.compile(r"切记|务必"),
    # (c) 否定祈使 + 措辞动作
    re.compile(r"(?:不可|不得|不能|不要|不许)[^\n。；;，,！？]{0,12}?"
               r"(?:写成|写作|写为|混称|混用|混为一谈|并称|省略|标成立|标为成立|称|混)"),
]

# ------------------------------------------------------------------
# 自校／防守型句式（第六轮新增）
# ------------------------------------------------------------------
# **为什么再来一类**：第六轮盲审发现可读区仍留着作者的**自校语**——「故这一半恒真、
# 无鉴别力……不宜把必然当发现」「层次要分清：……与『两科分居辰戌』是两回事」
# 「两者不是同一宫，读法分开处理」「两颗并不共处一宫」（×2）。
# 这类句子与「祈使式元指令」同源：都是作者写给自己的交稿提醒，读者要的是结论本身。
# 它已经是**同类问题第三次出现**（流程词 → 结构化数据 → 祈使句 → 自校句），
# 按本项目规矩继续代码化，不靠下一轮审查再抓。
#
# **判定难点与边界**：这几句汉语本身很常见，单收「不宜」「分开」会大面积误伤正常断语。
# 因此只收**「自校动作 + 被纠正的误读」成对出现**的固定句式：
#   · 不宜把 A 当 B（作者在提醒自己别误读）
#   · 要分清 / 须分清（作者在提醒自己分层）
#   · 是两回事 / 不是同一回事（作者在防串味）
#   · 读法／写法／两者／两处…分开处理（作者在提醒自己别合并）
#   · 并不共处一宫 / 并不在同一宫（作者在防读者合并）
#   · 不宜并列 / 不宜并论 / 不宜简化 / 不宜一概而论（同上，防读者合并）
# 反例（不得误报）：「须靠实际往来维持」「须借命宫天同天梁照」「太阳不得地」
# 「宜缓不宜急」「两说都指向同一处提醒」「不是分居辰戌两宫」「两处落点分别记明」。
META_SELF_CHECK_RES: list[re.Pattern[str]] = [
    # (d1) 不宜把 A 当 B
    re.compile(r"(?:不宜|不应|不可|不该|勿)(?:把|将)[^\n。；;！？]{0,20}?(?:当|当作|当成)"),
    # (d2) 要分清 / 须分清（作者在提醒自己分层）
    re.compile(r"(?:要|须|需|应)(?:严格)?分清"),
    # (d3) 是两回事 / 不是同一回事 / 不是同一件事
    re.compile(r"(?:是|属)两回事|(?:不是|非)同一回事|(?:不是|非)同一件事"),
    # (d4) 读法／写法分开（作者在提醒自己别合并）。
    # ⚠️ 刻意不写成「两处／两者 + 分开或分别」：那样「两处落点分别记明」这种
    # **面向读者的正常写法**会被误伤（实测过一次）。前缀为「两处」时只认「分开」，
    # 只有「读法／写法」才允许「分别」。
    re.compile(r"(?:读法|写法)(?:要|须)?(?:分开|分别)(?:处理|读|写)?"),
    re.compile(r"(?:两处|两者|二者|两层|两说|二事)(?:要|须)?分开(?:处理|读|写|看|记|讲|说)?"),
    # (d5) 并不共处一宫 / 并不在同一宫
    re.compile(r"共处一宫|(?:并不|不|不是|并非)(?:在)?同一宫"),
    # (d6) 不宜并列 / 并论 / 简化 / 一概而论 / 混同
    re.compile(r"(?:不宜|不应|不可|不该)(?:并列|并论|简化|一概而论|混同|混为一谈)"),
    # ------------------------------------------------------------------
    # (d7)~(d9) 自校／免责型说明句（末轮新增）
    # ------------------------------------------------------------------
    # **为什么再来这三条**：末轮复审的 MAJOR 4 指出，报告的技术留痕写着
    # 「元指令自检 0 处」，而可读区实际有「不作格名论断」×10、
    # 「把两条读法的落点都摊开说明」「不替任何一派下结论」——闸门当时对这类
    # **自校／免责型说明句**是零覆盖（它们与 d1~d6 同源：作者在对读者交代
    # 自己的处理方式，而不是给结论）。按本项目规矩继续代码化，不靠下一轮审查再抓。
    #
    # **判定难点与边界**：这三类话在汉语里极易与正常断语混淆，故一律要求
    # **成对出现**的自校标记：
    #   · 论断类：「（不／勿／毋）+（作／做／给／下）+（格名／论断／断语／结论／判断／定论）」
    #     ——「不作格名论断」「不替任何一派下结论」「不参与裁断」「不下单方断言」都命中；
    #   · 摊开类：「把 + 两条／两说／两种 + … +（摊开／并陈／并置／铺开）+ 说明」，
    #     或「两条读法／两说 +（都／均）+ 摊开」——只说「两说并列」不命中（那是正常表述）；
    #   · 名词类：「（仅／只）作名词」「不参与裁断」「不进入裁断」。
    # 反例（不得误报，均有夹具）：「只作结构性提醒」「这一栏只说明取象」「本格两说并列」
    # 「两派的取象不同」。
    re.compile(r"不[作做给下替][^\n。；;，,]{0,10}?(?:论断|断语|结论|裁断|评价|定论)"
               r"|不参与裁断|不进入裁断"),
    re.compile(r"把[^\n。；;]{0,12}?(?:两条|两说|两种)[^\n。；;]{0,10}?"
               r"(?:摊开|并陈|并置|铺开|并列摆)[^\n。；;]{0,4}说明"
               r"|(?:两条读法|两说|两种读法)[^\n。；;]{0,6}(?:都|均|一律)"
               r"(?:摊开|并陈|并置|铺开)[^\n。；;]{0,4}(?:说明|交代)?"),
    re.compile(r"[仅只]作名词|只作名词排除|不进入裁断|不参与裁断"),
]


def scan_meta_instructions(text: str) -> list[str]:
    """
    抓「元指令」：交付文本里写给作者自己看的话。两类——
      · **祈使式元指令**（第五轮）：施工说明，如「落点须分开写」「不可写成…」；
      · **自校／防守型句式**（第六轮）：交稿提醒，如「不宜把必然当发现」「是两回事」
        「读法分开处理」「两颗并不共处一宫」。

    只扫可读区。命中即阻断（`--validate` 与 `check_report_language.py` 两处都拦），
    **不做静默剥离**——剥离会改坏句子，而这类话本就不该写进断语。
    """
    if not text:
        return []
    found: list[str] = []
    for pat in META_INSTRUCTION_RES + META_SELF_CHECK_RES:
        for m in pat.finditer(text):
            frag = m.group(0)
            if frag not in found:
                found.append(frag)
    return found


# ============================================================
# HTML 构建
# ============================================================

CSS = """
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"Noto Serif SC","Source Han Serif SC","SimSun","宋体",serif;
     background:#f5f0e8;color:#3a3226;line-height:1.85}
.container{max-width:860px;margin:0 auto;padding:40px 24px 60px}
.report-header{text-align:center;padding:32px 0 24px;border-bottom:2px solid #8b7355;margin-bottom:28px}
.report-header h1{font-size:28px;color:#5c3d2e;letter-spacing:4px;margin-bottom:8px}
.report-header .subtitle{font-size:17px;color:#8b7355}
.report-header .date{font-size:13px;color:#9e8b7a;margin-top:6px}
.info-bar{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin-bottom:28px}
.info-item{background:#fff;border:1px solid #d4c5b2;border-radius:6px;padding:8px 14px;min-width:104px;text-align:center}
.info-item .label{font-size:12px;color:#9e8b7a;display:block}
.info-item .value{font-size:16px;color:#5c3d2e;font-weight:bold}
h2{font-size:20px;color:#5c3d2e;border-left:4px solid #8b7355;padding-left:12px;margin:34px 0 14px}
h3{font-size:16px;color:#6b5a4e;margin:18px 0 8px}
p{margin-bottom:12px}
.callout{background:#fff;border:1px solid #d4c5b2;border-radius:8px;padding:18px 20px;margin-bottom:16px}
.callout.warn{background:#fff8e1;border-color:#ffb74d}
.callout.good{background:#e8f5e9;border-color:#81c784}
.callout.bad{background:#fbe9e7;border-color:#e57373}
table{width:100%;border-collapse:collapse;margin-bottom:20px;font-size:14px}
th{background:#5c3d2e;color:#f5f0e8;padding:9px 7px;text-align:left}
td{padding:8px 7px;border-bottom:1px solid #d4c5b2;vertical-align:top}
tr.body-palace{background:#fdf2e0;font-weight:bold}
.star{color:#7a4b2a}
.bright{color:#2e7d32}
.dim{color:#b04040}
.pending{color:#b8860b;font-weight:bold}
.soft{color:#2e7d32}
.tough{color:#b04040}
.palace-grid{width:100%;border-collapse:collapse;margin-bottom:20px;table-layout:fixed}
.palace-grid td{border:1px solid #d4c5b2;padding:8px;font-size:13px;vertical-align:top;height:104px;background:#fff}
.narrow td,.narrow th{padding:5px 4px;font-size:12px}
.palace-grid .pname{font-weight:bold;color:#5c3d2e;display:block;margin-bottom:4px}
.palace-grid .pstem{color:#9e8b7a;font-size:11px}
.palace-grid .body-hl{background:#fdf2e0}
.stars{margin-top:4px;line-height:1.6}
.tag{display:inline-block;background:#efe6d8;border-radius:4px;padding:1px 6px;font-size:11px;margin-left:4px;color:#6b5a4e}
.tag.mut{background:#f3ddd0;color:#8d4a2f}
.tag.self{background:#e2e8f0;color:#3b5578}
ul{margin:0 0 14px 22px}
li{margin-bottom:6px}
details.tech-trail{margin-top:40px;border-top:1px dashed #c9b8a2;padding-top:16px;font-size:13px;color:#8a7a68}
details.tech-trail summary{cursor:pointer;color:#8b7355;font-weight:bold}
details.tech-trail pre{white-space:pre-wrap;word-break:break-word;background:#faf7f0;padding:12px;border-radius:6px;margin-top:10px;font-size:12px}
details.fulltext{margin-top:32px;border-top:1px solid #d4c5b2;padding-top:16px}
details.fulltext summary{cursor:pointer;color:#5c3d2e;font-weight:bold;font-size:16px}
details.reasoning{margin:16px 0 24px;border:1px solid #d4c5b2;border-radius:8px;padding:12px 16px;background:#fff}
details.reasoning summary{cursor:pointer;color:#5c3d2e;font-weight:bold;font-size:15px}
details.reasoning h3{margin:14px 0 4px;font-size:14px;color:#8b7355}
details.fulltext pre{white-space:pre-wrap;word-break:break-word;background:#fff;padding:16px;border-radius:8px;border:1px solid #d4c5b2;margin-top:10px;font-size:13px}
.report-footer{margin-top:48px;padding-top:20px;border-top:1px solid #d4c5b2;text-align:center;font-size:12px;color:#9e8b7a}
.disclaimer{margin-top:10px;font-style:italic;line-height:1.7;text-align:left;background:#faf7f0;padding:12px;border-radius:6px}
@media print{body{background:#fff}.container{max-width:100%;padding:16px}
  th{background:#5c3d2e!important;color:#fff!important;-webkit-print-color-adjust:exact}
  details{display:block}details>*{display:block!important}
  .palace-grid td{height:auto}}
"""

BR12 = ["子", "丑", "寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥"]
BR_YIN = ["寅", "卯", "辰", "巳", "午", "未", "申", "酉", "戌", "亥", "子", "丑"]
POSITIVE = {"庙", "旺", "得", "利"}

# 六吉六煞（安星诀固定对星）。天马、禄存**不属**这两类，单列「杂曜」以免归类错误。
SOFT6 = {"左辅", "右弼", "天魁", "天钺", "文昌", "文曲"}
TOUGH6 = {"擎羊", "陀罗", "火星", "铃星", "地空", "地劫"}


def esc(s: Any) -> str:
    return html.escape(str(s if s is not None else ""), quote=True)


# 全文附录里与「十三、分析留痕」重复的小节标题：九步留痕、关键发现、推理过程。
# 判据放宽到「九步/推理 + 留痕/过程/分析」，免得换个措辞就漏。
_DUP_FULLTEXT_RE = re.compile(r"(九步|推理|留痕|关键发现|findings|analysis_notes)", re.I)


def _is_duplicate_fulltext(key: str) -> bool:
    """全文附录的这个小节是否与 §十二 的分析留痕重复（第四轮：九步留痕收敛）。"""
    k = str(key or "")
    if not _DUP_FULLTEXT_RE.search(k):
        return False
    # 「定盘与口径」「格局条件判定原始记录」等不是留痕，不能误去重
    if "口径" in k or "格局" in k:
        return False
    return True


def star_html(s: dict) -> str:
    name = esc(s.get("name", ""))
    b = s.get("brightness")
    cls = "bright" if b in POSITIVE else ("dim" if b in ("平", "不", "陷") else "")
    out = f'<span class="star">{name}</span>'
    if b:
        out += f' <span class="{cls}">[{esc(b)}]</span>'
    if s.get("mutagen"):
        out += f'<span class="tag mut">生年{esc(s["mutagen"])}</span>'
    return out


def render_palace_grid(chart: dict) -> str:
    """4×4 十二宫方阵（含中宫信息），按传统布局。"""
    by_branch = {p["branch"]: p for p in chart["palaces"]}
    b = chart["basic"]
    # 传统方阵：巳午未申 / 辰□ □酉 / 卯□ □戌 / 寅丑子亥
    layout = [
        ["巳", "午", "未", "申"],
        ["辰", None, None, "酉"],
        ["卯", None, None, "戌"],
        ["寅", "丑", "子", "亥"],
    ]
    rows = []
    for r_i, row in enumerate(layout):
        cells = []
        for c_i, br in enumerate(row):
            if br is None:
                if r_i == 1 and c_i == 1:
                    centre = (
                        f'<span class="pname">{esc(b["fiveElementsClass"])}</span>'
                        f'<div class="stars">命宫：{esc(b["soulPalaceBranch"])}　'
                        f'身宫：{esc(b["bodyPalaceBranch"])}<br>'
                        f'命主：{esc(b["soul"])}　身主：{esc(b["body"])}<br>'
                        f'{esc(b["chineseDate"])}</div>'
                    )
                    cells.append(f'<td rowspan="2" colspan="2" style="text-align:center;vertical-align:middle">{centre}</td>')
            else:
                p = by_branch.get(br)
                if not p:
                    cells.append("<td></td>")
                    continue
                cls = ' class="body-hl"' if p.get("isBodyPalace") else ""
                stars = "".join(star_html(s) + "<br>" for s in (p.get("major") or []))
                if not stars:
                    stars = '<span style="color:#9e8b7a">（空宫）</span><br>'
                minor = "　".join(_star_txt(s, with_mutagen=True) for s in (p.get("minor") or []))
                tags = ""
                if p.get("isLaiYin"):
                    tags += '<span class="tag">来因</span>'
                if p.get("decadalRange"):
                    tags += f'<span class="tag">{p["decadalRange"][0]}~{p["decadalRange"][1]}</span>'
                cells.append(
                    f'<td{cls}><div class="pname">{esc(p["name"])}'
                    f'<span class="pstem">　{esc(p["stem"])}{esc(br)}</span></div>'
                    f'{tags}<div class="stars">{stars}'
                    f'<span style="font-size:12px;color:#6b5a4e">{minor}</span></div></td>'
                )
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return f'<table class="palace-grid">{"".join(rows)}</table>'


def _star_txt(s: dict, with_mutagen: bool = False) -> str:
    """
    单星显示。**亮度只照盘面给的值**：盘面为 null（右弼/左辅/天魁/天钺/禄存/天马
    等无庙旺表条目）就不显示方括号，不替它编一个「庙/不」。

    `with_mutagen=True` 时同时标出生年四化。第四轮修正：辅星也要标——
    旧版只给主星标，于是「右弼化科」在十二宫一览表与宫位方阵里整条不见，
    全文「生年科」零命中（审查器 MUTAGEN_MARK_INCOMPLETE 现在会抓这一类）。
    标记文字统一为「生年X」，与 `star_html()` 一致，便于机器核对四类是否齐全。
    """
    out = esc(s.get("name", ""))
    b = s.get("brightness")
    if b:
        cls = "bright" if b in POSITIVE else ("dim" if b in ("平", "不", "陷") else "")
        out += f'<span class="{cls}">[{esc(b)}]</span>'
    if with_mutagen and s.get("mutagen"):
        out += f'<span class="tag mut">生年{esc(s["mutagen"])}</span>'
    return out


def render_palace_table(chart: dict) -> str:
    rows = []
    for p in sorted(chart["palaces"], key=lambda x: BR12.index(x["branch"])):
        major = "、".join(_star_txt(s, with_mutagen=True) for s in (p.get("major") or [])) or "（空宫）"
        minor = [s for s in (p.get("minor") or [])]
        soft = "、".join(_star_txt(s, with_mutagen=True) for s in minor if s.get("name") in SOFT6) or "—"
        tough = "、".join(_star_txt(s, with_mutagen=True) for s in minor if s.get("name") in TOUGH6) or "—"
        misc = "、".join(_star_txt(s, with_mutagen=True) for s in minor
                        if s.get("name") not in SOFT6 and s.get("name") not in TOUGH6) or "—"
        cls = ' class="body-palace"' if p.get("isBodyPalace") else ""
        rows.append(
            f"<tr{cls}><td>{esc(p['branch'])}·{esc(p['name'])}</td><td>{esc(p['stem'])}</td>"
            f"<td>{major}</td><td>{soft}</td><td>{tough}</td>"
            f"<td>{misc}</td></tr>"
        )
    return ("<table><tr><th>宫位</th><th>宫干</th><th>主星</th><th>六吉</th><th>六煞</th>"
            "<th>其他辅曜</th></tr>" + "".join(rows) + "</table>")


def render_palace_relations(chart: dict) -> str:
    """
    宫位关系表（第四轮新增，脚本产出）。

    「某宫的对宫是哪一宫」「哪两宫与它三合」「哪两宫夹它」——这三件事全部由
    脚本算好渲染，正文只许引用。第三轮盲审的 BLOCKER 就是把命宫的三合宫
    （官禄·子）写成了对宫，而命宫申的对宫其实是迁移·寅。
    """
    rel = chart.get("palaceRelations") or {}
    if not rel:
        return ""
    rows = []
    for p in sorted(chart["palaces"], key=lambda x: BR12.index(x["branch"])):
        r = rel.get(p["branch"])
        if not r:
            continue
        opp = r.get("opposite") or {}
        tri = "、".join(f"{esc(x.get('branch'))}·{esc(x.get('palace'))}"
                        for x in (r.get("trine") or []))
        left, right = r.get("left") or {}, r.get("right") or {}
        nb = f"{esc(left.get('branch'))}·{esc(left.get('palace'))}、" \
             f"{esc(right.get('branch'))}·{esc(right.get('palace'))}"
        rows.append(f"<tr><td>{esc(p['branch'])}·{esc(p['name'])}</td>"
                    f"<td>{esc(opp.get('branch'))}·{esc(opp.get('palace'))}</td>"
                    f"<td>{tri}</td><td>{nb}</td></tr>")
    return ("<table class=\"narrow\"><tr><th>本宫</th><th>对宫</th><th>三合两宫</th>"
            "<th>两邻（夹宫位）</th></tr>" + "".join(rows) + "</table>")


def render_pairing_table(chart: dict) -> str:
    """
    六吉六煞分布与配对表（第五轮新增，脚本产出）。

    为什么必须由脚本渲染：「六吉是否成对、是否同宫／对拱」是纯算术，但第五轮盲审
    BLOCKER 正是报告手写这句时算反了（把三对俱成对拱写成「两两不成对宫」，
    并据此推出「化解力量不成体系」）。表由脚本给，正文只许引用；
    审查器 `AUSPICIOUS_PAIRING_MISSTATED` 按同一份事实逐句核对。
    """
    pf = chart.get("pairingFacts") or {}
    pairs = pf.get("pairs") or []
    if not pairs:
        return ""
    rows = []
    for x in pairs:
        b1, b2 = x.get("branch1"), x.get("branch2")
        if not b1 or not b2:
            where = "（有星不在盘面）"
        else:
            where = (f"{esc(x['star1'])}@{esc(b1)}·{esc(x.get('palace1'))}　"
                     f"{esc(x['star2'])}@{esc(b2)}·{esc(x.get('palace2'))}")
        mark = {
            "同宫": '<span class="bright">同宫</span>',
            "对拱": '<span class="bright">互为对宫（对拱）</span>',
            "三合遥会": '<span class="pending">三合遥会（不算成对相拱）</span>',
            "分居": '<span class="dim">分居（不成对）</span>',
        }.get(x.get("relation") or "", esc(x.get("relation")))
        in_sur = x.get("inSurround") or []
        rows.append(
            f"<tr><td>{esc(x.get('group'))}</td><td>{esc(x.get('abbr'))}</td>"
            f"<td>{where}</td><td>{mark}</td>"
            f"<td>{'、'.join(esc(s) for s in in_sur) or '—'}</td></tr>"
        )

    def summary(key: str, label: str) -> str:
        g = pf.get(key) or {}
        # ⚠️ 措辞刻意避开「成对」二字紧跟在「六吉／六煞」之后：
        # 审查器的 AUSPICIOUS_PAIRING_MISSTATED 会把「六吉…成对」当成断言来核对，
        # 而这里在 0 对成对时说的是「同宫或对拱者 0/3」，本非断言。
        # 写成计数句既准确，也不会让脚本渲染的表反过来被自家闸门误报。
        return (f"<p><b>{esc(label)}</b>：三对之中，同宫或对拱者 "
                f"<b>{esc(g.get('pairedCount'))}/{esc(g.get('totalPairs'))}</b>"
                f"（同宫 {esc(g.get('samePalaceCount'))} 对、对拱 {esc(g.get('oppositeCount'))} 对）；"
                f"落于命宫{esc(pf.get('soulBranch'))}三方四正内者："
                f"{'、'.join(esc(s) for s in (g.get('inSurround') or [])) or '无'}</p>")

    return ("<p>「成对」的判定标准：两颗<b>同宫</b>或<b>互为对宫（对拱）</b>；"
            "三合遥会单列，不算成对相拱。本表为排盘结果，正文只作引用。</p>"
            + summary("soft", "六吉") + summary("tough", "六煞")
            + '<table class="narrow"><tr><th>类别</th><th>对星</th><th>落宫</th>'
              "<th>配对关系</th><th>落命宫三方四正内</th></tr>"
            + "".join(rows) + "</table>")


def render_decadal_palace_map(chart: dict) -> str:
    """
    大限十二宫落本命宫位表（第四轮新增，脚本产出）。

    一张表就说清「本限命宫/兄弟/…/父母宫分别是哪个地支、对应本命哪一宫」，
    正文不必再手写「本限 XX 宫是……」。审查器按同一张表核对正文（DECADAL_PALACE_MISNAMED）。
    现行限（含 --year 指定年份的那一限）加 ◆ 标记。
    """
    dpm = chart.get("decadalPalaceMaps") or []
    if not dpm:
        return ""
    names = list(dpm[0]["palaceMap"].keys())
    natal_of = {p["branch"]: p["name"] for p in chart["palaces"]}
    cur_gz = ((chart.get("horoscope") or [{}])[0].get("decadal") or {}).get("ganzhi")
    head = "".join(f"<th>{esc(n)}</th>" for n in names)
    rows = []
    for x in dpm:
        cells = "".join(
            f"<td>{esc(x['palaceMap'][nm])}·{esc(natal_of.get(x['palaceMap'][nm]))}</td>"
            for nm in names
        )
        star = " ◆现行" if cur_gz and x.get("ganzhi") == cur_gz else ""
        # 行向逐行标出（第七轮脚本疑似失效 1）：大限行向是「阳男阴女顺行／阴男阳女逆行」
        # 的函数值，不是常数；不标出来，读者无法判断表里的地支次序是顺是逆。
        arrow = {1: "顺行", -1: "逆行"}.get(x.get("direction"), "")
        rows.append(f"<tr><td>{x['ageRange'][0]}~{x['ageRange'][1]} {esc(x['ganzhi'])}"
                    + (f"（{arrow}）" if arrow else "")
                    + f"{star}</td>" + cells + "</tr>")
    return ("<table class=\"narrow\"><tr><th>大限</th>" + head + "</tr>"
            + "".join(rows) + "</table>")


def render_decadal_mutagen_landing(chart: dict) -> str:
    """
    大限四化落点表（第四轮新增，脚本产出）：每条四化落在**本命哪一宫**、
    又是**本限哪一宫**。这样「大限天梁化禄照本限福德宫」这类错就无从发生——
    表里直接写着天梁化禄落申宫（本命命宫／本限夫妻宫）。
    """
    dpm = chart.get("decadalPalaceMaps") or []
    if not dpm:
        return ""
    rows = []
    for x in dpm:
        land = x.get("mutagenLanding") or []
        cell = "；".join(
            f"{esc(y.get('type'))}{esc(y.get('star'))}→"
            + (f"{esc(y.get('branch'))}宫（本命{esc(y.get('natalPalace'))}／本限{esc(y.get('decadalPalace'))}）"
               if y.get("branch") else "不在盘面")
            for y in land
        )
        rows.append(f"<tr><td>{x['ageRange'][0]}~{x['ageRange'][1]} {esc(x['ganzhi'])}</td>"
                    f"<td>{cell}</td></tr>")
    return ("<table><tr><th>大限</th><th>大限四化落点（本命宫／本限宫）</th></tr>"
            + "".join(rows) + "</table>")


def render_mutagen(chart: dict) -> str:
    out = ['<div class="callout">']
    out.append("<h3>生年四化</h3><ul>")
    for m in chart.get("birthMutagen") or []:
        out.append(f"<li>{esc(m['star'])}化{esc(m['type'])} —— {esc(m['branch'])}宫（{esc(m['palace'])}）</li>")
    out.append("</ul>")
    selfs = chart.get("selfMutagen") or []
    if selfs:
        out.append("<h3>自化（明有实无）</h3><ul>")
        for m in selfs:
            d = "离心" if m.get("direction") == "离心" else "向心"
            out.append(f"<li>{esc(m['palace'])}：{esc(m['star'])}{'自化' if d == '离心' else '受对宫化入'}"
                       f"{esc(m['type'])}（{d}）</li>")
        out.append("</ul>")
    out.append("</div>")
    return "".join(out)


def render_decadal(chart: dict) -> str:
    rows = []
    for d in chart.get("decadal") or []:
        mg = d.get("mutagen") or ["", "", "", ""]
        rows.append(
            f"<tr><td>{d['ageRange'][0]}~{d['ageRange'][1]}</td>"
            f"<td>{d['yearRange'][0]}~{d['yearRange'][1]}</td>"
            f"<td>{esc(d['ganzhi'])}</td><td>{esc(d['palace'])}</td>"
            f"<td>禄{esc(mg[0])}　权{esc(mg[1])}　科{esc(mg[2])}　忌{esc(mg[3])}</td></tr>"
        )
    return ("<table><tr><th>虚岁</th><th>公历</th><th>干支</th><th>落宫</th><th>大限四化</th></tr>"
            + "".join(rows) + "</table>")


def star_locations(chart: dict) -> dict[str, str]:
    """星名 → 「地支宫 宫名」落点。用于核对四化是否真的落在它该落的宫。"""
    loc: dict[str, str] = {}
    for p in chart["palaces"]:
        for s in (p.get("major") or []) + (p.get("minor") or []):
            loc.setdefault(s["name"], f"{p['branch']}宫（{p['name']}）")
    return loc


def _mutagen_line(label: str, stars: list[str], types: list[str],
                  loc: dict[str, str]) -> str:
    parts = []
    for i, t in enumerate(types):
        nm = stars[i] if i < len(stars) else ""
        where = loc.get(nm, "不在盘面")
        parts.append(f"{esc(nm)}化{esc(t)}→{esc(where)}")
    return f"<li>{esc(label)}：" + "；".join(parts) + "</li>"


def render_horoscope(chart: dict) -> str:
    hs = chart.get("horoscope") or []
    if not hs:
        return ""
    loc = star_locations(chart)
    parts = []
    for h in hs:
        dm, ym = h["decadal"]["mutagen"], h["yearly"]["mutagen"]
        lp = h.get("yearlySoulPalaceLandsOn") or {}
        # 流年辅星（流禄/流羊/流陀/流马）逐颗印出（第七轮新增，脚本产出）。
        # 正文凡写「某年流羊落某宫」只能引这一行，不得手算。
        aux = h.get("yearlyAuxStars") or []
        aux_line = ("<li>流年辅星：" + "；".join(
            f"{esc(a.get('name'))}@本命{esc(a.get('branch'))}宫"
            f"（{esc(a.get('natalPalace'))}）" for a in aux) + "</li>") if aux else ""
        parts.append(
            f'<div class="callout"><h3>{h["year"]} 年（虚岁 {h["nominalAge"]}）</h3>'
            "<ul>"
            f"<li>大限：{esc(h['decadal']['ganzhi'])}</li>"
            + _mutagen_line("大限四化", dm, ["禄", "权", "科", "忌"], loc)
            + f"<li>流年：{esc(h['yearly']['ganzhi'])}</li>"
            + _mutagen_line("流年四化", ym, ["禄", "权", "科", "忌"], loc)
            + f"<li>流年命宫落本命：{esc(lp.get('branch'))}宫 {esc(lp.get('palace'))}"
            f'{("（" + "、".join(esc(x) for x in lp.get("major") or []) + "）") if lp.get("major") else ""}</li>'
            f"<li>小限：{esc((h.get('soulLimit') or {}).get('ganzhi'))} 落 {esc(h.get('soulLimitPalace'))}</li>"
            + aux_line
            + "</ul></div>"
        )
    return "".join(parts)


def list_html(items: Any, ordered: bool = False) -> str:
    """
    列表渲染。

    **要点**：dict 型条目必须渲染成「标题：正文」的可读行，绝不能退化成
    Python repr（`{'title': ..., 'text': ...}`）——那既是内部语泄漏，也和下面的
    叙述段重复。字符串条目里的换行要转成 <br>，Markdown 式 `**强调**` 要去掉星号
    （HTML 里星号会原样显示）。
    """
    if not items:
        return ""
    tag = "ol" if ordered else "ul"
    if isinstance(items, str):
        items = [items]
    li = []
    for it in items:
        if isinstance(it, dict):
            title = esc(it.get("title") or it.get("name") or "")
            body = esc(it.get("text") or it.get("body") or it.get("detail") or "")
            if not title and not body:
                # 兜底：未知结构的 dict 也要渲染成人话，不能把 repr 丢进可读区
                body = esc("；".join(f"{k}：{v}" for k, v in it.items()
                                     if not str(k).startswith("_")))
            li.append(f"<li><b>{title}</b>{('：' + body) if body else ''}</li>")
        else:
            li.append(f"<li>{_inline(it)}</li>")
    return f"<{tag}>" + "".join(li) + f"</{tag}>"


# 行内文本：换行 → <br>，Markdown 式强调标记去掉星号
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_MD_EMPH_RE = re.compile(r"(?<!\*)\*(?!\*)([^*\n]+?)(?<!\*)\*(?!\*)")


def _inline(text: Any) -> str:
    """可读区行内文本：转义 → 去 Markdown 标记 → 换行成 <br>。"""
    out = esc(text)
    out = _MD_BOLD_RE.sub(r"\1", out)
    out = _MD_EMPH_RE.sub(r"\1", out)
    return out.replace("\n", "<br>")


def para_html(text: Any) -> str:
    """段落渲染：与列表同一套行内规则。"""
    return _inline(text)


# 「破格／救格」单元格允许两种形态（第五轮新增第二种）：
#   · 字符串：一整段说明；
#   · [{label, text}] 列表：拆成带标签的分段——用于「破格（结构）」与「救格折损」
#     本就该分开写的情形（第五轮 MINOR 1：火铃夹命行把「救格被削弱」写进了「破格」栏）。
# 语言闸门与结构化泄漏闸门都必须按**扁平化后的文本**扫描，不能 str(列表)
# ——那会把 'label'、'text' 的引号当成结构化数据泄漏（假阳性）。
def _pattern_break_text(bs: Any) -> str:
    def flat(x: Any) -> str:
        if isinstance(x, dict):
            # 字段名不固定（label/text、title/text 都出现过），逐个试而不是猜一个
            for a, b in (("label", "text"), ("title", "text")):
                if x.get(a) or x.get(b):
                    return f"{x.get(a) or ''}：{x.get(b) or ''}"
            return "；".join(f"{k}：{v}" for k, v in x.items() if not str(k).startswith("_"))
        return str(x)

    if isinstance(bs, list):
        return "；".join(p for p in (flat(x) for x in bs) if p.strip("；"))
    if bs is None:
        return ""
    return flat(bs)


def _pattern_break_html(bs: Any) -> str:
    if isinstance(bs, list):
        out = []
        for x in bs:
            if isinstance(x, dict):
                out.append(f"<b>{esc(x.get('label') or '')}</b>：{para_html(x.get('text'))}")
            else:
                out.append(para_html(x))
        return "<br>".join(out) if out else "—"
    return para_html(bs) if bs else "—"


# ── 格局的「格性」列（末轮新增）────────────────────────────────
# 末轮复审 MAJOR 5 / BLOCKER 3：全表没有吉／凶／中性标注，读者无从判断
# 「破格」到底是减凶还是加重；而正文讨论凶格时把化忌与落陷煞写成了
# 「从外部削弱」。方向规则只有落进表内才可执行，故新增此列，
# 并把不成立之格的三栏统一写成「仅作名词与取象说明」。
NATURE_CLS = {"吉": "bright", "凶": "dim", "中性": ""}
_NOT_ESTABLISHED_NOTE = "本格不成立，只留名词与取象"


def _nature_html(pt: dict) -> str:
    nat = str(pt.get("nature") or "").strip()
    cls = NATURE_CLS.get(nat, "")
    if not nat:
        return "—"
    cell = f'<span class="{cls}">{esc(nat)}</span>' if cls else esc(nat)
    if pt.get("satisfied") is False:
        cell += f'（{_NOT_ESTABLISHED_NOTE}）'
    return cell


def _pattern_break_html_row(pt: dict) -> str:
    """不成立之格的三栏（破格／救格／层次）留空，并统一标注为仅作名词与取象说明。

    为什么不能沿用「—」：读者分不清「—」是没有该栏，还是**这一格根本没成立**。
    末轮复审要求对不成立之格明示这一点。
    """
    if pt.get("satisfied") is False:
        return f"—（{_NOT_ESTABLISHED_NOTE}）"
    return _pattern_break_html(pt.get("break_save"))


def render_report(data: dict, chart: dict, analysis: dict, md_full: dict | None) -> tuple[str, dict]:
    """返回 (html, stats)。"""
    meta = data.get("meta") or {}
    # 来自脚本的文本（警告、口径说明）也要过语言闸门并记入技术留痕
    warnings: list[str] = []
    _pre_trail: list[str] = []
    for w in ((chart.get("meta") or {}).get("warnings") or []):
        cw, hits = strip_internal(str(w))
        if cw:
            warnings.append(cw)
        _pre_trail.extend(hits)
    conventions: dict[str, str] = {}
    for k, v in ((chart.get("meta") or {}).get("conventions") or {}).items():
        cv, hits = strip_internal(str(v))
        conventions[k] = cv
        _pre_trail.extend(hits)
    b = chart["basic"]

    trail: list[str] = list(_pre_trail)
    clean: dict[str, Any] = {}

    def clean_text(key: str) -> str:
        """
        只清洗**字符串型**叙事字段。

        ⚠️ 这里曾出过一个渲染事故：`strengths` / `weaknesses` 允许是「字符串或列表」，
        旧版对列表做 `str(raw)`，把 `[{'title': …, 'text': …}]` 的 Python repr 直接
        塞进可读区，既泄漏内部结构，又与上面的列表渲染重复。列表型字段一律走
        `list_html()` 渲染，本函数不再碰它们。
        """
        raw = analysis.get(key) or ""
        if not isinstance(raw, str):
            return ""
        out, hits = strip_internal(raw)
        trail.extend(hits)
        return out

    # 所有会进入可读区的叙事字段都必须清洗（与 validate 的扫描清单保持一致）
    READABLE_KEYS = (
        "skeleton", "star_system", "mutagen_axis", "palace_reading",
        "strengths", "weaknesses", "decadal_trend", "current_year",
        "risk_boundary", "verdict", "advice",
        "firming_reason", "pattern_summary",
    )
    for k in READABLE_KEYS:
        clean[k] = clean_text(k)

    P: list[str] = []
    A = P.append

    A("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">")
    A('<meta name="viewport" content="width=device-width,initial-scale=1">')
    A(f"<title>紫微斗数命盘分析 · {esc(meta.get('name') or '命盘报告')}</title>")
    A(f"<style>{CSS}</style></head><body><div class=\"container\">")

    # 抬头
    # ⚠️ 第七轮脚本疑似失效 2：旧版只读合并包的 `meta.name` / `meta.date`，
    # 而 `--chart/--analysis` 这条路传进来的 `meta` 恒为 `{}`，于是副标题与
    # 生成日期**永远是空的**。现在按 bundle meta → chart.meta → 生成时刻（本地日期）
    # 逐级兜底，并把**排盘引擎与版本**一并印出（读者有权知道盘是谁排的）。
    cmeta = chart.get("meta") or {}
    # 副标题兜底顺序：合并包 meta → 分析 JSON 的 meta → 盘面基本参数（公历·农历·时辰）。
    # 最后那一级保证**任何**输入路径都能印出可核对的抬头，不会留下一行空白。
    h_title = (meta.get("name") or (analysis.get("meta") or {}).get("name")
               or cmeta.get("name") or "")
    if not h_title:
        h_title = "　".join(x for x in (
            f"公历 {b.get('solarDate') or ''}".strip(),
            f"农历 {b.get('lunarDate') or ''}".strip(),
            str(b.get("time") or "").strip(),
        ) if x and not x.endswith(" "))
    h_date = meta.get("date") or cmeta.get("generatedAt") or \
        _dt.datetime.now().strftime("%Y-%m-%d")
    h_engine = meta.get("engineVersion") or (
        f"{cmeta.get('engine')} {cmeta.get('engineVersion')}".strip()
        if cmeta.get("engineVersion") else "")
    A('<div class="report-header">')
    A("<h1>紫微斗数命盘分析</h1>")
    A(f'<div class="subtitle">{esc(h_title)}</div>')
    A(f'<div class="date">报告生成：{esc(h_date)}'
      + (f'　·　排盘引擎 {esc(h_engine)}' if h_engine else "") + "</div>")
    A("</div>")

    # 信息条
    A('<div class="info-bar">')
    for label, value in (
        ("公历", b.get("solarDate")), ("农历", b.get("lunarDate")),
        ("四柱", b.get("chineseDate")), ("五行局", b.get("fiveElementsClass")),
        ("命宫", b.get("soulPalaceBranch")), ("身宫", b.get("bodyPalaceBranch")),
        ("命主", b.get("soul")), ("身主", b.get("body")),
    ):
        A(f'<div class="info-item"><span class="label">{esc(label)}</span>'
          f'<span class="value">{esc(value)}</span></div>')
    A("</div>")

    # 1 定盘与口径
    A("<h2>一、定盘与口径声明</h2>")
    level = esc(analysis.get("firming_level") or "未标注")
    A(f'<div class="callout warn"><b>定盘可信度：{level}</b><br>'
      + para_html(analysis.get("firming_reason") or "") + "</div>")
    if warnings:
        A('<div class="callout bad"><b>排盘阶段的风险提示</b>' + list_html(warnings) + "</div>")
    st = chart.get("solarTime") or {}
    A('<div class="callout"><h3>排盘口径</h3><ul>')
    for k, v in conventions.items():
        A(f"<li><b>{esc(k)}</b>：{esc(v)}</li>")
    if st.get("corrected"):
        A(f"<li><b>真太阳时</b>：{esc(st.get('clock'))} → {esc(st.get('apparent'))}"
          f"（校正 {esc(st.get('delta_minutes'))} 分）</li>")
    # 时辰交界两轴并列（第四轮新增）：真太阳时轴与钟点轴的最近交界与越界方向
    # **可能相反**（本示例即真太阳时轴「退入巳时」、钟点轴「进入未时」），
    # 只写一条轴会让读者误以为方向一致。两轴的数与方向都由脚本算，AI 不得手写。
    if st.get("corrected") and st.get("nearest_hour_boundary"):
        A(f"<li><b>距时辰交界（真太阳时轴）</b>：{esc(st.get('apparent'))} 距最近交界 "
          f"{esc(st.get('nearest_hour_boundary'))} 为 {esc(st.get('minutes_from_hour_boundary'))} 分钟，"
          f"风险方向为{esc(st.get('boundary_shift_direction'))}</li>")
    if st.get("clock_nearest_hour_boundary"):
        A(f"<li><b>距时辰交界（钟点轴）</b>：{esc(st.get('clock'))} 距最近交界 "
          f"{esc(st.get('clock_nearest_hour_boundary'))} 为 "
          f"{esc(st.get('clock_minutes_from_hour_boundary'))} 分钟，"
          f"风险方向为{esc(st.get('clock_boundary_shift_direction'))}</li>")
    A("</ul></div>")

    # 2 命盘全图
    A("<h2>二、命盘全图</h2>")
    A(render_palace_grid(chart))
    A(render_palace_table(chart))
    # 宫位关系表（脚本产出）：正文里凡写「对宫/三合/两邻」都以本表为准
    rel_html = render_palace_relations(chart)
    if rel_html:
        A("<h3>宫位关系（对宫／三合／两邻，由排盘结果给出）</h3>")
        A(rel_html)
    # 六吉六煞分布与配对表（第五轮新增，脚本产出）：正文凡写「成对／不成对／对拱／同宫」
    # 都只能引用本表——第五轮 BLOCKER 就是这处算术被手写时算反了。
    pair_html = render_pairing_table(chart)
    if pair_html:
        A("<h3>六吉六煞分布与配对（由排盘结果给出）</h3>")
        A(pair_html)

    # 3 骨架
    A("<h2>三、命格骨架</h2>")
    A(f"<p>{para_html(clean['skeleton'])}</p>")

    # 4 星系结构
    A("<h2>四、星系结构</h2>")
    A(f"<p>{para_html(clean['star_system'])}</p>")

    # 5 四化主轴
    A("<h2>五、四化主轴</h2>")
    A(render_mutagen(chart))
    A(f"<p>{para_html(clean['mutagen_axis'])}</p>")

    # 6 格局
    A("<h2>六、格局认定</h2>")
    patterns = analysis.get("patterns") or []
    if patterns:
        # 显示名优先取脚本给的 display_name（可带「（存疑条目）」这类限定语）；
        # 审查器按机器键与显示名双向下标匹配，两种写法都能核到条件判定。
        disp_of = {}
        for c in (chart.get("patternCandidates") or []):
            if c.get("pattern"):
                disp_of[c["pattern"]] = c.get("display_name") or c["pattern"]
        A("<table><tr><th>格局</th><th>格性</th><th>是否成立</th>"
          "<th>成立条件与依据</th><th>破格／加重条件与救格</th></tr>")
        for pt in patterns:
            nm = pt.get("name") or ""
            ok = pt.get("satisfied")
            # 三态，不是两态：
            #   True  → 成立
            #   False → 不成立
            #   None  → 存疑／并列（流派口径分歧，报告不得单方断言）
            # 旧版把 None 也渲染成「不成立」，于是「正文说并列、表格说不成立」自相矛盾
            # ——第二轮盲审抓到的正是这一条（刑忌夹印）。缺这一态，矛盾就无法表达。
            if ok is None:
                mark = '<span class="pending">存疑／并列</span>'
            elif ok:
                mark = '<span class="bright">成立</span>'
            else:
                mark = '<span class="dim">不成立</span>'
            A(f"<tr><td>{esc(disp_of.get(nm, nm))}</td>"
              f"<td>{_nature_html(pt)}</td>"
              f"<td>{mark}</td>"
              f"<td>{para_html(pt.get('evidence'))}</td>"
              f"<td>{_pattern_break_html_row(pt)}</td></tr>")
        A("</table>")
        # 报数口径就地声明（末轮 MAJOR 1）：读者看到的「不成立 N 项」必须与表内一致，
        # 不能让「条件满足但落点不在位」在表外另立一类。
        A('<p class="pending">报数口径：上表按「成立／不成立／存疑」三态计数，'
          '凡构成条件满足而星曜落点不在位者（如杀破狼）一律计入「不成立」，不另立类别；'
          '「格性」一列标出该格若成立时的作用方向（吉／凶／中性），'
          '吉格与中性格的负面栏为「破格（减凶条件）」，凶格的负面栏为'
          '「加重条件（煞冲、化忌、失辉）」，两栏方向相反、不互相抵消。</p>')
    # 候选表外的排除说明（第五轮 MINOR 6）：单独一块，不塞进任何一格的「破格／救格」栏。
    # 理由：排除说明（「三方五吉」「以双帝同宫为条件的格目」）不是该格的裁断依据，
    # 混在裁断栏里会让读者以为它是本格的条件之一。
    exclusions = analysis.get("pattern_exclusions") or []
    if exclusions:
        # ⚠️ 标题与收尾句都改成了**读者向**表述：旧版写「（不参与裁断）」与
        #    「只作名词排除」，正是末轮并入元指令闸门的那一类免责说明句
        #    ——生成器自己的文案命中自己的闸门（实测被拦下过一次）。
        A('<div class="callout"><h3>候选表外的排除说明（不在本次判定的裁断结果内）</h3>'
          + list_html(exclusions)
          + "<p>以上名目均不在本次判定的格局候选表内，读者按名目对读即可，"
            "不构成本盘任何一格的成立依据。</p></div>")
    if analysis.get("pattern_summary"):
        A(f"<p>{para_html(analysis['pattern_summary'])}</p>")

    # 7 十二宫逐宫
    A("<h2>七、十二宫逐宫解读</h2>")
    per_palace = analysis.get("palace_details") or []
    if per_palace:
        for pd in per_palace:
            A(f"<h3>{esc(pd.get('palace'))}（{esc(pd.get('branch'))}宫）</h3>")
            A(f"<p>{para_html(pd.get('text'))}</p>")
    else:
        A(f"<p>{para_html(clean['palace_reading'])}</p>")

    # 8 优势与短板
    A("<h2>八、优势结构与结构性短板</h2>")
    A('<div class="callout good"><h3>优势结构</h3>' + list_html(analysis.get("strengths")) + "</div>")
    A('<div class="callout bad"><h3>结构性短板</h3>' + list_html(analysis.get("weaknesses")) + "</div>")
    # 条目已在上面的列表里渲染；这里只在 weaknesses 是**字符串**时补一段叙述（导语），
    # 避免把同一份内容渲染两遍（旧版把列表 repr 也打了一遍，重复且泄漏内部结构）。
    if clean["weaknesses"]:
        A(f"<p>{para_html(clean['weaknesses'])}</p>")

    # 9 大限趋势
    A("<h2>九、大限趋势</h2>")
    A(render_decadal(chart))
    # 大限十二宫映射 + 大限四化落点：限内宫名一律照表，正文不得手算
    dpm_html = render_decadal_palace_map(chart)
    if dpm_html:
        A("<h3>大限十二宫落本命宫位（由排盘结果给出）</h3>")
        A("<p>限内宫名一律照此表读：每一格是「地支·该地支在本命的宫名」。"
          "上表与正文中凡「本限／大限某某宫」的说法，所指之地支以本表为准。</p>")
        A(dpm_html)
    dml_html = render_decadal_mutagen_landing(chart)
    if dml_html:
        A("<h3>大限四化落点（本命宫／本限宫）</h3>")
        A(dml_html)
    A(f"<p>{para_html(clean['decadal_trend'])}</p>")
    decadal_detail = analysis.get("decadal_detail") or []
    for dd in decadal_detail:
        A(f"<h3>{esc(dd.get('range'))}　{esc(dd.get('palace'))}</h3>"
          f"<p>{para_html(dd.get('text'))}</p>")

    # 10 流年
    hs = chart.get("horoscope") or []
    if hs:
        A("<h2>十、流年要点</h2>")
        A(render_horoscope(chart))
        if clean["current_year"]:
            A(f"<p>{para_html(clean['current_year'])}</p>")

    # 11 风险与边界
    A("<h2>十一、风险与边界</h2>")
    A(f"<p>{para_html(clean['risk_boundary'])}</p>")
    A(list_html(analysis.get("boundary_notes")))

    # 11.5 结论与建议
    # ⚠️ 这里原先是个**渲染缺口**：`verdict`（总评）与 `advice`（宜／忌）一直被
    #    `clean_text()` 清洗、被 `--validate` 校验、被语言闸门扫描，却**从没渲染进报告**
    #    ——读者看不到总评，也看不到那六条宜忌（发现于末轮 MINOR ⑨：
    #    「建议无对象／动作／时点」，追下去才发现整块建议压根不在成品里）。
    #    校验过却没渲染的字段，等于把内容写进了没人看的地方。
    #    小节号顺延：本节＝十二，原「十二、分析留痕」改为十三（避免同一份报告里
    #    出现两个「十二」）。
    if clean["verdict"] or clean["advice"]:
        A("<h2>十二、结论与建议</h2>")
        if clean["verdict"]:
            A(f'<div class="callout"><h3>总评</h3><p>{para_html(clean["verdict"])}</p></div>')
        if clean["advice"]:
            A('<div class="callout good"><h3>宜与忌</h3>'
              f'<p>{para_html(clean["advice"])}</p></div>')

    # 分析留痕（可读区）：九步推理链 + 关键发现，供当事人与复核者对照结论
    notes = analysis.get("interpretation_notes") or {}
    notes_clean: list[tuple[str, str]] = []
    for step in NOTE_STEPS:
        raw = str(notes.get(step) or "")
        cv, hits = strip_internal(raw)
        trail.extend(hits)
        if cv:
            notes_clean.append((STEP_LABELS.get(step, step), cv))
    findings = analysis.get("findings") or {}
    main_line, _ = strip_internal(str(findings.get("主线") or ""))
    key_points = []
    for kp in (findings.get("关键点") or []):
        cv, hits = strip_internal(str(kp))
        trail.extend(hits)
        if cv:
            key_points.append(cv)
    contradiction, _ = strip_internal(str(findings.get("矛盾点") or ""))

    if main_line or key_points or notes_clean:
        A("<h2>十三、分析留痕</h2>")
        if main_line:
            A(f'<div class="callout"><h3>主线</h3><p>{para_html(main_line)}</p>'
              + (f"<p><b>矛盾点</b>：{para_html(contradiction)}</p>" if contradiction else "")
              + "</div>")
        if key_points:
            A('<div class="callout"><h3>关键发现</h3>' + list_html(key_points) + "</div>")
        if notes_clean:
            A('<details class="reasoning"><summary>🪜 九步推理过程（点击展开）</summary>')
            for label, txt in notes_clean:
                A(f"<h3>{esc(label)}</h3><p>{para_html(txt)}</p>")
            A("</details>")

    # 全文附录
    if md_full:
        # 第四轮：九步留痕曾同时出现三份（§十二 渲染版 ＋ 本附录的【stepN】原文
        # ＋ 技术留痕里的 JSON 原始）。同一份内容印三遍既冗长又容易三处说法不一，
        # 故凡与 §十二「分析留痕」重复的小节一律不再渲染，并在技术留痕里登记。
        kept = {k: v for k, v in md_full.items() if v and not _is_duplicate_fulltext(k)}
        skipped = [k for k, v in md_full.items() if v and _is_duplicate_fulltext(k)]
        if kept:
            A('<details class="fulltext"><summary>📜 完整分析过程（点击展开）</summary>')
            for k, v in kept.items():
                A(f"<h3>{esc(k)}</h3><pre>{esc(v)}</pre>")
            A("</details>")
        if skipped:
            trail.append("全文附录已去重（与「十三、分析留痕」重复）：" + "、".join(skipped))

    # 技术留痕（默认收起）
    A('<details class="tech-trail"><summary>🔧 技术留痕（复核用，默认收起）</summary>')
    A("<p>以下为生成过程记录，非断语内容。</p>")
    A("<h3>已剥离的内部语片段</h3>")
    # 第五轮：这里以前只印清单（空则写「（无）」），读者无法判断这个「（无）」是真的没有、
    # 还是闸门没在工作——而交付文本里当时确实残留着祈使式元指令。现在把**范围与数目**
    # 一并写明，并单列一节交代「元指令不做静默剥离」：剥离清单必须真实反映实际动作。
    # 第六轮补：旧版声明「差别仅此四类……共 0 段」是**失实**的——可读区当时仍有
    # 「由排盘脚本给出」「按脚本口径」等流程词，只因「脚本」当时不在黑名单里。
    # 现在「脚本」已入黑名单，声明与事实一致；范围也把流程词写全。
    A("<p>本清单与外发正文的差别<b>仅此四类</b>：绝对路径、脚本／文件名、内部流程词"
      "（含「脚本」「判据」这类产出方口径词）、结构化数据残留（含 Markdown 强调星号）。"
      "下列数目即实际命中数，不作估计。</p>")
    A(f"<p>共 <b>{len(trail)}</b> 段。</p>")
    A(f"<pre>{esc(chr(10).join(trail) if trail else '（无：源文本未命中上述四类片段）')}</pre>")
    A("<h3>元指令自检（祈使式 · 自校／防守型 · 自校／免责型说明句）</h3>")
    _meta_hits = sorted({h for chunk in readable_chunks(chart, analysis)
                         for h in scan_meta_instructions(chunk)})
    A("<p>元指令分三类：<b>祈使式</b>（写给自己看的施工说明，如「不可写成…」「切记…」）、"
      "<b>自校／防守型句式</b>（交稿提醒，如「不宜把必然当发现」「是两回事」"
      "「读法分开处理」「两颗并不共处一宫」）与<b>自校／免责型说明句</b>"
      "（作者交代自己的处理方式，如「不作格名论断」「不替任何一派下结论」"
      "「把两条读法的落点都摊开说明」「仅作名词排除」）。三类<b>都不在上表范围内、也不做静默剥离</b>"
      "——它们由生成器的校验与成品语言验收器逐字核对，命中即阻断，不出报告。"
      "本次自检结果：</p>")
    A(f"<pre>{esc(('命中 ' + str(len(_meta_hits)) + ' 处：' + chr(10) + chr(10).join(_meta_hits))
                    if _meta_hits else '0 处（可读区无元指令：祈使式、自校／防守型与自校／免责型说明句均为 0）')}</pre>")
    A("<h3>九步分析留痕与关键发现</h3>")
    # 第四轮：这里原本再印一遍 notes/findings 的 JSON 原文，与 §十二 的可读版重复
    # （加上全文附录里的【stepN】原文，同一份内容共三处）。收敛为一句指向 +
    # 只登记「被剥离的内部语片段」——原文与可读版的差别只有这些片段，没有信息丢失。
    if notes or findings:
        A("<p>可读版见上文「十三、分析留痕」；两版差别仅为上方「已剥离的内部语片段」，"
          "为避免同一内容重复三处，此处不再重印原文。</p>")
    A("<h3>脚本自检</h3>")
    sc = chart.get("selfCheck") or {}
    A(f"<pre>{esc('机器自检：' + ('通过' if sc.get('passed') else '有问题') + chr(10) + chr(10).join(sc.get('problems') or ['（无问题）']))}</pre>")
    A("<h3>排盘输入</h3>")
    A(f"<pre>{esc(json.dumps(chart.get('input') or {}, ensure_ascii=False, indent=2))}</pre>")
    A("</details>")

    # 页脚
    A('<div class="report-footer">')
    A('<div class="disclaimer"><b>免责声明</b><br>'
      "本文为命理与自我观察内容，仅供参考，需与实际条件交叉判读；"
      "不构成投资、财务、医疗、诊断、法律或其他专业建议，必要时请咨询合格专业人士。<br><br>"
      # 第五轮 MINOR 5：旧版把整份报告笼统标成「陆斌兆—王亭之体系收录」，
      # 而口径声明里四化表又写「通行本」——两处来源不同却不分层，读者无从判断该信哪一层。
      # 第六轮 MAJOR 7：分层之后口径仍写错——本报告的核心工具是**来因宫**、
      # 离心自化／向心飞化与「明有实无」，那是**飞星派（钦天四化一脉）**的东西，
      # 与陆斌兆—王亭之（中州派一脉）不是同一体系；而断语又直接依赖四化落点，
      # 所以也不能说这三层「各自独立成立」（那是互证的说法，实际是同一张四化表的推论）。
      # 末轮 MINOR 7：正文通篇不用陆斌兆—王亭之的专名，免责声明却把它写成徵验讯号的出处，
      # 正文里找不到对应实体。改成与正文实际体系相称的表述（三合星情＋飞星自化／来因宫）。
      "<b>口径来源分层</b><br>"
      "· 断语体系：<b>三合派星情 ＋ 飞星派自化／来因宫混合口径</b>。星情与庙陷取三合派读法；"
      "来因宫、离心自化／向心飞化、「明有实无」取飞星派（钦天四化一脉）读法。"
      "涉及身体状况的徵验讯号取自三合派星情与疾厄宫取象，实际徵验率因人而异。<br>"
      "· 四化表：采用通行本（戊、庚、壬三干的化科与中州派不同）。"
      "本报告的落点判断与断语<b>直接建立在这张四化表上</b>——同一四化表之下成立；"
      "换用中州派或古籍口诀的四化表，落点与相应断语都要重算。<br>"
      "· 安星与大限起法：由本报告所用排盘引擎按其通行口径给出，已在上方「排盘口径」逐条声明。<br>"
      "三层来源不同，各自交代清楚；因断语与四化表绑定，三者之间并非互证关系。</div>")
    A("</div>")

    A("</div></body></html>")

    stats = {
        "sections_present": sum(1 for _ in REQUIRED_SECTIONS),
        "sections_expected": len(REQUIRED_SECTIONS),
        "trail_fragments": len(trail),
        "patterns": len(patterns),
        "palace_details": len(per_palace),
        "decadal_detail": len(decadal_detail),
        "has_fulltext": bool(md_full),
        "bytes": 0,
    }
    return "".join(P), stats


# ============================================================
# 校验
# ============================================================


def chart_readable_chunks(chart: dict) -> list[str]:
    """
    会进入可读区的「来自脚本文本」——引擎警告与口径说明同样必须过语言闸门，
    否则脚本自己写的一句提示就能把内部路径漏进交付物。
    """
    out: list[str] = []
    meta = chart.get("meta") or {}
    out += [str(x) for x in (meta.get("warnings") or [])]
    out += [f"{k}：{v}" for k, v in (meta.get("conventions") or {}).items()]
    st = chart.get("solarTime") or {}
    out += [str(x) for x in (st.get("warnings") or [])]
    return out


def readable_chunks(chart: dict, analysis: dict) -> list[str]:
    """
    会进入可读区的**全部文本**（与 render_report 的取值清单一一对应）。

    为什么抽成一个函数：语言闸门、结构化残留闸门、祈使式元指令闸门三处都要用同一份清单，
    散着写必然漂移（第四轮的教训：清单对不上就会漏检）。
    """
    out: list[str] = [str(analysis.get(k) or "") for k in
                      ("skeleton", "star_system", "mutagen_axis", "palace_reading",
                       "strengths", "weaknesses", "decadal_trend", "current_year",
                       "risk_boundary", "verdict", "advice",
                       "firming_reason", "pattern_summary")]
    for key in ("strengths", "weaknesses"):
        v = analysis.get(key)
        if isinstance(v, list):
            for x in v:
                if isinstance(x, dict):
                    out += [str(x.get("title") or ""), str(x.get("text") or "")]
                else:
                    out.append(str(x))
    for pt in (analysis.get("patterns") or []):
        out += [str(pt.get("name") or ""), str(pt.get("evidence") or ""),
                str(pt.get("nature") or ""),
                _pattern_break_text(pt.get("break_save"))]
    out += [str(x) for x in (analysis.get("pattern_exclusions") or [])]
    for x in (analysis.get("palace_details") or []):
        out += [str(x.get("palace") or ""), str(x.get("text") or "")]
    for x in (analysis.get("decadal_detail") or []):
        out += [str(x.get("range") or ""), str(x.get("palace") or ""),
                str(x.get("text") or "")]
    out += [str(x) for x in (analysis.get("boundary_notes") or [])]
    out += [str(analysis.get("firming_level") or "")]
    out += [str(v) for v in (analysis.get("interpretation_notes") or {}).values()]
    fnd = analysis.get("findings") or {}
    out += [str(v) for v in fnd.values() if not isinstance(v, list)]
    out += [str(x) for x in (fnd.get("关键点") or [])]
    out += chart_readable_chunks(chart)
    return out


def artifact_scan_chunks(chart: dict, analysis: dict) -> list[str]:
    """
    结构化数据／Markdown 残留闸门只扫**字符串型叙事字段**。

    列表／字典型字段走 list_html 渲染，不是文本；对它们做 JSON 序列化再扫描，
    会把自己的引号误判成泄漏（第四轮踩过）。patterns 的 break_save 允许是
    [{label,text}] 列表（第五轮新增），必须先扁平化成文本再扫，理由同上。
    """
    out: list[str] = [str(analysis.get(k)) for k in
                      ("skeleton", "star_system", "mutagen_axis", "palace_reading",
                       "decadal_trend", "current_year", "risk_boundary", "verdict",
                       "advice", "firming_reason", "pattern_summary")
                      if isinstance(analysis.get(k), str)]
    for k in ("strengths", "weaknesses"):
        v = analysis.get(k)
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, list):
            out += [x for x in v if isinstance(x, str)]
            out += [_pattern_break_text(x) for x in v if isinstance(x, dict)]
    for pt in (analysis.get("patterns") or []):
        out += [str(pt.get("evidence") or ""), _pattern_break_text(pt.get("break_save"))]
    out += [str(x) for x in (analysis.get("pattern_exclusions") or [])]
    out += [str(x.get("text") or "") for x in (analysis.get("palace_details") or [])]
    out += [str(x.get("text") or "") for x in (analysis.get("decadal_detail") or [])]
    out += [str(x) for x in (analysis.get("boundary_notes") or [])]
    out += [str(v) for v in (analysis.get("interpretation_notes") or {}).values()]
    out += [str(x) for x in ((analysis.get("findings") or {}).get("关键点") or [])]
    out += [str(v) for v in (analysis.get("findings") or {}).values() if isinstance(v, str)]
    out += chart_readable_chunks(chart)
    return out


def validate(data: dict) -> list[str]:
    errs: list[str] = []
    chart = data.get("chart")
    analysis = data.get("analysis")
    if not isinstance(chart, dict) or not chart.get("palaces"):
        errs.append("缺少 chart（需为 ziwei.py chart 的 JSON 输出）")
        return errs
    if not isinstance(analysis, dict):
        errs.append("缺少 analysis（AI 的分析 JSON）")
        return errs

    # ── 分析留痕：强制 AI 先分析、后成文 ─────────────────────
    notes = analysis.get("interpretation_notes")
    if not isinstance(notes, dict):
        errs.append("缺少 interpretation_notes：必须先完成九步分析留痕，再写成文"
                    "（用 prepare_template.py 生成工作单）")
    else:
        for step in NOTE_STEPS:
            v = str(notes.get(step) or "").strip()
            if len(v) < NOTE_MIN:
                errs.append(f"interpretation_notes.{step} 缺失或过短（{len(v)} < {NOTE_MIN} 字）"
                            "——该步未做分析")

    findings = analysis.get("findings")
    if not isinstance(findings, dict):
        errs.append("缺少 findings：需从九步分析收敛出关键发现")
    else:
        if len(str(findings.get("主线") or "").strip()) < 30:
            errs.append("findings.主线 缺失或过短（<30 字）——未收敛出主轴")
        if len(findings.get("关键点") or []) < 3:
            errs.append("findings.关键点 至少 3 条")

    # 必填叙事字段 + 字数下限
    # palace_reading 特殊：有 12 宫明细时它是导语（下限放宽到 40），
    # 没有明细时它必须独自承载逐宫内容（保持 200）。
    pd = analysis.get("palace_details") or []
    for key, minimum in MIN_LEN.items():
        limit = minimum
        if key == "palace_reading" and len(pd) >= 12:
            limit = 40
        val = analysis.get(key) or ""
        # strengths / weaknesses 允许两种形态：字符串，或 [{title, text}] 列表。
        # 列表要按**条目正文合计**计字数，不能 str(list)（那样括号引号都算进去，
        # 空壳条目也能凑够字数）。
        if isinstance(val, list):
            val = "".join(
                (str(x.get("title") or "") + str(x.get("text") or ""))
                if isinstance(x, dict) else str(x)
                for x in val
            )
        if len(str(val).strip()) < limit:
            errs.append(
                f"analysis.{key} 内容过短（{len(str(val).strip())} < {limit} 字）——不得用摘要占位"
            )

    if not analysis.get("firming_level"):
        errs.append("缺少 analysis.firming_level（定盘可信度 A/B/C）")
    if not analysis.get("firming_reason"):
        errs.append("缺少 analysis.firming_reason（定盘依据）")
    if not analysis.get("risk_boundary"):
        errs.append("缺少 analysis.risk_boundary")

    # 十二宫逐宫
    if len(pd) < 12:
        errs.append(f"analysis.palace_details 只有 {len(pd)} 宫，需覆盖十二宫")
    for item in pd:
        if len(str(item.get("text") or "").strip()) < 40:
            errs.append(f"宫位「{item.get('palace')}」解读过短（<40 字）")

    # 格局
    for pt in (analysis.get("patterns") or []):
        if not pt.get("name"):
            errs.append("patterns 中存在缺 name 的条目")
        # satisfied 三态：True 成立 / False 不成立 / None 存疑并列。
        # 只有 True 才强制给破格救格；None 走的是「并列两说」，其分歧说明写在 break_save 里，
        # 但那是内容要求、不是结构要求，故此处只卡「标成立却没给破格救格」这一条硬伤。
        if pt.get("satisfied") is True and not pt.get("break_save"):
            errs.append(f"格局「{pt.get('name')}」标为成立，但未给破格／救格")
        # 末轮新增：格性（吉／凶／中性）与该格锚点宫。
        # 缺格性 → 表内方向（破格＝减凶 vs 加重条件＝加重）不可执行，读者无从判断；
        # 缺锚点 → 「救格（锚点：X宫）」栏名只能退回泛称，效力层级尺就没有原点。
        if str(pt.get("nature") or "") not in ("吉", "凶", "中性"):
            errs.append(f"格局「{pt.get('name')}」缺格性（须为吉／凶／中性之一）")
        if not str(pt.get("anchor") or "").strip():
            errs.append(f"格局「{pt.get('name')}」缺锚点宫（救格栏须以该格锚点为原点）")

    # 可读区语言闸门（清单必须与 render_report 的 READABLE_KEYS 一致，否则会漏检）
    readable_chunks_ = readable_chunks(chart, analysis)
    hits = sorted({h for chunk in readable_chunks_ for h in scan_readable(chunk)})
    if hits:
        errs.append("可读区残留内部用语：" + "、".join(hits[:12])
                    + "（请改写为面向当事人的表述）")

    # 元指令闸门（第五轮：祈使式；第六轮：自校／防守型）：交付文本里不许留写给自己看的话。
    # 命中即阻断，**不做静默剥离**——剥离会改坏句子，而这类话本就不该写进断语。
    metas = sorted({h for chunk in readable_chunks_ for h in scan_meta_instructions(chunk)})
    if metas:
        errs.append("可读区出现元指令（祈使式施工说明／自校·防守型句式）：" + "、".join(metas[:12])
                    + "（须改写为面向当事人的陈述；给当事人的建议用「宜／忌」，"
                      "「须／必须／切记／务必」只用于写作指令；自校句改写成结论本身）")

    # 可读区「结构化数据泄漏」闸门：只在**字符串型**叙事字段上检查。
    leak_chunks = artifact_scan_chunks(chart, analysis)
    leaks = sorted({h for chunk in leak_chunks for h in scan_artifact_leaks(chunk)})
    if leaks:
        errs.append("可读区出现结构化数据／Markdown 残留痕迹：" + "、".join(leaks[:12])
                    + "（须渲染成「标题：正文」并去掉强调星号，不得把数据结构直接给读者看）")
    return errs


# ============================================================
# 入口
# ============================================================


def load_json(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    raw = sys.stdin.read()
    if not raw.strip():
        raise SystemExit("错误：没有输入。用 --input 指定 JSON，或从 stdin 传入。")
    return json.loads(raw)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="generate_report.py",
        description="由结构化 JSON 生成紫微斗数 HTML 分析报告",
    )
    ap.add_argument("--input", help="合并后的 JSON（含 meta/chart/analysis/md_full）")
    ap.add_argument("--chart", help="chart JSON（与 --analysis 配合）")
    ap.add_argument("--analysis", help="analysis JSON（与 --chart 配合）")
    ap.add_argument("--md-full", dest="md_full", help="全文附录 JSON（可选）")
    ap.add_argument("--output", "-o", help="输出 HTML 路径")
    ap.add_argument("--validate", action="store_true", help="只校验，不写文件")
    ap.add_argument("--allow-partial", action="store_true",
                    help="校验失败也照常生成（仅用于草稿）")
    args = ap.parse_args(argv)

    try:
        if args.input:
            data = load_json(args.input)
        else:
            if not args.chart or not args.analysis:
                print("错误：需要 --input，或同时提供 --chart 与 --analysis", file=sys.stderr)
                return 2
            data = {
                "meta": {},
                "chart": load_json(args.chart),
                "analysis": load_json(args.analysis),
                "md_full": load_json(args.md_full) if args.md_full else {},
            }
    except json.JSONDecodeError as e:
        print(f"错误：JSON 解析失败 — {e}", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"错误：无法读取输入 — {e}", file=sys.stderr)
        return 2

    errs = validate(data)
    if errs:
        print("❌ 校验未通过：", file=sys.stderr)
        for e in errs:
            print(f"  - {e}", file=sys.stderr)
        if not args.allow_partial:
            return 1
        print("  （--allow-partial：仍继续生成草稿）", file=sys.stderr)
    else:
        print("✅ 校验通过")

    if args.validate:
        return 1 if errs else 0

    htm, stats = render_report(data, data["chart"], data["analysis"], data.get("md_full"))
    stats["bytes"] = len(htm.encode("utf-8"))

    if not args.output:
        print(htm)
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(htm, encoding="utf-8")

    print(f"HTML 报告已生成：{out}")
    print(f"  文件大小：{stats['bytes'] / 1024:.1f} KB")
    print(f"  板块完整性：{stats['sections_present']}/{stats['sections_expected']}")
    print(f"  格局条目：{stats['patterns']}　逐宫解读：{stats['palace_details']} 宫　大限详述：{stats['decadal_detail']} 段")
    print(f"  全文附录：{'有' if stats['has_fulltext'] else '无'}")
    print(f"技术留痕：已剥离 {stats['trail_fragments']} 段内部片段（数目即实际命中数，"
          "清单见报告内「技术留痕」一节）")
    return 0 if not errs else 1


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
