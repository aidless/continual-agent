PY := python3
ARMS := no_memory,write_all,write_fail,write_verified

.PHONY: repro verify stream smoke phase1 phase2 report ablation clean

repro: stream smoke report

verify: stream
	$(PY) -m bench.stream --verify

stream:
	$(PY) -m bench.stream --build

smoke: stream
	$(PY) -m bench.runner --limit 2 --arms no_memory,write_verified --label smoke --parallel 2 --reset
	$(PY) -m bench.report --runs experiments/runs/smoke --out experiments/report/smoke

phase1: stream
	$(PY) -m bench.runner --limit 20 --arms $(ARMS) --label phase1 --parallel 4 --reset
	$(PY) -m bench.report --runs experiments/runs/phase1 --out experiments/report/phase1

phase2: stream
	$(PY) -m bench.runner --arms $(ARMS) --label phase2 --parallel 4 --reset
	$(PY) -m bench.report --runs experiments/runs/phase2 --out experiments/report/phase2
	$(PY) -m bench.retrieval_ablation --snapshots experiments/memory/phase2/write_fail_snapshots.jsonl --out experiments/report/phase2/retrieval_ablation.csv

report:
	$(PY) -m bench.report --runs experiments/runs/phase1 --out experiments/report/phase1

ablation:
	$(PY) -m bench.retrieval_ablation --snapshots experiments/memory/phase1/write_fail_snapshots.jsonl --out experiments/report/phase1/retrieval_ablation.csv

clean:
	rm -rf experiments/work experiments/traces experiments/reflection_raw
