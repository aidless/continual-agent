# 检索消融（同一真实记忆库快照）

recall@3 的分母是 query×3 个空位；coarse=同 bug 类别，fine=同逻辑题。

| 库规模 | 策略 | coarse recall@3 | fine recall@3 | 检索 ms |
|---|---|---|---|---|
| 0–10 | category | 0.000 | 0.000 | 0.27 |
| 0–10 | symptom | 0.000 | 0.000 | 0.07 |
| 0–10 | embedding | 0.000 | 0.000 | 0.59 |
