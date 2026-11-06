# dataset · 任务集快照说明

## 来源
`dataset/tasks/` 是只读快照，复制自 [`agent-reflection`](../../agent-reflection) 的 `bench/data/tasks`
（该仓库另有任务在写，本项目不修改它，只复制）。

- 快照内容：250 个任务目录，每个含 `task.json` 与 `workspace/`（`src/` + `tests/test_visible.py`）。
- 总大小：12 MB（250 个目录 / 1435 个文件）。
- 全量校验和（按相对路径字典序排序，逐个 `update(相对路径字节)` + `update(文件内容)` 累加 sha256）：
  `aeb679cf5bfcacc8bf5e033adcd525b926ad095d89782e3631b614831c9d5cde`
- 复算片段：

```python
import hashlib, pathlib
root = pathlib.Path("dataset/tasks")
h = hashlib.sha256()
for p in sorted(root.rglob("*"), key=lambda x: str(x.relative_to(root))):
    if p.is_file():
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
print(h.hexdigest())
```

## 结构
| 项 | 数量 |
|---|---|
| 任务总数 | 250 |
| 单缺陷任务 | 65（独立缺陷结构） |
| 双缺陷组合任务 | 185 |
| 类别 | 8 类：boundary_compare 36、paging_index 35、reduce_init 36、mutable_default 35、cache_state 27、parsing_robustness 27、recursion_base 27、type_conversion 27 |

`task.json` 关键字段：
- `prompt`：交给 Agent 的任务描述（不含答案）；
- `reference`：参考解文件路径 → 内容（判定阶段写入）；
- `hidden_tests`：隐藏测试源码（判定阶段写入，Agent 不可见）；
- `bug_category` / `family` / `kind`：任务元数据，**仅用于离线统计与污染分析**，不进入 Agent 上下文。

## 连续任务流（由 `bench/stream.py` 生成，不复制进 dataset）
- 120 个逻辑任务（每类 15）× 3 轮 = 360 题；seed 固定 11。
- 轮 0 用原始目录；轮 1/2 把模块名与模块级函数/类名加 `_x1` / `_x2` 后缀后写到
  `experiments/stream/c1|c2/`，不改缺陷结构。
- 因此**独立缺陷结构仍是 65 个**；360 题是"65 个缺陷结构的重复暴露 + 表面改名"，
  统计时必须按"逻辑任务"聚类，不能把 360 当 360 个独立样本。

## 复现
```bash
python -m bench.stream --build --verify   # 生成流并自检：缺陷版必失败、参考解必通过
```
自检结果：360/360 通过。

## 许可与隐私
任务代码是合成教学用例（纯标准库、无外部依赖），不含个人信息、密钥或真实业务数据。
沿用上游仓库的许可；若单独发布本快照，请保留本说明与来源标注。
