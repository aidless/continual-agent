# P1 重跑（改后）· n=20 × 4 臂 · 只报方向

> 口径：任务流是 120 个逻辑题 × 3 轮表面改名 = 360 次运行；独立缺陷结构仍是 **65 个**，
> 统计检验按 65 族聚类，360 只是名义题数，**不是 360 个独立样本**。本次只跑前 20 题。
> 数据：`runs/phase1/*.csv`、`memory/phase1/*.jsonl`；改前归档在 `runs/phase1_pre/`。
> **n=20，下面所有比较只用于验收与方向判断，不下结论。**

## 1. 本轮改了什么

按拍板的三条 + 调试中发现的 2 个实现级 bug：

1. `harness_meta` 类别：写入、**不注入**、只计入污染统计（错标率分母里剔除，单独报 `harness_meta占比`）。
2. 非任务逻辑失败不反思：`budget_time` / `budget_tokens` / `llm_error` / `harness_error`
   一律不写条目（`budget_time_skip` / `harness_skip:<mode>`）。
3. 规模走 A 案：120 × 4 臂 × 3 段，每段出中间报告、可断点续跑。
4. **修 `llm_error` 串写**：首次尝试主循环 LLM 报错时，旧逻辑仍当任务失败反思写条目，
   `run_with_retry` 重跑后条目留在库里、行上却记着 `budget_time_skip`。
   改前实测 3 例（write_fail #15、write_verified #8、#15）：日志墙钟 541.8s 而 CSV `latency_s=265.8s`，
   证明是两次尝试。改后重跑 **0 例**（runner 增加不变量告警：库增长但无 `lesson_written` 即打印，本次全程 0 条）。
5. **修 `harness_meta` 误判**：原规则在 symptom/root/fix 全文里找"工具名 + JSON"，
   把一条真任务教训（`SquareCache.get` 缓存键缺陷，symptom 里附带提了一句 `read_file` 的 JSON 被截断）
   整体判成 meta、于是不注入。改为只看 `root_cause` 第一句的工具/格式信号。
   历史 3 条 meta 仍全部命中，新误判 0。

## 2. 臂概览（改后）

| 臂 | n | 成功 | 成功率 | 注入率 | harm(n) | token/题 | 反思 token/题 | 单题墙钟 s | 库条目 |
|---|---|---|---|---|---|---|---|---|---|
| no_memory | 20 | 15 | 75.0% | 0/20 | — | 15,104 | 0 | 66.0 | 0 |
| write_all | 20 | 18 | 90.0% | 18/20 (90%) | 0/18 | 20,123 | 1,784 | 108.4 | 17（1 隔离） |
| write_fail | 20 | 15 | 75.0% | 15/20 (75%) | 0/15 | 18,805 | 368 | 70.9 | 5 |
| write_verified | 20 | 13 | 65.0% | 15/20 (75%) | 2/15 | 14,387 | 448 | 156.9 | 1 |

配对 McNemar（vs no_memory，同题同序）：write_all 3 赢/0 输 p=0.2500（+15.0pp）；
write_fail 1/1 p=1.0000（+0.0pp）；write_verified 2/4 p=0.6875（−10.0pp）。全部不显著。

## 3. 单独回答一：write_fail 的 −15pp 与 13.3% harm 还在不在同一方向

不能只看最后一次，三次跑的完整轨迹如下（同 20 题、同顺序、同端点）：

| 跑次 | 规则状态 | 成功 | 成功率 | 注入题 | harm | 库 |
|---|---|---|---|---|---|---|
| 改前（旧数据） | 无 meta 类别、超时也反思 | 14/20 | 70.0%（vs 基线 85%，−15pp） | 15 | 2/15 (13.3%) | 6 |
| 重跑 A | 修 `llm_error` 串写；meta 误判仍在 | 12/20 | 60.0%（vs 基线 75%，−15pp） | 8 | 2/8 (25.0%) | 3 |
| 重跑 B（最终） | 再修 meta 误判 | 15/20 | 75.0%（vs 基线 75%，**0pp**） | 15 | **0/15** | 5 |

**答案：在最终这版上，两边都不再是同一方向** —— 成功率差从 −15pp 变成 0pp，
注入后变差从 13.3% 变成 0/15。但重跑 A 仍然复现了 −15pp 与 harm（2/8），
说明 n=20 下 10–15pp 的摆动本身就可能是噪声（三次成功率 70% → 60% → 75%）。
因此结论只有一句：**修完之后，改前那个方向没有稳定复现；但也没有被证明消失。**
按预定规则，P2 第一段跑完用 120 题再做一次中途判断。

## 4. 单独回答二：新的 `harness_meta` 在污染统计里占多少

改后本轮：

| 臂 | 库条目 | harness_meta | 占比 | 是否被注入过 |
|---|---|---|---|---|
| write_all | 17 | 0 | 0.0% | — |
| write_fail | 5 | 1 | 20.0% | **否**（命中 0 次） |
| write_verified | 1 | 0 | 0.0% | — |
| **合计** | **23** | **1** | **4.3%** | **0 次** |

那 1 条是真正的工具格式问题：`DSML 工具调用标签格式错误导致 shell/find 调用未被解析`，
source 任务 `multi-mutable_default-merge_defaults-x-parsing-split_path`；写入后从未进入任何一次注入。

历史 3 条（`phase1_pre` 归档，按新规则重新归类后的改前/改后）：

| 臂 | 错标率 改前 | 错标率 改后 | meta 改前 | meta 改后 |
|---|---|---|---|---|
| write_all | 4/19 (21.1%) | 2/17 (11.8%) | 2/19 (10.5%) | 2/17 (11.8%) |
| write_fail | 1/6 (16.7%) | 0/5 (0.0%) | 1/6 (16.7%) | 1/5 (20.0%) |
| write_verified | 0/4 | 0/4 | 0/4 | 0/4 |

（改前的"错标"把 3 条 meta 算进了 parsing_robustness；改后它们从错标分母里剔除，单列 meta 占比。
分母变小，所以 write_all 的 meta 占比从 10.5% 略升到 11.8%，这是口径变化，不是新写入。）

## 5. 链路验收

- [x] `budget_time` 行不再增长：改后所有 `budget_time_skip` / `harness_skip:llm_error` 行的
      `memory_before == memory_after`，不变量告警 0 条。
- [x] `harness_meta` 写入 1 条、注入 0 次。
- [x] 三臂注入率 90% / 75% / 75%，逐题 `memory_injected` 落盘。
- [x] 反思成本单列：write_all 1,784、write_fail 368、write_verified 448 token/题。
- [x] 快照每 10 题一份，检索消融可离线回放。

**反例保留（改前）**：`write_verified` 第 16 题（#15，`multi-types-compare_values-x-cache_state-counter_reset`）
`budget_time` 失败（283.5s），仍写入条目：root_cause 为
"Counter.reset 未将内部计数/缓存清零，执行只修复了可见的 normalize.compare_values"。
该题隐藏测试最终仍失败，这条"教训"是在超时后诊断出的，不能证明来自真实观察。
改后同一任务不再产生任何条目（`budget_time_skip`，库不变）。

## 6. 下一步（P2 第一段与中途判断）

- 开 P2 第一段：`--start 0 --limit 120 --arms no_memory,write_all,write_fail,write_verified --label phase2 --parallel 4`。
- 第一段跑完先出中间报告，再做 write_fail 中途判断：
  若仍是"成功率明显低于 no_memory 且存在 harm"的方向，则把 write_fail 降级为**只做对照、不进结论**；
  剩余两段继续跑，不回头改已落盘的数据。
