"""append-only 教训库（event sourcing）。

事件：
  {"type":"add","lesson":{...}}
  {"type":"feedback","id":"L...","outcome":"success|fail"}
  {"type":"status","id":"L...","value":"active|quarantined|expired","reason":"..."}

加载时重放事件重建状态，进程崩溃不会损坏已落盘的教训。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from .textutil import jaccard, tokenize


@dataclass
class Lesson:
    id: str
    bug_category: str = ""
    symptom: str = ""
    root_cause: str = ""
    fix_pattern: str = ""
    language_constraints: str = ""
    family: str = ""
    source_task: str = ""
    source_cycle: int = 0
    policy: str = ""
    scope: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))
    # 状态机：provisional -> active -> quarantined / expired
    status: str = "active"
    injections: int = 0
    success_after: int = 0
    fail_after: int = 0
    dedup_hits: int = 0

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def falsified(self) -> bool:
        return self.fail_after >= 2 and self.success_after == 0

    def text_for_similarity(self) -> str:
        return " ".join(x for x in (self.symptom, self.root_cause, self.fix_pattern) if x)


class LessonStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lessons: dict[str, Lesson] = {}
        self.rejects: dict[str, int] = {}
        self.events = 0
        if self.path.exists():
            self._rebuild()

    # ---------- 持久化 ----------
    def _append(self, rec: dict) -> None:
        self.events += 1
        with open(self.path, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _rebuild(self) -> None:
        for line in self.path.read_text().splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            t = rec.get("type")
            if t == "add":
                d = rec["lesson"]
                self.lessons[d["id"]] = Lesson(**{k: v for k, v in d.items() if k in Lesson.__dataclass_fields__})
            elif t == "feedback":
                lid = rec.get("id")
                if lid in self.lessons:
                    l = self.lessons[lid]
                    if rec.get("outcome") == "success":
                        l.success_after += 1
                    else:
                        l.fail_after += 1
            elif t == "status":
                lid = rec.get("id")
                if lid in self.lessons:
                    self.lessons[lid].status = rec.get("value", "active")

    # ---------- 写入 ----------
    def reject(self, reason: str) -> None:
        self.rejects[reason] = self.rejects.get(reason, 0) + 1

    def add(self, lesson: dict, status: str = "active", dedup_threshold: float = 0.55) -> tuple[str, bool]:
        """同类且（根因+做法 Jaccard 高 或 具体符号高度重叠）则合并。返回 (id, 是否新建)。"""
        category = lesson.get("bug_category", "")
        new_scope = set(lesson.get("scope") or [])
        for existing in self.lessons.values():
            if existing.bug_category != category:
                continue
            sim = jaccard(
                tokenize(existing.root_cause + " " + existing.fix_pattern),
                tokenize(lesson.get("root_cause", "") + " " + lesson.get("fix_pattern", "")),
            )
            scope_sim = jaccard(set(existing.scope), new_scope) if existing.scope and new_scope else 0.0
            if sim >= dedup_threshold or scope_sim >= 0.5:
                existing.dedup_hits += 1
                if len(lesson.get("fix_pattern", "")) > len(existing.fix_pattern):
                    existing.fix_pattern = lesson["fix_pattern"]
                self._append({"type": "add", "lesson": existing.as_dict(), "merged": True})
                return existing.id, False
        lid = lesson.get("id") or f"L{int(time.time() * 1000)}{len(self.lessons)}"
        obj = Lesson(
            id=lid,
            bug_category=category,
            symptom=(lesson.get("symptom") or "")[:300],
            root_cause=(lesson.get("root_cause") or "")[:400],
            fix_pattern=(lesson.get("fix_pattern") or lesson.get("rule") or "")[:400],
            language_constraints=(lesson.get("language_constraints") or "")[:120],
            family=lesson.get("family", ""),
            source_task=lesson.get("source_task", ""),
            source_cycle=int(lesson.get("source_cycle", 0)),
            policy=lesson.get("policy", ""),
            scope=list(lesson.get("scope") or [])[:10],
            status=status,
        )
        self.lessons[lid] = obj
        self._append({"type": "add", "lesson": obj.as_dict()})
        return lid, True

    def feedback(self, ids: list[str], success: bool, promote: bool = False) -> None:
        for lid in ids:
            l = self.lessons.get(lid)
            if not l:
                continue
            self._append({"type": "feedback", "id": lid, "outcome": "success" if success else "fail"})
            if success:
                l.success_after += 1
                if promote and l.status == "provisional":
                    l.status = "active"
                    self._append({"type": "status", "id": lid, "value": "active", "reason": "validated"})
            else:
                l.fail_after += 1
                if l.status == "provisional" and l.injections >= 3 and l.success_after == 0:
                    l.status = "expired"
                    self._append({"type": "status", "id": lid, "value": "expired", "reason": "3_injections_no_success"})

    def mark_injected(self, ids: list[str]) -> None:
        for lid in ids:
            if lid in self.lessons:
                self.lessons[lid].injections += 1

    def quarantine(self) -> int:
        """把"注入≥2 次且从未成功"的条目隔离，返回本次隔离条数。"""
        n = 0
        for l in self.lessons.values():
            if l.status in ("active", "provisional") and l.fail_after >= 2 and l.success_after == 0:
                l.status = "quarantined"
                self._append({"type": "status", "id": l.id, "value": "quarantined", "reason": "falsified"})
                n += 1
        return n

    def expire_provisional(self, max_injections: int = 3) -> int:
        n = 0
        for l in self.lessons.values():
            if l.status == "provisional" and l.injections >= max_injections and l.success_after == 0:
                l.status = "expired"
                self._append({"type": "status", "id": l.id, "value": "expired", "reason": "unvalidated"})
                n += 1
        return n

    # ---------- 统计 ----------
    def active(self) -> list[Lesson]:
        return [l for l in self.lessons.values() if l.status in ("active", "provisional")]

    def state(self) -> dict:
        total = len(self.lessons)
        falsified = sum(1 for l in self.lessons.values() if l.falsified)
        statuses: dict[str, int] = {}
        for l in self.lessons.values():
            statuses[l.status] = statuses.get(l.status, 0) + 1
        return {
            "n_total": total,
            "n_active": len(self.active()),
            "n_falsified": falsified,
            "n_quarantined": statuses.get("quarantined", 0),
            "n_expired": statuses.get("expired", 0),
            "n_provisional": statuses.get("provisional", 0),
            "n_success": sum(1 for l in self.lessons.values() if l.success_after > 0),
            "n_injected_ever": sum(1 for l in self.lessons.values() if l.injections > 0),
            "bytes": self.path.stat().st_size if self.path.exists() else 0,
            "events": self.events,
            "rejects": dict(self.rejects),
            "statuses": statuses,
        }

    def dump(self) -> list[dict]:
        return [l.as_dict() for l in self.lessons.values()]
