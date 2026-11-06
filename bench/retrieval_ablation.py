"""离线检索消融：在真实增长的记忆库快照上，比较三种检索策略的召回质量。

为什么离线：检索策略若也开三条在线臂，成本 ×3 且记忆库演化互不相同，无法控制变量。
这里用同一份真实写入的库（默认取 `write_fail` 臂），在每个快照点用后续 5 题做查询，
只替换排序函数，测 recall@3。

两级相关性（都只用于评估，Agent 运行时不可见）：
  - coarse: 条目来源类别 == 当前题主类别；
  - fine:   条目来源逻辑题 == 当前题逻辑题（同一个缺陷变体，表面重命名也算）。
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
from continual.retrieval import Retriever, embed, query_text
from continual.store import Lesson
from continual.textutil import infer_categories, jaccard, tokenize

BUCKETS = [(0, 10), (11, 25), (26, 50), (51, 90), (91, 10**9)]


class _Shim:
    def __init__(self, lessons):
        self.lessons = {l.id: l for l in lessons}

    def mark_injected(self, ids):
        pass


def build_query(task: dict, task_dir: Path) -> dict:
    src_ids = []
    src = task_dir / "workspace" / "src"
    for p in src.glob("*.py"):
        import ast
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        src_ids += [n.name for n in tree.body if hasattr(n, "name")]
    hint = " ".join(sorted(set(src_ids) | {p.stem for p in src.glob("*.py")} | {task.get("family", "")}))
    text = " ".join([task["prompt"], hint])
    return {
        "prompt": task["prompt"], "retrieval_hint": hint, "visible_failure": "",
        "src_identifiers": sorted(set(src_ids)),
        "observed_categories": sorted(infer_categories(text)),
        "id": task["id"],
    }


def relevance(lesson: Lesson, task: dict, logical: dict) -> tuple[bool, bool]:
    coarse = lesson.bug_category == task.get("bug_category")
    src_logical = logical.get(lesson.source_task, lesson.source_task)
    fine = src_logical == logical.get(task["id"], task["id"])
    return coarse, fine


def evaluate(lessons: list[dict], queries: list[dict], strategy: str) -> dict:
    injectable = [Lesson(**{k: v for k, v in d.items() if k in Lesson.__dataclass_fields__})
                  for d in lessons if d.get("status") in ("active", "provisional")]
    injectable = [l for l in injectable if not l.falsified]
    store = _Shim(injectable)
    r = Retriever(strategy, k=3)
    coarse_hits = fine_hits = 0
    elapsed = []
    for q in queries:
        task = q["task"]
        t0 = time.perf_counter()
        chosen, _ = r.retrieve(task, store)
        elapsed.append(time.perf_counter() - t0)
        for l in chosen:
            c, f = relevance(l, task, q["logical"])
            coarse_hits += int(c)
            fine_hits += int(f)
    return {
        "bank": len(injectable),
        "n_queries": len(queries),
        "coarse_recall": coarse_hits / max(1, len(queries) * 3),
        "fine_recall": fine_hits / max(1, len(queries) * 3),
        "ms": statistics.mean(elapsed) * 1000 if elapsed else 0.0,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", default=str(ROOT / "experiments" / "memory" / "phase1" / "write_fail_snapshots.jsonl"))
    ap.add_argument("--out", default=str(ROOT / "experiments" / "report" / "retrieval_ablation.csv"))
    ap.add_argument("--window", type=int, default=5)
    a = ap.parse_args()

    snaps = [json.loads(x) for x in Path(a.snapshots).read_text().splitlines() if x.strip()]
    manifest = {}
    logical = {}
    for line in (ROOT / "experiments" / "stream" / "tasks.jsonl").read_text().splitlines():
        r = json.loads(line)
        manifest[r["stream_index"]] = r
        t = json.loads((Path(r["dir"]) / "task.json").read_text())
        logical[r["task_id"]] = r["logical_id"]
        r["task"] = t
    rows = []
    for snap in snaps:
        idx = snap["stream_index"]
        queries = [{"task": build_query(manifest[j]["task"], Path(manifest[j]["dir"])),
                    "logical": logical} for j in range(idx + 1, idx + 1 + a.window) if j in manifest]
        if not queries:
            continue
        for strategy in ("category", "symptom", "embedding"):
            res = evaluate(snap["lessons"], queries, strategy)
            rows.append({"stream_index": idx, "strategy": strategy, **res})
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    # 分桶汇总
    lines = ["# 检索消融（同一真实记忆库快照）", "",
             "recall@3 的分母是 query×3 个空位；coarse=同 bug 类别，fine=同逻辑题。", "",
             "| 库规模 | 策略 | coarse recall@3 | fine recall@3 | 检索 ms |", "|---|---|---|---|---|"]
    for lo, hi in BUCKETS:
        for strategy in ("category", "symptom", "embedding"):
            sel = [r for r in rows if lo <= r["bank"] <= hi and r["strategy"] == strategy]
            if not sel:
                continue
            lines.append(f"| {lo}–{hi if hi < 10**9 else '∞'} | {strategy} | "
                         f"{statistics.mean(x['coarse_recall'] for x in sel):.3f} | "
                         f"{statistics.mean(x['fine_recall'] for x in sel):.3f} | "
                         f"{statistics.mean(x['ms'] for x in sel):.2f} |")
    Path(out).with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
