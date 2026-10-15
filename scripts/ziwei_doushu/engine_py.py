"""
ziwei_doushu/engine_py.py — x-iztro（Python）引擎实现

职责：把 `x-iztro` 的输出映射成与 `scripts/engine.js` **逐字段相同**的 `astrolabe` 结构，
使上层 `chart.py` 的归一化代码对两条引擎完全一视同仁、原样不动。

设计约束（照 `engine.js` 的契约来，不添油加醋）：
  · 不做任何安星重算 —— x-iztro 的 `to_json()` 自述输出 iztro 的每一个键、值相同，
    所以一律优先用它 + 属性访问；本文件里没有任何一条排盘公式。
  · 字段名与嵌套形状逐一对照 engine.js：palaces[] / decadal[] / horoscope[] 三张表的
    每一层键名、null 与空串的取舍、数组顺序，都必须一致。
  · x-iztro 没有的字段（`jieqiPillar`：立春口径四柱，现由 node 侧 lunar-lite 提供）
    **置 None 并记 note，绝不伪造**。

输入（与 engine.js 的 stdin 完全一致）:
  { "date": "1990-06-07", "calendar": "solar"|"lunar", "timeIndex": 4,
    "gender": "男", "years": [2026, 2027], "fixLeap": true }
输出:
  { ok: true, engine: "x-iztro", version, astrolabe: {...}, notes: [...] }
  或 { ok: false, error, message }
"""

from __future__ import annotations

import importlib
import json
from typing import Any

ENGINE_NAME = "x-iztro"

# ---------------------------------------------------------------- 模块导入

_MODULE: Any = None
_MODULE_TRIED = False

#: 除 site-packages 之外，还可能被解包到这些位置（受限环境的绕行安装）。
FALLBACK_DIRS: tuple[str, ...] = (".x-iztro", "vendor/x-iztro", ".deps/x-iztro")


def _fallback_paths() -> list[str]:
    """skill 根目录下的兜底导入路径（从 scripts/ziwei_doushu/ 上溯两级）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return [str(root / d) for d in FALLBACK_DIRS]


def load_module(force: bool = False) -> Any:
    """
    导入 `x_iztro`。成功返回模块，失败返回 None（**不抛异常**）。

    先走正常 import；失败再试 skill 内的兜底目录（供 wheel 解包安装的场景）。
    结果缓存，重复调用没有代价。
    """
    global _MODULE, _MODULE_TRIED
    if _MODULE is not None and not force:
        return _MODULE
    if _MODULE_TRIED and not force:
        return None
    _MODULE_TRIED = True

    try:
        _MODULE = importlib.import_module("x_iztro")
        return _MODULE
    except Exception:  # noqa: BLE001 — 缺依赖不是错误，是「这条引擎不可用」
        pass

    import sys

    for p in _fallback_paths():
        if p not in sys.path:
            sys.path.insert(0, p)
    try:
        _MODULE = importlib.import_module("x_iztro")
        return _MODULE
    except Exception:  # noqa: BLE001
        _MODULE = None
        return None


def module_version() -> str | None:
    """读 x_iztro.__version__，退回包元数据；都没有则 None。"""
    mod = load_module()
    if mod is None:
        return None
    v = getattr(mod, "__version__", None)
    if isinstance(v, str) and v:
        return v
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("x-iztro")
    except Exception:  # noqa: BLE001
        return None


def available() -> bool:
    return load_module() is not None


# ---------------------------------------------------------------- 映射工具
# 这一节逐行对照 engine.js 的 pick()/pickAll()：engine.js 用 `s.brightness || null`
# 把空串折叠成 null，Python 侧（x-iztro 的星对象也用空串表示「无」）必须同样处理。


def _s(v: Any) -> Any:
    """空串 → None（对齐 JS 的 `x || null`）；其余原样。"""
    if v is None:
        return None
    if isinstance(v, str) and v == "":
        return None
    return v


def _pick(star: Any) -> dict[str, Any]:
    return {
        "name": getattr(star, "name", None),
        "brightness": _s(getattr(star, "brightness", None)),
        "mutagen": _s(getattr(star, "mutagen", None)),
        "scope": _s(getattr(star, "scope", None)),
    }


def _pick_all(stars: Any) -> list[dict[str, Any]]:
    return [_pick(s) for s in (stars or [])]


# ---------------------------------------------------------------- 输入解析


def _gender_key(gender: str) -> str:
    """engine.js 收「男/女」；x-iztro 要 'male'/'female'。"""
    g = str(gender).strip()
    if g in ("男", "male", "Male", "M", "m"):
        return "male"
    if g in ("女", "female", "Female", "F", "f"):
        return "female"
    raise ValueError(f"性别无法识别：{gender!r}（应为 男 / 女）")


def _years(req: dict[str, Any]) -> list[int]:
    out: list[int] = []
    for y in req.get("years") or []:
        try:
            out.append(int(y))
        except (TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------- 具体字段


def _palaces(astrolabe: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in astrolabe.palaces:
        decadal = getattr(p, "decadal", None)
        out.append({
            "name": p.name,
            "stem": p.heavenly_stem,
            "branch": p.earthly_branch,
            "isBodyPalace": bool(p.is_body_palace),
            "major": _pick_all(getattr(p, "major_stars", None)),
            "minor": _pick_all(getattr(p, "minor_stars", None)),
            "adjective": [s.name for s in (getattr(p, "adjective_stars", None) or [])],
            "spirits": {
                "suiqian": _s(getattr(p, "suiqian12", None)),
                "jiangqian": _s(getattr(p, "jiangqian12", None)),
                "changsheng": _s(getattr(p, "changsheng12", None)),
                "boshi": _s(getattr(p, "boshi12", None)),
            },
            "decadalRange": list(decadal.range) if decadal is not None
                            and getattr(decadal, "range", None) is not None else None,
        })
    return out


def _decadal(astrolabe: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for x in astrolabe.decadal_list():
        mut = list(getattr(x, "mutagen", None) or [])
        out.append({
            "ageRange": list(x.age_range) if x.age_range is not None else None,
            "yearRange": list(x.year_range) if x.year_range is not None else None,
            "ganzhi": x.heavenly_stem + x.earthly_branch,
            "stem": x.heavenly_stem,
            "branch": x.earthly_branch,
            "palace": x.palace_name,
            "mutagen": mut[:4],
        })
    return out


def _flat_stars(groups: Any, names: Any) -> list[dict[str, Any]]:
    """
    对齐 engine.js 的 flat()：stars 是「按宫分组的嵌套数组」，按 palaceNames 索引展开；
    单颗星（非数组）也接受。
    """
    out: list[dict[str, Any]] = []
    for i, grp in enumerate(groups or []):
        palace_name = (names[i] if names and i < len(names) else "") or ""
        items = grp if isinstance(grp, (list, tuple)) else [grp]
        for s in items:
            if not s:
                continue
            out.append({
                "palace": palace_name,
                "name": getattr(s, "name", None),
                "brightness": _s(getattr(s, "brightness", None)),
                "mutagen": _s(getattr(s, "mutagen", None)),
                "scope": _s(getattr(s, "scope", None)),
            })
    return out


def _horoscope(astrolabe: Any, years: list[int], time_index: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for y in years:
        try:
            # engine.js 固定取该年 06-15 12:00 为观察点；这里同样的日期与时辰，
            # 保证两条引擎的流年数据落在同一天上。
            h = astrolabe.horoscope(target_date=f"{int(y)}-06-15",
                                    target_time_index=int(time_index))
        except Exception:  # noqa: BLE001 — 单年失败不拖垮整盘（对齐 engine.js continue）
            continue

        age = getattr(h, "age", None)
        dec = h.decadal
        year_item = h.yearly
        month_item = getattr(h, "monthly", None)

        out.append({
            "year": int(y),
            "nominalAge": getattr(age, "nominal_age", None) if age is not None else None,
            "soulLimit": ({
                "ganzhi": age.heavenly_stem + age.earthly_branch,
                "branch": age.earthly_branch,
            } if age is not None else None),
            "decadal": {
                "ganzhi": dec.heavenly_stem + dec.earthly_branch,
                "mutagen": list(getattr(dec, "mutagen", None) or [])[:4],
                "stars": _flat_stars(getattr(dec, "stars", None),
                                     getattr(dec, "palace_names", None)),
            },
            "yearly": {
                "ganzhi": year_item.heavenly_stem + year_item.earthly_branch,
                "mutagen": list(getattr(year_item, "mutagen", None) or [])[:4],
                "stars": _flat_stars(getattr(year_item, "stars", None),
                                     getattr(year_item, "palace_names", None)),
            },
            "monthly": ({
                "ganzhi": month_item.heavenly_stem + month_item.earthly_branch,
            } if month_item is not None else None),
        })
    return out


# ---------------------------------------------------------------- 入口


def run(req: dict[str, Any]) -> dict[str, Any]:
    """
    与 engine.js 同一契约。任何异常都折叠成 {ok:false, error, message}，不外抛。
    """
    mod = load_module()
    if mod is None:
        return {
            "ok": False,
            "error": "X_IZTRO_NOT_FOUND",
            "message": "未安装 x-iztro。请运行：python ziwei.py deps（或 pip install x-iztro）",
        }

    date = req.get("date")
    if not date:
        return {"ok": False, "error": "MISSING_DATE"}
    time_index = req.get("timeIndex")
    if time_index is None:
        return {"ok": False, "error": "MISSING_TIME_INDEX"}
    if not req.get("gender"):
        return {"ok": False, "error": "MISSING_GENDER"}

    calendar = req.get("calendar") or "solar"
    fix_leap = bool(req.get("fixLeap", True))
    try:
        gender_key = _gender_key(req["gender"])
        time_index = int(time_index)
    except (ValueError, TypeError) as e:
        return {"ok": False, "error": "BAD_INPUT", "message": str(e)}

    notes: list[str] = []

    try:
        factory = mod.astro.Astro()
        if calendar == "lunar":
            # 与 engine.js 的 astrolabeByLunarDate(date, tx, gender,
            #                          isLeapMonth=false, fixLeap, 'zh-CN') 对齐：
            # 闰月一律 false（要不要修闰由 fixLeap 决定，不让调用方另开一个开关）。
            astrolabe = factory.by_lunar(str(date), time_index, gender_key,
                                         is_leap_month=False, fix_leap=fix_leap,
                                         language="zh-CN")
        else:
            # ⚠️ 必须与 engine.js 第 74 行**逐字对齐**：
            #     astro.astrolabeBySolarDate(date, tx, gender, true, 'zh-CN')
            # solar 路把 fixLeap **写死为 true**（用 iztro 的默认值），请求里的
            # fixLeap 只作用于 lunar 路。曾把请求里的 fixLeap 透传到这里，
            # 结果「出生日期落在闰月内」的盘在两引擎间整盘平移一宫
            # （实测 2006-09-19 早子时女：node 命宫酉 / python 命宫申）。
            astrolabe = factory.by_solar(str(date), time_index, gender_key,
                                         fix_leap=True, language="zh-CN")
    except Exception as e:  # noqa: BLE001 — 引擎抛错也是「结果」，不是崩溃
        return {"ok": False, "error": "X_IZTRO_THREW", "message": str(e)}

    try:
        palaces = _palaces(astrolabe)
        decadal = _decadal(astrolabe)
        horoscope = _horoscope(astrolabe, _years(req), time_index)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "X_IZTRO_MAPPING_FAILED", "message": str(e)}

    # x-iztro 没有立春口径四柱（engine.js 由 lunar-lite 提供）。不伪造，如实置 None 并记 note。
    notes.append("jieqiPillar 需 node 引擎")

    return {
        "ok": True,
        "engine": ENGINE_NAME,
        "version": module_version() or "unknown",
        "astrolabe": {
            "solarDate": astrolabe.solar_date,
            "lunarDate": astrolabe.lunar_date,
            "chineseDate": astrolabe.chinese_date,
            "fiveElementsClass": astrolabe.five_elements_class,
            "soul": astrolabe.soul,
            "body": astrolabe.body,
            "soulPalaceBranch": astrolabe.earthly_branch_of_soul_palace,
            "bodyPalaceBranch": astrolabe.earthly_branch_of_body_palace,
            "sign": astrolabe.sign,
            "zodiac": astrolabe.zodiac,
            "time": astrolabe.time,
            "timeRange": astrolabe.time_range,
            "palaces": palaces,
            "decadal": decadal,
            "horoscope": horoscope,
            # 节气四柱（立春分界，八字口径）——仅供对照，紫微斗数不用它取四化
            "jieqiPillar": None,
        },
        "notes": notes,
    }


if __name__ == "__main__":  # 便于手工对照：读 stdin、写 stdout，与 engine.js 同形
    import sys

    raw = sys.stdin.read()
    try:
        _req = json.loads(raw or "{}")
    except json.JSONDecodeError as e:
        sys.stdout.write(json.dumps({"ok": False, "error": "BAD_JSON", "message": str(e)}))
    else:
        sys.stdout.write(json.dumps(run(_req), ensure_ascii=False))
