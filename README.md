# 深井系列 · 持续学习 Agent

在**连续任务流**里研究 Agent 的记忆写入、检索、污染与饱和。不是"记忆能不能提分"的又一次 A/B，
而是回答四个工程问题：

1. 什么该写？全部写 / 只写失败 / 只写"被验证有效"的教训（+ 具体性闸门）。
2. 三种检索（类别 / 症状 / embedding）在**持续增长的库**上召回怎么衰减。
3. 库变大后，错误教训会不会互相污染；污染能不能画出时间曲线。
4. 连续跑 300–500 题，"越用越强"在哪一步饱和，之后是持平还是缓慢变差。

前置基线（只读复用）：`agent-reflection` 已证明
250 题 × 3 臂、no_memory 78.0%、mem_v2 80.4%、注入率 93.6%、配对 p=0.451、
变体聚类 +3.7pp（p=0.098）、成本 +21.5% token——**检索能通，收益测不出来**。
本项目换问题，不重复它。

## 一条命令复算

```bash
make repro
```

`repro` = 生成 360 题任务流并离线自检（缺陷版必失败、参考解必通过）+ 跑 2 题 × 2 臂冒烟
+ 出报告。完整主实验（360 题 × 4 臂，约 5–7 小时）：

```bash
make phase2
```

## 目录

```
continual/        写入策略、检索、污染、统计指标
  policies.py     三种写入规则（write_all / write_fail / write_verified）
  store.py        append-only 教训库（事件溯源、去重、隔离、过期）
  retrieval.py    三种检索 + 统一渲染预算
  contamination.py 污染四指标的口径
  metrics.py      Wilson CI、McNemar、bootstrap 斜率
  memory.py       一次 episode 的生命周期组合
agent/            从基线仓库只读复制的执行器（loop / llm / tools / rollback / config）
bench/
  stream.py       任务流生成器（120 逻辑题 × 3 轮 = 360）+ 自检
  judge.py        隐藏测试判定
  runner.py       在线 runner（臂间并行、臂内串行）
  report.py       每 50 题曲线、配对检验、饱和、污染、成本
  retrieval_ablation.py 离线检索消融（真实库快照上 recall@3）
dataset/          250 题只读快照 + 说明（含 sha256）
experiments/      runs/*.csv、memory/*.jsonl、report/、traces/
DESIGN.md         设计稿（先看这个）
blog_series.md    三篇选题与每篇要用的真实数字
```

## 任务流怎么来

- `dataset/tasks/`：250 题快照（65 个单缺陷 + 185 个双缺陷组合，8 类）。
- 连续流：每类 15 题（共 120 逻辑题）× 3 轮 = 360 题，seed 固定 11。
  - 轮 0 原始；轮 1/2 只把模块名与模块级函数/类名加 `_x1`/`_x2` 后缀。
  - **口径（全文统一）**：任务流是 120 个逻辑题 × 3 轮表面改名 = 360 次运行；独立缺陷结构仍是 **65 个**，
    所以统计检验按 65 族聚类，360 只是名义题数，**不是 360 个独立样本**。报告、饱和判定、README 三处都按这句话写。
- 所有臂共用同一 manifest 顺序，配对检验成立。

## 三种写入策略

| 策略 | 反思时机 | 写入门槛 | 生命周期 |
|---|---|---|---|
| `write_all` | 每个 episode（成功也反思） | 可用性检查 | 直接 active |
| `write_fail` | 仅失败 | 可用性检查 | 直接 active；检索排除已证伪 |
| `write_verified` | 仅失败 | 可用性 + 具体性闸门（点名符号 + 可验证判据/具体用例） | provisional → 成功晋升 active；3 次注入无成功则 expired |

补充规则：Agent 自身工具/JSON 动作格式类教训归为 `harness_meta`，**写入但不注入**，只计入污染统计
（单独报占比，不计入错标率分母）；非任务逻辑失败（`budget_time` / `budget_tokens` / `llm_error` /
`harness_error`）**一律不反思**，避免重试把第一次尝试的条目串到别行。

## 环境

全部从环境变量读取，代码不打印、不落盘密钥：

| 变量 | 默认 | 说明 |
|---|---|---|
| `AGENT_BASE_URL` | `http://127.0.0.1:3425/v1` | OpenAI 兼容端点 |
| `AGENT_API_KEY` | 空 | 需要时由调用方注入 |
| `AGENT_MODEL` | `opencode-go/deepseek-v4.1-flash` | 模型名 |
| `AGENT_PROVIDER` | `openai` | `openai` / `ollama` |

无可用端点时会报 `llm_error` 并中止该臂；报告里会标注"未验证"，不会把模拟数字混进结果。
依赖只有 `pytest`（判定用），其余为 Python 3.10+ 标准库。

## 当前状态

- [x] 任务流生成 + 360/360 离线自检通过
- [x] 全链路打通（真实失败 → 写入 → 下题注入 → 反馈 → 记账）
- [x] P1 重跑：20 题 × 4 臂（改后验收通过；见 [P1 结果](experiments/report/phase1/p1_summary.md)）
- [ ] P2：120 题 × 4 臂 × 3 段（A 案；每段出中间报告，可断点续跑）
- [ ] P3：按饱和形状决定是否补到 500

P1 重跑摘要（n=20，**不用于结论**）：成功率 no_memory 75.0%、write_all 90.0%、write_fail 75.0%、
write_verified 65.0%；注入率 90% / 75% / 75%；harm 0/18、0/15、2/15；配对检验全部不显著。
`harness_meta` 全库 1/23 (4.3%)，写入后注入 0 次；`budget_time` 与 `llm_error` 重跑不再写条目。

结果表在 `experiments/report/`；本 README 只放结论级数字，且等跑出来再填。

## 已知局限

1. c1/c2 是表面改名，不是全新任务；独立缺陷结构仍是 **65 个**（360 只是名义题数，统计按 65 族聚类），
   不能外推到"没见过的新缺陷"。
2. `embedding` 是 **char 3-gram + 256 维哈希词袋的降级实现**，不是神经语义 embedding；
   三策略对比的结论只在"本实现下"成立，不外推为"语义检索更好/更差"。
   接入 bge/nomic 只需替换 `continual/retrieval.py` 的 `embed()`，**未实测**。
3. 单次 pass/fail 有噪声；temperature=0 降低但不消除随机性。
4. 任务集是合成教学用例，规模与真实工程分布有差距。

## 致谢与边界

执行器（agent/）与任务集复制自 `agent-reflection`；原仓库未被修改。本项目只做实验与公开仓库，
不发布到 PyPI、不发 PR、不向社交平台发文章（除非逐篇确认）。
