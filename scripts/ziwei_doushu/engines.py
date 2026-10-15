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


def _lunar_ts_version() -> str | None:
    """`lunar-typescript`（历法库）的版本 —— 闰月查表用它，不用排盘引擎。"""
    pkg = NODE_MODULES / "lunar-typescript" / "package.json"
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


def _run_node(req: dict[str, Any], timeout: int = 120, need_iztro: bool = True) -> dict[str, Any]:
    import subprocess

    from . import chart as chartmod  # 延迟导入

    node = chartmod.find_node()
    if not node:
        return {"ok": False, "error": "NODE_NOT_FOUND",
                "message": "未找到 node。请安装 Node.js 18+，或设置环境变量 ZIWEI_NODE。"}
    if not ENGINE_JS.exists():
        return {"ok": False, "error": "ENGINE_MISSING", "message": f"引擎文件缺失：{ENGINE_JS}"}
    # 排盘才需要 iztro；历法查表模式（need_iztro=False）只用 lunar-typescript。
    if need_iztro and _iztro_version() is None:
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


def _node_batch_chunk(part: list[dict[str, Any]], out_path: str, timeout: int) -> list[dict[str, Any]] | None:
    """一个分片的 node 批量排盘；结果落 `out_path` 再读回（不挤管道）。"""
    res = _run_node({"mode": "batch", "requests": part, "out": out_path}, timeout=timeout)
    if not res.get("ok"):
        return None
    try:
        payload = json.loads(Path(out_path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != len(part):
        return None
    return [r if isinstance(r, dict) else {"ok": False, "error": "ENGINE_BAD_RESULT"}
            for r in results]


def run_many(reqs: list[dict[str, Any]], timeout: int = 600,
             chunk: int = 32, workers: int = 0) -> list[dict[str, Any]] | None:
    """
    一次（或几个并行的）node 子进程排多张盘（`engine.js` 的 `mode: "batch"`）。

    语义与逐张 `run(req, engine="node")` **逐个同形同序**（完整 astrolabe，无裁剪），
    只是省掉了每张盘的进程启动开销（实测 ~0.23s/次；闰月穷举一次要排上百张，
    启动开销比计算本身还大）。结果经临时文件回流，不走管道 ——
    96 张盘的完整 JSON 约 800KB，挤管道既慢又容易顶到系统管道缓冲。
    分片之间用线程池并行（排盘是纯 CPU 的子进程，不共享状态）：
    单进程排 96 张约 4.5s，4 片并行约 1.5s。顺序按分片序号拼回，与 `reqs` 一一对应。

    返回与 `reqs` 等长的结果列表；node 不可用 / iztro 缺失 / 任一分片失败时返回 None，
    由调用方**逐张退回** `run(..., engine="node")`（不因为批量失败就弄丢对拍）。
    `chunk` / `workers` 只影响吞吐，对结果无影响（`workers=0` 自动按 CPU 数，上限 4）。
    """
    import concurrent.futures as _cf
    import os
    import tempfile

    if not reqs:
        return []
    node = _node_exe()
    if node is None or not ENGINE_JS.exists() or _iztro_version() is None:
        return None

    size = max(1, int(chunk or len(reqs)))
    parts: list[list[dict[str, Any]]] = [list(reqs[i:i + size])
                                         for i in range(0, len(reqs), size)]
    n_workers = int(workers) if workers else 0
    if not n_workers:
        env = os.environ.get("ZIWEI_BATCH_WORKERS", "").strip()
        try:
            n_workers = int(env) if env else min(4, max(1, (os.cpu_count() or 1)))
        except ValueError:
            n_workers = 1
    n_workers = max(1, min(n_workers, len(parts)))

    with tempfile.TemporaryDirectory(prefix="ziwei-batch-") as tmpdir:
        paths = [os.path.join(tmpdir, f"batch-{i}.json") for i in range(len(parts))]
        if n_workers == 1 or len(parts) == 1:
            chunks = [_node_batch_chunk(p, path, timeout)
                      for p, path in zip(parts, paths)]
        else:
            with _cf.ThreadPoolExecutor(max_workers=n_workers) as ex:
                chunks = list(ex.map(lambda pp: _node_batch_chunk(pp[0], pp[1], timeout),
                                     list(zip(parts, paths))))
    if any(c is None for c in chunks):
        return None
    out: list[dict[str, Any]] = []
    for c in chunks:
        out.extend(c)  # type: ignore[arg-type]
    return out


# ---------------------------------------------------------------- 历法查表

def leap_months(lo: int, hi: int, timeout: int = 120) -> list[dict[str, Any]] | None:
    """
    闰月**查表**（不是扫描）：一次子进程把 `lo`-`hi` 年段的所有闰月问出来。

    走 `engine.js` 的 `mode: "leap_months"`，由 `lunar-typescript` 的
    `LunarYear.fromYear(y).getLeapMonth()` / `Lunar.fromYmd(y, -m, d)` 回答；
    本模块**不做任何历法推导**，只搬运。

    返回（按年升序）：
      [{lunarYear, leapMonth, lengthDays,
        picks: [{lunarDay, solarDate, lunarDate}]}]   # picks = 第 1 / 中间 / 最后一天
    以下情况返回 None（调用方负责报「闰月穷举需要 node 引擎」）：
      · node 不可用 / engine.js 缺失
      · lunar-typescript 缺失（引擎回 LUNAR_LIB_NOT_FOUND）
      · 引擎调用失败或返回结构不对
    返回 [] 与 None 的差别：[] = 问到了，这些年确实没有闰月。
    """
    try:
        lo_i, hi_i = int(lo), int(hi)
    except (TypeError, ValueError):
        return None
    if lo_i > hi_i:
        lo_i, hi_i = hi_i, lo_i
    if _node_exe() is None or not ENGINE_JS.exists() or _lunar_ts_version() is None:
        return None

    res = _run_node({"mode": "leap_months", "years": [lo_i, hi_i]},
                    timeout=timeout, need_iztro=False)
    if not res.get("ok"):
        return None
    months = res.get("leapMonths")
    if not isinstance(months, list):
        return None
    out: list[dict[str, Any]] = []
    for m in months:
        if not isinstance(m, dict):
            return None
        out.append({
            "lunarYear": m.get("lunarYear"),
            "leapMonth": m.get("leapMonth"),
            "lengthDays": m.get("lengthDays"),
            "picks": [dict(p) for p in (m.get("picks") or []) if isinstance(p, dict)],
            "warnings": list(res.get("warnings") or []),
        })
    return out


__all__ = ["detect", "pick", "run", "run_many", "active_name", "compare", "leap_months",
           "INSTALL_HINT", "PY", "NODE", "ENGINE_JS", "NODE_MODULES"]
