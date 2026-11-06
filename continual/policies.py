"""三种写入规则。

| 策略 | 反思时机 | 写入门槛 | 生命周期 |
|---|---|---|---|
| `write_all` | 每个 episode（成功 + 失败） | 可用性（非空话/非 prompt 复述） | 直接 active，永久保留 |
| `write_fail` | 只写失败 | 可用性 | 直接 active，永久保留（检索层隔离 falsified） |
| `write_verified` | 只写失败 | 可用性 + 具体性闸门（点名符号 + 可验证判据） | provisional；注入后成功才晋升 active；3 次注入无成功则过期；fail≥2 且 0 成功隔离 |

三条规则共用同一个 store 与同一套检索，差异只在"写什么、什么时候写、写进去后怎么淘汰"。
"""
from __future__ import annotations

import json
import re

from .store import LessonStore
from .textutil import is_harness_meta, normalize_category

FAIL_PROMPT = """分析一次失败的 Agent 执行，产出可迁移的 bug 类别级教训。
只输出一个 JSON 对象，不要推理过程。

任务: {prompt}
执行轨迹:
{trajectory}
最终判定: {judge_detail}

字段必须具体、可执行，不要空话：
{{"bug_category":"从 boundary_compare / paging_index / reduce_init / mutable_default / cache_state / parsing_robustness / recursion_base / type_conversion 中选最贴切的一个",
"symptom":"测试是怎么失败的（可观察症状）","root_cause":"根因，具体到函数/语句",
"fix_pattern":"下次遇到同类症状的具体做法","language_constraints":"适用语言/库约束，如 Python3 纯标准库",
"rule":"下次遇到X就做Y"}}

失败版要求：root_cause 或 fix_pattern 里最好出现本任务中真实存在的函数名/文件名/命令名。
只写"要仔细/别改测试/别只满足可见测试"这类复述系统提示的教训会被直接丢弃。
"""

SUCCESS_PROMPT = """分析一次**成功**的 Agent 执行，产出可迁移的 bug 类别级经验。
只输出一个 JSON 对象，不要推理过程。

任务: {prompt}
执行轨迹:
{trajectory}
最终判定: {judge_detail}

字段：
{{"bug_category":"从 boundary_compare / paging_index / reduce_init / mutable_default / cache_state / parsing_robustness / recursion_base / type_conversion 中选最贴切的一个",
"symptom":"失败时观察到的症状","root_cause":"真正的根因，具体到函数/语句",
"fix_pattern":"这次奏效的具体修法（含可验证判据更佳）","language_constraints":"适用语言/库约束",
"rule":"下次遇到X就做Y"}}

只写"通读源码/认真检查"这类空话会被丢弃。
"""

VERIFY_PROMPT = """分析一次失败的 Agent 执行，产出**可验证**的 bug 类别级教训。
只输出一个 JSON 对象，不要推理过程。

任务: {prompt}
执行轨迹:
{trajectory}
最终判定: {judge_detail}

这条教训会被注入到别的任务，必须同时满足下面三条，缺一条就输出 {{"bug_category":"no_category"}}：
(a) 点名具体符号/函数/参数/条件（例如 clamp、hi、n <= 0、"空列表"），不能只写"边界情况"；
(b) 给出可验证判据（例如"输入 [] 应返回 []"、"last_page(0) 应返回 0"），不能只写"要小心"；
(c) bug_category 必须是下列之一：boundary_compare / paging_index / reduce_init /
    mutable_default / cache_state / parsing_robustness / recursion_base / type_conversion

输出字段（每项尽量 ≤60 字）：
{{"bug_category":"...","symptom":"测试怎么失败的","root_cause":"根因，具体到函数/语句",
"fix_pattern":"下次的具体做法，必须含可验证判据","language_constraints":"Python3 纯标准库等约束",
"rule":"下次遇到X就做Y"}}
"""

_PLACEHOLDERS = {"...", "…", "待定", "todo", "unknown", "n/a"}
_BOILERPLATE_PHRASES = ("不得修改 tests", "不要修改测试", "调用 finish", "修复 src", "通读源码", "仔细检查")
_BOILERPLATE_TOKENS = {"tests", "test", "src", "finish", "pytest", "run_tests", "summary", "workspace", "json"}
_GATE_GENERIC = {
    "the", "and", "when", "then", "should", "would", "could", "return", "returns", "value", "result",
    "input", "output", "case", "condition", "specific", "same", "different", "function", "parameter",
    "argument", "example", "expected", "actual", "line", "code", "python", "list", "dict", "str",
    "int", "float", "bool", "always", "never", "only", "avoid", "check", "consider", "ensure",
    "handle", "correct", "test", "tests", "src", "finish", "pytest",
}
_GATE_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_GATE_MINI_VAR = re.compile(r"\b[A-Za-z]\s*(?:<=|>=|==|!=|<|>|\+|-|\*|/|%)")
_GATE_CONDITION = (
    "空列表", "空字符串", "空输入", "空值", "负数", "零", "越界", "边界", "off-by-one", "off_by_one",
    "none", "null", "[]", '""', "''", "last_page", "first_page", "最后一个", "第一个", "缺失",
    "typeerror", "indexerror",
)
_GATE_VERIFY = re.compile(
    r"(?:应(?:该)?(?:返回|等于|输出|为)|期望|预期|断言|assert|returns?\b|"
    r"should return|expected|equals|==|>=|<=|!=|→|->)", re.I,
)


def is_usable(lesson: dict) -> bool:
    root = (lesson.get("root_cause") or "").strip()
    fix = (lesson.get("fix_pattern") or lesson.get("rule") or "").strip()
    if len(root) < 10 or len(fix) < 8:
        return False
    for text in (root, fix):
        if text.lower() in _PLACEHOLDERS or set(text) <= set(".…-— "):
            return False
    rule = (lesson.get("rule") or fix)
    phrase_hits = sum(1 for p in _BOILERPLATE_PHRASES if p in rule)
    anchors = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", rule)) - _BOILERPLATE_TOKENS
    if phrase_hits >= 1 and not anchors:
        return False
    return True


def has_specific_symbol(text: str) -> bool:
    low = (text or "").lower()
    if any(c in low for c in _GATE_CONDITION):
        return True
    if _GATE_MINI_VAR.search(text or ""):
        return True
    return any(t.lower() not in _GATE_GENERIC and len(t) >= 3 for t in _GATE_IDENT.findall(text or ""))


def has_verifiable_check(text: str) -> bool:
    for m in _GATE_VERIFY.finditer(text or ""):
        tail = text[m.end(): m.end() + 40]
        if re.search(r"\d", tail) or re.search(r"\b(none|null|true|false|list|dict|str|int|float|bool)\b", tail, re.I):
            return True
        if any(c in tail for c in ("[]", '""', "''", "空")):
            return True
        if any(t.lower() not in _GATE_GENERIC and len(t) >= 2 for t in _GATE_IDENT.findall(tail)):
            return True
    return False


def has_concrete_case(text: str) -> bool:
    """具体判据的宽松版：出现比较/期望词，且附近有字面量或数字。"""
    if not re.search(r"(==|!=|<=|>=|应(?:该)?(?:返回|等于)|期望|预期|expected|assert)", text or "", re.I):
        return False
    return bool(re.search(r"\b\d+\b|\[\]|\[[^\]]{0,40}\]|none|\"\"|''", text or "", re.I))


def extract_json_any(text: str) -> dict | None:
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1] if text.count("```") >= 2 else text
        text = text.lstrip("json").lstrip()
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
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


class WritePolicy:
    name = "base"
    reflect_on_success = False
    require_specificity = False
    provisional = False

    def after_episode(self, store: LessonStore, task: dict, success: bool, trajectory: list[str],
                      judge_detail: str, llm, task_family: str = "",
                      failure_mode: str = "") -> tuple[dict | None, object, str]:
        """返回 (写入的 lesson dict 或 None, usage, 诊断)。失败反思失败时返回 None。"""
        if success and not self.reflect_on_success:
            return None, _usage(), ""
        # 非任务逻辑失败（超时/预算/LLM 故障）不反思：拿不到可靠"根因 → 修法"，
        # 只会编出无关教训；且这类 episode 会被 run_with_retry 重跑，写入会串到别的行。
        mode = failure_mode or ""
        if not success and mode.startswith("budget_"):
            store.reject(mode)
            return None, _usage(), ("budget_time_skip" if mode.startswith("budget_time")
                                    else f"harness_skip:{mode}")
        if not success and mode in ("llm_error", "harness_error"):
            store.reject(mode)
            return None, _usage(), f"harness_skip:{mode}"
        prompt = (SUCCESS_PROMPT if success else
                  (VERIFY_PROMPT if self.require_specificity else FAIL_PROMPT)).format(
            prompt=task["prompt"][:500],
            trajectory="\n".join(trajectory[-8:])[:2000],
            judge_detail=judge_detail[:400],
        ) + "\n总输出不超过 600 个字符；每个字段一句话，不要解释。"
        try:
            text, u = llm.chat([{"role": "user", "content": prompt}], max_tokens=5000, json_mode=True)
        except Exception as e:  # LLMError 等；不把反思故障当任务失败
            return None, _usage(), f"reflect_error: {type(e).__name__}"
        obj = extract_json_any(text)
        if not isinstance(obj, dict) or not obj.get("root_cause"):
            _dump_raw(self.name, task.get("id", "?"), text)
            return None, u, "reflect_parse_failed"
        # Agent 自身工具/JSON 动作格式的"教训"：写入但不注入，只计入污染统计
        meta = is_harness_meta(obj)
        if meta:
            obj["bug_category"] = "harness_meta"
        else:
            cat = normalize_category(obj.get("bug_category"), task_family)
            if not cat:
                store.reject("no_category")
                return None, u, "no_category"
            obj["bug_category"] = cat
        if not is_usable(obj):
            store.reject("boilerplate")
            return None, u, "boilerplate"
        if self.require_specificity and not meta:
            action = obj.get("fix_pattern") or obj.get("rule") or ""
            if not has_specific_symbol(obj.get("root_cause", "") + " " + action):
                store.reject("no_symbol")
                return None, u, "no_symbol"
            if not has_verifiable_check(action + " " + obj.get("symptom", "")) \
                    and not has_concrete_case(action):
                store.reject("no_check")
                _dump_reject("no_check", task.get("id", "?"), obj)
                return None, u, "no_check"
        obj["family"] = task_family
        obj["source_task"] = task["id"]
        obj["source_cycle"] = int(task.get("cycle", 0))
        obj["policy"] = self.name
        known = set(task.get("src_identifiers") or [])
        text_all = " ".join(str(obj.get(k, "")) for k in ("symptom", "root_cause", "fix_pattern", "rule"))
        obj["scope"] = sorted(i for i in known if i in text_all)
        status = "provisional" if self.provisional else "active"
        lid, created = store.add(obj, status=status)
        return {"id": lid, "created": created, "status": status, **obj}, u, ""


class AllPolicy(WritePolicy):
    name = "write_all"
    reflect_on_success = True


class FailPolicy(WritePolicy):
    name = "write_fail"


class VerifiedPolicy(WritePolicy):
    name = "write_verified"
    require_specificity = True
    provisional = True


def get_policy(name: str) -> WritePolicy:
    return {"write_all": AllPolicy(), "write_fail": FailPolicy(), "write_verified": VerifiedPolicy()}[name]


def _usage():
    from agent.llm import Usage
    return Usage()


def _dump_raw(policy: str, task_id: str, text) -> None:
    """反思输出无法解析时落盘原文，便于定位是模型输出问题还是解析器问题。"""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "experiments" / "reflection_raw"
    p.mkdir(parents=True, exist_ok=True)
    body = text if isinstance(text, str) else repr(text)
    (p / f"{policy}__{task_id}.txt").write_text(body[:8000])


def _dump_reject(reason: str, task_id: str, obj: dict) -> None:
    """记录被闸门拒绝的候选教训，用于报告里给拒绝原因分布做例子。"""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent / "experiments" / "reflection_rejects"
    p.mkdir(parents=True, exist_ok=True)
    (p / f"{reason}__{task_id}.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2))
