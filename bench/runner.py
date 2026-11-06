"""连续任务流 runner：每个 arm 独立记忆库，严格按 manifest 顺序在线学习。

臂之间可并行（各自独立），臂内必须串行——记忆是顺序依赖的，不能并发打乱。
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.config import Budget, LLMConfig, cost_mode
from agent.llm import LLMClient
from agent.loop import ReactAgent, run_with_retry
from agent.tools import Toolbox
from continual.memory import ContinualMemory
from continual.textutil import infer_categories

from bench.judge import judge
from bench.stream import load_stream

FIELDS = [
    "arm", "stream_index", "task_id", "logical_id", "cycle", "primary_category", "kind",
    "success", "judge_detail", "failure_mode", "steps", "calls", "tool_calls", "plan_calls",
    "reflect_calls", "prompt_tokens", "completion_tokens", "reflect_tokens", "total_tokens",
    "cached_tokens", "latency_s", "retrieval_ms", "rollback_used", "test_runs",
    "memory_injected", "memory_injected_count", "memory_before", "memory_after",
    "memory_bytes", "memory_falsified", "lesson_written", "lesson_status", "policy_note", "error",
]
SNAPSHOT_EVERY = 10


def src_identifiers(ws: Path) -> list[str]:
    import ast
    ids: list[str] = []
    for p in (ws / "src").glob("*.py"):
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                ids.append(node.name)
    return sorted(set(ids))


def retrieval_hint(task: dict, ws: Path) -> str:
    files = [p.stem for p in (ws / "src").glob("*.py")]
    visible = (ws / "tests" / "test_visible.py").read_text()
    funcs = [w.replace("test_", "") for w in visible.split() if "test_" in w]
    return " ".join(sorted(set(files) | set(funcs) | set(src_identifiers(ws)) | {task.get("family", "")}))


def visible_failure(ws: Path) -> str:
    import os
    import subprocess
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ws / "src")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests/test_visible.py"],
        cwd=str(ws), capture_output=True, text=True, env=env,
    )
    return (proc.stdout + "\n" + proc.stderr)[:1200]


def prepare_task(row: dict, ws: Path) -> dict:
    task = dict(row["task"])
    task["_dir"] = row["dir"]
    task["retrieval_hint"] = retrieval_hint(task, ws)
    task["src_identifiers"] = src_identifiers(ws)
    task["visible_failure"] = visible_failure(ws)
    task["observed_categories"] = sorted(infer_categories(
        " ".join([task["prompt"], task["retrieval_hint"], task["visible_failure"]])))
    task["cycle"] = row["cycle"]
    return task


def write_row(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(row)


def snapshot(arm: str, index: int, store, mem_dir: str) -> None:
    path = Path(mem_dir) / f"{arm}_snapshots.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps({"stream_index": index, "lessons": store.dump()}, ensure_ascii=False) + "\n")


def run_arm(cfg: LLMConfig, arm: str, rows: list[dict], args) -> str:
    policy = None if arm == "no_memory" else arm
    mode = cost_mode("baseline")
    work_root = Path(args.work_dir) / arm
    trace_root = Path(args.trace_dir) / arm
    csv_path = Path(args.csv_dir) / f"{arm}.csv"
    mem_path = Path(args.memory_dir) / f"{arm}.jsonl"
    if args.reset:
        for p in (mem_path, Path(args.memory_dir) / f"{arm}_snapshots.jsonl", csv_path):
            if p.exists():
                p.unlink()

    memory = ContinualMemory(str(mem_path), policy=policy, strategy=args.strategy,
                             topk=args.topk, max_chars=args.max_chars) if policy else None
    done = set()
    if csv_path.exists():
        with open(csv_path) as f:
            done = {r["task_id"] for r in csv.DictReader(f)}

    llm = LLMClient(cfg)
    consecutive_errors = 0
    for i, row in enumerate(rows, 1):
        if row["task_id"] in done:
            continue
        ws = work_root / row["task_id"]
        ckpt = work_root / f"_ckpt_{row['task_id']}"
        for p in (ws, ckpt):
            if p.exists():
                shutil.rmtree(p)
        shutil.copytree(Path(row["dir"]) / "workspace", ws)
        tb = Toolbox(ws, cost=mode, tests_readonly=True, ckpt_dir=ckpt)
        agent = ReactAgent(llm, tb, memory=memory, cost=mode, budget=Budget(
            max_steps=args.max_steps, wall_timeout_s=args.wall_timeout, max_total_tokens=args.max_tokens,
        ), trace_dir=trace_root)
        task = prepare_task(row, ws)
        before = memory.state() if memory else {"n_total": 0, "n_falsified": 0, "bytes": 0}
        t0 = time.time()
        res = run_with_retry(agent, task, lambda: judge(task, ws), arm, trace_root / f"{row['task_id']}.txt")
        after = memory.state() if memory else {"n_total": 0, "n_falsified": 0, "bytes": 0}
        if after["n_total"] > before["n_total"] and not res.lesson_written:
            print(f"[{arm}] 警告: 库 {before['n_total']}->{after['n_total']} 但无 lesson_written", flush=True)
        out = {
            "arm": arm, "stream_index": row["stream_index"], "task_id": row["task_id"],
            "logical_id": row["logical_id"], "cycle": row["cycle"],
            "primary_category": row["primary_category"], "kind": row["kind"],
            "success": int(res.success), "judge_detail": res.judge_detail,
            "failure_mode": res.failure_mode or "", "steps": res.steps, "calls": res.calls,
            "tool_calls": res.tool_calls, "plan_calls": res.plan_calls,
            "reflect_calls": res.reflect_calls, "prompt_tokens": res.prompt_tokens,
            "completion_tokens": res.completion_tokens,
            "reflect_tokens": res.reflect_prompt_tokens + res.reflect_completion_tokens,
            "total_tokens": res.all_tokens, "cached_tokens": res.cached_tokens,
            "latency_s": res.latency_s, "retrieval_ms": res.retrieval_ms,
            "rollback_used": res.rollback_used, "test_runs": res.test_runs,
            "memory_injected": "|".join(res.memory_injected),
            "memory_injected_count": res.memory_injected_count,
            "memory_before": before["n_total"], "memory_after": after["n_total"],
            "memory_bytes": after["bytes"], "memory_falsified": after["n_falsified"],
            "lesson_written": res.lesson_written, "lesson_status": res.lesson_status,
            "policy_note": res.policy_note, "error": res.error,
        }
        write_row(csv_path, out)
        status = "PASS" if res.success else f"FAIL({res.failure_mode})"
        print(f"[{arm}] {i}/{len(rows)} #{row['stream_index']} {row['task_id']} {status} "
              f"inj={res.memory_injected_count} mem={after['n_total']} tok={res.all_tokens} "
              f"{round(time.time() - t0, 1)}s", flush=True)
        if memory is not None and (i % SNAPSHOT_EVERY == 0):
            snapshot(arm, row["stream_index"], memory.store, args.memory_dir)
        if res.failure_mode == "llm_error":
            consecutive_errors += 1
            if consecutive_errors >= 3:
                print(f"[{arm}] 连续 3 次 LLM 故障，中止", flush=True)
                break
        else:
            consecutive_errors = 0
        shutil.rmtree(ws, ignore_errors=True)
        shutil.rmtree(ckpt, ignore_errors=True)
    if memory is not None:
        snapshot(arm, rows[-1]["stream_index"], memory.store, args.memory_dir)
        print(f"[{arm}] 结束: {memory.state()}", flush=True)
    return arm


def _arm_job(payload):
    cfg_kwargs, arm, rows, args = payload
    return run_arm(LLMConfig(**cfg_kwargs), arm, rows, args)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="no_memory,write_fail,write_verified")
    ap.add_argument("--limit", type=int, default=0, help="只跑任务流前 N 题")
    ap.add_argument("--start", type=int, default=0, help="跳过前 N 题（分阶段续跑用）")
    ap.add_argument("--strategy", default="category", choices=["category", "symptom", "embedding"])
    ap.add_argument("--topk", type=int, default=3)
    ap.add_argument("--max-chars", type=int, default=900)
    ap.add_argument("--max-steps", type=int, default=12)
    ap.add_argument("--wall-timeout", type=int, default=240)
    ap.add_argument("--max-tokens", type=int, default=90000)
    ap.add_argument("--csv-dir", default=str(ROOT / "experiments" / "runs"))
    ap.add_argument("--memory-dir", default=str(ROOT / "experiments" / "memory"))
    ap.add_argument("--work-dir", default=str(ROOT / "experiments" / "work"))
    ap.add_argument("--trace-dir", default=str(ROOT / "experiments" / "traces"))
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--label", default="", help="实验标签，写入 runs/<label>/")
    args = ap.parse_args()

    if args.label:
        args.csv_dir = str(Path(args.csv_dir) / args.label)
        args.memory_dir = str(Path(args.memory_dir) / args.label)
    rows = load_stream()
    if args.start:
        rows = rows[args.start:]
    if args.limit:
        rows = rows[: args.limit]
    if not rows:
        print("任务流为空", file=sys.stderr)
        return 1

    cfg = LLMConfig()
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    print(f"模型={cfg.label()} 任务={len(rows)} 臂={arms} 检索={args.strategy}", flush=True)
    payloads = [(dict(base_url=cfg.base_url, api_key=cfg.api_key, model=cfg.model,
                      provider=cfg.provider, temperature=cfg.temperature, timeout_s=cfg.timeout_s),
                 arm, rows, args) for arm in arms]
    if len(arms) == 1 or args.parallel == 1:
        for p in payloads:
            _arm_job(p)
    else:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=min(args.parallel, len(arms))) as ex:
            list(ex.map(_arm_job, payloads))
    print("完成 ->", args.csv_dir, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
