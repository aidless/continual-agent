"""持续记忆：store + retriever + write policy 的组合体。

一次 episode 的完整生命周期：
  检索（注入前） -> Agent 执行 -> 反馈注入条目 -> 写入新教训 -> 淘汰（隔离/过期）
"""
from __future__ import annotations

from .policies import get_policy
from .retrieval import Retriever
from .store import LessonStore


class ContinualMemory:
    def __init__(self, path: str, policy: str = "write_fail", strategy: str = "category",
                 topk: int = 3, max_chars: int = 900):
        self.store = LessonStore(path)
        self.policy = get_policy(policy)
        self.retriever = Retriever(strategy, k=topk, max_chars=max_chars)
        self.policy_name = policy
        self.strategy = strategy
        self.last_retrieval_ms = 0.0

    # ---------- 检索 ----------
    def retrieve(self, task: dict) -> tuple[list, str]:
        lessons, text = self.retriever.retrieve(task, self.store)
        self.last_retrieval_ms = self.retriever.last_ms
        return lessons, text

    # ---------- 反馈 + 写入 ----------
    def feedback(self, ids: list[str], success: bool) -> None:
        promote = self.policy.name == "write_verified"
        self.store.feedback(ids, success, promote=promote)

    def after_episode(self, task: dict, success: bool, trajectory: list[str],
                      judge_detail: str, llm, failure_mode: str = "") -> tuple[dict | None, object, str]:
        lesson, usage, note = self.policy.after_episode(
            self.store, task, success, trajectory, judge_detail, llm,
            task_family=task.get("family", ""),
            failure_mode=failure_mode,
        )
        # 淘汰：隔离已证伪条目；过期未验证的 provisional 条目
        self.store.quarantine()
        if self.policy.provisional:
            self.store.expire_provisional(max_injections=3)
        return lesson, usage, note

    # ---------- 观测 ----------
    def state(self) -> dict:
        st = self.store.state()
        st["policy"] = self.policy_name
        st["retrieval"] = self.strategy
        return st
