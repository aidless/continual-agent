"""污染指标：定义与计算都写在这里，报告只引用这里的口径。

四个指标：
1. `label_noise`  —— 写入条目的 bug_category 与来源任务的真实类别不一致的比例。
   只在离线分析时可用（真实类别来自任务元数据，Agent 看不到）。分类只按类别，不看变体。
   指向双缺陷任务里"次缺陷"的条目不算错标。
2. `falsified_share` —— 被后续证伪的条目占比：fail_after>=2 且 success_after==0。
3. `harm_rate` —— 注入后"相对同题 no_memory 基线变差"的比例：
   baseline 成功 & 记忆臂失败 的题数 / 有注入的题数。需要同序配对。
4. `quarantine_events` —— 运行期真正被隔离的条目数（会写进 state）。

另有 `harness_meta_share`：Agent 自身工具/动作格式类条目占比。这类条目只写入、不注入，
单独统计，并且**不计入 label_noise 的分母**，避免与"任务类别错标"重复计算。
"""
from __future__ import annotations

from .store import LessonStore


def _category(lesson, category_fn=None) -> str:
    return category_fn(lesson) if category_fn else lesson.bug_category


def label_noise(store: LessonStore, true_categories: dict[str, set[str]],
                category_fn=None) -> tuple[int, int, float]:
    """返回 (错标条数, 可核对条数, 比例)。

    `true_categories`：task_id -> 该任务可接受的类别集合。
    双缺陷任务的经验教训若指向**次缺陷**，不算错标；只有既不是主类别、也不在 family 里的才计为错标。
    source_task 不在映射里的条目不参与。harness_meta 条目由 `harness_meta_share` 单独统计。
    """
    checked = wrong = 0
    for l in store.lessons.values():
        truth = true_categories.get(l.source_task)
        if not truth:
            continue
        cat = _category(l, category_fn)
        if cat == "harness_meta":
            continue
        checked += 1
        if cat not in truth:
            wrong += 1
    return wrong, checked, (wrong / checked if checked else 0.0)


def harness_meta_share(store: LessonStore, category_fn=None) -> tuple[int, int, float]:
    total = len(store.lessons)
    meta = sum(1 for l in store.lessons.values() if _category(l, category_fn) == "harness_meta")
    return meta, total, (meta / total if total else 0.0)


def falsified_share(store: LessonStore) -> tuple[int, int, float]:
    total = len(store.lessons)
    falsified = sum(1 for l in store.lessons.values() if l.falsified)
    return falsified, total, (falsified / total if total else 0.0)


def harm_rate(injected_rows: list[dict]) -> tuple[int, int, float]:
    """injected_rows: 每行需含 success（记忆臂）与 baseline_success（同题基线）。"""
    n = len(injected_rows)
    harmed = sum(1 for r in injected_rows if r.get("baseline_success") and not r["success"])
    return harmed, n, (harmed / n if n else 0.0)


def contaminated_by_category(rows: list[dict]) -> dict[str, dict]:
    """按类别拆开"记忆臂 vs 同题基线"，用来定位污染是否集中在某类。"""
    out: dict[str, dict] = {}
    for r in rows:
        cat = r.get("primary_category", "unknown")
        d = out.setdefault(cat, {"n": 0, "win": 0, "loss": 0, "injected": 0})
        d["n"] += 1
        d["injected"] += int(bool(r.get("injected_count")))
        if r["success"] and not r.get("baseline_success"):
            d["win"] += 1
        elif r.get("baseline_success") and not r["success"]:
            d["loss"] += 1
    return out
