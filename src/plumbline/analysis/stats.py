"""
Confidence intervals and comparisons.

A reliability certificate that reports "conformance 87.5%" from 40 runs and
stops there is not evidence, it is a point estimate wearing a suit. With 40
runs, 87.5% has a 95% interval of roughly 74% to 94%. An engineer deciding
whether an agent is safe to ship needs the interval, and a reviewer comparing
two architectures needs to know whether the gap between them survives it.

Wilson intervals rather than the normal approximation, because proportions here
are routinely near 0 or 1 where the normal approximation produces intervals that
extend past the ends of the scale and understate uncertainty exactly when the
result looks best.

Comparisons between arms use a two-sided permutation test. It makes no
distributional assumption, which matters because these are small samples of
correlated binary outcomes, and it is exact enough at these sizes to compute
directly.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

Z_95 = 1.959963984540054


@dataclass
class Proportion:
    successes: int
    total: int
    lo: float = 0.0
    hi: float = 0.0

    @property
    def value(self) -> float:
        return (self.successes / self.total) if self.total else 0.0

    @property
    def pct(self) -> float:
        return 100.0 * self.value

    def describe(self, digits: int = 1) -> str:
        if not self.total:
            return "n/a"
        return (f"{self.pct:.{digits}f}% "
                f"[{100 * self.lo:.{digits}f}, {100 * self.hi:.{digits}f}] "
                f"n={self.total}")

    def to_dict(self) -> dict:
        return {"successes": self.successes, "total": self.total,
                "value": round(self.value, 6),
                "ci_low": round(self.lo, 6), "ci_high": round(self.hi, 6)}


def wilson(successes: int, total: int, z: float = Z_95) -> Proportion:
    """Wilson score interval for a binomial proportion."""
    if total <= 0:
        return Proportion(0, 0, 0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    half = (z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))) / denom
    return Proportion(successes, total, max(0.0, centre - half),
                      min(1.0, centre + half))


@dataclass
class Comparison:
    label_a: str
    label_b: str
    a: Proportion
    b: Proportion
    diff: float              # b - a, in percentage points
    p_value: float
    n_permutations: int

    @property
    def significant(self) -> bool:
        return self.p_value < 0.05

    def describe(self) -> str:
        mark = "significant" if self.significant else "not significant"
        return (f"{self.label_a} {self.a.pct:.1f}% -> {self.label_b} {self.b.pct:.1f}%  "
                f"({self.diff:+.1f} pts, p={self.p_value:.4f}, {mark})")

    def to_dict(self) -> dict:
        return {"a": self.label_a, "b": self.label_b,
                "a_stats": self.a.to_dict(), "b_stats": self.b.to_dict(),
                "diff_pts": round(self.diff, 3),
                "p_value": round(self.p_value, 6),
                "significant_at_05": self.significant,
                "n_permutations": self.n_permutations}


def permutation_test(a: list[bool], b: list[bool], *, n: int = 20_000,
                     seed: int = 11) -> float:
    """Two-sided permutation test on the difference in success rates.

    Pools both samples, reshuffles the group labels n times, and asks how often
    a difference at least this large appears by chance alone.
    """
    if not a or not b:
        return 1.0
    obs = abs(sum(b) / len(b) - sum(a) / len(a))
    pool = list(a) + list(b)
    na = len(a)
    rng = random.Random(seed)
    extreme = 0
    for _ in range(n):
        rng.shuffle(pool)
        pa = sum(pool[:na]) / na
        pb = sum(pool[na:]) / (len(pool) - na)
        if abs(pb - pa) >= obs - 1e-12:
            extreme += 1
    # add-one smoothing: a p-value of exactly zero overstates certainty
    return (extreme + 1) / (n + 1)


def compare(label_a: str, a: list[bool], label_b: str, b: list[bool],
            *, n_permutations: int = 20_000) -> Comparison:
    pa, pb = wilson(sum(a), len(a)), wilson(sum(b), len(b))
    p = permutation_test(a, b, n=n_permutations)
    return Comparison(label_a, label_b, pa, pb,
                      (pb.value - pa.value) * 100.0, p, n_permutations)


def bootstrap_ci(values: list[float], *, n: int = 10_000, seed: int = 13,
                 alpha: float = 0.05) -> tuple[float, float]:
    """Percentile bootstrap interval for a mean, used for continuous measures
    such as step count or latency where Wilson does not apply."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    k = len(values)
    means = []
    for _ in range(n):
        means.append(sum(rng.choice(values) for _ in range(k)) / k)
    means.sort()
    lo = means[int((alpha / 2) * n)]
    hi = means[min(n - 1, int((1 - alpha / 2) * n))]
    return (lo, hi)
