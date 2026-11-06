"""把逐题 CSV 变成：每 50 题一段的成功率曲线、配对检验、饱和斜率、污染曲线、成本账。

用法：
  python -m bench.report --runs experiments/runs --out experiments/report
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from continual.contamination import contaminated_by_category, harness_meta_share, label_noise
from continual.metrics import mcnemar_exact, slope_ci, wilson
from continual.store import LessonStore
from continual.textutil import FAMILY_TO_CATEGORY, is_harness_meta

ROOT = Path(__file__).resolve().parent.parent
BLOCK = 50


def load_rows(runs: Path) -> dict[str, list[dict]]:
    out = {}
    for f in sorted(runs.glob("*.csv")):
        rows = list(csv.DictReader(open(f)))
        if not rows:
            continue
        for r in rows:
            for k in ("success", "stream_index", "cycle", "memory_injected_count", "memory_before",
                      "memory_after", "memory_bytes", "memory_falsified", "total_tokens",
                      "reflect_tokens", "retrieval_ms", "latency_s"):
                if k in r:
                    r[k] = float(r[k]) if k in ("latency_s", "retrieval_ms") else int(float(r[k]))
            r["success"] = bool(r["success"])
        out[f.stem] = rows
    return out


def join_baseline(arm_rows: list[dict], base_rows: list[dict]) -> list[dict]:
    base = {r["stream_index"]: r for r in base_rows}
    out = []
    for r in arm_rows:
        b = base.get(r["stream_index"])
        if not b:
            continue
        out.append(dict(r, baseline_success=b["success"]))
    return out


def blocks(rows: list[dict]) -> list[dict]:
    out = []
    for start in range(0, max((r["stream_index"] for r in rows), default=0) + 1, BLOCK):
        chunk = [r for r in rows if start <= r["stream_index"] < start + BLOCK]
        if not chunk:
            continue
        k = sum(1 for r in chunk if r["success"])
        lo, hi = wilson(k, len(chunk))
        out.append({"index": start, "n": len(chunk), "k": k, "rate": k / len(chunk),
                    "lo": lo, "hi": hi,
                    "tokens": sum(r["total_tokens"] for r in chunk) / len(chunk),
                    "reflect": sum(r["reflect_tokens"] for r in chunk) / len(chunk),
                    "memory": chunk[-1]["memory_after"],
                    "falsified": chunk[-1]["memory_falsified"]})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default=str(ROOT / "experiments" / "runs"))
    ap.add_argument("--out", default=str(ROOT / "experiments" / "report"))
    a = ap.parse_args()
    runs, out = Path(a.runs), Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    arms = load_rows(runs)
    if not arms:
        print("没有 CSV")
        return 1
    manifest = {}
    mf = ROOT / "experiments" / "stream" / "tasks.jsonl"
    if mf.exists():
        for line in mf.read_text().splitlines():
            r = json.loads(line)
            manifest[r["task_id"]] = r
    base = arms.get("no_memory", [])
    memory_dir = runs.parent.parent / "memory" / runs.name
    truth: dict[str, set[str]] = {}
    for k, v in manifest.items():
        ok = {v.get("primary_category", "")}
        for token in str(v.get("family", "")).split("+"):
            cat = FAMILY_TO_CATEGORY.get(token.strip())
            if cat:
                ok.add(cat)
        truth[k] = {c for c in ok if c}

    n_stream = max((r['stream_index'] for r in next(iter(arms.values()))), default=0) + 1
    scope_note = (f"任务流是 120 个逻辑题 × 3 轮表面改名 = {n_stream} 次运行；"
                  if n_stream == 360 else
                  f"本次只跑了完整流的 {n_stream} 题（完整流为 120 个逻辑题 × 3 轮 = 360 次运行）；")
    lines = ["# 连续学习实验报告", "",
             f"任务流 {n_stream} 次运行，臂: {', '.join(arms)}。每 {BLOCK} 题一段；CI 为 Wilson 95%。",
             "",
             f"> **口径**：{scope_note}独立缺陷结构仍是 **65 个**，所以统计检验按 65 族聚类，"
             f"{n_stream} 只是名义题数，**不是 {n_stream} 个独立样本**。"
             "下文所有区间与 p 值都按名义序列计算，不额外校正族内相关，解释时应偏保守。", ""]

    # 0. 臂概览（P1 验收口径）
    lines += ["", "## 0. 臂概览（成功率 / 注入率 / harm / 成本）", "",
              "| 臂 | n | 成功 | 成功率 | 注入率 | harm(n) | token/题 | 反思 token/题 | 单题墙钟 s |",
              "|---|---|---|---|---|---|---|---|---|"]
    for arm, rows in arms.items():
        n = len(rows)
        k = sum(1 for r in rows if r["success"])
        inj = sum(1 for r in rows if r["memory_injected_count"] > 0)
        harmed = ""
        if base and arm != "no_memory":
            joined = join_baseline(rows, base)
            ij = [r for r in joined if r["memory_injected_count"] > 0]
            h = sum(1 for r in ij if r["baseline_success"] and not r["success"])
            harmed = f"{h}/{len(ij)}"
        lines.append(f"| {arm} | {n} | {k} | {k / n:.1%} | {inj}/{n} ({inj / n:.1%}) | {harmed or '—'} | "
                     f"{sum(r['total_tokens'] for r in rows) / n:.0f} | "
                     f"{sum(r['reflect_tokens'] for r in rows) / n:.0f} | "
                     f"{sum(r.get('latency_s', 0.0) for r in rows) / n:.1f} |")

    # 1. 分段成功率
    lines += ["", "## 1. 每 50 题成功率（Wilson 95% CI）", "",
              "| 臂 | 段 | n | 成功 | 成功率 | 95% CI | token/题 | 反思 token/题 | 库规模 | 已证伪 |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    curve_rows = []
    for arm, rows in arms.items():
        for b in blocks(rows):
            lines.append(f"| {arm} | {b['index']}–{b['index'] + BLOCK - 1} | {b['n']} | {b['k']} | "
                         f"{b['rate']:.1%} | {b['lo']:.1%}–{b['hi']:.1%} | {b['tokens']:.0f} | "
                         f"{b['reflect']:.0f} | {b['memory']} | {b['falsified']} |")
            curve_rows.append(dict(arm=arm, **b))

    # 2. 按轮次（表面改名迁移）
    lines += ["", "## 2. 按轮次成功率（c0 原始 / c1、c2 表面改名）", "",
              "| 臂 | 轮次 | n | 成功 | 成功率 | 95% CI |", "|---|---|---|---|---|---|"]
    for arm, rows in arms.items():
        for cyc in (0, 1, 2):
            chunk = [r for r in rows if r["cycle"] == cyc]
            if not chunk:
                continue
            k = sum(1 for r in chunk if r["success"])
            lo, hi = wilson(k, len(chunk))
            lines.append(f"| {arm} | c{cyc} | {len(chunk)} | {k} | {k / len(chunk):.1%} | "
                         f"{lo:.1%}–{hi:.1%} |")

    # 3. 按类别
    lines += ["", "## 3. 按类别成功率", "", "| 臂 | 类别 | n | 成功率 |", "|---|---|---|---|"]
    for arm, rows in arms.items():
        for cat in sorted({r["primary_category"] for r in rows}):
            chunk = [r for r in rows if r["primary_category"] == cat]
            lines.append(f"| {arm} | {cat} | {len(chunk)} | "
                         f"{sum(1 for r in chunk if r['success']) / len(chunk):.1%} |")

    # 4. 配对检验（vs no_memory，同 stream_index）
    lines += ["", "## 4. 与固定基线臂的配对检验（同一题、同一顺序）", ""]
    paired_rows = []
    if base:
        lines += ["| 臂 | 段 | 不一致 | 臂赢/基线赢 | McNemar p | 净差(pp) |", "|---|---|---|---|---|---|"]
        for arm, rows in arms.items():
            if arm == "no_memory":
                continue
            joined = join_baseline(rows, base)
            for start in range(0, len(rows) + BLOCK, BLOCK):
                chunk = [r for r in joined if start <= r["stream_index"] < start + BLOCK]
                if not chunk:
                    continue
                b = sum(1 for r in chunk if r["success"] and not r["baseline_success"])
                c = sum(1 for r in chunk if r["baseline_success"] and not r["success"])
                p = mcnemar_exact(b, c)
                lines.append(f"| {arm} | {start}–{start + BLOCK - 1} | {b + c} | {b}/{c} | "
                             f"{p:.4f} | {(b - c) / len(chunk) * 100:+.1f} |")
                paired_rows.append(dict(arm=arm, start=start, win=b, loss=c, p=p,
                                        net=(b - c) / len(chunk)))
            tot_b = sum(1 for r in joined if r["success"] and not r["baseline_success"])
            tot_c = sum(1 for r in joined if r["baseline_success"] and not r["success"])
            lines.append(f"| **{arm} 合计** | — | {tot_b + tot_c} | {tot_b}/{tot_c} | "
                         f"{mcnemar_exact(tot_b, tot_c):.4f} | "
                         f"{(tot_b - tot_c) / max(1, len(joined)) * 100:+.1f} |")

    # 3. 饱和
    lines += ["", "## 5. 饱和判定（最后 100 题的最小二乘斜率，单位：每 50 题的百分点）", "",
              f"> 口径同文首：{scope_note}独立缺陷结构仍是 **65 个**；"
              "斜率按名义序列计算，未按 65 族聚类校正，判定规则写死（CI 下界>0 上升 / 上界<0 下降 / 其余测不出）。", "",
              "| 臂 | 斜率(pp/50题) | 95% CI | 判定 |", "|---|---|---|---|"]
    for arm, rows in arms.items():
        tail = sorted(rows, key=lambda r: r["stream_index"])[-100:]
        xs = [r["stream_index"] for r in tail]
        ys = [1.0 if r["success"] else 0.0 for r in tail]
        s, lo, hi = slope_ci(xs, ys)
        verdict = ("上升" if lo > 0 else "下降" if hi < 0 else "持平/测不出")
        lines.append(f"| {arm} | {s:+.2f} | {lo:+.2f}–{hi:+.2f} | {verdict} |")

    # 4. 污染
    lines += ["", "## 6. 污染指标", "",
              "口径：`错标率` = 写入条目类别既不是来源任务主类别、也不在其缺陷族（family）内的比例；"
              "双缺陷任务指向次缺陷的条目不算错标；"
              "`harness_meta占比` = Agent 自身工具/动作格式类条目 / 全库（只写入、不注入，单独统计，"
              "且不计入错标率分母）；"
              "`证伪率` = fail_after≥2 且 success_after==0 的条目占比；"
              "`注入后变差率` = 有注入且基线成功而本臂失败的题数 / 有注入题数。", ""]
    for arm, rows in arms.items():
        if arm == "no_memory":
            continue
        joined = join_baseline(rows, base) if base else []
        injected = [r for r in joined if r["memory_injected_count"] > 0]
        harmed = sum(1 for r in injected if r["baseline_success"] and not r["success"])
        falsified = rows[-1]["memory_falsified"]
        total = rows[-1]["memory_after"]
        cats = contaminated_by_category(joined)
        lines.append(f"- **{arm}**: 注入题 {len(injected)}/{len(joined)}，注入后变差 "
                     f"{harmed}/{len(injected)} ({(harmed / len(injected) if injected else 0):.1%})；"
                     f"已证伪 {falsified}/{total} ({(falsified / total if total else 0):.1%})")
        mem_path = memory_dir / f"{arm}.jsonl"
        if mem_path.exists():
            st = LessonStore(mem_path)

            def _eff(l):
                d = {"symptom": l.symptom, "root_cause": l.root_cause, "fix_pattern": l.fix_pattern,
                     "language_constraints": l.language_constraints}
                return "harness_meta" if is_harness_meta(d) else l.bug_category

            wrong0, checked0, rate0 = label_noise(st, truth)
            meta, total_m, share = harness_meta_share(st, _eff)
            wrong1, checked1, rate1 = label_noise(st, truth, _eff)
            lines.append(f"  - 错标率：改前（按写入类别）{wrong0}/{checked0} ({rate0:.1%})"
                         f" → 改后（harness_meta 单列）{wrong1}/{checked1} ({rate1:.1%})；"
                         f"harness_meta {meta}/{total_m} ({share:.1%})；"
                         f"库状态 {st.state()['statuses']}；拒绝原因 {st.state()['rejects']}")
        for cat, d in sorted(cats.items()):
            if d["injected"]:
                lines.append(f"  - {cat}: n={d['n']} 注入={d['injected']} 胜={d['win']} 负={d['loss']}")

    if base:
        lines += ["", "### 6.1 污染随段变化", "",
                  "| 臂 | 段 | 有注入题 | 变差/注入 | 证伪/库 |", "|---|---|---|---|---|"]
        for arm, rows in arms.items():
            if arm == "no_memory":
                continue
            joined = join_baseline(rows, base)
            for start in range(0, len(rows) + BLOCK, BLOCK):
                chunk = [r for r in joined if start <= r["stream_index"] < start + BLOCK]
                if not chunk:
                    continue
                inj = [r for r in chunk if r["memory_injected_count"] > 0]
                harmed = sum(1 for r in inj if r["baseline_success"] and not r["success"])
                total = chunk[-1]["memory_after"]
                lines.append(f"| {arm} | {start}–{start + BLOCK - 1} | {len(inj)} | "
                             f"{harmed}/{len(inj)} | {chunk[-1]['memory_falsified']}/{total} |")

    # 5. 成本
    lines += ["", "## 7. 成本账", "",
              "| 臂 | token/题 | 反思 token/题 | 检索 ms/题 | 单题墙钟 s | 库字节 | 事件数 |",
              "|---|---|---|---|---|---|---|"]
    for arm, rows in arms.items():
        n = len(rows)
        lines.append(f"| {arm} | {sum(r['total_tokens'] for r in rows) / n:.0f} | "
                     f"{sum(r['reflect_tokens'] for r in rows) / n:.0f} | "
                     f"{sum(r['retrieval_ms'] for r in rows) / n:.1f} | "
                     f"{sum(r.get('latency_s', 0.0) for r in rows) / n:.1f} | "
                     f"{rows[-1]['memory_bytes']} | — |")

    (out / "report.md").write_text("\n".join(lines) + "\n")
    with open(out / "blocks.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(curve_rows[0].keys())) if curve_rows else None
        if w:
            w.writeheader()
            w.writerows(curve_rows)
    with open(out / "paired.csv", "w", newline="") as f:
        if paired_rows:
            w = csv.DictWriter(f, fieldnames=list(paired_rows[0].keys()))
            w.writeheader()
            w.writerows(paired_rows)
    with open(out / "memory_curve.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["arm", "stream_index", "memory_after", "memory_bytes",
                                          "memory_falsified", "injected"])
        w.writeheader()
        for arm, rows in arms.items():
            for r in rows:
                w.writerow({"arm": arm, "stream_index": r["stream_index"],
                            "memory_after": r["memory_after"], "memory_bytes": r["memory_bytes"],
                            "memory_falsified": r["memory_falsified"],
                            "injected": r["memory_injected_count"]})
    print(f"报告 -> {out / 'report.md'}")
    from bench.plot import line_chart
    series = []
    for arm in arms:
        pts = [(b["index"] + BLOCK / 2, b["rate"]) for b in blocks(arms[arm])]
        series.append((arm, [p[0] for p in pts], [p[1] for p in pts]))
    line_chart(out / "success.svg", series, "每 50 题成功率", "成功率", ymax=1.0)
    mem_series = []
    for arm, rows in arms.items():
        if arm == "no_memory":
            continue
        mem_series.append((arm, [r["stream_index"] for r in rows], [r["memory_after"] for r in rows]))
    if mem_series:
        line_chart(out / "memory.svg", mem_series, "记忆库规模", "条目数")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
