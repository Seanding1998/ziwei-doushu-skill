#!/usr/bin/env python3
"""
ziwei-doushu / scripts/ziwei.py — 统一命令行入口（跨平台：Windows / macOS / Linux）

原则：**一切可计算的东西交给脚本**，AI 只做判断、取舍、综合与表述。

用法示例
--------
  python ziwei.py deps                                  # 安装/检查 iztro 依赖
  python ziwei.py chart  --date 1990-06-07 --tx 4 --gender 男 --year 2026
  python ziwei.py chart  --date 1990-06-07 --tx 6 --gender 男 --json
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
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from pathlib import Path

# 允许直接 `python scripts/ziwei.py` 运行（无需安装为包）
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ziwei_doushu import astro, chart as chartmod, render  # noqa: E402


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
    try:
        info = chartmod.ensure_deps(force=args.force, quiet=args.quiet)
        _emit({"ok": True, **info}, as_json=True, pretty=True)
        return 0
    except Exception as e:  # noqa: BLE001
        return _fail(e)


def cmd_chart(args) -> int:
    try:
        c = chartmod.build_chart(
            date_str=args.date, gender=args.gender,
            time=args.time, time_index=args.tx, longitude=args.lng,
            calendar="lunar" if args.lunar else "solar",
            years=_years(args.year),
            fix_leap=not args.no_fix_leap,
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

    两种口径都取自 iztro 及其历法依赖 lunar-lite，不做本地近似。
    """
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
                                  calendar="lunar" if args.lunar else "solar")
        non_jieqi = eng["astrolabe"]["chineseDate"]
        jq = eng["astrolabe"].get("jieqiPillar")
    except Exception as e:  # noqa: BLE001
        return _fail(e)

    if not jq:
        raise_msg = ("无法取得节气四柱（lunar-lite 不可用）。请重装依赖：python ziwei.py deps")
        return _fail(chartmod.ZiweiError("JIEQI_UNAVAILABLE", raise_msg))

    jieqi = jq["text"]
    zs, bs = non_jieqi.split()[0][0], jieqi.split()[0][0]
    z_mut = [f"{s}化{t}" for s, t in zip(astro.MUTAGEN_TABLE[zs], astro.MUTAGEN_NAMES)]
    b_mut = [f"{s}化{t}" for s, t in zip(astro.MUTAGEN_TABLE[bs], astro.MUTAGEN_NAMES)]
    conflict = zs != bs

    result = {
        "ok": True,
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
        "note": "两种口径均由 iztro 及其历法依赖 lunar-lite 计算，非本地近似。",
    }
    _emit(result, as_json=True, pretty=True)
    return 0


# ---------------- 参数 ----------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ziwei.py",
        description="紫微斗数排盘 / 大限流年 —— 确定性计算（Python 编排 + iztro 引擎）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：\n"
               "  python ziwei.py deps\n"
               "  python ziwei.py tree --date 1990-06-07 --tx 4 --gender 男 --year 2026\n"
               "  python ziwei.py chart --date 1990-06-07 --tx 4 --gender 男 --json\n",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

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

    d = sub.add_parser("deps", help="安装/检查 iztro 依赖（不装全局、不动工作区）")
    d.add_argument("--force", action="store_true")
    d.add_argument("--quiet", action="store_true")
    d.add_argument("--json", action="store_true", help="(默认即 JSON 输出)")
    d.set_defaults(func=cmd_deps)

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
            "hint": "用法见 python ziwei.py <chart|tree|md|pillar|deps> --help",
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

