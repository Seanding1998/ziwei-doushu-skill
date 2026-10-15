"""
ziwei_doushu/render.py — 排版层（确定性）

排版归脚本，文字归 AI。这样每次输出的盘面格式完全一致，AI 不必花 token 排表格。
"""

from __future__ import annotations

from typing import Any

from . import astro

BR12 = astro.BR12


def _starmark(chart: dict[str, Any], branch: str, star: dict[str, Any]) -> str:
    """星曜 + 亮度 + 生年四化 + 自化标记。"""
    out = star.get("name", "")
    if star.get("brightness"):
        out += f"[{star['brightness']}]"
    if star.get("mutagen"):
        out += f"[生年{star['mutagen']}]"
    for m in chart.get("selfMutagen") or []:
        if m["branch"] == branch and m["star"] == star.get("name"):
            out += f"[{m['symbol']}{m['type']}]"
    return out


def _stars(chart: dict[str, Any], branch: str, key: str) -> str:
    lst = []
    for p in chart["palaces"]:
        if p["branch"] == branch:
            for s in p.get(key) or []:
                if isinstance(s, dict):
                    lst.append(_starmark(chart, branch, s))
                else:
                    lst.append(str(s))
    return ",".join(lst) or "无"


def render_tree(chart: dict[str, Any]) -> str:
    """文墨天机风格的树状盘面。"""
    L: list[str] = []

    def t(s: str = "") -> None:
        L.append(s)

    b, m = chart["basic"], chart["meta"]
    st = chart["solarTime"]

    t("紫微斗数命盘")
    t("│")
    t(f"├引擎 : {m['engine']} {m['engineVersion']}")
    t("├安星口径")
    for k, v in m["conventions"].items():
        t(f"│ ├{k} : {v}")
    t("│")
    t("├基本信息")
    t(f"│ ├性别 : {chart['input']['gender']}")
    lng = chart["input"].get("longitude")
    t(f"│ ├地理经度 : {lng:.3f}" if isinstance(lng, (int, float)) else "│ ├地理经度 : 未提供")
    t(f"│ ├钟表时间 : {chart['input']['date']} {st.get('clock') or ''}")
    if st.get("corrected"):
        t(f"│ ├真太阳时 : {st['apparent']}（校正 {st['delta_minutes']:+.2f} 分"
          f"＝经度项 {st['longitude_term']:+.2f} + 均时差 {st['equation_of_time']:+.2f}）")
    else:
        t("│ ├真太阳时 : 未校正")
    t(f"│ ├农历时间 : {b['lunarDate']} {chart['input']['timeName']}时")
    t(f"│ ├四柱(农历年口径) : {b['chineseDate']}")
    t(f"│ ├五行局 : {b['fiveElementsClass']}")
    t(f"│ └命主:{b['soul']}; 身主:{b['body']}; 身宫:{b['bodyPalaceBranch']}"
      + ("(=命宫，命身同宫)" if b["sameSoulBody"] else "")
      + f"; 来因宫:{b['laiYinPalace']['branch'] + '宫' + b['laiYinPalace']['palace'] if b['laiYinPalace'] else '无'}")
    for w in m.get("warnings") or []:
        t(f"│ ⚠ {w}")

    t("│")
    t("├命盘十二宫")
    ps = sorted(chart["palaces"], key=lambda p: BR12.index(p["branch"]))
    for i, p in enumerate(ps):
        last = i == len(ps) - 1
        tags = []
        if p.get("isBodyPalace"):
            tags.append("身宫")
        if p.get("isLaiYin"):
            tags.append("来因")
        if p.get("isSoulPalace"):
            tags.append("命宫")
        t(f"│ {'└' if last else '├'}{p['name']}[{p['stem']}{p['branch']}]"
          + (f"[{']['.join(tags)}]" if tags else ""))
        c = "  " if last else "│ "
        t(f"│ {c}├主星 : {_stars(chart, p['branch'], 'major')}")
        t(f"│ {c}├辅星 : {_stars(chart, p['branch'], 'minor')}")
        t(f"│ {c}├小星 : {','.join(p.get('adjective') or []) or '无'}")
        sp = p.get("spirits") or {}
        t(f"│ {c}├神煞 : 岁前{sp.get('suiqian')} 将前{sp.get('jiangqian')} "
          f"长生{sp.get('changsheng')} 博士{sp.get('boshi')}")
        dr = p.get("decadalRange")
        t(f"│ {c}├大限 : {dr[0]}~{dr[1]}虚岁" if dr else f"│ {c}├大限 : -")
        t(f"│ {c}├小限 : {','.join(str(x) for x in (p.get('soulAges') or [])[:5])}虚岁")
        t(f"│ {c}└流年 : {','.join(str(x) for x in (p.get('yearAges') or [])[:5])}虚岁")

    obs = chart["observations"]
    t("│")
    t(f"├十四主星 : {obs['fourteenComplete']}/14 齐备")
    t(f"├六吉 : {' '.join(obs['sixSoft']) or '无'}")
    t(f"├六煞 : {' '.join(obs['sixTough']) or '无'}")
    t(f"└空宫 : {' '.join(e['branch'] + '·' + e['palace'] for e in obs['emptyPalaces']) or '无'}")

    t("│")
    t("├四化")
    t("│ ├生年 : " + " ".join(f"{x['star']}化{x['type']}({x['branch']}·{x['palace']})"
                              for x in chart["birthMutagen"]))
    cm = [x for x in chart["selfMutagen"] if x["direction"] == "离心"]
    tm = [x for x in chart["selfMutagen"] if x["direction"] == "向心"]
    t("│ ├离心自化(↓) : " + (" ".join(f"{x['palace']}({x['from_stem']}){x['star']}自化{x['type']}"
                                      for x in cm) or "无"))
    t("│ └向心自化(↑) : " + (" ".join(f"{x['palace']}←{x['from_branch']}({x['from_stem']})"
                                      f"{x['star']}{x['type']}" for x in tm) or "无"))

    t("│")
    t("├命宫三方四正")
    for label, key in (("本宫", "self"), ("对宫", "opposite"), ("三合", "trine1"), ("三合", "trine2")):
        q = chart["surround"].get(key)
        if not q:
            continue
        stars = ",".join(
            (s["name"] + (f"[{s['brightness']}]" if s.get("brightness") else "")
             + (f"[生年{s['mutagen']}]" if s.get("mutagen") else ""))
            for s in (q.get("major") or []) + (q.get("minor") or [])
        ) or "（空宫）"
        t(f"│ ├{label} {q['branch']}宫 {q['name']} : {stars}")
    fl, fr = chart["flank"].get("left"), chart["flank"].get("right")
    lk = "，".join(f"{x['star']}({x['kind']})" for x in chart["flankKinds"]["left"]) or "中性"
    rk = "，".join(f"{x['star']}({x['kind']})" for x in chart["flankKinds"]["right"]) or "中性"
    t(f"│ └夹宫 左邻{fl['branch']}宫{fl['name']}＝{lk} ；右邻{fr['branch']}宫{fr['name']}＝{rk}")

    # 宫位关系全表（第四轮新增）：任意一宫的对宫/三合/两邻都由脚本给，
    # AI 正文只能引用，不得自己推。第三轮盲审错在「对宫官禄」（实为三合）。
    rel = chart.get("palaceRelations") or {}
    if rel:
        t("│")
        t("├宫位关系（对宫／三合／两邻）")
        ps2 = sorted(chart["palaces"], key=lambda p: BR12.index(p["branch"]))
        for i2, p in enumerate(ps2):
            r = rel.get(p["branch"])
            if not r:
                continue
            last2 = i2 == len(ps2) - 1
            opp = r.get("opposite") or {}
            tri = "、".join(f"{x['branch']}·{x['palace']}" for x in (r.get("trine") or []))
            nb = (f"{r['left']['branch']}·{r['left']['palace']}、"
                  f"{r['right']['branch']}·{r['right']['palace']}")
            t(f"│ {'└' if last2 else '├'}{p['branch']}·{p['name']}"
              f" 对宫={opp.get('branch')}·{opp.get('palace')} 三合={tri} 两邻={nb}")

    # 六吉六煞配对（第五轮新增）：成不成对、同不同宫、对不对拱，全部由脚本给。
    # 第五轮盲审 BLOCKER 就是这处算术被手写时算反了（三对俱成对拱写成「两两不成对宫」）。
    pf = chart.get("pairingFacts") or {}
    if pf.get("pairs"):
        t("│")
        t("├六吉六煞配对（成对＝同宫或互为对宫；三合遥会不算成对）")
        items = pf["pairs"]
        for i4, x in enumerate(items):
            last4 = i4 == len(items) - 1
            ins = "、".join(x.get("inSurround") or []) or "无"
            t(f"│ {'└' if last4 else '├'}{x['group']}·{x['abbr']} "
              f"{x['star1']}@{x.get('branch1')}·{x.get('palace1')} ／ "
              f"{x['star2']}@{x.get('branch2')}·{x.get('palace2')} → {x.get('relation')}"
              f"（落命宫三方四正内：{ins}）")
        for key, label in (("soft", "六吉"), ("tough", "六煞")):
            g = pf.get(key) or {}
            t(f"│ ├{label}小结：三对之中同宫或对拱者 {g.get('pairedCount')}/{g.get('totalPairs')}"
              f"（同宫 {g.get('samePalaceCount')}／对拱 {g.get('oppositeCount')}）；"
              f"落命宫三方四正内者：{'、'.join(g.get('inSurround') or []) or '无'}")

    t("│")
    t("├大限")
    for d in chart["decadal"]:
        mg = d.get("mutagen") or ["?", "?", "?", "?"]
        t(f"│ ├{str(d['ageRange'][0]) + '~' + str(d['ageRange'][1]):>7}虚岁 "
          f"公历{d['yearRange'][0]}~{d['yearRange'][1]} {d['ganzhi']} 落{d['palace']} "
          f"四化 禄{mg[0]} 权{mg[1]} 科{mg[2]} 忌{mg[3]}")
        land = d.get("mutagenLanding") or []
        if land:
            t("│ │ 四化落点(本命宫/本限宫) : " + " ".join(
                f"{x['type']}{x['star']}→{x['branch']}·本命{x['natalPalace']}"
                f"/本限{x['decadalPalace']}" for x in land))

    # 大限十二宫映射（第四轮新增）：限内宫名一律由脚本给，禁止手算
    dpm = chart.get("decadalPalaceMaps") or []
    if dpm:
        focus = dpm[0]
        hs0 = chart.get("horoscope") or []
        if hs0:
            gz = (hs0[0].get("decadal") or {}).get("ganzhi")
            focus = next((x for x in dpm if x.get("ganzhi") == gz), focus)
        t("│")
        t(f"├大限十二宫映射（示例：{focus['ageRange'][0]}~{focus['ageRange'][1]}虚岁 "
          f"{focus['ganzhi']}，大限命宫落本命{focus['natalPalaceOfSoul']}宫）")
        items = list(focus["palaceMap"].items())
        for i3, (nm, br) in enumerate(items):
            t(f"│ {'└' if i3 == len(items) - 1 else '├'}大限{nm}宫 = {br}宫"
              f"（本命{next((p['name'] for p in chart['palaces'] if p['branch'] == br), '?')}）")

    if chart["horoscope"]:
        t("│")
        t("├流年")
        for h in chart["horoscope"]:
            dm, ym = h["decadal"]["mutagen"], h["yearly"]["mutagen"]
            t(f"│ ├{h['year']} 虚岁{h['nominalAge']} "
              f"大限{h['decadal']['ganzhi']}(禄{dm[0]}/权{dm[1]}/科{dm[2]}/忌{dm[3]}) "
              f"流年{h['yearly']['ganzhi']}(禄{ym[0]}/权{ym[1]}/科{ym[2]}/忌{ym[3]})")
            lp = h["yearlySoulPalaceLandsOn"]
            t(f"│ │ 流年命宫落本命 {lp['branch']}宫 {lp['palace']} {' '.join(lp['major'])}")
            sl = h.get("soulLimit")
            t(f"│ │ 小限 {sl['ganzhi'] if sl else '-'} 落{h.get('soulLimitPalace') or '-'}")
            t("│ │ 流耀 : " + " ".join(
                f"{s['palace']}:{s['name']}" + (f"化{s['mutagen']}" if s.get("mutagen") else "")
                for s in h["yearly"]["stars"]))

    t("│")
    t(f"└[机器自检: {'通过' if chart['selfCheck']['passed'] else '有问题'}]")
    return "\n".join(L)


def render_markdown(chart: dict[str, Any]) -> str:
    """十二宫表格（便于贴进报告）。"""
    b, st = chart["basic"], chart["solarTime"]
    out = []
    out.append(f"- 公历：{b['solarDate']}　农历：{b['lunarDate']}　{chart['input']['timeName']}时")
    if st.get("corrected"):
        out.append(f"- 真太阳时：{st['apparent']}（校正 {st['delta_minutes']:+.1f} 分）")
    out.append(f"- 四柱（农历年口径）：{b['chineseDate']}")
    out.append(f"- 五行局：{b['fiveElementsClass']}　命宫：{b['soulPalaceBranch']}　"
               f"身宫：{b['bodyPalaceBranch']}{'（命身同宫）' if b['sameSoulBody'] else ''}")
    out.append(f"- 命主：{b['soul']}　身主：{b['body']}　"
               f"来因宫：{b['laiYinPalace']['branch'] + '宫' + b['laiYinPalace']['palace'] if b['laiYinPalace'] else '无'}")
    out.append("")
    out.append("| 宫位 | 宫干 | 主星 | 六吉六煞 | 大限 |")
    out.append("|---|---|---|---|---|")
    for p in sorted(chart["palaces"], key=lambda x: BR12.index(x["branch"])):
        tag = " ★身宫" if p.get("isBodyPalace") else ""
        major = ",".join(
            s["name"] + (f"[{s['brightness']}]" if s.get("brightness") else "")
            + (f"★化{s['mutagen']}" if s.get("mutagen") else "")
            for s in (p.get("major") or [])
        ) or "（空宫）"
        minor = ",".join(
            s["name"] + (f"[{s['brightness']}]" if s.get("brightness") else "")
            for s in (p.get("minor") or [])
        ) or "—"
        dr = p.get("decadalRange")
        out.append(f"| {p['branch']}·{p['name']}{tag} | {p['stem']} | {major} | {minor} | "
                   f"{str(dr[0]) + '-' + str(dr[1]) if dr else '—'} |")
    out.append("")
    out.append("**生年四化**：" + "、".join(
        f"{x['star']}化{x['type']}（{x['branch']}·{x['palace']}）" for x in chart["birthMutagen"]))
    if chart["selfMutagen"]:
        out.append("**自化**：" + "、".join(
            f"{x['palace']}{x['symbol']}{x['star']}化{x['type']}" for x in chart["selfMutagen"]))

    # 宫位关系表（第四轮新增）：正文凡写「对宫/三合/两邻」都照这张表
    rel = chart.get("palaceRelations") or {}
    if rel:
        out.append("")
        out.append("**宫位关系（对宫／三合／两邻）**")
        out.append("| 本宫 | 对宫 | 三合两宫 | 两邻 |")
        out.append("|---|---|---|---|")
        for p in sorted(chart["palaces"], key=lambda x: BR12.index(x["branch"])):
            r = rel.get(p["branch"])
            if not r:
                continue
            opp = r.get("opposite") or {}
            tri = "、".join(f"{x['branch']}·{x['palace']}" for x in (r.get("trine") or []))
            nb = f"{r['left']['branch']}·{r['left']['palace']}、{r['right']['branch']}·{r['right']['palace']}"
            out.append(f"| {p['branch']}·{p['name']} | {opp.get('branch')}·{opp.get('palace')} "
                       f"| {tri} | {nb} |")

    # 六吉六煞配对表（第五轮新增）：正文凡写「成对／不成对／同宫／对拱」都照这张表
    pf = chart.get("pairingFacts") or {}
    if pf.get("pairs"):
        out.append("")
        out.append("**六吉六煞分布与配对**（成对＝同宫或互为对宫；三合遥会不算成对相拱）")
        out.append("| 类别 | 对星 | 落宫 | 配对关系 | 落命宫三方四正内 |")
        out.append("|---|---|---|---|---|")
        for x in pf["pairs"]:
            out.append(f"| {x['group']}·{x['abbr']} | "
                       f"{x['star1']}@{x.get('branch1')}·{x.get('palace1')}／"
                       f"{x['star2']}@{x.get('branch2')}·{x.get('palace2')} | "
                       f"{x.get('relation')} | "
                       f"{'、'.join(x.get('inSurround') or []) or '—'} |")
        for key, label in (("soft", "六吉"), ("tough", "六煞")):
            g = pf.get(key) or {}
            out.append(f"- {label}小结：三对之中同宫或对拱者 {g.get('pairedCount')}/"
                       f"{g.get('totalPairs')}（同宫 {g.get('samePalaceCount')}／"
                       f"对拱 {g.get('oppositeCount')}）；落命宫三方四正内者："
                       f"{'、'.join(g.get('inSurround') or []) or '无'}")

    # 大限十二宫映射（第四轮新增）
    dpm = chart.get("decadalPalaceMaps") or []
    if dpm:
        names = [nm for nm, _ in dpm[0]["palaceMap"].items()]
        out.append("")
        out.append("**大限十二宫落本命宫位**（限内宫名一律照此表，勿手算）")
        out.append("| 大限 | " + " | ".join(names) + " |")
        out.append("|---" * (len(names) + 1) + "|")
        for x in dpm:
            cells = []
            for nm in names:
                br = x["palaceMap"][nm]
                natal = next((p["name"] for p in chart["palaces"] if p["branch"] == br), "?")
                cells.append(f"{br}·{natal}")
            out.append(f"| {x['ageRange'][0]}~{x['ageRange'][1]} {x['ganzhi']} | "
                       + " | ".join(cells) + " |")
    return "\n".join(out)
