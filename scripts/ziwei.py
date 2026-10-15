#!/usr/bin/env python3
"""
ziwei-doushu / scripts/ziwei.py — 统一命令行入口（跨平台：Windows / macOS / Linux）

原则：**一切可计算的东西交给脚本**，AI 只做判断、取舍、综合与表述。

用法示例
--------
  python ziwei.py deps                                  # 给两条引擎路都铺好依赖
  python ziwei.py engines                               # 探测可用引擎（auto 会选哪条）
  python ziwei.py engines --compare 200                 # 两条引擎随机盘逐字段比对
  python ziwei.py chart  --date 1990-06-07 --tx 4 --gender 男 --year 2026
  python ziwei.py chart  --date 1990-06-07 --tx 6 --gender 男 --engine python
  python ziwei.py tree   --date 1990-06-07 --tx 4 --gender 男 --year 2026
  python ziwei.py md     --date 1990-06-07 --tx 4 --gender 男
  python ziwei.py pillar --date 1990-06-07 --tx 4
  python ziwei.py deps --json

输出约定
--------
  chart  默认输出 JSON（给 AI 消费）；加 --pretty 输出缩进 JSON
  tree   输出文墨天机风格树状盘面
  md     输出 Markdown 表格（贴报告用）
  任何失败都输出 {"ok": false, "error": CODE, "message": "..."} 并以非 0 退出

引擎选择
--------
  全局参数 --engine auto|python|node 各子命令都接受。
  auto（默认）：先 python（x-iztro，同进程、不依赖 Node），不可用再 node（iztro）。
  两条都不装时排盘报 NO_ENGINE，并给出安装指引。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import random
import sys
from pathlib import Path

# 允许直接 `python scripts/ziwei.py` 运行（无需安装为包）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ziwei_doushu import astro, chart as chartmod, engines, render  # noqa: E402


def _emit(obj, as_json: bool = True, pretty: bool = False) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2 if pretty else None))
    else:
        print(obj)


def _fail(err: Exception) -> int:
    if isinstance(err, chartmod.ZiweiError):
        print(json.dumps(err.as_dict(), ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "error": type(err).__name__, "message": str(err)},
                         ensure_ascii=False))
    return 1


def _years(s: str | None) -> list[int]:
    if not s:
        return []
    out = []
    for part in str(s).replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except ValueError:
            raise chartmod.ZiweiError("BAD_YEAR", f"年份无法识别：{part!r}") from None
    return out


# ---------------- 子命令 ----------------
def cmd_deps(args) -> int:
    """
    按需给两条引擎路都铺好：先 pip install x-iztro，失败再 npm install iztro。
    最后打印两条路各自的可用状态。两条都不可用才算失败。
    """
    try:
        info = chartmod.ensure_deps_all(force=args.force, quiet=args.quiet)
    except Exception as e:  # noqa: BLE001
        return _fail(e)
    if not args.quiet:
        for e in info["engines"]:
            mark = "可用  " if e["available"] else "不可用"
            ver = e["version"] or "-"
            print(f"[ziwei] {mark} {e['kind']:6s} {e['name']:8s} {ver:10s} {e['detail']}",
                  file=sys.stderr)
        print(f"[ziwei] auto 将使用：{info['auto'] or '（无可用引擎）'}", file=sys.stderr)
    _emit(info if not info["ok"] else {"ok": True, **info}, as_json=True, pretty=True)
    return 0 if info["ok"] else 1


def cmd_engines(args) -> int:
    """
    探测可用引擎并列状态；`--compare N` 时用两条引擎各排 N 张随机盘逐字段比对。
    """
    status = engines.detect()
    avail = [e for e in status if e["available"]]
    out = {
        "ok": bool(avail),
        "engines": status,
        "auto": engines.active_name("auto") or None,
        "priority": "auto：先 python（x-iztro，同进程、不依赖 Node）→ 再 node（iztro）",
        "message": None if avail else engines.INSTALL_HINT,
    }

    if args.compare:
        if len(avail) < 2:
            out["ok"] = False
            out["compare"] = {
                "requested": args.compare, "compared": 0, "mismatches": 0,
                "error": "COMPARE_NEEDS_TWO_ENGINES",
                "message": "比对需要两条引擎都可用；当前可用：" +
                           (", ".join(e["kind"] for e in avail) or "无"),
            }
            _emit(out, as_json=True, pretty=True)
            return 1
        out["compare"] = _compare(args.compare, seed=args.seed)

    _emit(out, as_json=True, pretty=True)
    return 0 if out["ok"] else 1


# ---------------- 双引擎比对 ----------------
_TX_NOTE = {
    0: "早子", 12: "晚子",
}


def _diff(a, b, path: str = "", limit: int = 0) -> list[tuple[str, object, object]]:
    """递归逐字段比对；limit>0 时只收集前 limit 条差异（探针用）。"""
    out: list[tuple[str, object, object]] = []
    stack = [(path, a, b)]
    while stack:
        p, x, y = stack.pop()
        if limit and len(out) >= limit:
            break
        if isinstance(x, dict) and isinstance(y, dict):
            for k in sorted(set(x) | set(y)):
                if k not in x:
                    out.append((f"{p}.{k}", "<缺>", y[k]))
                elif k not in y:
                    out.append((f"{p}.{k}", x[k], "<缺>"))
                else:
                    stack.append((f"{p}.{k}", x[k], y[k]))
        elif isinstance(x, list) and isinstance(y, list):
            if len(x) != len(y):
                out.append((f"{p}.长度", len(x), len(y)))
            for i, (xi, yi) in enumerate(zip(x, y)):
                stack.append((f"{p}[{i}]", xi, yi))
        elif isinstance(x, bool) != isinstance(y, bool):
            out.append((p, x, y))
        elif x != y:
            out.append((p, x, y))
    out.sort(key=lambda t: t[0])
    return out


def _norm_for_compare(res: dict) -> dict:
    """
    比对前的归一化：只去掉引擎自身元信息（引擎名/版本/notes），
    `astrolabe` 内部一律原样——**不做任何字段豁免**，缺字段就是要报的差异。
    """
    return {"astrolabe": res.get("astrolabe")}


def _random_req(rng: random.Random, want: dict) -> dict:
    """
    随机出生盘。优先满足 want（五行局 / 晚子时 / 闰月标记），拿不到就退回随机盘。
    kind: 'leap'（农历闰月）| 'latezi'（晚子时）| 'normal'
    """
    if want.get("kind") == "leap":
        # 农历闰月：给一个农历日期并让引擎按 isLeapMonth 解析（本工具口径 false + fixLeap 修正）
        y = rng.randint(1950, 2010)
        m = rng.choice([2, 3, 4, 5, 6, 7, 8])
        d = rng.randint(1, 28)
        return {"date": f"{y}-{m:02d}-{d:02d}", "calendar": "lunar",
                "timeIndex": rng.randint(0, 12), "gender": rng.choice(["男", "女"]),
                "years": [rng.randint(2024, 2030)], "fixLeap": True}
    if want.get("kind") == "latezi":
        ti = 12
    else:
        ti = rng.randint(0, 12)
    y = rng.randint(1930, 2010)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    return {"date": f"{y}-{m:02d}-{d:02d}", "calendar": "solar",
            "timeIndex": ti, "gender": rng.choice(["男", "女"]),
            "years": [rng.randint(2024, 2030)],
            "fixLeap": rng.choice([True, True, False])}


FIVE_CLASSES = ("水二局", "木三局", "金四局", "土五局", "火六局")


def _find_class_requests(rng: random.Random, want_classes: list[str],
                         need_early: bool, need_late: bool,
                         tries: int = 400) -> list[dict]:
    """
    定向找盘：抽候选盘（只跑 python 路，便宜）直到六种五行局与早晚子时都覆盖到。
    找不齐就返回找到的那些——覆盖情况会在输出里如实报告，不假装。
    """
    found: list[dict] = []
    need = set(want_classes)
    if not need and not need_early and not need_late:
        return found
    for _ in range(tries):
        if not need and not (need_early or need_late):
            break
        if need_early:
            req = {"date": f"{rng.randint(1930, 2010)}-{rng.randint(1, 12):02d}-"
                          f"{rng.randint(1, 28):02d}", "calendar": "solar",
                   "timeIndex": 0, "gender": rng.choice(["男", "女"]),
                   "years": [], "fixLeap": True}
        elif need_late:
            req = {"date": f"{rng.randint(1930, 2010)}-{rng.randint(1, 12):02d}-"
                          f"{rng.randint(1, 28):02d}", "calendar": "solar",
                   "timeIndex": 12, "gender": rng.choice(["男", "女"]),
                   "years": [], "fixLeap": True}
        else:
            req = _random_req(rng, {"kind": "normal"})
        res = engines.run(req, engine="python")
        if not res.get("ok"):
            continue
        five = res["astrolabe"].get("fiveElementsClass")
        keep = False
        if need_early and req["timeIndex"] == 0:
            keep, need_early = True, False
        if need_late and req["timeIndex"] == 12:
            keep, need_late = True, False
        if five in need:
            keep = True
            need.discard(five)
        if keep:
            found.append(req)
    return found


def _compare(n: int, seed: int | None = None) -> dict:
    """
    用两条引擎各排 N 张随机盘并逐字段比对。

    覆盖策略：随机公历盘为主，另外**定向补足**——
      · 六种五行局：抽到未覆盖的局就记下，最后按「每种局至少一张」报告覆盖
      · 早晚子时：显式补 timeIndex=0（早子）与 12（晚子）
      · 闰月：显式补若干农历输入盘（fixLeap=True）
    输出：不一致条数与首个差异样例（按字段路径归并计数）。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    mismatches: list[dict] = []
    by_path: dict[str, int] = {}
    first_sample: dict | None = None
    covered_five: dict[str, int] = {}
    covered_tx: set[int] = set()
    calendars = {"solar": 0, "lunar": 0}
    ok = 0

    # 定向补充：晚子、早子、农历盘
    plan: list[dict] = ([{"kind": "latezi"}] * max(1, n // 20)
                        + [{"kind": "leap"}] * max(1, n // 20)
                        + [{"kind": "normal"}] * max(1, n // 20))
    while len(plan) < n:
        plan.append({"kind": "normal"})
    rng.shuffle(plan)
    plan = plan[:n]

    for i, want in enumerate(plan):
        req = _random_req(rng, want)
        py = engines.run(req, engine="python")
        nd = engines.run(req, engine="node")
        if not (py.get("ok") and nd.get("ok")):
            mismatches.append({
                "index": i, "request": req,
                "error": "ENGINE_FAILED",
                "python": py.get("error") or py.get("message"),
                "node": nd.get("error") or nd.get("message"),
            })
            by_path["<engine_failed>"] = by_path.get("<engine_failed>", 0) + 1
            continue

        a = _norm_for_compare(nd)["astrolabe"]
        b = _norm_for_compare(py)["astrolabe"]
        # 归一化：python 路没有 jieqiPillar（node 侧由 lunar-lite 提供），
        # 这是**已声明的已知差异**，从比对基准里去掉并单列在 knownDifferences。
        b = {k: v for k, v in b.items() if k != "jieqiPillar"}
        a = {k: v for k, v in a.items() if k != "jieqiPillar"}

        cal = req["calendar"]
        calendars[cal] = calendars.get(cal, 0) + 1
        covered_tx.add(req["timeIndex"])
        five = a.get("fiveElementsClass")
        if five:
            covered_five[five] = covered_five.get(five, 0) + 1

        d = _diff(a, b)
        if d:
            for p, x, y in d:
                by_path[p] = by_path.get(p, 0) + 1
            mismatches.append({
                "index": i, "request": req,
                "diffCount": len(d),
                "diffs": [{"path": p, "node": x, "python": y} for p, x, y in d[:12]],
            })
            if first_sample is None:
                first_sample = mismatches[-1]
        else:
            ok += 1

    # 覆盖补足：把没抽到的五行局 / 早晚子时定向补齐（补的盘同样参与比对）
    missing = [c for c in FIVE_CLASSES if c not in covered_five]
    extra = _find_class_requests(
        rng, missing,
        need_early=0 not in covered_tx and len(plan) >= 5,
        need_late=12 not in covered_tx and len(plan) >= 5)
    for req in extra:
        py = engines.run(req, engine="python")
        nd = engines.run(req, engine="node")
        plan.append(req)
        if not (py.get("ok") and nd.get("ok")):
            mismatches.append({"index": len(plan) - 1, "request": req,
                               "error": "ENGINE_FAILED",
                               "python": py.get("error") or py.get("message"),
                               "node": nd.get("error") or nd.get("message")})
            by_path["<engine_failed>"] = by_path.get("<engine_failed>", 0) + 1
            continue
        a = _norm_for_compare(nd)["astrolabe"]
        b = _norm_for_compare(py)["astrolabe"]
        b = {k: v for k, v in b.items() if k != "jieqiPillar"}
        a = {k: v for k, v in a.items() if k != "jieqiPillar"}
        calendars[req["calendar"]] = calendars.get(req["calendar"], 0) + 1
        covered_tx.add(req["timeIndex"])
        five = a.get("fiveElementsClass")
        if five:
            covered_five[five] = covered_five.get(five, 0) + 1
        d = _diff(a, b)
        if d:
            for p, x, y in d:
                by_path[p] = by_path.get(p, 0) + 1
            mismatches.append({"index": len(plan) - 1, "request": req, "diffCount": len(d),
                               "diffs": [{"path": p, "node": x, "python": y} for p, x, y in d[:12]]})
            if first_sample is None:
                first_sample = mismatches[-1]
        else:
            ok += 1

    return {
        "requested": n,
        "compared": len(plan),
        "identical": ok,
        "mismatches": len(mismatches),
        "byPath": dict(sorted(by_path.items(), key=lambda kv: -kv[1])),
        "coverage": {
            "calendar": calendars,
            "timeIndex": {str(t): _TX_NOTE.get(t, "") for t in sorted(covered_tx)},
            "fiveElementsClass": covered_five,
            "earlyLateZi": {"早子(0)": 0 in covered_tx, "晚子(12)": 12 in covered_tx},
        },
        "knownDifferences": [
            "jieqiPillar：node 路由 lunar-lite 提供立春口径四柱；python 路（x-iztro）没有该字段，"
            "如实置 null 并记 notes。该字段仅供八字口径对照，紫微斗数取四化不用它，故不影响 schema。"
        ],
        "firstMismatch": first_sample,
        "mismatchDetails": mismatches[:20],
    }


def cmd_chart(args) -> int:
    try:
        c = chartmod.build_chart(
            date_str=args.date, gender=args.gender,
            time=args.time, time_index=args.tx, longitude=args.lng,
            calendar="lunar" if args.lunar else "solar",
            years=_years(args.year),
            fix_leap=not args.no_fix_leap,
            engine=getattr(args, "engine", "auto"),
        )
    except Exception as e:  # noqa: BLE001
        return _fail(e)

    if args.tree:
        print(render.render_tree(c))
        return 0
    if args.md:
        print(render.render_markdown(c))
        return 0
    _emit(c, as_json=True, pretty=args.pretty)
    return 0


def cmd_tree(args) -> int:
    args.tree = True
    return cmd_chart(args)


def cmd_md(args) -> int:
    args.md = True
    return cmd_chart(args)


def cmd_pillar(args) -> int:
    """
    四柱口径对照：农历年口径（紫微斗数用）vs 立春口径（八字用）。
    两者年干可能不同，而生年干决定全部四化——必须让用户知道用的是哪套。

    农历年口径来自所选排盘引擎（auto/python/node 都提供）。
    立春口径四柱（`jieqiPillar`）目前**只有 node 路**能提供（iztro 的历法依赖 lunar-lite）：
      · 指定的引擎给的 jieqiPillar 为空时，自动回退到 node 路取该字段，并在 note 里说明；
      · node 也不可用时明确告知用户，而不是装作拿到了。
    """
    engine = getattr(args, "engine", "auto")
    try:
        birth = chartmod.parse_date(args.date)
        tst = None
        tx = args.tx
        if tx is None:
            if not args.time:
                raise chartmod.ZiweiError("MISSING_TIME", "需要 --time 或 --tx")
            hh, mm = chartmod.parse_time(args.time)
            tst = astro.apparent_solar_time(birth, hh, mm, args.lng)
            tx = tst["time_index"]
        eng = chartmod.run_engine(birth.isoformat(), tx, args.gender,
                                  calendar="lunar" if args.lunar else "solar",
                                  engine=engine)
        non_jieqi = eng["astrolabe"]["chineseDate"]
        jq = eng["astrolabe"].get("jieqiPillar")
    except Exception as e:  # noqa: BLE001
        return _fail(e)

    notes: list[str] = list(eng.get("notes") or [])
    jieqi_engine = eng.get("engine")
    if not jq and engine != "node":
        # 回退到 node 路单独取 jieqiPillar（python 路没有这个字段，不伪造）
        try:
            nd = chartmod.run_engine(birth.isoformat(), tx, args.gender,
                                     calendar="lunar" if args.lunar else "solar",
                                     engine="node")
            jq = nd["astrolabe"].get("jieqiPillar")
            if jq:
                jieqi_engine = nd.get("engine")
                notes.append(f"jieqiPillar 由 node 引擎（{nd.get('engine')} "
                             f"{nd.get('version')}）提供；本盘其余数据来自 {eng.get('engine')}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"node 引擎不可用，无法取立春口径四柱：{getattr(e, 'message', e)}")
            jieqi_engine = None

    if not jq:
        return _fail(chartmod.ZiweiError(
            "JIEQI_UNAVAILABLE",
            "无法取得节气四柱（立春口径）——该字段由 node 引擎的 lunar-lite 提供。"
            "请安装 Node.js 18+ 并运行：python scripts/ziwei.py deps；"
            "或指定 --engine node。农历年口径年柱见下方 nonJieqiPillar。",
        ))

    jieqi = jq["text"]
    zs, bs = non_jieqi.split()[0][0], jieqi.split()[0][0]
    z_mut = [f"{s}化{t}" for s, t in zip(astro.MUTAGEN_TABLE[zs], astro.MUTAGEN_NAMES)]
    b_mut = [f"{s}化{t}" for s, t in zip(astro.MUTAGEN_TABLE[bs], astro.MUTAGEN_NAMES)]
    conflict = zs != bs

    result = {
        "ok": True,
        "engine": eng.get("engine"),
        "engineVersion": eng.get("version"),
        "jieqiPillarEngine": jieqi_engine,
        "lunarDate": eng["astrolabe"]["lunarDate"],
        "solarTime": tst,
        "nonJieqiPillar": non_jieqi,
        "jieqiPillar": jieqi,
        "jieqiParts": {k: v for k, v in jq.items() if k != "text"},
        "ziweiYearStem": zs,
        "baziYearStem": bs,
        "yearStemConflict": conflict,
        "ziweiMutagen": z_mut,
        "baziMutagen": b_mut,
        "warning": (
            f"年干口径冲突：紫微斗数取【{zs}】（农历年），八字立春口径取【{bs}】。"
            "两套四化完全不同，务必声明所用体系，不可混用。"
            if conflict else "两种口径年干一致，无冲突。"
        ),
        "note": "农历年口径由所选排盘引擎给出；立春口径四柱由 node 引擎的 lunar-lite 计算，非本地近似。",
        "engineNotes": notes,
    }
    _emit(result, as_json=True, pretty=True)
    return 0


# ---------------- 参数 ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ziwei.py",
        description="紫微斗数排盘 / 大限流年 —— 确定性计算（Python 编排 + x-iztro/iztro 多引擎）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python ziwei.py deps\n"
               "  python ziwei.py engines --compare 200\n"
               "  python ziwei.py tree --date 1990-06-07 --tx 4 --gender 男 --year 2026\n"
               "  python ziwei.py chart --date 1990-06-07 --tx 4 --gender 男 --engine node\n",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_engine(sp):
        sp.add_argument("--engine", default="auto", choices=["auto", "python", "node"],
                        help="排盘引擎：auto（默认，先 python 再 node）/ python（x-iztro）/ node（iztro）")

    def common(sp):
        sp.add_argument("--date", required=True, help="出生日期，公历；格式 1990-06-07 / 1990/6/7 / 1990年6月7日")
        sp.add_argument("--time", help="出生钟表时间 HH:mm")
        sp.add_argument("--tx", type=int, choices=range(0, 13),
                        help="时辰序号 0-12（0=早子 1=丑 … 11=亥 12=晚子）；给了就不再校正")
        sp.add_argument("--gender", required=True, choices=["男", "女"])
        sp.add_argument("--lng", type=float, help="出生地经度（东经为正），如武汉 114.31")
        sp.add_argument("--lunar", action="store_true", help="--date 视为农历")
        sp.add_argument("--year", help="要看的流年公历年份，逗号分隔，如 2026,2027")
        sp.add_argument("--no-fix-leap", action="store_true", help="不修正闰月")
        add_engine(sp)

    d = sub.add_parser("deps", help="给两条引擎路都铺好依赖（pip x-iztro / npm iztro，不装全局）")
    d.add_argument("--force", action="store_true")
    d.add_argument("--quiet", action="store_true")
    d.add_argument("--json", action="store_true", help="(默认即 JSON 输出)")
    d.set_defaults(func=cmd_deps)

    eg = sub.add_parser("engines", help="探测可用引擎；--compare N 用两条引擎各排 N 张随机盘逐字段比对")
    eg.add_argument("--compare", type=int, default=0, metavar="N",
                    help="随机盘比对张数，如 200（需两条引擎都可用）")
    eg.add_argument("--seed", type=int, default=None, help="随机种子（复现同一批比对盘）")
    eg.add_argument("--json", action="store_true", help="(默认即 JSON)")
    eg.set_defaults(func=cmd_engines)

    c = sub.add_parser("chart", help="输出完整 JSON（含盘面、四化、自化、大限、流年）")
    common(c)
    c.add_argument("--json", action="store_true", help="(默认即 JSON)")
    c.add_argument("--pretty", action="store_true", help="缩进 JSON")
    c.add_argument("--tree", action="store_true", help="改为输出树状盘面")
    c.add_argument("--md", action="store_true", help="改为输出 Markdown 表格")
    c.set_defaults(func=cmd_chart)

    t = sub.add_parser("tree", help="输出文墨天机风格树状盘面")
    common(t)
    t.add_argument("--json", action="store_true")
    t.add_argument("--pretty", action="store_true")
    t.add_argument("--md", action="store_true")
    t.set_defaults(func=cmd_tree, tree=True)

    m = sub.add_parser("md", help="输出 Markdown 十二宫表格")
    common(m)
    m.add_argument("--json", action="store_true")
    m.add_argument("--pretty", action="store_true")
    m.add_argument("--tree", action="store_true")
    m.set_defaults(func=cmd_md, md=True)

    pl = sub.add_parser("pillar", help="四柱口径对照（农历年 vs 立春，查年干是否冲突）")
    pl.add_argument("--date", required=True)
    pl.add_argument("--time")
    pl.add_argument("--tx", type=int, choices=range(0, 13))
    pl.add_argument("--gender", default="男", choices=["男", "女"])
    pl.add_argument("--lng", type=float)
    pl.add_argument("--lunar", action="store_true")
    add_engine(pl)
    pl.set_defaults(func=cmd_pillar)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_cli().parse_args(argv)

    # Windows 控制台默认 GBK，会因中文/箭头字符崩掉；统一切到 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass

    try:
        return args.func(args)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # noqa: BLE001
        return _fail(e)


class _JsonArgumentParser(argparse.ArgumentParser):
    """
    让 argparse 的用法错误也走 JSON 契约（默认会打印纯文本并以 2 退出，
    AI 消费时无法统一解析）。
    """

    def error(self, message: str):  # type: ignore[override]
        print(json.dumps({
            "ok": False, "error": "BAD_ARGS", "message": message,
            "hint": "用法见 python ziwei.py <chart|tree|md|pillar|deps|engines> --help",
        }, ensure_ascii=False))
        raise SystemExit(2)

    def exit(self, status: int = 0, message: str | None = None):  # type: ignore[override]
        if message:
            sys.stderr.write(message)
        raise SystemExit(status)


# 让主解析器与所有子解析器都用 JSON 错误输出
_ORIG = build_parser


def build_cli() -> argparse.ArgumentParser:
    p = _ORIG()
    p.__class__ = _JsonArgumentParser
    for action in p._actions:  # noqa: SLF001
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001
            for sp in action.choices.values():
                sp.__class__ = _JsonArgumentParser
    return p


if __name__ == "__main__":
    raise SystemExit(main())

