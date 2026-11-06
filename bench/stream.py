"""连续任务流生成器。

任务流 = 120 个逻辑任务 × 3 轮暴露，共 360 题：
  - 轮 0 `c0`：数据集原始任务（模块名/函数名原样）；
  - 轮 1 `c1`：同一逻辑任务，模块名与全部函数/类名加 `_x1` 后缀（表面改写，缺陷结构不变）；
  - 轮 2 `c2`：同上，加 `_x2` 后缀。

为什么这样构造（必须在报告里如实写明）：
  轮 1/2 与轮 0 共享缺陷结构与隐藏测试，只是标识符表面变化。它们**不是新增独立信息**，
  而是用来测"记忆是否依赖具体函数名"以及"重复暴露后的饱和/衰减"。独立变体数仍是 65。

用法：
  python -m bench.stream --build          # 生成 tasks.jsonl 与轮 1/2 的任务目录
  python -m bench.stream --build --verify # 额外跑 buggy 必失败 / reference 必通过
"""
from __future__ import annotations

import argparse
import ast
import json
import random
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "dataset" / "tasks"
STREAM_DIR = ROOT / "experiments" / "stream"
LOGICAL_PER_CATEGORY = 15
CYCLES = (0, 1, 2)
CATEGORIES = [
    "boundary_compare", "paging_index", "reduce_init", "mutable_default",
    "cache_state", "parsing_robustness", "recursion_base", "type_conversion",
]
ORDER = ["c0", "c1", "c2"]


def _identifiers(src: str) -> list[str]:
    """只重命名模块级函数/类；类方法不改名（避免 pop/append 这类与内置类型方法撞名）。"""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            and not (n.name.startswith("__") and n.name.endswith("__"))]


def _rename(text: str, mapping: dict[str, str]) -> str:
    for old, new in sorted(mapping.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<![\w]){re.escape(old)}(?![\w])", new, text)
    return text


def _build_cycle_task(orig_dir: Path, cycle: int, out_dir: Path) -> dict:
    meta = json.loads((orig_dir / "task.json").read_text())
    src_files = sorted(orig_dir.glob("workspace/src/*.py"))
    mapping: dict[str, str] = {}
    rename_file: dict[str, str] = {}
    for p in src_files:
        module = p.stem
        mapping[module] = f"{module}_x{cycle}"
        rename_file[f"src/{module}.py"] = f"src/{module}_x{cycle}.py"
        for ident in _identifiers(p.read_text()):
            mapping[ident] = f"{ident}_x{cycle}"
    sources = {rename_file[f"src/{p.stem}.py"]: _rename(p.read_text(), mapping) for p in src_files}
    visible = _rename((orig_dir / "workspace/tests/test_visible.py").read_text(), mapping)
    hidden = _rename(meta["hidden_tests"], mapping)
    prompt = _rename(meta["prompt"], mapping)
    reference = {rename_file.get(rel, rel): _rename(content, mapping) for rel, content in meta["reference"].items()}

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "workspace/src").mkdir(parents=True, exist_ok=True)
    (out_dir / "workspace/tests").mkdir(parents=True, exist_ok=True)
    for name, content in sources.items():
        (out_dir / "workspace" / name).write_text(content)
    (out_dir / "workspace/tests/test_visible.py").write_text(visible)
    new_meta = dict(meta, id=f"c{cycle}-{meta['id']}", logical_id=meta["id"], cycle=cycle,
                    prompt=prompt, hidden_tests=hidden, reference=reference,
                    _dir=str(out_dir))
    (out_dir / "task.json").write_text(json.dumps(new_meta, ensure_ascii=False, indent=2))
    return new_meta


def select_logical(seed: int = 11, per_category: int = LOGICAL_PER_CATEGORY) -> list[dict]:
    """每个主类别取 per_category 个任务（先单缺陷后组合），再按类别轮转交错。"""
    all_tasks = []
    for d in sorted(DATA_DIR.iterdir()):
        f = d / "task.json"
        if f.exists():
            meta = json.loads(f.read_text())
            meta["_dir"] = str(d)
            all_tasks.append(meta)
    rng = random.Random(seed)
    per_cat: dict[str, list[dict]] = {}
    for cat in CATEGORIES:
        pool = [t for t in all_tasks if t.get("bug_category") == cat]
        pool.sort(key=lambda t: (t["kind"] != "single", t["id"]))
        pool = pool[: max(per_category * 2, per_category)]
        rng.shuffle(pool)
        per_cat[cat] = pool[:per_category]
    out = []
    for r in range(per_category):
        for cat in CATEGORIES:
            out.append(per_cat[cat][r])
    return out


def build(seed: int = 11, verify: bool = False) -> list[dict]:
    STREAM_DIR.mkdir(parents=True, exist_ok=True)
    base = select_logical(seed)
    rows: list[dict] = []
    for cycle in CYCLES:
        for idx, meta in enumerate(base):
            out_dir = Path(meta["_dir"]) if cycle == 0 else STREAM_DIR / f"c{cycle}" / meta["id"]
            if cycle == 0:
                task = dict(meta)
            else:
                if out_dir.exists():
                    shutil.rmtree(out_dir)
                task = _build_cycle_task(Path(meta["_dir"]), cycle, out_dir)
            rows.append({
                "stream_index": len(rows),
                "task_id": task["id"],
                "logical_id": task.get("logical_id", task["id"]),
                "cycle": cycle,
                "dir": task["_dir"],
                "primary_category": task.get("bug_category", ""),
                "family": task.get("family", ""),
                "kind": task.get("kind", ""),
            })
    manifest = STREAM_DIR / "tasks.jsonl"
    manifest.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    print(f"任务流: {len(rows)} 题 -> {manifest}")
    print(f"  轮 0: {sum(1 for r in rows if r['cycle'] == 0)}，轮 1/2: "
          f"{sum(1 for r in rows if r['cycle'] > 0)}（共享缺陷结构，非独立变体）")
    if verify:
        failed = verify_stream(rows)
        if failed:
            print(f"自检失败 {failed} 个", file=sys.stderr)
    return rows


def verify_stream(rows: list[dict], limit: int = 0) -> int:
    """每个任务：buggy 原始工作区必须判失败，写入 reference 后必须判通过。"""
    import tempfile
    sys.path.insert(0, str(ROOT))
    from bench.judge import judge
    bad = 0
    checked = rows[:limit] if limit else rows
    for i, row in enumerate(checked):
        task = json.loads((Path(row["dir"]) / "task.json").read_text())
        task["_dir"] = row["dir"]
        ok_broken, detail = judge(task, Path(row["dir"]) / "workspace")
        if ok_broken:
            print(f"[FAIL] {task['id']} 缺陷版居然通过: {detail}", file=sys.stderr)
            bad += 1
            continue
        with tempfile.TemporaryDirectory(prefix="ref-") as td:
            ws = Path(td) / "ws"
            shutil.copytree(Path(row["dir"]) / "workspace", ws)
            for rel, content in task["reference"].items():
                (ws / rel).parent.mkdir(parents=True, exist_ok=True)
                (ws / rel).write_text(content)
            ok_ref, detail = judge(task, ws)
        if not ok_ref:
            print(f"[FAIL] {task['id']} 参考解不通过: {detail}", file=sys.stderr)
            bad += 1
        elif i % 30 == 0:
            print(f"  {i + 1}/{len(checked)} ok", flush=True)
    print(f"自检完成: {len(checked)} 题，异常 {bad}")
    return bad


def load_stream() -> list[dict]:
    manifest = STREAM_DIR / "tasks.jsonl"
    if not manifest.exists():
        return build()
    rows = [json.loads(x) for x in manifest.read_text().splitlines() if x.strip()]
    for r in rows:
        f = Path(r["dir"]) / "task.json"
        if not f.exists():
            raise FileNotFoundError(f"任务目录缺失: {r['dir']}，先 python -m bench.stream --build")
        meta = json.loads(f.read_text())
        meta["_dir"] = r["dir"]
        r["task"] = meta
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--verify-limit", type=int, default=0)
    ap.add_argument("--seed", type=int, default=11)
    a = ap.parse_args()
    rows = build(seed=a.seed)
    if a.verify:
        verify_stream(rows, limit=a.verify_limit)
