#!/usr/bin/env python3
"""
ziwei-doushu / scripts/ziwei.py — 统一命令行入口（跨平台：Windows / macOS / Linux）

原则：**一切可计算的东西交给脚本**，AI 只做判断、取舍、综合与表述。

用法示例
--------
  python ziwei.py deps                                  # 给两条引擎路都铺好依赖
  python ziwei.py engines                               # 探测可用引擎（auto 会选哪条）
  python ziwei.py engines --compare 200                 # 两条引擎随机盘逐字段比对
  python ziwei.py engines --leap-sweep 1995-2030        # 确定性闰月穷举对拍（见下方「对拍覆盖」）
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

对拍覆盖（`engines --compare`）
------------------------------
  `--compare N` 是**随机抽样**：抽到「阳历生日落在闰月内」的概率约 1%，
  所以 200 张 0 差异**不能**证明 solar 路的 fixLeap 口径对齐。
  要真正堵住这类漏检，用 `--leap-sweep A-B`（不传则不跑，不影响普通 --compare 的耗时）：
  闰月由**历法库查表**给出（lunar-typescript 的 `LunarYear.getLeapMonth` / `Lunar.fromYmd`，
  一次 node 子进程问完整个年段，不按天扫描），对每个闰月覆盖
  第 1 天／中间一天／最后一天 × 时辰{早子0, 午6, 晚子12} × 性别{男,女} × fixLeap{true,false}，
  比对结果与覆盖数写进 `compare.coverage.leapSweep*`（`mismatches` 必须为 0）。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
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


def _parse_year_range(spec: str, flag: str) -> tuple[int, int]:
    """`1995-2030` / `1995..2030` / `1995-` / `-2030` / `2020` → (起, 止)，闭区间。"""
    raw = str(spec or "").strip().replace("..", "-").replace("—", "-").replace("－", "-")
    parts = raw.split("-")
    if len(parts) == 1:
        try:
            y = int(parts[0])
        except ValueError:
            raise chartmod.ZiweiError("BAD_YEAR_RANGE",
                                      f"{flag} 无法识别：{spec!r}（形如 1995-2030）") from None
        return y, y
    if len(parts) != 2:
        raise chartmod.ZiweiError("BAD_YEAR_RANGE",
                                  f"{flag} 无法识别：{spec!r}（形如 1995-2030）")
    today = _dt.date.today().year

    def one(txt: str, fallback: int) -> int:
        txt = txt.strip()
        if not txt:
            return fallback
        try:
            return int(txt)
        except ValueError:
            raise chartmod.ZiweiError("BAD_YEAR_RANGE",
                                      f"{flag} 无法识别：{spec!r}（形如 1995-2030）") from None

    return one(parts[0], 1900), one(parts[1], today)


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
    探测可用引擎并列状态。

    `--compare N`          —— 两条引擎各排 N 张随机盘逐字段比对（随机抽样）
    `--leap-sweep A-B`     —— 额外做一次**确定性闰月穷举**（见 `_leap_sweep`）
    两者可单用也可合用；不看 `--compare` 也能只跑闰月穷举。
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
    sweep_spec = getattr(args, "leap_sweep", None)

    if args.compare or sweep_spec:
        if len(avail) < 2:
            need = ("COMPARE_NEEDS_TWO_ENGINES" if args.compare
                    else "LEAP_SWEEP_NEEDS_TWO_ENGINES")
            err = (f"比对需要两条引擎都可用；当前可用："
                   f"{', '.join(e['kind'] for e in avail) or '无'}")
            if sweep_spec and not args.compare:
                err = f"{_LEAP_SWEEP_NEEDS_NODE} 当前可用：{', '.join(e['kind'] for e in avail) or '无'}"
            out["ok"] = False
            out["compare"] = {
                "requested": args.compare, "compared": 0, "mismatches": 0,
                "error": need, "message": err,
            }
            _emit(out, as_json=True, pretty=True)
            return 1

    if args.compare:
        out["compare"] = _compare(
            args.compare, seed=args.seed,
            leap_sweep=sweep_spec,
            leap_limit=getattr(args, "leap_sweep_limit", _LEAP_COMBO_LIMIT) or 0)
        # 顶层也放一份闰月穷举明细（与「只用 --leap-sweep」时的形状一致，便于脚本取用）
        sweep = out["compare"].pop("leapSweep", None)
        if sweep is not None:
            out["leapSweep"] = sweep
            out["leapSweepMismatches"] = out["compare"].pop("leapSweepMismatches", [])
            if out["compare"].get("leapSweepOk") is False:
                out["ok"] = False
            if sweep.get("error"):
                out["ok"] = False
        elif out["compare"]["mismatches"]:
            out["ok"] = False
    elif sweep_spec:
        sweep = _leap_sweep(sweep_spec,
                            limit=getattr(args, "leap_sweep_limit", _LEAP_COMBO_LIMIT) or 0,
                            seed=args.seed)
        out["ok"] = bool(avail) and sweep["mismatches"] == 0 and not sweep.get("error")
        out["leapSweep"] = {k: v for k, v in sweep.items() if k != "mismatchDetails"}
        out["leapSweepMismatches"] = sweep["mismatchDetails"]
        out["compare"] = {
            "requested": 0,
            "compared": sweep["combosCompared"],
            "identical": sweep["combosCompared"] - sweep["mismatches"],
            "mismatches": sweep["mismatches"],
            "byPath": dict(sweep["byPath"]),
            "coverage": _sweep_coverage(sweep),
            "knownDifferences": KNOWN_DIFFERENCES,
            "firstMismatch": (sweep["mismatchDetails"] or [None])[0],
            "mismatchDetails": sweep["mismatchDetails"],
            "mode": "leap-sweep",
        }

    _emit(out, as_json=True, pretty=True)
    return 0 if out["ok"] else 1


# ---------------- 双引擎比对 ----------------
_TX_NOTE = {
    0: "早子", 12: "晚子",
}

#: 确定性闰月穷举：每个闰月至少覆盖第 1 天 / 中间一天 / 最后一天。
_LEAP_DAYS_PER_MONTH = 3
#: 闰月日期要跑的时辰与性别：早子(0) / 午(6) / 晚子(12) × 男 / 女。
_LEAP_TIME_INDICES = (0, 6, 12)
_LEAP_GENDERS = ("男", "女")
#: 每个闰月默认跑多少个「日期 × 时辰 × 性别 × fixLeap」组合（0 = 不限）。
#: 超上限时按「fixLeap=false 优先、但两边都留」分配配额 —— false 正是暴露本次 bug 的取值，
#: 但只跑 false 会让 coverage 变成不诚实的数字，所以 true 也必须占到份额。
_LEAP_COMBO_LIMIT = 96
#: `--leap-sweep` 不带区间时的默认年段（含两端）。
_LEAP_SWEEP_DEFAULT = "1995-2030"
#: 查历法库要 node（`lunar-typescript` 是 npm 包）；同时**对照也必须两条引擎都在**。
#: 缺 node 时明确报错，不再退回「扫排盘引擎」的旧办法。
_LEAP_SWEEP_NEEDS_NODE = (
    "闰月穷举需要 node 引擎：闰月来自历法库（lunar-typescript），对照也由 node 路 iztro 完成。"
    "请安装 Node.js 18+ 并运行 python scripts/ziwei.py deps 后重试。"
)

KNOWN_DIFFERENCES = [
    "jieqiPillar：node 路由 lunar-lite 提供立春口径四柱；python 路（x-iztro）没有该字段，"
    "如实置 null 并记 notes。该字段仅供八字口径对照，紫微斗数取四化不用它，故不影响 schema。"
]


def _sweep_coverage(sweep: dict) -> dict:
    """
    闰月穷举的覆盖信息，**在既有 coverage 键之外新增** `leapSweep*`，不动原有键。

    键名沿用旧版（下游脚本按名取值），只有两处语义随「查表取代扫描」而变：
      · `leapSweepScanRoute` 由 "python"（按天扫）改为 "calendar-table"；
      · `leapSweepDaysScanned` 恒为 `null`（查表不扫日期），说明见 `leapSweepScanNote`。
    """
    return {
        "leapSweep": True,
        "leapSweepSpec": sweep["spec"],
        "leapSweepScanRoute": sweep["scanRoute"],
        "leapSweepScanNote": sweep["scanNote"],
        "leapSweepYearsScanned": sweep["yearsScanned"],
        # 语义已失效：改历法查表后没有「扫描天数」这回事。保留键名以兼容下游，
        # 但一律给 null —— 一个会被读成「扫了 13149 天」的数字比缺键更坏。
        "leapSweepDaysScanned": sweep["daysScanned"],
        "leapSweepYearsWithLeapMonth": sweep["yearsWithLeapMonth"],
        "leapSweepLeapMonthsFound": sweep["leapMonthsFound"],
        "leapSweepDaysPerMonth": sweep["daysPerLeapMonth"],
        "leapSweepDaysSwept": sweep["daysSwept"],
        "leapSweepDates": sweep["datesSwept"],
        "leapSweepTimeIndices": sweep["timeIndices"],
        "leapSweepGenders": sweep["genders"],
        "leapSweepFixLeapVariants": sweep["fixLeapVariants"],
        "leapSweepCombosRequested": sweep["combosRequested"],
        "leapSweepCombosCompared": sweep["combosCompared"],
        "leapSweepCombosByFixLeap": sweep["combosByFixLeap"],
        "leapSweepCombosByTimeIndex": sweep["combosByTimeIndex"],
        "leapSweepCombosByMonth": sweep["combosByLeapMonth"],
        "leapSweepDaysByMonth": sweep["daysByLeapMonth"],
        "leapSweepIdentityLock": sweep["identityLock"],
        "leapSweepMismatches": sweep["mismatches"],
        "leapSweepByPath": sweep["byPath"],
        "leapSweepElapsedSec": sweep["elapsedSec"],
        "leapSweepMonths": sweep["leapMonths"],
    }


def _sweep_by_path(sweep: dict) -> dict[str, int]:
    """把闰月穷举的差异按字段路径归并计数（与随机抽样同一口径）。"""
    return dict(sweep.get("byPath") or {})


class _Comparer:
    """
    两条引擎的逐字段比对器（随机抽样与闰月穷举共用同一套记账）。

    归一化口径与历次口径一致：只取 `astrolabe`；`jieqiPillar` 是**已声明的已知差异**
    （node 路由 lunar-lite 提供，python 路如实为 null），从基准里去掉并单列。
    """

    def __init__(self) -> None:
        self.mismatches: list[dict] = []
        self.by_path: dict[str, int] = {}
        self.first_sample: dict | None = None
        self.covered_five: dict[str, int] = {}
        self.covered_tx: set[int] = set()
        self.calendars: dict[str, int] = {"solar": 0, "lunar": 0}
        self.ok = 0

    def run(self, req: dict, index: int) -> dict:
        """排一张盘并比对；返回 {request, ok, five, diffs, lunarDate}。"""
        return self._run_with(req, index, engines.run(req, engine="node"))

    def run_sweep(self, cases: list[dict], rng: random.Random) -> list[dict]:
        """
        闰月穷举的批量跑法：**只把 node 路的子进程合批**，比对口径一字未动。

        每个用例仍然各自排一次 python 路（同进程，便宜），node 路则由 `engines.run_many`
        一次子进程排完一批 —— 逐张起 node 是每次 ~0.23s 的纯启动开销，
        96 个用例光启动就要 22s。批量失败时**逐张退回**，不弄丢对拍。
        """
        reqs = [_leap_request(c, rng) for c in cases]
        node_results = engines.run_many(reqs)
        out: list[dict] = []
        for i, req in enumerate(reqs):
            nd = node_results[i] if node_results is not None else engines.run(req, engine="node")
            out.append(self._run_with(req, i, nd))
        return out

    def _run_with(self, req: dict, index: int, nd: dict) -> dict:
        """排 python 路并把两条路的结果记进账本（`nd` 由调用方给）。"""
        py = engines.run(req, engine="python")
        cal = req.get("calendar") or "solar"
        self.calendars[cal] = self.calendars.get(cal, 0) + 1
        if req.get("timeIndex") is not None:
            self.covered_tx.add(req["timeIndex"])
        if nd is None:
            nd = {"ok": False, "error": "ENGINE_BATCH_FAILED"}

        if not (py.get("ok") and nd.get("ok")):
            item = {"index": index, "request": req, "error": "ENGINE_FAILED",
                    "python": py.get("error") or py.get("message"),
                    "node": nd.get("error") or nd.get("message")}
            self.mismatches.append(item)
            self.by_path["<engine_failed>"] = self.by_path.get("<engine_failed>", 0) + 1
            if self.first_sample is None:
                self.first_sample = item
            return {"request": req, "ok": False, "five": None, "diffs": [],
                    "lunarDate": None, "error": item}

        a = _norm_for_compare(nd)["astrolabe"] or {}
        b = _norm_for_compare(py)["astrolabe"] or {}
        # python 路没有 jieqiPillar（node 侧由 lunar-lite 提供）——已知差异，两边都去掉
        a = {k: v for k, v in a.items() if k != "jieqiPillar"}
        b = {k: v for k, v in b.items() if k != "jieqiPillar"}

        five = a.get("fiveElementsClass")
        if five:
            self.covered_five[five] = self.covered_five.get(five, 0) + 1

        d = _diff(a, b)
        if d:
            for p, _x, _y in d:
                self.by_path[p] = self.by_path.get(p, 0) + 1
            item = {"index": index, "request": req, "diffCount": len(d),
                    "diffs": [{"path": p, "node": x, "python": y} for p, x, y in d[:12]]}
            self.mismatches.append(item)
            if self.first_sample is None:
                self.first_sample = item
        else:
            self.ok += 1
        return {"request": req, "ok": not d, "five": five, "diffs": d,
                "lunarDate": a.get("lunarDate")}


# ---------------- 确定性闰月穷举 ----------------
#
# 为什么需要它：`--compare N` 是**随机抽样**，公历生日落在闰月内的概率约 1%，
# 所以「200 张 0 差异」并不能证明 solar 路的 fixLeap 口径对齐 —— 这正是漏掉
# 「engine_py solar 路透传 fixLeap」那个整盘平移一宫 bug 的原因。
#
# 闰月**怎么发现**：查历法库，不扫日期。`lunar-typescript`（iztro 的历法依赖，已提为
# 直接依赖）能直接回答「这一年闰几月」：`LunarYear.fromYear(y).getLeapMonth()` 给闰月号
# （0 = 无闰月），`Lunar.fromYmd(y, -m, d)`（负月号 = 闰月）给该日公历/农历。
# 于是整个年段只要**一次 node 子进程**就能拿到全部闰月；早先那版按天扫 A–B 年、
# 拿排盘引擎当日历用（1995–2030 扫 13149 天、27–33 秒），是把简单事做笨了。
# 拿到闰月后，下面照旧把第 1 / 中间 / 最后一天 × 时辰 × 性别 × fixLeap 全部对拍。


def _sweep_table(lo: int, hi: int, note: list[str]) -> tuple[list[dict] | None, int, None]:
    """
    闰月发现：`engines.leap_months` 查历法库（lunar-typescript），返回月度记录。

    返回 `(months, years_queried, None)`；`months` 为 None 表示**查表本身做不到**
    （node 不可用 / lunar-typescript 缺失），调用方据此报错，**不再退回按天扫描**。
    `days_scanned` 恒为 None —— 查表不扫日期，沿用旧键名只会给出误导性的数字。
    """
    t0 = _dt.datetime.now()
    fetched = engines.leap_months(lo, hi)
    if fetched is None:
        return None, 0, None
    elapsed = round((_dt.datetime.now() - t0).total_seconds(), 2)
    note.append(
        f"闰月发现：历法查表（lunar-typescript 的 LunarYear.getLeapMonth / Lunar.fromYmd），"
        f"整个 {lo}-{hi} 年段一次 node 子进程问完，**不扫描日期**（{elapsed}s）。"
    )

    months: list[dict] = []
    lib_warnings: list[str] = []
    for rec in fetched:
        for msg in rec.get("warnings") or []:
            if msg not in lib_warnings:
                lib_warnings.append(str(msg))
        y = rec.get("lunarYear")
        mnum = rec.get("leapMonth")
        length = rec.get("lengthDays")
        cands: list[dict] = []
        for p in rec.get("picks") or []:
            cands.append({"date": p.get("solarDate"), "lunarDate": p.get("lunarDate"),
                          "lunarDay": p.get("lunarDay")})
        cands = [c for c in cands if c["date"] and c["lunarDay"] is not None]
        cands.sort(key=lambda c: c["lunarDay"])
        if not cands:
            note.append(f"{y} 年闰{mnum}月：历法库没有给出可用的日期，已跳过")
            continue
        if length not in (29, 30):
            note.append(f"{y} 年闰{mnum}月的长度 {length} 不在 29/30 之内，建议人工核对")
        months.append({
            "index": len(months) + 1, "year": y, "month": mnum,
            "monthLabel": f"{y} 闰{mnum}月", "lengthDays": length,
            "inferredLengthDays": length,
            "firstDate": cands[0]["date"], "lastDate": cands[-1]["date"],
            "pickedDays": [c["lunarDay"] for c in cands], "candidates": cands,
            # 旧「按天扫描」口径下这是**扫描到的**天数；查表后全月天数已知，直接用 lengthDays。
            "candidateCount": length,
            "scanKind": "calendar-table (lunar-typescript)",
            "warnings": [],
        })
    for msg in lib_warnings:
        note.append(str(msg))
    if not months:
        note.append(f"历法库回答 {lo}-{hi} 一个闰月都没有（该年段通常有 13 个左右）。")
    return months, hi - lo + 1, None


def _leap_cases(months: list[dict]) -> tuple[list[dict], int]:
    """
    每个闰月 × {第1天, 中间一天, 最后一天} × timeIndex{0,6,12} × gender{男,女}
    × fixLeap{true,false} 生成比对用例，顺序上 **fixLeap=false 全部排在前面**。
    """
    cases: list[dict] = []
    for rec in months:
        for cand in rec["candidates"]:
            for fix in (False, True):
                for gender in _LEAP_GENDERS:
                    for tx in _LEAP_TIME_INDICES:
                        cases.append({
                            "leapMonthIndex": rec["index"], "monthLabel": rec["monthLabel"],
                            "date": cand["date"], "lunarDate": cand["lunarDate"],
                            "lunarDay": cand["lunarDay"], "timeIndex": tx,
                            "gender": gender, "fixLeap": fix,
                        })
    return cases, len(months) * _LEAP_DAYS_PER_MONTH * len(_LEAP_TIME_INDICES) * \
        len(_LEAP_GENDERS) * 2


def _leap_request(case: dict, rng: random.Random) -> dict:
    """
    ⚠️ 流年必须落在出生**之后**。闰月穷举会命中 2025 / 2028 这些「未来年份」的出生日期，
    而旧版把年份写死成 `randint(2024, 2030)`，于是造出「出生前流年」这种越界请求：
    两条引擎对它的处理不同（node 给甲子、python 给癸未），对拍器因此报出一堆**假不一致**
    （实测全量 468 例里 23 例，100% 是这个原因）。现在按出生年下推。
    """
    birth_year = int(str(case["date"])[:4])
    lo = max(2024, birth_year + 1)
    return {"date": case["date"], "calendar": "solar", "timeIndex": case["timeIndex"],
            "gender": case["gender"], "years": [rng.randint(lo, lo + 6)],
            "fixLeap": case["fixLeap"]}


def _leap_sweep(spec: str, limit: int, seed: int | None = None) -> dict:
    """
    确定性闰月穷举：见文件上方注释。返回的 `coverage` 会并入 `compare` 的输出。
    """
    lo, hi = _parse_year_range(spec, "--leap-sweep")
    if lo > hi:
        lo, hi = hi, lo
    note: list[str] = []
    t0 = _dt.datetime.now()

    months, years_scanned, days_scanned = _sweep_table(lo, hi, note)
    route = "calendar-table"
    error: str | None = None
    if months is None:
        # 查表做不到（node / lunar-typescript 缺失）。**不退回首版按天扫描**：
        # 排盘引擎不是历法库，那条路既慢又只是绕远。
        months = []
        error = _LEAP_SWEEP_NEEDS_NODE
        note.append(error)

    cases, requested = _leap_cases(months)
    if limit and len(cases) > limit:
        # 配额：fixLeap=false 优先（正是暴露本 bug 的取值），但**也要保住 true**，
        # 否则 coverage 会变成「true 覆盖 0 例」这种不诚实的数字。
        falses = [c for c in cases if not c["fixLeap"]]
        trues = [c for c in cases if c["fixLeap"]]
        n_false = min(len(falses), max(limit // 2, limit - len(trues)))
        cases = falses[:n_false] + trues[:max(0, limit - n_false)]
    dates_swept = sorted({c["date"] for c in cases})

    rng = random.Random(seed) if seed is not None else random.Random()
    cmp = _Comparer()
    covered_months: dict[str, int] = {}
    covered_days: dict[str, list[str]] = {}
    fix_counts = {"true": 0, "false": 0}
    tx_counts: dict[str, int] = {}
    # 日历恒等：同一个公历日期，不论请求里 fixLeap 取什么，引擎返回的 lunarDate 必须逐字相同
    # （solar 路把 fixLeap 写死为 true）。这是本 bug 的**直接指纹**，比逐字段 diff 更早报警。
    calendar_identity = {"checked": 0, "lunarDateChangedByFixLeap": 0}
    lunar_seen: dict[str, set] = {}

    # 用例批量跑：node 路一次子进程排一批（省掉上百次 ~0.23s 的进程启动），
    # python 路与比对口径一字未动；批量失败会自动逐张退回。
    results = cmp.run_sweep(cases, rng)

    for case, out in zip(cases, results):
        if out.get("error"):
            continue
        fix_counts["false" if not case["fixLeap"] else "true"] += 1
        label = case["monthLabel"]
        covered_months[label] = covered_months.get(label, 0) + 1
        covered_days.setdefault(label, [])
        if case["date"] not in covered_days[label]:
            covered_days[label].append(case["date"])
        k = str(case["timeIndex"])
        tx_counts[k] = tx_counts.get(k, 0) + 1
        calendar_identity["checked"] += 1
        seen = lunar_seen.setdefault(case["date"], set())
        if out.get("lunarDate"):
            seen.add(out["lunarDate"])
        if len(seen) > 1:
            calendar_identity["lunarDateChangedByFixLeap"] += 1

    t1 = _dt.datetime.now()
    return {
        "spec": f"{lo}-{hi}",
        "yearsScanned": years_scanned,
        "yearsWithLeapMonth": len({m["year"] for m in months}),
        "leapMonthsFound": len(months),
        "leapMonths": months,
        "daysPerLeapMonth": _LEAP_DAYS_PER_MONTH,
        "daysSwept": len(dates_swept),
        "datesSwept": dates_swept,
        "timeIndices": list(_LEAP_TIME_INDICES),
        "genders": list(_LEAP_GENDERS),
        "fixLeapVariants": [True, False],
        "combosRequested": requested,
        "combosCompared": len(cases),
        "combosByFixLeap": fix_counts,
        "combosByTimeIndex": dict(sorted(tx_counts.items(), key=lambda kv: int(kv[0]))),
        "combosByLeapMonth": covered_months,
        "daysByLeapMonth": covered_days,
        "identityLock": calendar_identity,
        "scanRoute": route,
        "scanNote": note,
        # 查表不扫日期 ⇒ 恒为 None（不再给出一个会被读成「扫了 0 天/13149 天」的数字）。
        "daysScanned": None,
        "error": error,
        "elapsedSec": round((t1 - t0).total_seconds(), 1),
        "mismatches": len(cmp.mismatches),
        "byPath": dict(sorted(cmp.by_path.items(), key=lambda kv: -kv[1])),
        "mismatchDetails": cmp.mismatches[:20],
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


def _compare(n: int, seed: int | None = None,
             leap_sweep: str | None = None, leap_limit: int = 0) -> dict:
    """
    用两条引擎各排 N 张随机盘并逐字段比对。

    覆盖策略：随机公历盘为主，另外**定向补足**——
      · 六种五行局：抽到未覆盖的局就记下，最后按「每种局至少一张」报告覆盖
      · 早晚子时：显式补 timeIndex=0（早子）与 12（晚子）
      · 闰月：① 若干农历输入盘（fixLeap 两个取值都跑）；
              ② **确定性闰月穷举** —— 只有在显式给了 `--leap-sweep A-B` 时才跑，
                 因为随机抽样抽到「阳历生日落在闰月内」的概率约 1%，
                 0 差异并不代表安全（这正是本 bug 被漏掉的原因）。
    输出：不一致条数与首个差异样例（按字段路径归并计数）。
    """
    rng = random.Random(seed) if seed is not None else random.Random()
    cmp = _Comparer()
    plan: list[dict] = []

    # 定向补充：晚子、农历闰月探针、早子
    baseline: list[dict] = ([{"kind": "latezi"}] * max(1, n // 20)
                            + [{"kind": "leap"}] * max(1, n // 20)
                            + [{"kind": "normal"}] * max(1, n // 20))
    while len(baseline) < n:
        baseline.append({"kind": "normal"})
    rng.shuffle(baseline)
    baseline = baseline[:n]

    def _case(want: dict, idx: int) -> None:
        """跑一张随机盘；农历闰月探针 fixLeap 两个取值各跑一遍。"""
        req = _random_req(rng, want)
        cmp.run(req, idx)
        if req.get("calendar") == "lunar":
            alt = dict(req, fixLeap=not req.get("fixLeap", True))
            cmp.run(alt, idx)
        plan.append(req)

    for i, want in enumerate(baseline):
        _case(want, i)

    # 覆盖补足：把没抽到的五行局 / 早晚子时定向补齐（补的盘同样参与比对）
    missing = [c for c in FIVE_CLASSES if c not in cmp.covered_five]
    extra = _find_class_requests(
        rng, missing,
        need_early=0 not in cmp.covered_tx and len(plan) >= 5,
        need_late=12 not in cmp.covered_tx and len(plan) >= 5)
    for req in extra:
        cmp.run(req, len(plan))
        plan.append(req)

    coverage = {
        "calendar": cmp.calendars,
        "timeIndex": {str(t): _TX_NOTE.get(t, "") for t in sorted(cmp.covered_tx)},
        "fiveElementsClass": cmp.covered_five,
        "earlyLateZi": {"早子(0)": 0 in cmp.covered_tx, "晚子(12)": 12 in cmp.covered_tx},
    }

    result = {
        "requested": n,
        "compared": len(plan),
        "identical": cmp.ok,
        "mismatches": len(cmp.mismatches),
        "byPath": dict(sorted(cmp.by_path.items(), key=lambda kv: -kv[1])),
        "coverage": coverage,
        "knownDifferences": KNOWN_DIFFERENCES,
        "firstMismatch": cmp.first_sample,
        "mismatchDetails": cmp.mismatches[:20],
    }

    # 确定性闰月穷举（可选，显式传 --leap-sweep 才跑）
    if leap_sweep:
        sweep = _leap_sweep(leap_sweep, limit=leap_limit or 0, seed=seed)
        coverage.update(_sweep_coverage(sweep))
        coverage["leapSweepLeapMonths"] = [
            {k: v for k, v in m.items() if k != "candidates"} for m in sweep["leapMonths"]
        ]
        result["leapSweep"] = {k: v for k, v in sweep.items() if k != "mismatchDetails"}
        result["leapSweepMismatches"] = sweep["mismatchDetails"]
        result["leapSweepOk"] = sweep["mismatches"] == 0
        result["leapSweepMismatchCount"] = sweep["mismatches"]
        if sweep.get("error"):
            # 查表本身没做成（node / lunar-typescript 缺失）：这不是「0 差异」，是没测到。
            result["leapSweepError"] = sweep["error"]
            result["leapSweepOk"] = False
        # 总账：随机抽样与闰月穷举的不一致都算进来，别让 0 差异只覆盖随机那部分
        result["mismatches"] = len(cmp.mismatches) + sweep["mismatches"]
        result["randomMismatches"] = len(cmp.mismatches)
        result["randomCompared"] = len(plan)
        result["compared"] = len(plan) + sweep["combosCompared"]
        result["identical"] = cmp.ok + (sweep["combosCompared"] - sweep["mismatches"])
        if sweep["mismatches"]:
            result["mismatchDetails"] = (sweep["mismatchDetails"] +
                                         result["mismatchDetails"])[:20]
            if result["firstMismatch"] is None:
                result["firstMismatch"] = sweep["mismatchDetails"][0]
        for path, cnt in _sweep_by_path(sweep).items():
            result["byPath"][path] = result["byPath"].get(path, 0) + cnt
        result["byPath"] = dict(sorted(result["byPath"].items(), key=lambda kv: -kv[1]))

    return result


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
               "  python ziwei.py engines --leap-sweep 1995-2030\n"
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

    eg = sub.add_parser("engines",
                        help="探测可用引擎；--compare N 随机对拍；--leap-sweep A-B 闰月穷举对拍")
    eg.add_argument("--compare", type=int, default=0, metavar="N",
                    help="随机盘比对张数，如 200（需两条引擎都可用）")
    eg.add_argument("--leap-sweep", nargs="?", const=_LEAP_SWEEP_DEFAULT,
                    default=None, metavar="A-B",
                    help="确定性闰月穷举：从历法库（lunar-typescript）查出 A-B 年里的每个闰月，"
                         "覆盖其"
                         f"第 1/中间/最后一天 × 早子·午·晚子 × 男·女 × fixLeap 两取值。"
                         f"不带区间用默认 {_LEAP_SWEEP_DEFAULT}；不传该参数则完全不跑。"
                         "可与 --compare 合用")
    eg.add_argument("--leap-sweep-limit", type=int, default=_LEAP_COMBO_LIMIT, metavar="M",
                    help=f"闰月穷举的比对上限（默认 {_LEAP_COMBO_LIMIT}，0=不限）。"
                         "超上限时先砍 fixLeap=true，fixLeap=false 的组合全部保留")
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

