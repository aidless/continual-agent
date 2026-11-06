# 连续学习实验报告

任务流 20 次运行，臂: no_memory, write_all, write_fail, write_verified。每 50 题一段；CI 为 Wilson 95%。

> **口径**：本次只跑了完整流的 20 题（完整流为 120 个逻辑题 × 3 轮 = 360 次运行）；独立缺陷结构仍是 **65 个**，所以统计检验按 65 族聚类，20 只是名义题数，**不是 20 个独立样本**。下文所有区间与 p 值都按名义序列计算，不额外校正族内相关，解释时应偏保守。


## 0. 臂概览（成功率 / 注入率 / harm / 成本）

| 臂 | n | 成功 | 成功率 | 注入率 | harm(n) | token/题 | 反思 token/题 | 单题墙钟 s |
|---|---|---|---|---|---|---|---|---|
| no_memory | 20 | 17 | 85.0% | 0/20 (0.0%) | — | 15441 | 0 | 64.6 |
| write_all | 20 | 17 | 85.0% | 19/20 (95.0%) | 0/19 | 20618 | 1980 | 75.5 |
| write_fail | 20 | 14 | 70.0% | 15/20 (75.0%) | 2/15 | 18595 | 1101 | 84.9 |
| write_verified | 20 | 17 | 85.0% | 12/20 (60.0%) | 1/12 | 18256 | 690 | 92.6 |

## 1. 每 50 题成功率（Wilson 95% CI）

| 臂 | 段 | n | 成功 | 成功率 | 95% CI | token/题 | 反思 token/题 | 库规模 | 已证伪 |
|---|---|---|---|---|---|---|---|---|---|
| no_memory | 0–49 | 20 | 17 | 85.0% | 64.0%–94.8% | 15441 | 0 | 0 | 0 |
| write_all | 0–49 | 20 | 17 | 85.0% | 64.0%–94.8% | 20618 | 1980 | 19 | 1 |
| write_fail | 0–49 | 20 | 14 | 70.0% | 48.1%–85.5% | 18595 | 1101 | 6 | 0 |
| write_verified | 0–49 | 20 | 17 | 85.0% | 64.0%–94.8% | 18256 | 690 | 4 | 0 |

## 2. 按轮次成功率（c0 原始 / c1、c2 表面改名）

| 臂 | 轮次 | n | 成功 | 成功率 | 95% CI |
|---|---|---|---|---|---|
| no_memory | c0 | 20 | 17 | 85.0% | 64.0%–94.8% |
| write_all | c0 | 20 | 17 | 85.0% | 64.0%–94.8% |
| write_fail | c0 | 20 | 14 | 70.0% | 48.1%–85.5% |
| write_verified | c0 | 20 | 17 | 85.0% | 64.0%–94.8% |

## 3. 按类别成功率

| 臂 | 类别 | n | 成功率 |
|---|---|---|---|
| no_memory | boundary_compare | 3 | 33.3% |
| no_memory | cache_state | 2 | 100.0% |
| no_memory | mutable_default | 3 | 66.7% |
| no_memory | paging_index | 3 | 100.0% |
| no_memory | parsing_robustness | 2 | 100.0% |
| no_memory | recursion_base | 2 | 100.0% |
| no_memory | reduce_init | 3 | 100.0% |
| no_memory | type_conversion | 2 | 100.0% |
| write_all | boundary_compare | 3 | 33.3% |
| write_all | cache_state | 2 | 100.0% |
| write_all | mutable_default | 3 | 66.7% |
| write_all | paging_index | 3 | 100.0% |
| write_all | parsing_robustness | 2 | 100.0% |
| write_all | recursion_base | 2 | 100.0% |
| write_all | reduce_init | 3 | 100.0% |
| write_all | type_conversion | 2 | 100.0% |
| write_fail | boundary_compare | 3 | 33.3% |
| write_fail | cache_state | 2 | 50.0% |
| write_fail | mutable_default | 3 | 66.7% |
| write_fail | paging_index | 3 | 100.0% |
| write_fail | parsing_robustness | 2 | 100.0% |
| write_fail | recursion_base | 2 | 100.0% |
| write_fail | reduce_init | 3 | 100.0% |
| write_fail | type_conversion | 2 | 0.0% |
| write_verified | boundary_compare | 3 | 66.7% |
| write_verified | cache_state | 2 | 100.0% |
| write_verified | mutable_default | 3 | 66.7% |
| write_verified | paging_index | 3 | 100.0% |
| write_verified | parsing_robustness | 2 | 100.0% |
| write_verified | recursion_base | 2 | 100.0% |
| write_verified | reduce_init | 3 | 100.0% |
| write_verified | type_conversion | 2 | 50.0% |

## 4. 与固定基线臂的配对检验（同一题、同一顺序）

| 臂 | 段 | 不一致 | 臂赢/基线赢 | McNemar p | 净差(pp) |
|---|---|---|---|---|---|
| write_all | 0–49 | 2 | 1/1 | 1.0000 | +0.0 |
| **write_all 合计** | — | 2 | 1/1 | 1.0000 | +0.0 |
| write_fail | 0–49 | 3 | 0/3 | 0.2500 | -15.0 |
| **write_fail 合计** | — | 3 | 0/3 | 0.2500 | -15.0 |
| write_verified | 0–49 | 2 | 1/1 | 1.0000 | +0.0 |
| **write_verified 合计** | — | 2 | 1/1 | 1.0000 | +0.0 |

## 5. 饱和判定（最后 100 题的最小二乘斜率，单位：每 50 题的百分点）

> 口径同文首：本次只跑了完整流的 20 题（完整流为 120 个逻辑题 × 3 轮 = 360 次运行）；独立缺陷结构仍是 **65 个**；斜率按名义序列计算，未按 65 族聚类校正，判定规则写死（CI 下界>0 上升 / 上界<0 下降 / 其余测不出）。

| 臂 | 斜率(pp/50题) | 95% CI | 判定 |
|---|---|---|---|
| no_memory | -0.49 | -1.83–+0.44 | 持平/测不出 |
| write_all | +0.71 | -0.35–+1.97 | 持平/测不出 |
| write_fail | -0.30 | -2.04–+1.39 | 持平/测不出 |
| write_verified | -1.02 | -2.60–+0.00 | 持平/测不出 |

## 6. 污染指标

口径：`错标率` = 写入条目类别既不是来源任务主类别、也不在其缺陷族（family）内的比例；双缺陷任务指向次缺陷的条目不算错标；`harness_meta占比` = Agent 自身工具/动作格式类条目 / 全库（只写入、不注入，单独统计，且不计入错标率分母）；`证伪率` = fail_after≥2 且 success_after==0 的条目占比；`注入后变差率` = 有注入且基线成功而本臂失败的题数 / 有注入题数。

- **write_all**: 注入题 19/20，注入后变差 0/19 (0.0%)；已证伪 1/19 (5.3%)
  - 错标率：改前（按写入类别）4/19 (21.1%) → 改后（harness_meta 单列）2/17 (11.8%)；harness_meta 2/19 (10.5%)；库状态 {'active': 18, 'quarantined': 1}；拒绝原因 {}
- **write_fail**: 注入题 15/20，注入后变差 2/15 (13.3%)；已证伪 0/6 (0.0%)
  - 错标率：改前（按写入类别）1/6 (16.7%) → 改后（harness_meta 单列）0/5 (0.0%)；harness_meta 1/6 (16.7%)；库状态 {'active': 6}；拒绝原因 {}
- **write_verified**: 注入题 12/20，注入后变差 1/12 (8.3%)；已证伪 0/4 (0.0%)
  - 错标率：改前（按写入类别）0/4 (0.0%) → 改后（harness_meta 单列）0/4 (0.0%)；harness_meta 0/4 (0.0%)；库状态 {'active': 4}；拒绝原因 {}

### 6.1 污染随段变化

| 臂 | 段 | 有注入题 | 变差/注入 | 证伪/库 |
|---|---|---|---|---|
| write_all | 0–49 | 19 | 0/19 | 1/19 |
| write_fail | 0–49 | 15 | 2/15 | 0/6 |
| write_verified | 0–49 | 12 | 1/12 | 0/4 |

## 7. 成本账

| 臂 | token/题 | 反思 token/题 | 检索 ms/题 | 单题墙钟 s | 库字节 | 事件数 |
|---|---|---|---|---|---|---|
| no_memory | 15441 | 0 | 0.0 | 64.6 | 0 | — |
| write_all | 20618 | 1980 | 0.5 | 75.5 | 21937 | — |
| write_fail | 18595 | 1101 | 0.3 | 84.9 | 8321 | — |
| write_verified | 18256 | 690 | 0.9 | 92.6 | 5243 | — |
