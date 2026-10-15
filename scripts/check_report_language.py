#!/usr/bin/env python3
"""
ziwei-doushu / scripts/check_report_language.py — 对外交付语言验收器

用途：对着**已生成的 HTML 报告**复检「可读区有没有内部流程语」，并复查
**结构化数据泄漏**——列表渲染器漏处理 dict 条目时，可读区会出现
`{'title': '…', 'text': '…'}` 这类 Python repr；Markdown 强调星号也会原样显示。
第五轮起再查一类：**祈使式元指令**（「落点须分开写」「切记…」「不可写成…」「不能写成…」
「不可混称…」）——那是写给自己看的施工说明，读者不该看到（同类问题第二次出现，故代码化）。
**第六轮再补一类：自校／防守型句式**（「不宜把必然当发现」「层次要分清」「是两回事」
「读法分开处理」「两颗并不共处一宫」「不宜并列」）——同属写给自己看的交稿提醒，
同类问题第三次出现，故一并代码化。
与 `generate_report.py --validate` 的区别：`--validate` 查组装前的 JSON，
本脚本查最终 HTML——能在交付前抓住任何漏网的内部语（含手工改过的报告）。

**末轮再补第三类：自校／免责型说明句**（「不作格名论断」「不替任何一派下结论」
「不参与裁断」「把两条读法的落点都摊开说明」「仅作名词排除」）——作者在交代自己的
处理方式，而不是给读者结论。同类问题第四次出现，故一并代码化。

判定口径与 `generate_report.py` 同源（直接 import），避免两处黑名单漂移。

用法：
    python check_report_language.py report.html
    python check_report_language.py --verbose report.html
    python check_report_language.py --self-test      # 只跑元指令规则的夹具与反例

退出码：0 = 可读区干净；1 = 发现残留；2 = 参数/文件错误。
"""

from __future__ import annotations

import argparse
import html as html_mod
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import generate_report as G  # noqa: E402

TRAIL_MARKER = '<details class="tech-trail">'
FOOTER_MARKER = '<div class="report-footer">'


# ============================================================
# 元指令规则的夹具与反例自测（第五轮：祈使式；第六轮：自校／防守型）
# ============================================================
# 每个新规则都要有「应被检出」与「不得误报」两类夹具——只证明「能报错」不够，
# 闸门最大的风险是把正确的话判成错（「须靠」「不得地」「两处落点分别记明」这类
# 常用写法就极易误伤）。判据与 generate_report.scan_meta_instructions 同源
# （直接调它，不重写一份）。
META_SELF_TEST: list[tuple[str, str, bool]] = [
    # (用例名, 文本, 是否应被检出)
    ("须 + 分开写", "落点须分开写：两宫的确切落点见下。", True),
    ("须 + 分开说", "救格分处两宫，须分别说清。", True),
    ("不可写成", "故不主此格。落点不可写成「两宫都不在」。", True),
    ("不能写成", "两颗并不共处一宫，不能写成「同宫」。", True),
    ("不得写成", "同一宫叠加，不得写成「分居两宫」。", True),
    ("不可混称", "与右弼这一条属不同层次——不可混称「两科分居辰戌」。", True),
    ("不可混用", "两套体系属不同口径，不可混用。", True),
    ("切记", "切记日月反背不可与日月同宫断语混用。", True),
    ("务必", "务必注明该格的流派口径。", True),
    ("必须 + 写清", "两条轴必须分别写清。", True),
    ("必须 + 一致", "限内宫名一律照此表读，含义必须与本表一致。", True),
    ("不得 + 标成立", "不构成本命主格，不得标成立。", True),
    # —— 自校／防守型句式（第六轮新增）：作者写给自己的交稿提醒 ——
    ("自校·不宜把…当", "这一半恒真、无鉴别力，不宜把必然当发现。", True),
    ("自校·要分清", "层次要分清：叠加的是那三条同落辰宫的科。", True),
    ("自校·是两回事", "这与「两科分居辰戌」是两回事。", True),
    ("自校·读法分开处理", "两者不是同一宫，读法分开处理。", True),
    ("自校·并不共处一宫", "未宫天钺与命宫天同，两颗并不共处一宫。", True),
    ("自校·不宜并列", "该宫属本宫三方四正内的事业面，不宜并列。", True),
    # —— 自校／免责型说明句（末轮新增）：作者在交代自己的处理方式，而不是给结论 ——
    ("免责·不作格名论断", "该格在本盘不成立，不作格名论断。", True),
    ("免责·不作判断", "两种口径对本盘有实际差别，本报告并列两说，不替任何一派下结论。", True),
    ("免责·不参与裁断", "候选表之外的名目一律只作名词列举，不参与裁断。", True),
    ("免责·把两条读法摊开说明", "两派的取象不同，本报告把两条读法的落点都摊开说明。", True),
    ("免责·两说都摊开", "两说都摊开说明，读者可自行对读。", True),
    ("免责·仅作名词", "本项只保留名目，仅作名词，不进入取象。", True),
    ("免责·只作名词排除", "本盘既无此配置，只作名词排除。", True),
    ("免责·不进入裁断", "三方五吉一类只列名目，不进入裁断。", True),
    # —— 以下为「不得误报」的反例：都是真实报告里出现过的正常断语 ——
    ("反例·须靠（事理的必然）", "一处集中、一处呼应，力量须靠实际往来维持。", False),
    ("反例·须借（星曜取象）", "迁移空宫须借命宫天同天梁照，属命强迁弱。", False),
    ("反例·须持续", "科有两处，但仍须持续经营，不会自动生息。", False),
    ("反例·须结合实际（边界声明）", "无法仅凭命盘确定的事项，均须结合实际条件判断。", False),
    ("反例·亮度词「不得地」", "太阳不得地独坐，辅星右弼带生年化科。", False),
    ("反例·宜／忌（给当事人的建议）", "财务上宜缓不宜急，忌急进强求。", False),
    # —— 自校类规则的反例：这些写法都是**面向读者**的，不得被判成自校语 ——
    ("反例·两处落点分别记明", "两处落点分别记明：一在未宫，一在申宫。", False),
    ("反例·不是分居两宫", "两条科同落辰宫，不是分居辰戌两宫。", False),
    ("反例·两颗帝星不同宫", "紫微与天府两颗帝星不同宫，各自的作用面不同。", False),
    ("反例·指向同一处", "两说都指向同一处提醒：协作时留意边界与情面。", False),
    ("反例·两处不同的宫位", "未宫与申宫是两处不同的宫位。", False),
    ("反例·分处两宫", "救格分处两宫：一在未宫，一在申宫。", False),
    # —— 末轮新增规则的反例（不得误报）——
    ("反例·只作结构性提醒", "财务与健康两处只作结构性提醒，不构成投资建议。", False),
    ("反例·本栏只说明取象", "本栏只说明取象与落点，不作别的用途。", False),
    ("反例·本格两说并列", "本格两说并列，两种口径对本盘有实际差别。", False),
    ("反例·两派的取象不同", "两派的取象不同，落点却在本盘一致。", False),
]


def run_self_test() -> int:
    """对元指令规则跑夹具与反例，返回 0=全过 / 1=有失败。"""
    print("=== 元指令规则自测（祈使式 · 自校／防守型 · 自校／免责型说明句）"
          "— check_report_language ===")
    failed = 0
    for name, sample, should_hit in META_SELF_TEST:
        hits = G.scan_meta_instructions(sample)
        got = bool(hits)
        ok = got == should_hit
        if not ok:
            failed += 1
        mark = "✓" if ok else "✗"
        want = "应检出" if should_hit else "不得误报"
        detail = ("命中 " + "、".join(hits)) if hits else "未命中"
        print(f"  {mark} [{want}] {name} — {detail}")
        if not ok:
            print(f"      文本：{sample}")
    print(f"\n合计 {len(META_SELF_TEST) - failed}/{len(META_SELF_TEST)} 通过")
    return 0 if failed == 0 else 1


def client_region(markup: str) -> str:
    """
    取可读区＝技术留痕 <details> 之前的部分，**另加页脚**。

    页脚（免责声明与口径来源分层）渲染在技术留痕折叠区之后；若只按 <details>
    位置切一刀，页脚就落在可读区之外、语言闸门扫不到它——可读区的定义必须覆盖
    读者真正能看到的全部内容。故此处显式把页脚并回扫描范围。
    """
    idx = markup.find(TRAIL_MARKER)
    region = markup[:idx] if idx >= 0 else markup
    fidx = markup.find(FOOTER_MARKER)
    if fidx >= 0:
        region = region + "\n" + markup[fidx:]
    return region


def html_to_text(markup: str) -> str:
    """还原成「读者实际看到的文字」：去脚本/样式/注释/标签，再解实体。"""
    s = re.sub(r"<script\b.*?</script>", " ", markup, flags=re.S | re.I)
    s = re.sub(r"<style\b.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    s = re.sub(r"<[^>]+>", " ", s)
    return html_mod.unescape(s)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_report_language.py")
    ap.add_argument("path", nargs="?", help="已生成的 HTML 报告路径")
    ap.add_argument("--verbose", action="store_true", help="打印命中上下文")
    ap.add_argument("--self-test", action="store_true",
                    help="只跑元指令（祈使式 · 自校／防守型 · 自校／免责型说明句）"
                         "规则的夹具与反例自测，不需要报告文件")
    args = ap.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if not args.path:
        print("错误：需要报告路径，或用 --self-test 只跑规则自测", file=sys.stderr)
        return 2

    p = Path(args.path)
    if not p.exists():
        print(f"错误：文件不存在 — {p}", file=sys.stderr)
        return 2

    markup = p.read_text(encoding="utf-8", errors="replace")

    problems: list[str] = []
    text = html_to_text(client_region(markup))
    hits = G.scan_readable(text)
    if hits:
        problems.append("可读区残留内部用语：" + "、".join(hits[:20]))
        if args.verbose:
            for h in hits[:20]:
                for m in re.finditer(re.escape(h), text):
                    ctx = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
                    print(f"    命中「{h}」上下文： …{ctx.strip()}…", file=sys.stderr)

    # 元指令（第五轮：祈使式；第六轮：自校／防守型）：交付文本里不许留写给自己看的话。
    # 与内部流程词分开报，因为处置方式不同——流程词可以改写，这类要**整句重写**。
    metas = G.scan_meta_instructions(text)
    if metas:
        problems.append("可读区出现元指令（写给自己看的施工说明或交稿提醒）："
                        + "、".join(metas[:20]))
        if args.verbose:
            for h in metas[:20]:
                for m in re.finditer(re.escape(h), text):
                    ctx = text[max(0, m.start() - 40): m.end() + 40].replace("\n", " ")
                    print(f"    命中「{h}」上下文： …{ctx.strip()}…", file=sys.stderr)

    # 结构化数据／Markdown 残留：列表渲染器漏处理 dict 条目时，可读区会出现
    # `{'title': …}` 这类内部结构；Markdown 强调星号在 HTML 里也会原样显示。
    # 它们都不是「内部流程词」，旧闸门抓不到，所以单列一条。
    leaks = G.scan_artifact_leaks(text)
    if leaks:
        problems.append("可读区出现结构化数据／Markdown 残留痕迹："
                        + "、".join(leaks[:20]))
        if args.verbose:
            for h in leaks[:20]:
                for m in re.finditer(re.escape(h), text):
                    ctx = text[max(0, m.start() - 60): m.end() + 60].replace("\n", " ")
                    print(f"    命中「{h}」上下文： …{ctx.strip()}…", file=sys.stderr)

    # 技术留痕必须存在且默认收起
    idx = markup.find(TRAIL_MARKER)
    if idx < 0:
        problems.append("缺少「技术留痕」折叠区（<details class=\"tech-trail\">）")
    else:
        seg = markup[idx: idx + 400]
        if re.search(r"<details[^>]*\bopen\b", seg):
            problems.append("「技术留痕」区未默认收起（存在 open 属性）")

    # 必查板块
    # ⚠️ 「结论与建议」也要查：`verdict` / `advice` 这两个字段曾被校验、被扫描却
    #    **从不渲染**（末轮 MINOR ⑨ 追出来的渲染缺口）。加进必查清单，
    #    以后谁再把这块渲染删掉，闸门会立刻报「缺少板块」。
    for sec in ("命盘全图", "格局", "十二宫逐宫", "优势结构与结构性短板", "大限趋势",
                "风险与边界", "结论与建议"):
        if sec not in text:
            problems.append(f"缺少板块：{sec}")

    if problems:
        print("❌ 语言/结构验收未通过：", file=sys.stderr)
        for x in problems:
            print(f"  - {x}", file=sys.stderr)
        return 1

    print(f"✅ 可读区干净，结构完整 — {p}")
    return 0


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass
    raise SystemExit(main())
