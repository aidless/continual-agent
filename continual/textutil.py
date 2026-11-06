"""文本工具：分词、Jaccard、bug 类别推断。

类别推断只用 Agent 可观察文本（prompt / 可见测试 / 源码标识符 / 失败输出），
不读 task.json 里的 ground-truth 字段。
"""
from __future__ import annotations

import re

STOPWORDS = set(
    """a an the is are was be been to of in on at for with and or not no it this that these those
    you your i we they he she do does did done can could should would will if then than as by from
    python code test tests file files function fix bug source workspace agent json call tool step
    """.split()
)

CATEGORIES = [
    "boundary_compare",
    "paging_index",
    "reduce_init",
    "mutable_default",
    "cache_state",
    "parsing_robustness",
    "recursion_base",
    "type_conversion",
    # 非任务缺陷：Agent 自身动作/工具格式错误。写入但不注入，只计入污染统计。
    "harness_meta",
]

FAMILY_TO_CATEGORY = {
    "boundary": "boundary_compare",
    "paging": "paging_index",
    "reduce_init": "reduce_init",
    "mutable_default": "mutable_default",
    "cache_state": "cache_state",
    "parsing": "parsing_robustness",
    "recursion": "recursion_base",
    "types": "type_conversion",
}

# 与 agent-reflection v2 的词典一致；函数/模块重命名后仍靠通用词（paging、boundary）命中。
CATEGORY_KEYWORDS = {
    "boundary_compare": [
        "lower_bound", "upper_bound", "bisect", "insert_position", "first_nonnegative",
        "peak_index", "rotated", "search_rotated", "off_by_one", "off-by-one",
        "mid =", "lo, hi", "len(arr) - 1", "len(arr)-1",
    ],
    "paging_index": [
        "paging", "page", "last_page", "paginate", "slice", "chunk", "offset", "limit", "per_page",
    ],
    "reduce_init": [
        "reduce", "fold", "min_or_none", "max_or_none", "argmax", "argmin", "initial", "initializer",
    ],
    "mutable_default": ["mutable", "default", "stack", "append", "shared", "collect"],
    "cache_state": ["cache", "memo", "reset", "state", "counter", "flush"],
    "parsing_robustness": ["parse", "csv", "split", "strip", "parse_kv", "parse_range", "parse_float"],
    "recursion_base": ["recursion", "recursive", "tree", "count_nodes", "tree_height", "base case"],
    "type_conversion": ["format", "normalize", "to_int", "to_float", "isinstance", "type(", "str"],
}


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}|[\u4e00-\u9fff]{1,4}", (text or "").lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def normalize_category(value: str | None, family: str = "") -> str:
    v = (value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if v in CATEGORIES:
        return v
    # 重命名任务族名可能带 "boundary+x1" 之类后缀，取第一段
    for token in str(family or "").split("+"):
        base = token.split("_x")[0].strip()
        if base in FAMILY_TO_CATEGORY:
            return FAMILY_TO_CATEGORY[base]
    return ""


def infer_categories(text: str) -> set[str]:
    """从可观察文本推断类别；重命名后的函数名不再命中词典，因此同时用通用词与模块名。"""
    low = (text or "").lower()
    hits = set()
    for cat, words in CATEGORY_KEYWORDS.items():
        if any(w.lower() in low for w in words):
            hits.add(cat)
    for cat in CATEGORIES:
        if cat in low:
            hits.add(cat)
    return hits


# ---------- harness_meta 判定 ----------
# 只匹配"Agent 自身动作/工具调用格式"的强特征，避免误伤正常任务教训。
# 反例：只说"用 edit_file 修改某文件"、或"工具调用失败后改代码"不算 meta；必须是格式/JSON/闭合类问题。
_META_TARGET = ("edit_file", "read_file", "write_file", "run_tests", "tool_use", "tool call", "tool-call",
                "function_call", "function call", "工具调用", "工具格式", "动作格式")
# 强格式词：出现这些才说明问题在"动作格式"本身，而不是在任务代码里
_META_FORMAT = ("json", "xml", "格式", "转义", "escape", "闭合")
_META_PARSE = ("解析失败", "无法解析", "不能解析", "截断", "未闭合", "parse error", "truncat", "malformed")


def _meta_first_sentence(lesson: dict) -> str:
    """取 root_cause 的第一句：meta 教训的第一句主语就是工具/动作格式。"""
    root = str(lesson.get("root_cause") or "")
    first = re.split(r"[。；;\n]", root, maxsplit=1)[0]
    return first[:120].lower()


def is_harness_meta(lesson: dict) -> bool:
    """这条"教训"是否在讲 Agent 自己的工具/JSON 动作格式，而不是任务代码缺陷。

    只看 root_cause 的第一句，避免"真任务缺陷 + 附带提一句 read_file JSON 被截断"
    被整体误判为 meta（P1 重跑实测到 1 例：SquareCache 缓存键缺陷）。
    """
    first = _meta_first_sentence(lesson)
    if not first:
        return False
    if any(t in first for t in _META_TARGET) and (
            any(f in first for f in _META_FORMAT) or any(p in first for p in _META_PARSE)):
        return True
    # 没有点名工具、但第一句明确在讲 JSON 动作格式
    if "json" in first and any(f in first for f in ("action", "转义", "escape", "闭合", "工具", "tool", "payload")):
        return True
    return False
