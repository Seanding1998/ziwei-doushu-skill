"""
ziwei_doushu/chart.py — 排盘编排层（跨平台）

设计：
  · Python 负责一切可计算的东西（真太阳时、四化、自化、三方四正、夹宫、岁数表、自检）
  · Node/iztro 只当一个「函数」调用，通过 stdin/stdout 传 JSON
  · 不依赖 bash / PowerShell / .cmd —— 用 shutil.which + argv 列表，Windows/macOS/Linux 通用
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import astro

SKILL_ROOT = Path(__file__).resolve().parents[2]
ENGINE_JS = Path(__file__).resolve().parents[1] / "engine.js"
NODE_MODULES = SKILL_ROOT / "node_modules"
NPM_CACHE = SKILL_ROOT / ".npm-cache"
IZTRO_PIN = "2.6.2"


def _bundled_node_candidates() -> list[Path]:
    """
    兜底 Node 位置（仅当 PATH 上找不到 node 时使用）。
    全部按平台条件生成，未命中的平台返回空列表——保证跨平台不会因硬编码路径报错。
    """
    cands: list[Path] = []
    home = Path.home()
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            # DSH 运行时自带的 node
            cands.append(Path(local) / "Programs" / "DeepSeek Harness" / "resources"
                         / "app.asar.unpacked" / "node" / "node.exe")
            cands.append(Path(local) / "dsh" / "dsh-runtimes" / "dsh-primary-runtime"
                         / "dependencies" / "node" / "bin" / "node.exe")
        for env in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(env)
            if base:
                cands.append(Path(base) / "nodejs" / "node.exe")
    else:
        for p in ("/usr/local/bin/node", "/usr/bin/node", "/opt/homebrew/bin/node"):
            cands.append(Path(p))
    # nvm / fnm / volta 常见落点
    cands.append(home / ".volta" / "bin" / ("node.exe" if os.name == "nt" else "node"))
    cands.append(home / ".local" / "bin" / "node")
    return cands


class ZiweiError(RuntimeError):
    """带错误码的可预期失败。"""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.extra = extra

    def as_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": self.code, "message": self.message, **self.extra}


# ---------- 可执行文件解析（跨平台） ----------
def find_node() -> str | None:
    for env in ("ZIWEI_NODE", "NODE_BIN"):
        v = os.environ.get(env)
        if v and Path(v).exists():
            return v
    p = shutil.which("node")
    if p:
        return p
    for cand in _bundled_node_candidates():
        if cand.exists():
            return str(cand)
    return None


def find_npm() -> list[str] | None:
    """
    返回可直接传给 subprocess 的 argv 前缀。
    Windows 上 npm 是 npm.cmd，必须整体作为 argv[0] 交给 shell=False 的调用，
    否则会报 WinError 193。这里显式处理三种平台形态。
    """
    for env in ("ZIWEI_NPM", "NPM_BIN"):
        v = os.environ.get(env)
        if v:
            return [v]
    # Windows 上 npm 是 npm.cmd（必须整体作为 argv[0]，否则 WinError 193）；POSIX 上是 npm
    names = ("npm.cmd", "npm.exe", "npm") if os.name == "nt" else ("npm", "npm.cmd")
    for name in names:
        p = shutil.which(name)
        if p:
            return [p]
    # 兜底：node 同目录 / 上层 lib 下的 npm-cli.js（nvm、n 等布局）
    node = find_node()
    if node:
        base = Path(node).resolve().parent
        for cand in (
            base / "node_modules" / "npm" / "bin" / "npm-cli.js",
            base / ".." / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
            base / ".." / ".." / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        ):
            if cand.exists():
                return [str(Path(node)), str(cand.resolve())]
    return None


def _env_with_node_path() -> dict[str, str]:
    """让 Node 能解析到 skill 内的 node_modules（跨平台的 NODE_PATH 约定）。"""
    env = os.environ.copy()
    existing = env.get("NODE_PATH", "")
    env["NODE_PATH"] = str(NODE_MODULES) + (os.pathsep + existing if existing else "")
    return env


# ---------- 依赖 ----------
def iztro_version() -> str | None:
    pkg = NODE_MODULES / "iztro" / "package.json"
    if not pkg.exists():
        return None
    try:
        return json.loads(pkg.read_text(encoding="utf-8")).get("version")
    except Exception:
        return None


def ensure_deps(force: bool = False, quiet: bool = False) -> dict[str, Any]:
    """把 iztro 装进 skill 目录。不装全局、不动工作区。"""
    have = iztro_version()
    if have and not force:
        return {"ok": True, "installed": False, "version": have, "path": str(NODE_MODULES)}

    npm = find_npm()
    if not npm:
        raise ZiweiError("NPM_NOT_FOUND",
                         "未找到 npm。请安装 Node.js 18+ 后重试，或设置环境变量 ZIWEI_NPM 指向 npm 可执行文件。")
    node = find_node()
    if not node:
        raise ZiweiError("NODE_NOT_FOUND",
                         "未找到 node。请安装 Node.js 18+，或设置环境变量 ZIWEI_NODE。")

    NODE_MODULES.mkdir(parents=True, exist_ok=True)
    NPM_CACHE.mkdir(parents=True, exist_ok=True)
    pkg_json = SKILL_ROOT / "package.json"
    if not pkg_json.exists():
        pkg_json.write_text(
            json.dumps({"name": "ziwei-doushu-skill", "private": True}, ensure_ascii=False),
            encoding="utf-8",
        )

    base = npm + ["install", f"iztro@{IZTRO_PIN}", "--no-audit", "--no-fund",
                  "--prefix", str(SKILL_ROOT)]

    attempts: list[tuple[str, list[str]]] = [
        ("默认缓存", list(base)),
        ("skill 内本地缓存", base + ["--cache", str(NPM_CACHE)]),
    ]
    # 中国大陆网络常见：直连失败、走代理成功。代理仅在前两次失败后才尝试。
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    if proxy:
        attempts.append(("环境代理 " + proxy,
                         base + ["--cache", str(NPM_CACHE), "--proxy", proxy, "--https-proxy", proxy]))

    last_err = ""
    for label, cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=600, cwd=str(SKILL_ROOT))
            if r.returncode == 0 and iztro_version():
                return {"ok": True, "installed": True, "version": iztro_version(),
                        "path": str(NODE_MODULES), "via": label}
            last_err = (r.stderr or r.stdout or "").strip()[-1200:]
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"
        if not quiet:
            print(f"[ziwei] {label} 安装未成功，尝试下一种方式…", file=sys.stderr)

    raise ZiweiError(
        "IZTRO_INSTALL_FAILED",
        f"iztro@{IZTRO_PIN} 安装失败。可手动执行：npm install iztro@{IZTRO_PIN} --prefix \"{SKILL_ROOT}\"",
        stderr=last_err,
    )


# ---------- 引擎调用 ----------
def run_engine(date: str, time_index: int, gender: str, calendar: str = "solar",
               years: list[int] | None = None, fix_leap: bool = True,
               timeout: int = 120) -> dict[str, Any]:
    node = find_node()
    if not node:
        raise ZiweiError("NODE_NOT_FOUND",
                         "未找到 node。请安装 Node.js 18+，或设置环境变量 ZIWEI_NODE。")
    if not ENGINE_JS.exists():
        raise ZiweiError("ENGINE_MISSING", f"引擎文件缺失：{ENGINE_JS}")
    if iztro_version() is None:
        raise ZiweiError("IZTRO_NOT_FOUND", "未安装 iztro。请先运行：python ziwei.py deps")

    req = {
        "date": date, "calendar": calendar, "timeIndex": int(time_index),
        "gender": gender, "years": years or [], "fixLeap": bool(fix_leap),
    }
    try:
        r = subprocess.run(
            [node, str(ENGINE_JS)],
            input=json.dumps(req),
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env=_env_with_node_path(), cwd=str(SKILL_ROOT),
        )
    except subprocess.TimeoutExpired:
        raise ZiweiError("ENGINE_TIMEOUT", f"引擎调用超时（{timeout}s）") from None

    out = (r.stdout or "").strip()
    if not out:
        raise ZiweiError("ENGINE_NO_OUTPUT",
                         "引擎没有输出。", stderr=(r.stderr or "")[-1200:], returncode=r.returncode)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise ZiweiError("ENGINE_BAD_JSON", f"引擎输出无法解析：{e}",
                         stdout=out[:800], stderr=(r.stderr or "")[-800:]) from None
    if not data.get("ok"):
        raise ZiweiError(data.get("error", "ENGINE_ERROR"),
                         data.get("message", "引擎返回失败"), detail=data.get("detail"))
    return data


# ---------- 日期解析 ----------
_DATE_RE = re.compile(r"^\s*(\d{4})\s*[-/年.]\s*(\d{1,2})\s*[-/月.]\s*(\d{1,2})\s*日?\s*$")
_TIME_RE = re.compile(r"^\s*(\d{1,2})\s*[:：时点]\s*(\d{1,2})?\s*分?\s*$")


def parse_date(s: str) -> _dt.date:
    m = _DATE_RE.match(s)
    if not m:
        raise ZiweiError("BAD_DATE", f"日期格式无法识别：{s!r}（期望 1990-06-07 / 1990/6/7 / 1990年6月7日）")
    y, mo, d = (int(x) for x in m.groups())
    try:
        return _dt.date(y, mo, d)
    except ValueError as e:
        raise ZiweiError("BAD_DATE", f"日期不合法：{s!r} — {e}") from None


def parse_time(s: str) -> tuple[int, int]:
    m = _TIME_RE.match(s)
    if not m:
        raise ZiweiError("BAD_TIME", f"时间格式无法识别：{s!r}（期望 12:07 / 12时7分）")
    hh = int(m.group(1))
    mm = int(m.group(2) or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ZiweiError("BAD_TIME", f"时间不合法：{s!r}")
    return hh, mm


# ---------- 主流程 ----------
POSITIVE_BRIGHTNESS = {"庙", "旺", "得", "利"}


def build_chart(
    date_str: str,
    gender: str,
    time: str | None = None,
    time_index: int | None = None,
    longitude: float | None = None,
    calendar: str = "solar",
    years: list[int] | None = None,
    fix_leap: bool = True,
) -> dict[str, Any]:
    """排出完整命盘 + 大限 + 流年，附全部确定性派生数据。"""
    if gender not in ("男", "女"):
        raise ZiweiError("BAD_GENDER", "性别只能是 男 或 女")

    birth = parse_date(date_str)
    warnings: list[str] = []

    # 1) 定时辰
    if time_index is not None:
        if not (0 <= int(time_index) <= 12):
            raise ZiweiError("BAD_TIME_INDEX", "--tx 必须是 0-12 的整数")
        _tidx = int(time_index)
        # 只给了时辰序号、没给钟点：钟点轴上的「距交界多远、往哪边偏」无从计算，
        # 一律留 None——绝不拿时辰中点假装成出生时刻（那等于编一个盘面事实）。
        tst = {
            "corrected": False, "clock": None, "apparent": None, "delta_minutes": 0.0,
            "equation_of_time": None, "longitude_term": None, "day_shift": 0,
            "time_index": _tidx, "time_name": astro.TIME_NAMES[_tidx],
            "minutes_from_hour_boundary": None,
            "nearest_hour_boundary": None,
            "nearest_boundary_minutes": None,
            "boundary_shift_direction": None,
            "clock_minutes_from_hour_boundary": None,
            "clock_nearest_hour_boundary": None,
            "clock_boundary_shift_direction": None,
            "warnings": ["直接指定了时辰序号，未做真太阳时校正。"],
        }
    else:
        if not time:
            raise ZiweiError("MISSING_TIME", "必须提供出生时间（--time 12:07）或时辰序号（--tx 0-12）")
        hh, mm = parse_time(time)
        tst = astro.apparent_solar_time(birth, hh, mm, longitude)
    warnings.extend(tst["warnings"])

    # 2) 引擎排盘（晚子时 iztro 以日期为准；星盘本身在测试中与文墨天机一致）
    eng = run_engine(
        date=birth.isoformat(), time_index=tst["time_index"], gender=gender,
        calendar=calendar, years=years or [], fix_leap=fix_leap,
    )
    a = eng["astrolabe"]

    # 3) 派生数据（全部 Python 算，不靠 AI）
    chinese = a["chineseDate"].split()
    year_pillar = chinese[0]
    year_stem, year_branch = year_pillar[0], year_pillar[1]

    palaces = a["palaces"]
    for p in palaces:
        p["isLaiYin"] = (p["stem"] == year_stem)
        p["soulAges"] = astro.soul_limit_ages(p["branch"], year_branch, gender)
        p["yearAges"] = astro.yearly_ages(p["branch"], year_branch)
        p["isSoulPalace"] = (p["branch"] == a["soulPalaceBranch"])

    soul_branch = a["soulPalaceBranch"]

    # 生年四化落宫
    birth_mut = []
    for p in palaces:
        for key in ("major", "minor"):
            for s in p.get(key) or []:
                if s.get("mutagen"):
                    birth_mut.append({"star": s["name"], "type": s["mutagen"],
                                      "branch": p["branch"], "palace": p["name"]})
    self_mut = astro.self_mutagen(palaces)

    surround = astro.surrounded(palaces, soul_branch)
    flank = astro.flanking(palaces, soul_branch)

    # 3b) 宫位关系结构化表（第四轮新增）
    #     任意一宫的对宫 / 三合两宫 / 两邻，全部由脚本产出。
    #     报告正文凡出现「对宫 X」「X 在 Y 三合」「X、Y 分居 Z 两邻」，
    #     都必须引这张表；审查器按同一规则逐句核对。
    palace_relations = {
        p["branch"]: astro.relations_of(palaces, p["branch"]) for p in palaces
    }

    # 3c) 六吉六煞配对事实（第五轮新增）
    #     第五轮盲审的 BLOCKER 是「六吉不成对」——报告自己算了半天，算反了。
    #     这类「成对／同宫／对拱」的算术一律由脚本给，正文只许引用。
    pair_facts = astro.pairing_facts(palaces, soul_branch)

    # 4) 大限：五行局数即起运虚岁，用于交叉校验引擎
    decadal = a["decadal"]
    start_age = None
    if decadal:
        start_age = decadal[0]["ageRange"][0] if soul_branch == _branch_of(decadal[0], palaces) else None
    fw = astro.five_elements_from_palace_stem(
        next((p["stem"] + p["branch"] for p in palaces if p["branch"] == soul_branch), "")
    )
    expected_start = {"水二局": 2, "木三局": 3, "金四局": 4, "土五局": 5, "火六局": 6}.get(
        a["fiveElementsClass"])
    if expected_start and decadal and decadal[0]["ageRange"][0] != expected_start:
        warnings.append(
            f"起运岁数异常：五行局 {a['fiveElementsClass']} 应为 {expected_start} 岁起运，"
            f"引擎给出 {decadal[0]['ageRange'][0]} 岁。"
        )

    # 5) 流年
    horoscope = []
    by_branch = {p["branch"]: p for p in palaces}
    for h in a["horoscope"]:
        yb = h["yearly"]["ganzhi"][1]
        landed = by_branch.get(yb)
        soul_limit_b = h["soulLimit"]["branch"] if h.get("soulLimit") else None
        # 流年辅星（流禄/流羊/流陀/流马）由脚本算，报告只许引用——
        # 第七轮 MINOR ④要写「2026 流羊落本命命宫」，手算这一步是新的失分点。
        ygz = h["yearly"]["ganzhi"]
        aux = astro.yearly_aux_stars(ygz[0], ygz[1])
        horoscope.append({
            **h,
            "yearlySoulPalaceLandsOn": {
                "branch": yb,
                "palace": landed["name"] if landed else None,
                "major": [s["name"] for s in (landed or {}).get("major") or []],
            },
            "soulLimitPalace": (by_branch.get(soul_limit_b) or {}).get("name") if soul_limit_b else None,
            "yearlyAuxStars": [
                {"name": nm, "branch": br,
                 "natalPalace": (by_branch.get(br) or {}).get("name")}
                for nm, br in aux.items() if br
            ],
        })

    # 6) 自检
    problems = astro.validate_palaces(palaces)
    if problems:
        warnings.extend("机器自检：" + x for x in problems)

    # 7) 盘面观察（事实陈述，不是断语）
    observations = _observe(palaces, soul_branch, surround, flank, year_stem)

    # 8) 格局条件判定：脚本只判「古法构成条件是否满足」，
    #     satisfied / break_save 一律留空交 AI 按全盘裁断
    pattern_candidates = astro.pattern_candidates(palaces, soul_branch)

    # 9) 大限十二宫映射 + 大限四化落点（第四轮新增）
    #    「大限福德宫落在哪个地支」这类算术，AI 手算必错（第三轮盲审 BLOCKER）。
    #    这里一次算完：每个大限的 宫名→地支 / 地支→宫名 两张表，
    #    以及该限四化每一条落在**本命哪一宫**、又是**本限哪一宫**。
    star_at: dict[str, str] = {}
    for p in palaces:
        for key in ("major", "minor"):
            for s in p.get(key) or []:
                star_at.setdefault(s["name"], p["branch"])
    branch_palace = {p["branch"]: p for p in palaces}
    # 大限行向：阳男阴女顺行／阴男阳女逆行（见 astro.decadal_direction）。
    # ⚠️ 行向只决定「哪一支是大限命宫**，不改变限内十二宫名的排法**——限内宫名一律与本命同向
    # （逆行排布，见 astro.palace_name_map 的 iztro 实测依据）。第七轮一度把行向套到宫名表上，
    # 使顺行盘（阳男/阴女）的 palaceMap 整表错位，已回退。
    dec_dir = astro.decadal_direction(year_stem, gender)
    # 行向自检：引擎给的 decadal 序列（起运限→次限→…）在方向上必须与算出的行向一致。
    # 用**序列单调性**判，而不是拿某一宫去套宫名——后者会把「宫名→地支」的表
    # 与 decode 出的宫名混着用，等于自证（初版就是那样，误报了 11 条）。
    if len(decadal) >= 2:
        _B = astro.BR12
        _step = (_B.index(decadal[1]["branch"]) - _B.index(decadal[0]["branch"])) % 12
        _steps = {(_B.index(decadal[k + 1]["branch"]) - _B.index(decadal[k]["branch"])) % 12
                  for k in range(len(decadal) - 1)}
        if _steps != {_step} or _step not in (1, 11):
            warnings.append("大限序列自检未通过：引擎给出的大限地支序列不是等步长的顺行或逆行，"
                            "十二宫映射无法核对。")
        elif (_step == 1) != (dec_dir > 0):
            warnings.append(
                f"大限行向自检未通过：按「阳男阴女顺行／阴男阳女逆行」，生年干{year_stem}、"
                f"性别{gender}应为{'顺行' if dec_dir > 0 else '逆行'}，"
                f"但引擎给出的限序是{'顺行' if _step == 1 else '逆行'}。"
            )
    decadal_palace_maps: list[dict[str, Any]] = []
    for d in decadal or []:
        soul_b = d.get("branch")
        if not soul_b:
            continue
        pmap = astro.palace_name_map(soul_b)
        bmap = astro.palace_branch_map(soul_b)
        landing: list[dict[str, Any]] = []
        for typ, star in zip(astro.MUTAGEN_NAMES, d.get("mutagen") or []):
            b = star_at.get(star)
            landing.append({
                "type": typ,
                "star": star,
                "branch": b,
                "natalPalace": (branch_palace.get(b) or {}).get("name") if b else None,
                "decadalPalace": bmap.get(b) if b else None,
            })
        entry = {
            "ageRange": d.get("ageRange"),
            "yearRange": d.get("yearRange"),
            "ganzhi": d.get("ganzhi"),
            "soulBranch": soul_b,
            "natalPalaceOfSoul": (branch_palace.get(soul_b) or {}).get("name"),
            "direction": dec_dir,
            "palaceMap": pmap,       # 宫名 → 地支
            "branchMap": bmap,       # 地支 → 宫名
            "mutagenLanding": landing,
        }
        decadal_palace_maps.append(entry)
        # 顺手挂回 decadal 条目，消费方不必再自己 join 两张表
        d["palaceMap"] = pmap
        d["branchMap"] = bmap
        d["natalPalaceOfSoul"] = entry["natalPalaceOfSoul"]
        d["mutagenLanding"] = landing

    version = eng.get("version", "?")
    return {
        "ok": True,
        "meta": {
            "engine": eng.get("engine", "iztro"),
            "engineVersion": version,
            # 生成时刻：报告抬头要用它印「报告生成：…」。
            # 旧版让渲染器去读合并包的 `meta.date`，而 `--chart/--analysis` 这条路
            # 传进去的 `meta` 是空的，于是抬头日期恒为空（第七轮脚本疑似失效 2）。
            "generatedAt": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "conventions": {
                "年柱口径": "农历年（正月初一分界）——紫微斗数取此，用于生年四化",
                "五行局": "由命宫宫干纳音定",
                "大限起法": "起运虚岁=五行局数；行向按「阳男阴女顺行／阴男阳女逆行」定"
                            "（现代软件通行口径；古籍卷二另有异说）。本盘行向见下表各行。",
                "四化表": "通行本（戊庚壬的「科」与中州派不同）",
                # 第七轮 MINOR ③：小限口径此前从未声明。读者看不到口径，就无法判断
                # 表里的小限落宫是照哪一套算的。
                "小限口径": "自生年支所属三合局的起宫起算，男顺女逆，一年一宫；"
                            "与「小限自命宫起」之说不同，属通行软件口径。"
                            "本盘逐年小限落宫见第十节。",
                "身宫口径": "身宫取本命命宫的对宫体系之外的另一套取法，本盘落在财帛宫；"
                            "各派对身宫是否另立作用时点说法不一，本报告的用法是只在具体大限与流年上引动它。",
                # ⚠️ 口径声明是**给读者看的事实陈述**，不能写成写给操作者的祈使句：
                # 第五轮新增的语言闸门（祈使式元指令）会拦住「不可混用」这类写法，
                # 而这段文字会进可读区（§一 排盘口径），所以在此改为陈述句。
                "提醒": "八字若按立春分界，年柱可能不同，四化随之改变；两套体系属不同口径，"
                        "各自的结论只在各自体系内成立",
            },
            "warnings": warnings,
        },
        "input": {
            "date": date_str, "calendar": calendar, "gender": gender,
            "time": time, "timeIndex": tst["time_index"], "timeName": tst["time_name"],
            "longitude": longitude,
        },
        "solarTime": tst,
        "basic": {
            **{k: a[k] for k in ("solarDate", "lunarDate", "chineseDate",
                                 "fiveElementsClass", "soul", "body",
                                 "soulPalaceBranch", "bodyPalaceBranch",
                                 "sign", "zodiac", "time", "timeRange")},
            "yearStem": year_stem, "yearBranch": year_branch,
            "sameSoulBody": a["soulPalaceBranch"] == a["bodyPalaceBranch"],
            "laiYinPalace": next(({"branch": p["branch"], "palace": p["name"]}
                                  for p in palaces if p["isLaiYin"]), None),
        },
        "palaces": palaces,
        "birthMutagen": birth_mut,
        "selfMutagen": self_mut,
        "surround": surround,
        "flank": flank,
        "palaceRelations": palace_relations,
        "pairingFacts": pair_facts,
        "decadalPalaceMaps": decadal_palace_maps,
        "flankKinds": {
            "left": astro.flanking_kinds(flank.get("left")),
            "right": astro.flanking_kinds(flank.get("right")),
        },
        "decadal": decadal,
        "horoscope": horoscope,
        "observations": observations,
        "patternCandidates": pattern_candidates,
        "selfCheck": {"problems": problems, "passed": not problems},
    }


def _branch_of(decadal_entry: dict[str, Any], palaces: list[dict[str, Any]]) -> str | None:
    return decadal_entry.get("branch")


def _observe(palaces, soul_branch, surround, flank, year_stem) -> dict[str, Any]:
    """
    纯事实观察（不含断语）：
      · 十四主星落宫与亮度
      · 六吉六煞分布
      · 亮度强弱落在哪些宫
      · 空宫清单
    AI 依据这些事实做解读，不需要自己数。
    """
    all14 = ["紫微", "天机", "太阳", "武曲", "天同", "廉贞",
             "天府", "太阴", "贪狼", "巨门", "天相", "天梁", "七杀", "破军"]
    fourteen, bright_strong, bright_weak = [], [], []
    soft, tough, empty = [], [], []

    for p in palaces:
        majors = p.get("major") or []
        if not majors:
            empty.append({"branch": p["branch"], "palace": p["name"]})
        for s in majors:
            if s["name"] in all14:
                fourteen.append({"star": s["name"], "branch": p["branch"],
                                 "palace": p["name"], "brightness": s.get("brightness")})
                if s.get("brightness") in POSITIVE_BRIGHTNESS:
                    bright_strong.append(f"{s['name']}[{s['brightness']}]@{p['branch']}·{p['name']}")
                elif s.get("brightness") in ("平", "不", "陷"):
                    bright_weak.append(f"{s['name']}[{s['brightness']}]@{p['branch']}·{p['name']}")
        for key in ("major", "minor"):
            for s in p.get(key) or []:
                if s["name"] in astro.SOFT6:
                    soft.append(f"{s['name']}@{p['branch']}·{p['name']}")
                if s["name"] in astro.TOUGH6:
                    tough.append(f"{s['name']}@{p['branch']}·{p['name']}")

    return {
        "fourteenComplete": len(fourteen),
        "fourteen": fourteen,
        "brightStrong": bright_strong,
        "brightWeak": bright_weak,
        "sixSoft": soft,
        "sixTough": tough,
        "emptyPalaces": empty,
        "soulPalace": soul_branch,
    }
