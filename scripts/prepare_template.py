#!/usr/bin/env python3
"""
ziwei-doushu / scripts/prepare_template.py — 生成分析工作单（给 AI 填）

**为什么需要它**：报告不是固定脚本拼出来的。盘面数据由脚本算，**解读必须由 AI 做**。
本脚本把 `ziwei.py chart` 的输出里那些「已成事实、AI 不该重复抄」的部分
（十二宫星曜、四化落点、自化、三方四正、夹宫、大限表、格局候选）
自动填进模板，把 AI 的注意力**全部留给真正需要判断的地方**：

  · 九步分析的逐步留痕（interpretation_notes）—— 强制先分析、后成文
  · 定盘等级与理由
  · 哪些格局真成立（候选只是条件判定，需按全盘裁断）
  · 十二宫逐宫的解读文字
  · 优势与短板的取舍、大限流年的趋势
  · 风险与边界

用法：
    python prepare_template.py --chart chart.json --output analysis.json
    python prepare_template.py --chart chart.json > analysis.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ziwei_doushu import astro  # noqa: E402


def _stars(lst) -> str:
    if not lst:
        return "无"
    out = []
    for s in lst:
        t = s.get("name") or str(s)
        if isinstance(s, dict):
            if s.get("brightness"):
                t += f"[{s['brightness']}]"
            if s.get("mutagen"):
                t += f"化{s['mutagen']}"
        out.append(t)
    return "、".join(out)


def build(chart: dict) -> dict:
    b = chart["basic"]
    obs = chart.get("observations") or {}

    palace_lines = []
    for p in chart["palaces"]:
        mag = ""
        if p.get("isBodyPalace"):
            mag += "★身宫 "
        if p.get("isLaiYin"):
            mag += "●来因 "
        dr = p.get("decadalRange")
        palace_lines.append(
            f"{p['branch']}宫 {p['name']}{('（' + mag.strip() + '）') if mag else ''}"
            f"｜宫干{p['stem']}"
            f"｜主星：{_stars(p.get('major'))}"
            f"｜辅煞：{_stars(p.get('minor'))}"
            f"｜大限：{str(dr[0]) + '~' + str(dr[1]) if dr else '-'}"
        )

    birth = "　".join(
        f"{m['star']}化{m['type']}→{m['branch']}宫{m['palace']}"
        for m in (chart.get("birthMutagen") or [])
    )
    # 自化：离心用本宫宫干，向心用对宫宫干——两者必须分别标注，否则会误读成同一个宫干
    stem_of = {p["name"]: p["stem"] for p in chart["palaces"]}
    self_parts = []
    for m in (chart.get("selfMutagen") or []):
        own = stem_of.get(m["palace"], m.get("from_stem"))
        if m["direction"] == "离心":
            self_parts.append(f"{m['palace']}（本宫宫干{own}）{m['star']}自化{m['type']}")
        else:
            self_parts.append(
                f"{m['palace']}（本宫{own}）{m['star']}受对宫{m['from_branch']}"
                f"（宫干{m['from_stem']}）化入{m['type']}"
            )
    selfs = "　".join(self_parts)

    dec = " / ".join(
        f"{d['ageRange'][0]}~{d['ageRange'][1]}虚岁({d['yearRange'][0]}~{d['yearRange'][1]}) "
        f"{d['ganzhi']}落{d['palace']} 禄{d['mutagen'][0]}权{d['mutagen'][1]}科{d['mutagen'][2]}忌{d['mutagen'][3]}"
        for d in (chart.get("decadal") or [])[:4]
    )

    horo = " / ".join(
        f"{h['year']}年虚岁{h['nominalAge']} 流年命宫落本命{h['yearlySoulPalaceLandsOn']['branch']}宫"
        f"{h['yearlySoulPalaceLandsOn']['palace']} 流年四化禄{h['yearly']['mutagen'][0]}"
        f"权{h['yearly']['mutagen'][1]}科{h['yearly']['mutagen'][2]}忌{h['yearly']['mutagen'][3]}"
        for h in (chart.get("horoscope") or [])
    )

    # 格局候选：脚本只判条件，AI 需按全盘裁断（satisfied / break_save 留空）
    cand = []
    for pc in (chart.get("patternCandidates") or chart.get("patternChecks") or []):
        cand.append({
            # name 用机器键（审查器按它匹配）；display_name 可带「（存疑条目）」这类限定语
            "name": pc.get("pattern") or pc.get("name"),
            "script_conditions_met": bool(pc.get("conditions_met", pc.get("script_condition_met"))),
            "script_evidence": pc.get("evidence"),
            "satisfied": None,        # ← AI 裁断：此格在本盘是否真成立
            "evidence": "",           # ← AI 写：成立/不成立的依据
            # ← AI 写：破格与救格（标成立则必填）。
            #   可以是字符串；也可写成 [{"label": "破格（结构）", "text": "…"},
            #   {"label": "救格折损", "text": "…"}] 两段——两件事本就不同时用这种写法。
            "break_save": "",
        })

    # 六吉六煞配对事实（第五轮新增）：成不成对、同不同宫、对不对拱全部由脚本给，
    # 正文只许引用。旧版把判据交给 AI 手写，结果「六吉两两不成对宫」写反了。
    pf = chart.get("pairingFacts") or {}
    pair_lines = [
        f"{x['group']}·{x['abbr']}：{x['star1']}@{x.get('branch1')}·{x.get('palace1')} ／ "
        f"{x['star2']}@{x.get('branch2')}·{x.get('palace2')} → {x.get('relation')}"
        f"（落命宫三方四正内：{'、'.join(x.get('inSurround') or []) or '无'}）"
        for x in (pf.get("pairs") or [])
    ]
    pair_summary = "；".join(
        f"{label}三对之中同宫或对拱者 {(pf.get(key) or {}).get('pairedCount')}/"
        f"{(pf.get(key) or {}).get('totalPairs')}"
        for key, label in (("soft", "六吉"), ("tough", "六煞"))
    )

    return {
        "_INSTRUCTIONS": {
            "读我": "这是分析工作单。带 ◆ 的字段由 AI 填写，脚本已把盘面事实填进 _CHART_FACTS，不要重抄。",
            "强制顺序": [
                "1) 先读 references/interpretation.md（判读规则、格局层次、10 类必须并列的流派分歧、误判清单）",
                "2) 再读 references/report-schema.md（字段与字数下限）",
                "3) **先完成九步分析并写进 interpretation_notes**，每步写「看到了什么→据此判断什么」",
                "4) **再从分析结果写成文**（skeleton / star_system / … / risk_boundary）",
                "5) 最后跑 generate_report.py --validate，按报错逐条修",
            ],
            "禁止": [
                "跳过 interpretation_notes 直接拼字段（脚本会判不合格）",
                "把 _CHART_FACTS 原样抄成正文（正文是你的判断，不是盘面复述）",
                "AI 自行推导星曜落宫或四化（一律以 chart 为准）",
                "AI 自行断言六吉六煞谁跟谁成对／同宫／对拱（照「六吉六煞配对」栏引用）",
                "可读区出现文件名、路径、内部流程词（语言闸门会拦）",
                "**把写作指令写进正文**（「落点须分开写」「不可写成…」「切记…」「不可混称…」）"
                "——祈使式元指令闸门会拦；给当事人的建议一律用「宜／忌」",
                "**把自校／防守型句子写进正文**（「不宜把必然当发现」「层次要分清」「是两回事」"
                "「读法分开处理」「两颗并不共处一宫」「不宜并列」）——元指令闸门会拦；"
                "这类话要改写成读者向的结论本身，或移入技术留痕",
                "**可读区出现产出方口径词**（「脚本」「判据」）——写「排盘结果」「本次判定」",
                "**效力层级写错**（第六轮）：按 references/interpretation.md §3.2 的尺——"
                "1 同宫（贴身）＞ 2 两邻（夹）＞ 3 三合会照 ＞ 4 对宫照（含三合宫的对宫）＞ 5 遥会。"
                "两邻属贴身层，**不得写「效力次一等」**；对宫照不得高于两邻；"
                "吉星与煞星同宫写「贴身但含煞」；「贴身护格有几颗」只数第 1～2 级",
                "**破格／救格／层次三栏串味**（第六轮）：破格栏只列外部削弱条件（煞冲、空劫、"
                "失辉、宫破、逢冲破），本格的形成形态（亮度只居中上、配置是否对称）写进"
                "「层次与效力说明」栏；「救格折损」属救格一侧；凶格就地写明方向"
                "（破格＝减凶、救格折损＝加重）；格层次与命局层次两个标签不混用",
                "把候选表外的格名（如「三方五吉」「以双帝同宫为条件的格目」）写进某一格的"
                "破格／救格栏——统一登记到 pattern_exclusions，渲染成独立的「排除说明」一节",
            ],
            "九步": [
                "1 定盘校验：真太阳时是否近交界、是否做过往事反推 → 定 A/B/C",
                "2 读骨架：命宫／身宫／来因宫／五行局／命主身主",
                "3 定星系类型：紫微落宫 → 六种组合之一",
                "4 本宫＋三方四正",
                "5 看亮度（分星判断再叠乘，庙陷不可整宫论）",
                "6 看辅佐煞杂曜——**六吉六煞的成对／同宫／对拱一律照"
                "「配对事实」栏引用，不要自己判断谁跟谁成对**；再六煞落哪、禄存被羊陀夹在哪",
                "7 看四化：生年→大限→流年，三层不可混",
                "8 夹宫扫描",
                "9 格局认定（报格局必须同时报成立条件与破格/救格）",
            ],
        },
        "_CHART_FACTS": {
            "四柱农历年口径": b["chineseDate"],
            "农历": b["lunarDate"],
            "五行局": b["fiveElementsClass"],
            "命宫": b["soulPalaceBranch"],
            "身宫": b["bodyPalaceBranch"] + ("（命身同宫）" if b.get("sameSoulBody") else ""),
            "命主": b["soul"],
            "身主": b["body"],
            "来因宫": (b["laiYinPalace"]["branch"] + "宫" + b["laiYinPalace"]["palace"]) if b.get("laiYinPalace") else "无",
            "生年四化": birth,
            "自化": selfs or "无",
            "十四主星齐备": f"{obs.get('fourteenComplete')}/14",
            "庙旺集中": "、".join(obs.get("brightStrong") or []) or "无",
            "失辉集中": "、".join(obs.get("brightWeak") or []) or "无",
            "六吉": "、".join(obs.get("sixSoft") or []) or "无",
            "六煞": "、".join(obs.get("sixTough") or []) or "无",
            # 第五轮新增：配对事实（成对＝同宫或互为对宫；三合遥会不算）
            "六吉六煞配对": pair_lines or ["（盘面未产出配对事实，请检查 chart 输出版本）"],
            "配对小结": pair_summary or "无",
            "空宫": "、".join(f"{e['branch']}·{e['palace']}" for e in (obs.get("emptyPalaces") or [])) or "无",
            "十二宫": palace_lines,
            "三方四正": {
                k: (f"{v['branch']}宫{v['name']}：{_stars(v.get('major'))}｜{_stars(v.get('minor'))}" if v else "无")
                for k, v in (chart.get("surround") or {}).items()
            },
            "夹宫": {
                "左邻": (f"{chart['flank']['left']['branch']}宫{chart['flank']['left']['name']}"
                        f"（{'、'.join(x['star'] + x['kind'] for x in chart['flankKinds']['left']) or '中性'}）")
                if chart.get("flank", {}).get("left") else "无",
                "右邻": (f"{chart['flank']['right']['branch']}宫{chart['flank']['right']['name']}"
                        f"（{'、'.join(x['star'] + x['kind'] for x in chart['flankKinds']['right']) or '中性'}）")
                if chart.get("flank", {}).get("right") else "无",
            },
            "大限前四限": dec,
            "流年": horo or "（未指定年份，可先用 --year 补）",
            "机器自检": "通过" if (chart.get("selfCheck") or {}).get("passed") else
                        "有问题：" + "；".join((chart.get("selfCheck") or {}).get("problems") or []),
            "排盘告警": (chart.get("meta") or {}).get("warnings") or [],
            "真太阳时": (f"{chart['solarTime'].get('clock')} → {chart['solarTime'].get('apparent')}"
                        f"（校正 {chart['solarTime'].get('delta_minutes')} 分）"
                        if (chart.get("solarTime") or {}).get("corrected") else "未校正"),
        },

        # ===== ◆ AI 必填 =====
        "interpretation_notes": {
            "_说明": "九步分析留痕。每步写「看到了什么 + 据此判断什么」，不是复述盘面。这是报告的依据链。",
            "step1_定盘": "",
            "step2_骨架": "",
            "step3_星系类型": "",
            "step4_本宫三方四正": "",
            "step5_亮度": "",
            "step6_辅佐煞杂曜": "",
            "step7_四化三层": "",
            "step8_夹宫": "",
            "step9_格局裁断": "",
        },
        "findings": {
            "_说明": "从九步分析收敛出的**关键发现**（3–7 条）。这些是报告的骨架，不是盘面复述。",
            "主线": "",
            "关键点": [],
            "矛盾点": "",
        },

        "firming_level": "",
        "firming_reason": "",
        "skeleton": "",
        "star_system": "",
        "mutagen_axis": "",
        "palace_reading": "",
        "palace_details": [
            {"palace": p["name"], "branch": p["branch"], "text": ""}
            for p in sorted(chart["palaces"], key=lambda x: astro.BR12.index(x["branch"]))
        ],
        "patterns": cand,
        "pattern_exclusions": [],
        "pattern_summary": "",
        "strengths": [],
        "weaknesses": [],
        "decadal_trend": "",
        "decadal_detail": [],
        "current_year": "",
        "risk_boundary": "",
        "boundary_notes": [],
        "verdict": "",
        "advice": "",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="prepare_template.py",
        description="由 chart JSON 生成 AI 可填的分析工作单",
    )
    ap.add_argument("--chart", help="chart JSON 路径；省略则从 stdin 读")
    ap.add_argument("--output", "-o", help="输出路径；省略则打到 stdout")
    args = ap.parse_args(argv)

    raw = Path(args.chart).read_text(encoding="utf-8") if args.chart else sys.stdin.read()
    try:
        chart = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"错误：chart JSON 解析失败 — {e}", file=sys.stderr)
        return 2
    if not chart.get("ok") or not chart.get("palaces"):
        print("错误：输入不是有效的 chart 输出（缺 palaces）", file=sys.stderr)
        return 2

    tpl = build(chart)
    text = json.dumps(tpl, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        n_palace = len(tpl["palace_details"])
        n_pat = len(tpl["patterns"])
        print(f"工作单已生成：{args.output}")
        print(f"  待填：九步分析 ×9　十二宫解读 ×{n_palace}　格局裁断 ×{n_pat}　正文 ×8")
        print("  提示：先填 interpretation_notes，再写成文；最后跑 --validate 收口")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
