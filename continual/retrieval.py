"""三种检索策略：按 bug 类别 / 按症状 / 按 embedding 相似度。

三者共享同一套"准入 → 排序 → 渲染"骨架，只替换打分函数，保证对比公平：
  - 准入：active/provisional，排除 falsified / quarantined / expired / harness_meta；
  - 排序：策略打分，同分按创建时间（新条目优先会被 created_at 升序打破平局）；
  - 渲染：与类别策略完全相同的文本模板与字数预算（top-3, 总计 900 字），
    因此 token 成本差异只来自检索条数与命中与否，不来自渲染格式。

embedding 说明（必须与结果一起读）：本机端点不提供 /v1/embeddings，ollama 也未安装
embedding 模型，因此这里用**本地词法哈希向量**（char 3-gram + 词 token，256 维，
blake2b 哈希，L2 归一化，余弦相似度）。它是"无神经模型可用时的降级实现"，不是语义
embedding；若将来接入 bge/nomic，只需替换 `embed()`，接口不变。
"""
from __future__ import annotations

import hashlib
import math

from .store import Lesson, LessonStore
from .textutil import infer_categories, jaccard, tokenize

EMBED_DIM = 256
RENDER_MAX_CHARS = 900
ENTRY_MAX_CHARS = 220


def _bucket(token: str) -> int:
    h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(h, "big") % EMBED_DIM


def embed(text: str) -> list[float]:
    """词 token + char 3-gram 的哈希词袋向量（L2 归一化）。"""
    vec = [0.0] * EMBED_DIM
    toks = tokenize(text)
    grams = set()
    low = (text or "").lower()
    for w in toks:
        grams.update(w[i:i + 3] for i in range(max(1, len(w) - 2)))
    for t in list(toks) + sorted(grams):
        vec[_bucket(t)] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def query_text(task: dict) -> str:
    return " ".join([
        task.get("prompt", ""),
        task.get("retrieval_hint", ""),
        task.get("visible_failure", ""),
    ])


class Retriever:
    def __init__(self, strategy: str = "category", k: int = 3, max_chars: int = RENDER_MAX_CHARS):
        assert strategy in ("category", "symptom", "embedding")
        self.strategy = strategy
        self.k = k
        self.max_chars = max_chars
        self.last_ms = 0.0

    # ---------- 打分 ----------
    def score(self, task: dict, lesson: Lesson, ctx: dict) -> float:
        if self.strategy == "category":
            cat_hit = bool(lesson.bug_category) and lesson.bug_category in ctx["observed"]
            sim = jaccard(ctx["qt"], tokenize(lesson.text_for_similarity()))
            anchor = _anchor(lesson, ctx["ids"])
            return 3.0 * float(cat_hit) + 2.0 * sim + 0.4 * anchor
        if self.strategy == "symptom":
            # 只用症状/根因的相似度，不给类别分（类别仅用于同分时的稳定排序，不参与分数）
            sim = jaccard(ctx["qt"], tokenize(lesson.symptom + " " + lesson.root_cause))
            return 2.5 * sim + 0.3 * _anchor(lesson, ctx["ids"])
        qv = ctx["qvec"]
        return 2.5 * cosine(qv, embed(lesson.text_for_similarity()))

    # ---------- 检索 ----------
    def retrieve(self, task: dict, store: LessonStore) -> tuple[list[Lesson], str]:
        import time
        t0 = time.perf_counter()
        text = query_text(task)
        observed = set(task.get("observed_categories") or []) | infer_categories(text)
        ctx = {
            "qt": tokenize(text),
            "ids": set(task.get("src_identifiers") or []),
            "observed": observed,
            "qvec": embed(text) if self.strategy == "embedding" else None,
        }
        scored = []
        for l in store.lessons.values():
            if l.status not in ("active", "provisional"):
                continue
            if l.falsified:
                continue
            # harness_meta 只写入、只统计，永不注入
            if l.bug_category == "harness_meta":
                continue
            s = self.score(task, l, ctx)
            if s > 0:
                scored.append((s, l))
        scored.sort(key=lambda x: (-x[0], x[1].created_at))
        chosen = [l for _, l in scored[: self.k]]
        store.mark_injected([l.id for l in chosen])
        self.last_ms = round((time.perf_counter() - t0) * 1000, 3)
        return chosen, self._render(chosen)

    def _render(self, chosen: list[Lesson]) -> str:
        block, used = [], 0
        for l in chosen:
            entry = (
                f"- 类别: {l.bug_category} | 症状: {l.symptom[:80]}\n"
                f"  根因: {l.root_cause[:120]}\n  下次: {l.fix_pattern[:120]}"
            )[:ENTRY_MAX_CHARS]
            if used + len(entry) > self.max_chars:
                break
            block.append(entry)
            used += len(entry)
        if not block:
            return ""
        return "历史教训（来自同类失败，仅供参考，需结合当前代码验证）:\n" + "\n".join(block)


def _anchor(lesson: Lesson, ids: set[str]) -> float:
    if not lesson.scope or not ids:
        return 0.0
    return len(set(lesson.scope) & ids) / len(set(lesson.scope) | ids)
