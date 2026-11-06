# 连续学习实验报告

任务流 20 次运行，臂: no_memory, write_all, write_fail, write_verified。每 50 题一段；CI 为 Wilson 95%。

> **口径**：本次只跑了完整流的 20 题（完整流为 120 个逻辑题 × 3 轮 = 360 次运行）；独立缺陷结构仍是 **65 个**，所以统计检验按 65 族聚类，20 只是名义题数，**不是 20 个独立样本**。下文所有区间与 p 值都按名义序列计算，不额外校正族内相关，解释时应偏保守。


## 0. 臂概览（成功率 / 注入率 / harm / 成本）

| 臂 | n | 成功 | 成功率 | 注入率 | harm(n) | token/题 | 反思 token/题 | 单题墙钟 s |
|---|---|---|---|---|---|---|---|---|
| no_memory | 20 | 15 | 75.0% | 0/20 (0.0%) | — | 15104 | 0 | 66.0 |
| write_all | 20 | 18 | 90.0% | 18/20 (90.0%) | 0/18 | 20123 | 1784 | 108.4 |
| write_fail | 20 | 15 | 75.0% | 15/20 (75.0%) | 0/15 | 18805 | 368 | 70.9 |
| write_verified | 20 | 13 | 65.0% | 15/20 (75.0%) | 2/15 | 14387 | 448 | 156.9 |

## 1. 每 50 题成功率（Wilson 95% CI）

| 臂 | 段 | n | 成功 | 成功率 | 95% CI | token/题 | 反思 token/题 | 库规模 | 已证伪 |
|---|---|---|---|---|---|---|---|---|---|
| no_memory | 0–49 | 20 | 15 | 75.0% | 53.1%–88.8% | 15104 | 0 | 0 | 0 |
| write_all | 0–49 | 20 | 18 | 90.0% | 69.9%–97.2% | 20123 | 1784 | 17 | 1 |
| write_fail | 0–49 | 20 | 15 | 75.0% | 53.1%–88.8% | 18805 | 368 | 5 | 0 |
| write_verified | 0–49 | 20 | 13 | 65.0% | 43.3%–81.9% | 14387 | 448 | 1 | 0 |

## 2. 按轮次成功率（c0 原始 / c1、c2 表面改名）

| 臂 | 轮次 | n | 成功 | 成功率 | 95% CI |
|---|---|---|---|---|---|
| no_memory | c0 | 20 | 15 | 75.0% | 53.1%–88.8% |
| write_all | c0 | 20 | 18 | 90.0% | 69.9%–97.2% |
| write_fail | c0 | 20 | 15 | 75.0% | 53.1%–88.8% |
| write_verified | c0 | 20 | 13 | 65.0% | 43.3%–81.9% |

## 3. 按类别成功率

| 臂 | 类别 | n | 成功率 |
|---|---|---|---|
| no_memory | boundary_compare | 3 | 33.3% |
| no_memory | cache_state | 2 | 100.0% |
| no_memory | mutable_default | 3 | 33.3% |
| no_memory | paging_index | 3 | 100.0% |
| no_memory | parsing_robustness | 2 | 100.0% |
| no_memory | recursion_base | 2 | 100.0% |
| no_memory | reduce_init | 3 | 100.0% |
| no_memory | type_conversion | 2 | 50.0% |
| write_all | boundary_compare | 3 | 66.7% |
| write_all | cache_state | 2 | 100.0% |
| write_all | mutable_default | 3 | 66.7% |
| write_all | paging_index | 3 | 100.0% |
| write_all | parsing_robustness | 2 | 100.0% |
| write_all | recursion_base | 2 | 100.0% |
| write_all | reduce_init | 3 | 100.0% |
| write_all | type_conversion | 2 | 100.0% |
| write_fail | boundary_compare | 3 | 66.7% |
| write_fail | cache_state | 2 | 50.0% |
| write_fail | mutable_default | 3 | 33.3% |
| write_fail | paging_index | 3 | 100.0% |
| write_fail | parsing_robustness | 2 | 100.0% |
| write_fail | recursion_base | 2 | 100.0% |
| write_fail | reduce_init | 3 | 100.0% |
| write_fail | type_conversion | 2 | 50.0% |
| write_verified | boundary_compare | 3 | 66.7% |
| write_verified | cache_state | 2 | 0.0% |
| write_verified | mutable_default | 3 | 66.7% |
| write_verified | paging_index | 3 | 66.7% |
| write_verified | parsing_robustness | 2 | 100.0% |
| write_verified | recursion_base | 2 | 100.0% |
| write_verified | reduce_init | 3 | 66.7% |
| write_verified | type_conversion | 2 | 50.0% |

## 4. 与固定基线臂的配对检验（同一题、同一顺序）

| 臂 | 段 | 不一致 | 臂赢/基线赢 | McNemar p | 净差(pp) |
|---|---|---|---|---|---|
| write_all | 0–49 | 3 | 3/0 | 0.2500 | +15.0 |
| **write_all 合计** | — | 3 | 3/0 | 0.2500 | +15.0 |
| write_fail | 0–49 | 2 | 1/1 | 1.0000 | +0.0 |
| **write_fail 合计** | — | 2 | 1/1 | 1.0000 | +0.0 |
| write_verified | 0–49 | 6 | 2/4 | 0.6875 | -10.0 |
| **write_verified 合计** | — | 6 | 2/4 | 0.6875 | -10.0 |

## 5. 饱和判定（最后 100 题的最小二乘斜率，单位：每 50 题的百分点）

> 口径同文首：本次只跑了完整流的 20 题（完整流为 120 个逻辑题 × 3 轮 = 360 次运行）；独立缺陷结构仍是 **65 个**；斜率按名义序列计算，未按 65 族聚类校正，判定规则写死（CI 下界>0 上升 / 上界<0 下降 / 其余测不出）。

| 臂 | 斜率(pp/50题) | 95% CI | 判定 |
|---|---|---|---|
| no_memory | -1.62 | -3.02–-0.18 | 下降 |
| write_all | -0.60 | -1.90–+0.00 | 持平/测不出 |
| write_fail | -0.71 | -2.22–+1.00 | 持平/测不出 |
| write_verified | -0.19 | -2.07–+1.61 | 持平/测不出 |

## 6. 污染指标

口径：`错标率` = 写入条目类别既不是来源任务主类别、也不在其缺陷族（family）内的比例；双缺陷任务指向次缺陷的条目不算错标；`harness_meta占比` = Agent 自身工具/动作格式类条目 / 全库（只写入、不注入，单独统计，且不计入错标率分母）；`证伪率` = fail_after≥2 且 success_after==0 的条目占比；`注入后变差率` = 有注入且基线成功而本臂失败的题数 / 有注入题数。

- **write_all**: 注入题 18/20，注入后变差 0/18 (0.0%)；已证伪 1/17 (5.9%)
  - 错标率：改前（按写入类别）3/17 (17.6%) → 改后（harness_meta 单列）3/17 (17.6%)；harness_meta 0/17 (0.0%)；库状态 {'active': 16, 'quarantined': 1}；拒绝原因 {}
- **write_fail**: 注入题 15/20，注入后变差 0/15 (0.0%)；已证伪 0/5 (0.0%)
  - 错标率：改前（按写入类别）0/4 (0.0%) → 改后（harness_meta 单列）0/4 (0.0%)；harness_meta 1/5 (20.0%)；库状态 {'active': 5}；拒绝原因 {}
- **write_verified**: 注入题 15/20，注入后变差 2/15 (13.3%)；已证伪 0/1 (0.0%)
  - 错标率：改前（按写入类别）0/1 (0.0%) → 改后（harness_meta 单列）0/1 (0.0%)；harness_meta 0/1 (0.0%)；库状态 {'active': 1}；拒绝原因 {}

### 6.1 污染随段变化

| 臂 | 段 | 有注入题 | 变差/注入 | 证伪/库 |
|---|---|---|---|---|
| write_all | 0–49 | 18 | 0/18 | 1/17 |
| write_fail | 0–49 | 15 | 0/15 | 0/5 |
| write_verified | 0–49 | 15 | 2/15 | 0/1 |

## 7. 成本账

| 臂 | token/题 | 反思 token/题 | 检索 ms/题 | 单题墙钟 s | 库字节 | 事件数 |
|---|---|---|---|---|---|---|
| no_memory | 15104 | 0 | 0.0 | 66.0 | 0 | — |
| write_all | 20123 | 1784 | 1.8 | 108.4 | 21875 | — |
| write_fail | 18805 | 368 | 0.6 | 70.9 | 7646 | — |
| write_verified | 14387 | 448 | 0.5 | 156.9 | 2107 | — |
