"""
ziwei_doushu/engines.py — 多引擎调度层（「有什么就用什么」）

本模块是排盘引擎的**唯一入口**：`chart.py` 与 `ziwei.py` 都只认这里的
`detect()` / `run()`，不再直接 spawn node、也不再直接 import x_iztro。

两条路，互不替换、互不删除：
  · python —— `x-iztro`（PyPI，MIT，cp310-abi3 wheel）。**auto 首选**：在同一进程里算，
    无子进程开销、不依赖 Node。
  · node   —— `iztro@2.6.2`（npm，MIT），由 `scripts/engine.js` 经 stdin/stdout 调用。
    历史上唯一的一条路，保留原样。

契约：`run(req)` 的入参与返回与 `engine.js` 完全一致 ——
  req:  {date, calendar, timeIndex, gender, years[], fixLeap}
  ret:  {ok:true, engine, version, astrolabe:{...}} + 可选的 notes[]
        {ok:false, error, message}
任何异常都折叠成 {ok:false, ...}，不把堆栈抛给调用方。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from . import engine_py

SKILL_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = Path(__file__).resolve().parents[1]
ENGINE_JS = SCRIPTS_DIR / "engine.js"
NODE_MODULES = SKILL_ROOT / "node_modules"

PY = "python"
NODE = "node"

#: 两条路都不可用时的安装指引（zh + en，直接可复制）。
INSTALL_HINT = (
    "未找到任何排盘引擎。任装其一即可（两条路只需一条）：\n"
    "  · Python 路：pip install x-iztro\n"
    "  · Node 路：安装 Node.js 18+ 后运行 python scripts/ziwei.py deps\n"
    "或者一步到位：python scripts/ziwei.py deps（会依次尝试 pip 与 npm）。"
)


# ---------------------------------------------------------------- 探测

def _node_exe() -> str | None:
    """复用 chart.py 的跨平台查找（含 DSH 自带 node 兜底位置）。"""
    from . import chart as chartmod  # 延迟导入：避免与 chart 的顶层 import 成环

    return chartmod.find_node()


def _iztro_version() -> str | None:
    pkg = NODE_MODULES / "iztro" / "package.json"
    if not pkg.exists():
        return None
    try:
        return json.loads(pkg.read_text(encoding="utf-8")).get("version")
    except Exception:  # noqa: BLE001
        return None


def _python_engine() -> dict[str, Any]:
    ok = engine_py.available()
    version = engine_py.module_version() if ok else None
    if ok:
        detail = f"x-iztro {version or '?'}（同进程内计算，无子进程）"
    else:
        detail = "未安装；可执行：pip install x-iztro"
    return {"name": "x-iztro", "kind": PY, "version": version, "available": ok, "detail": detail}


def _node_engine() -> dict[str, Any]:
    node = _node_exe()
    iztro = _iztro_version()
    ok = bool(node) and iztro is not None and ENGINE_JS.exists()
    if ok:
        detail = f"iztro {iztro} @ {node}（子进程调用 engine.js）"
    else:
        missing = []
        if not node:
            missing.append("未找到 node（装 Node.js 18+ 或设 ZIWEI_NODE）")
        if iztro is None:
            missing.append("未安装 iztro（pip/npm：python scripts/ziwei.py deps）")
        if not ENGINE_JS.exists():
            missing.append(f"引擎脚本缺失：{ENGINE_JS}")
        detail = "；".join(missing) or "不可用"
    return {"name": "iztro", "kind": NODE, "version": iztro, "available": ok, "detail": detail}


def detect() -> list[dict[str, Any]]:
    """
    探测可用引擎。顺序即 auto 的优先级（python 在前）。
    每项：{name, kind, version, available, detail}
    """
    return [_python_engine(), _node_engine()]


def pick(engine: str = "auto") -> dict[str, Any] | None:
    """按优先级选一条可用引擎；engine 为显式值时只在同名引擎里选。"""
    want = (engine or "auto").strip().lower()
    found = detect()
    if want == "auto":
        for e in found:
            if e["available"]:
                return e
        return None
    for e in found:
        if e["kind"] == want:
            return e if e["available"] else None
    return None


def active_name(engine: str = "auto") -> str | None:
    e = pick(engine)
    return None if e is None else f"{e['name']} ({e['kind']})"


def compare(engine: str = "auto") -> list[dict[str, Any]]:
    """
    探测两条路各自的可用性，供 `engines` 子命令打印。
    不参与计算，只做一次「import + 找文件」级别的判定。
    """
    return detect()


# ---------------------------------------------------------------- 调用

def _run_python(req: dict[str, Any]) -> dict[str, Any]:
    return engine_py.run(req)


def _run_node(req: dict[str, Any], timeout: int = 120) -> dict[str, Any]:
    import subprocess

    from . import chart as chartmod  # 延迟导入

    node = chartmod.find_node()
    if not node:
        return {"ok": False, "error": "NODE_NOT_FOUND",
                "message": "未找到 node。请安装 Node.js 18+，或设置环境变量 ZIWEI_NODE。"}
    if not ENGINE_JS.exists():
        return {"ok": False, "error": "ENGINE_MISSING", "message": f"引擎文件缺失：{ENGINE_JS}"}
    if _iztro_version() is None:
        return {"ok": False, "error": "IZTRO_NOT_FOUND",
                "message": "未安装 iztro。请先运行：python ziwei.py deps"}

    try:
        r = subprocess.run(
            [node, str(ENGINE_JS)],
            input=json.dumps(req),
            capture_output=True, text=True, encoding="utf-8",
            timeout=timeout, env=chartmod._env_with_node_path(), cwd=str(SKILL_ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ENGINE_TIMEOUT", "message": f"引擎调用超时（{timeout}s）"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "ENGINE_SPAWN_FAILED", "message": f"{type(e).__name__}: {e}"}

    out = (r.stdout or "").strip()
    if not out:
        return {"ok": False, "error": "ENGINE_NO_OUTPUT", "message": "引擎没有输出。",
                "stderr": (r.stderr or "")[-1200:], "returncode": r.returncode}
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": "ENGINE_BAD_JSON", "message": f"引擎输出无法解析：{e}",
                "stdout": out[:800], "stderr": (r.stderr or "")[-800:]}


def run(req: dict[str, Any], engine: str = "auto", timeout: int = 120) -> dict[str, Any]:
    """
    排一张盘。`req` 与 engine.js 的 stdin 完全一致，返回结构完全一致。

    engine:
      "auto"   —— 先 python（x-iztro），再 node（iztro）；都没有则 NO_ENGINE
      "python" —— 只走 x-iztro，不可用则 ENGINE_NOT_AVAILABLE
      "node"   —— 只走 iztro，不可用则 ENGINE_NOT_AVAILABLE
    """
    want = (engine or "auto").strip().lower()
    if want not in ("auto", PY, NODE):
        return {"ok": False, "error": "BAD_ENGINE",
                "message": f"未知引擎 {engine!r}（可选 auto / python / node）"}

    try:
        if want == "auto":
            for cand in detect():
                if not cand["available"]:
                    continue
                res = _run_python(req) if cand["kind"] == PY else _run_node(req, timeout)
                # auto 下若首选引擎自己失败（如 x-iztro 抛错），退回另一条路，
                # 但把首选引擎的失败原因记进 notes —— 不静默换引擎。
                if res.get("ok"):
                    return res
                fallback = _fallback(req, timeout, skip=cand["kind"])
                if fallback is not None:
                    notes = list(fallback.get("notes") or [])
                    notes.append(f"auto：{cand['name']} 失败（{res.get('error')}），已改用 "
                                 f"{fallback.get('engine')}")
                    fallback["notes"] = notes
                    return fallback
                return res
            return {"ok": False, "error": "NO_ENGINE", "message": INSTALL_HINT}

        cand = pick(want)
        if cand is None:
            info = next((e for e in detect() if e["kind"] == want), None)
            out: dict[str, Any] = {
                "ok": False, "error": "ENGINE_NOT_AVAILABLE",
                "message": f"引擎 {want} 不可用：{info['detail'] if info else '未探测到'}",
            }
            # 只有「另一条路也没有」时才贴全量安装指引（否则容易读成「什么都装不上」）。
            if not any(e["available"] for e in detect()):
                out["hint"] = INSTALL_HINT
            return out
        return _run_python(req) if cand["kind"] == PY else _run_node(req, timeout)
    except Exception as e:  # noqa: BLE001 — 兜底：任何异常都变成结果，不外抛
        return {"ok": False, "error": "ENGINE_DISPATCH_FAILED",
                "message": f"{type(e).__name__}: {e}"}


def _fallback(req: dict[str, Any], timeout: int, skip: str) -> dict[str, Any] | None:
    """auto 模式下的另一条路（skip 为已失败的那条）。都没有则 None。"""
    for cand in detect():
        if cand["kind"] == skip or not cand["available"]:
            continue
        res = _run_python(req) if cand["kind"] == PY else _run_node(req, timeout)
        if res.get("ok"):
            return res
    return None


__all__ = ["detect", "pick", "run", "active_name", "compare",
           "INSTALL_HINT", "PY", "NODE", "ENGINE_JS", "NODE_MODULES"]
