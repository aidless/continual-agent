"""ReAct 循环：思考 -> 工具 -> 观察 -> 再思考。

包含：最大步数、墙钟超时、token 预算、死循环检测、重复动作检测、自动回滚、
子目标分解、按开关启用上下文裁剪与早停。
"""
from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from .config import Budget, CostMode
from .llm import LLMClient, LLMError, Usage
from .tools import TOOL_SPECS, Toolbox

DEBUG_GUARD = os.environ.get("AGENT_DEBUG_GUARD", "") == "1"

SYSTEM_TEMPLATE = """你是修复 Python 小型仓库缺陷的工程 Agent。
工作区: {ws}
规则:
- 只能读写工作区内的文件；tests/ 与 test_*.py 只读，必须修源码而不是改测试。
- 隐藏测试覆盖边界情况，只让可见测试通过不算修好，要修根因。
- 每次只输出一个 JSON 对象，不要输出任何其他文字。
- 格式: {{"thought":"简要推理","action":"工具名","args":{{...}}}}
- 完成: {{"thought":"...","action":"finish","args":{{"summary":"改了什么"}}}}

{specs}
子目标（规划器给出，可调整）:
{subgoals}
{memory}
"""

PLAN_PROMPT = """把下面这个修 bug 任务拆成 2-4 个可验证的子目标，只输出 JSON：
{{"subgoals":["1. ...","2. ..."]}}
任务: {prompt}
工作区文件: {files}
"""

@dataclass
class EpisodeResult:
    task_id: str
    arm: str
    success: bool = False
    judge_detail: str = ""
    steps: int = 0
    calls: int = 0
    tool_calls: int = 0
    plan_calls: int = 0
    reflect_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reflect_prompt_tokens: int = 0
    reflect_completion_tokens: int = 0
    cached_tokens: int = 0
    latency_s: float = 0.0
    failure_mode: str = ""
    rollback_used: int = 0
    rollback_verified: bool = False
    rollback_checks: int = 0
    test_runs: int = 0
    memory_injected: list[str] = field(default_factory=list)
    memory_injected_count: int = 0
    subgoals: list[str] = field(default_factory=list)
    error: str = ""
    lesson_written: str = ""
    lesson_status: str = ""
    policy_note: str = ""
    retrieval_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def all_tokens(self) -> int:
        return self.total_tokens + self.reflect_prompt_tokens + self.reflect_completion_tokens


def extract_action(text: str) -> dict | None:
    """从模型输出里抽取第一个含 action 的平衡 JSON 对象。"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```")[1] if "```" in text[3:] else text[3:]
        text = text.lstrip("json")
    candidates = []
    depth = 0
    start = None
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : i + 1])
                start = None
    for c in candidates:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "action" in obj:
            return obj
    return None


def extract_json_any(text: str) -> dict | None:
    """抽取第一个可解析的平衡 JSON 对象（不要求含 action 字段，用于反思结果）。"""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1] if text.count("```") >= 2 else text
        text = text.lstrip("json").lstrip()
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    obj = json.loads(text[start: i + 1])
                except json.JSONDecodeError:
                    start = None
                    continue
                if isinstance(obj, dict):
                    return obj
                start = None
    return None


class ReactAgent:
    def __init__(
        self,
        llm: LLMClient,
        toolbox: Toolbox,
        memory=None,
        cost: CostMode | None = None,
        budget: Budget | None = None,
        trace_dir: Path | None = None,
    ):
        self.llm = llm
        self.toolbox = toolbox
        self.memory = memory
        self.cost = cost or CostMode()
        self.budget = budget or Budget()
        self.trace_dir = Path(trace_dir) if trace_dir else None
        self.usage = Usage()

    # ---------- prompt ----------
    def _plan(self, task: dict) -> list[str]:
        n_src = len([p for p in (self.toolbox.root / "src").glob("*.py")]) if (self.toolbox.root / "src").exists() else 0
        if self.cost.planner_skip_max_files and n_src <= self.cost.planner_skip_max_files:
            return ["直接定位失败测试对应的源码，修复后跑全量测试"]
        files = ", ".join(sorted(p.relative_to(self.toolbox.root).as_posix() for p in (self.toolbox.root / "src").glob("*.py")))
        try:
            text, u = self.llm.chat(
                [{"role": "user", "content": PLAN_PROMPT.format(prompt=task["prompt"], files=files)}],
                max_tokens=200,
            )
            self.usage.add(u)
            obj = extract_action(text) or {}
            subs = obj.get("subgoals")
            if not subs:
                m = json.loads(text[text.find("{"): text.rfind("}") + 1]) if "{" in text else {}
                subs = m.get("subgoals", [])
            if isinstance(subs, list) and subs:
                return [str(s)[:120] for s in subs[:4]]
        except (LLMError, json.JSONDecodeError, ValueError):
            pass
        return ["复现失败测试", "定位并修根因", "跑全量测试确认"]

    # ---------- 主循环 ----------
    def run(self, task: dict, judge_fn, arm: str = "", trace_path: Path | None = None) -> EpisodeResult:
        res = EpisodeResult(task_id=task["id"], arm=arm)
        t0 = time.time()
        self.usage = Usage()
        self.toolbox.snap.snapshot("episode-start")

        subgoals = self._plan(task)
        res.subgoals = subgoals
        res.plan_calls = self.usage.calls
        res.prompt_tokens = self.usage.prompt_tokens
        res.completion_tokens = self.usage.completion_tokens

        memory_text, injected = "", []
        if self.memory is not None:
            lessons, memory_text = self.memory.retrieve(task)
            injected = [l.id for l in lessons]
            res.retrieval_ms = float(getattr(self.memory, "last_retrieval_ms", 0.0))
        res.memory_injected = injected
        res.memory_injected_count = len(injected)

        system = SYSTEM_TEMPLATE.format(
            ws=self.toolbox.root, specs=TOOL_SPECS,
            subgoals="\n".join(f"  {i + 1}. {s}" for i, s in enumerate(subgoals)),
            memory=(memory_text + "\n") if memory_text else "",
        )
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": task["prompt"] + f"\n最多 {self.budget.max_steps} 步，工作区: {self.toolbox.root}"}]
        trajectory: list[str] = [f"task: {task['prompt']}"]
        early: list[str] = []  # 被上下文裁剪掉的历史压缩成短摘要
        read_targets: set[str] = set()
        read_streak = 0
        repeat_counts: dict[str, int] = {}
        last_test_digest = None
        last_fail_ids, writes_since_test = "", 0
        judged: tuple[bool, str] | None = None
        abort_reason = ""

        for step in range(1, self.budget.max_steps + 1):
            if time.time() - t0 > self.budget.wall_timeout_s:
                res.failure_mode = "budget_time"
                break
            if self.usage.total_tokens > self.budget.max_total_tokens:
                res.failure_mode = "budget_tokens"
                break
            res.steps = step
            try:
                text, u = self.llm.chat(messages, max_tokens=self.cost.max_output_tokens)
                self.usage.add(u)
            except LLMError as e:
                res.failure_mode = "llm_error"
                res.error = str(e)[:200]
                break

            action = extract_action(text)
            if action is None:
                observation = "无法解析：请只输出一个 JSON 对象，包含 thought/action/args。"
                trajectory.append(f"[step {step}] 解析失败：{text[:120]}")
                messages += [{"role": "assistant", "content": text[:400]},
                             {"role": "user", "content": observation}]
                continue

            name = str(action.get("action", ""))
            args = action.get("args") or {}
            if not isinstance(args, dict):
                args = {"value": args}
            thought = str(action.get("thought", ""))[:200]
            key = f"{name}:{json.dumps(args, sort_keys=True, ensure_ascii=False)}"
            repeat_counts[key] = repeat_counts.get(key, 0) + 1
            if name == "read_file":
                # 只统计"重读同一个文件"：多模块仓库里逐文件通读是正常行为，
                # 读 7 个不同文件不该被判成卡死（这正是优化臂误杀的原因）。
                target = str(args.get("path", ""))
                read_streak = read_streak + 1 if target in read_targets else 0
                read_targets.add(target)
            elif name in ("grep", "list_files"):
                read_streak = 0
            else:
                read_streak = 0
            trajectory.append(f"[step {step}] 思考: {thought}\n  动作: {name} {json.dumps(args, ensure_ascii=False)[:200]}")

            if name == "finish":
                judged = judge_fn()
                trajectory.append(f"  判定: {judged[1][:200]}")
                break

            # 写操作前打点：自动回滚时能精确撤销最近一次坏改动
            if name in ("edit_file", "write_file") and self.toolbox.enable_rollback:
                cid = self.toolbox.snapshot_before_write(f"pre-write-step{step}")
                writes_since_test += 1
                if DEBUG_GUARD:
                    trajectory.append(f"  [ckpt] step={step} 保存 {cid} 丢弃={self.toolbox.last_pruned}")

            out = self.toolbox.call(name, args)
            res.tool_calls += 1
            if name == "run_tests":
                res.test_runs += 1
            observation = out.as_text()[: self.cost.obs_max_chars]
            trajectory.append(f"  观察: {observation[:300]}")

            # 死循环检测
            if repeat_counts[key] >= 3:
                observation += "\n[护栏] 同一动作已重复 3 次，结果不会变。请换个方式（读源码/写局部用例/回滚）。"
            if repeat_counts[key] >= 5:
                abort_reason = "repeated_action"
                res.failure_mode = "repeated_action"
                messages += [{"role": "assistant", "content": text[:600]},
                             {"role": "user", "content": observation}]
                break
            if read_streak >= 7:
                abort_reason = "stuck_reading"
                res.failure_mode = "stuck_reading"
                messages += [{"role": "assistant", "content": text[:600]},
                             {"role": "user", "content": observation + "\n[护栏] 连续 7 步只读不写，终止。"}]
                break

            # 同一批失败测试连续出现（即：这次改动没有改变任何失败项）-> 撤销最近一次写。
            # 用"失败测试集合"而不是整段输出哈希：断言的期望/实际值会随编辑变化，
            # 旧实现因此永远判不出"没有进展"，回滚分支在 64 条记录里一次都没进过。
            if name == "run_tests" and not out.ok and self.toolbox.enable_rollback:
                res.rollback_checks += 1
                ids = str(out.meta.get("failed_ids") or "")
                digest = out.meta.get("digest")
                same = bool(ids) and ids == last_fail_ids and writes_since_test > 0
                if not ids:  # 解析不到测试 id 时退回指纹比较
                    same = bool(digest) and digest == last_test_digest and writes_since_test > 0
                if DEBUG_GUARD:
                    trajectory.append(
                        f"  [guard] step={step} failed_ids={ids[:120]!r} prev={last_fail_ids[:120]!r} "
                        f"writes={writes_since_test} digest={digest} same={same} ckpt={self.toolbox.snap.latest()}")
                last_fail_ids, last_test_digest = ids, digest
                writes_since_test = 0
                if same:
                    cid = self.toolbox.snap.latest()
                    if cid:
                        rep = self.toolbox.snap.restore(cid)
                        res.rollback_used += 1
                        res.rollback_verified = res.rollback_verified or rep.verified
                        observation += (
                            f"\n[护栏] 失败测试集合与上次完全相同（{ids[:100]}），说明刚做的改动没有进展，"
                            f"已回滚到 {cid}（恢复 {rep.restored} 文件，哈希校验"
                            f"{'通过' if rep.verified else '失败'}）。换思路。"
                        )
                        trajectory.append(f"  回滚: {cid} verified={rep.verified} "
                                          f"restored={rep.restored} removed={rep.removed}")
                        last_fail_ids, last_test_digest, writes_since_test = "", None, 0

            if name == "rollback":
                # 只有真正执行的恢复才算一次回滚；被权限拒绝的调用（如 no_rollback 臂）不计。
                if out.ok:
                    res.rollback_used += 1
                    if out.meta.get("verified"):
                        res.rollback_verified = True

            # 优化模式：可见测试通过就收工，省一次模型调用。
            # 但必须用完整判分（含隐藏测试）确认；判分不过就继续，
            # 否则这条"降本手段"会直接吞掉本可成功的任务（实测导致 cache_state-counter_reset 变错）。
            if self.cost.auto_finish_on_pass and name == "run_tests" and out.ok:
                probe = judge_fn()
                trajectory.append(f"  提前结束判定: {probe[1][:200]}")
                if probe[0]:
                    judged = probe
                    break
                observation += "\n[提示] 可见测试通过，但未公开的隐藏测试仍未通过。请继续检查边界情况。"
                judged = None

            messages += [{"role": "assistant", "content": text[:1200]},
                         {"role": "user", "content": "观察结果:\n" + observation}]
            if self.cost.history_window and len(messages) > 3 + self.cost.history_window:
                # 被裁掉的部分压成一行摘要，避免模型"忘了读过什么"而反复重读
                dropped = messages[2 : len(messages) - self.cost.history_window]
                for m in dropped:
                    if m["role"] == "user" and m["content"].startswith("观察结果"):
                        early.append(m["content"].splitlines()[0][:100])
                    elif m["role"] == "assistant":
                        early.append(m["content"][:80])
                early = early[-10:]
                tail = messages[-self.cost.history_window:]
                progress = (f"[上下文裁剪] 已执行 {step} 步。早前动作摘要:\n" + "\n".join(early)
                            if early else f"[上下文裁剪] 已执行 {step} 步。")
                messages = messages[:2] + [{"role": "user", "content": progress}] + tail

        if judged is None:
            judged = judge_fn()
        res.success, res.judge_detail = judged
        res.calls = self.usage.calls
        res.prompt_tokens = self.usage.prompt_tokens
        res.completion_tokens = self.usage.completion_tokens
        res.cached_tokens = self.usage.cached_tokens
        if not res.success and not res.failure_mode:
            res.failure_mode = "wrong_fix" if not abort_reason else abort_reason

        # 反馈注入条目 -> 按写入策略决定是否反思、写入什么、淘汰什么
        if self.memory is not None:
            if injected:
                self.memory.feedback(injected, res.success)
            lesson, u, note = self.memory.after_episode(
                task, res.success, trajectory, res.judge_detail, self.llm,
                failure_mode=res.failure_mode)
            res.reflect_calls = u.calls
            res.reflect_prompt_tokens = u.prompt_tokens
            res.reflect_completion_tokens = u.completion_tokens
            if note:
                res.policy_note = note[:120]
            if lesson:
                res.lesson_written = str(lesson.get("id", ""))
                res.lesson_status = str(lesson.get("status", ""))

        res.latency_s = round(time.time() - t0, 1)
        if trace_path:
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text("\n".join(trajectory))
        return res


def run_with_retry(agent: ReactAgent, task: dict, judge_fn, arm: str, trace_path: Path, max_retry: int = 1) -> EpisodeResult:
    """LLM 层瞬时故障时重跑一次；任务逻辑失败不重跑（保持成功率定义干净）。"""
    last = None
    for _ in range(max_retry + 1):
        try:
            r = agent.run(task, judge_fn, arm=arm, trace_path=trace_path)
        except Exception as e:  # 循环内部异常（非任务失败）
            r = EpisodeResult(task_id=task["id"], arm=arm, success=False, failure_mode="harness_error",
                              error=f"{type(e).__name__}: {e}")
            trace_path.parent.mkdir(parents=True, exist_ok=True)
            trace_path.write_text(traceback.format_exc())
        last = r
        if r.failure_mode not in ("llm_error", "harness_error"):
            return r
    return last
