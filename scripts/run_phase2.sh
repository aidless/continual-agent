#!/usr/bin/env bash
# 分三段跑完 360 题 × 4 臂（120 × 4 × 3），每段出一次累计中间报告；
# 可随时中断，重跑本脚本会按 task_id 跳过已完成题（第 1 段只在全新目录时重置）。
set -euo pipefail
cd "$(dirname "$0")/.."
ARMS="no_memory,write_all,write_fail,write_verified"

for idx in 1 2 3; do
  seg=$(( (idx - 1) * 120 ))
  echo "=== P2 第 ${idx} 段：start=${seg} limit=120 $(date) ==="
  extra=""
  # 只在完全没有 P2 数据时重置；已有数据一律续跑（runner 按 task_id 跳过已完成题）
  if [ ! -d experiments/runs/phase2 ] || [ -z "$(ls -A experiments/runs/phase2 2>/dev/null)" ]; then
    extra="--reset"
  fi
  python3 -m bench.runner --start "$seg" --limit 120 --arms "$ARMS" \
    --label phase2 --parallel 4 $extra
  python3 -m bench.report --runs experiments/runs/phase2 \
    --out "experiments/report/p2_seg${idx}"
  echo "=== P2 第 ${idx} 段报告 -> experiments/report/p2_seg${idx}（累计口径）==="
done

python3 -m bench.report --runs experiments/runs/phase2 --out experiments/report/phase2
python3 -m bench.retrieval_ablation \
  --snapshots experiments/memory/phase2/write_fail_snapshots.jsonl \
  --out experiments/report/phase2/retrieval_ablation.csv
