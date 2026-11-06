"""统计工具：Wilson CI、McNemar 精确检验、bootstrap 斜率。全部只用标准库。"""
from __future__ import annotations

import math
import random


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def binom_pmf(k: int, n: int, p: float = 0.5) -> float:
    return math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))


def mcnemar_exact(b: int, c: int) -> float:
    """配对检验：b=仅 A 成功，c=仅 B 成功；双侧精确二项检验。"""
    n = b + c
    if n == 0:
        return 1.0
    obs = min(b, c)
    p = sum(binom_pmf(i, n) for i in range(obs + 1)) * 2
    return min(1.0, p)


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def bootstrap_ci(xs: list[float], n_boot: int = 20000, seed: int = 7, alpha: float = 0.05) -> tuple[float, float]:
    if not xs:
        return 0.0, 0.0
    rng = random.Random(seed)
    n = len(xs)
    vals = sorted(mean([xs[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot))
    lo = vals[int(alpha / 2 * n_boot)]
    hi = vals[int((1 - alpha / 2) * n_boot) - 1]
    return lo, hi


def paired_diff_ci(diffs: list[float], n_boot: int = 20000, seed: int = 7) -> tuple[float, float, float]:
    """配对差（百分比点）的均值与 95% CI。"""
    m = mean(diffs)
    lo, hi = bootstrap_ci(diffs, n_boot=n_boot, seed=seed)
    return m, lo, hi


def slope_per_50(xs: list[float], ys: list[float]) -> float:
    """最小二乘斜率，换算成"每 50 个任务"的成功率变化（ys 为 0/1）。"""
    n = len(xs)
    if n < 3:
        return 0.0
    mx = mean(xs)
    my = mean(ys)
    denom = sum((x - mx) ** 2 for x in xs)
    if denom == 0:
        return 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / denom
    return b * 50


def slope_ci(xs: list[float], ys: list[float], n_boot: int = 20000, seed: int = 7) -> tuple[float, float, float]:
    point = slope_per_50(xs, ys)
    n = len(xs)
    if n < 3:
        return point, 0.0, 0.0
    rng = random.Random(seed)
    vals = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        b = slope_per_50([xs[i] for i in idx], [ys[i] for i in idx])
        vals.append(b)
    vals.sort()
    return point, vals[int(0.025 * n_boot)], vals[int(0.975 * n_boot) - 1]
