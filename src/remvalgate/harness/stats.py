"""Paired tests and intervals for the evaluation harness.

McNemar mid-p compares two systems run once each on the same problems, using only the
discordant pairs. Wilson gives an interval for one proportion. Bonett-Price gives an interval
for the difference of two paired proportions. The incident-clustered bootstrap is provisional:
which interval is primary for the candidate-level comparison is still open for the group.
Counts must be non-negative ints; anything else raises ValueError.
"""

import math
import random
from collections.abc import Callable, Sequence
from statistics import NormalDist
from typing import TypeVar

T = TypeVar("T")


def _check_counts(**counts: int) -> None:
    for name, value in counts.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{name} must be a non-negative int, got {value!r}")


def _z(conf: float) -> float:
    if not 0 < conf < 1:
        raise ValueError(f"conf must be between 0 and 1, got {conf}")
    return NormalDist().inv_cdf(1 - (1 - conf) / 2)


def mcnemar_midp(n12: int, n21: int) -> float:
    """Two-sided McNemar mid-p value from the two discordant counts.

    Follows Fagerland, Lydersen and Laake 2013, equations 5 and 6: the exact conditional
    two-sided p minus the point probability of the observed count, or 1 - f / 2 when the
    counts are equal. With no discordant pairs there is no evidence and the result is 1.0.
    Raises ValueError for a negative or non-int count.
    """
    _check_counts(n12=n12, n21=n21)
    n = n12 + n21
    if n == 0:
        return 1.0
    k = min(n12, n21)
    point = math.comb(n, k) / 2**n
    if n12 == n21:
        return 1 - point / 2
    return 2 * sum(math.comb(n, x) for x in range(k + 1)) / 2**n - point


def wilson(x: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Wilson score interval for the proportion x / n, without continuity correction.

    Raises ValueError for a negative or non-int count, n = 0, x > n, or conf outside (0, 1).
    """
    _check_counts(x=x, n=n)
    if n == 0 or x > n:
        raise ValueError(f"need 0 <= x <= n and n >= 1, got x={x}, n={n}")
    z = _z(conf)
    p = x / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    denom = 1 + z * z / n
    return max(0.0, (centre - half) / denom), min(1.0, (centre + half) / denom)


def bonett_price(n12: int, n21: int, n: int, conf: float = 0.95) -> tuple[float, float]:
    """Bonett-Price adjusted Wald interval for the paired difference (n12 - n21) / n.

    n12 counts pairs where only the first system succeeds, n21 pairs where only the second
    does, and n all pairs. Adds one to each discordant count and two to n (Bonett and Price
    2012); the interval is clipped to [-1, 1]. Raises ValueError for a negative or non-int
    count, n = 0, n12 + n21 > n, or conf outside (0, 1).
    """
    _check_counts(n12=n12, n21=n21, n=n)
    if n == 0 or n12 + n21 > n:
        raise ValueError(f"need n >= 1 and n12 + n21 <= n, got {n12} + {n21}, n={n}")
    p12 = (n12 + 1) / (n + 2)
    p21 = (n21 + 1) / (n + 2)
    diff = p12 - p21
    half = _z(conf) * math.sqrt((p12 + p21 - diff * diff) / (n + 2))
    return max(-1.0, diff - half), min(1.0, diff + half)


def cluster_bootstrap(
    clusters: Sequence[Sequence[T]],
    stat: Callable[[list[T]], float],
    n_resamples: int = 2000,
    conf: float = 0.95,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap interval that resamples whole clusters (incidents).

    Each resample draws len(clusters) clusters with replacement and passes all their items,
    flattened, to stat, so no item is split from its cluster. Provisional: the percentile
    bootstrap may under-cover at about 16 clusters. Raises ValueError for no clusters, an
    empty cluster, n_resamples < 1 or conf outside (0, 1).
    """
    if len(clusters) == 0 or any(len(c) == 0 for c in clusters):
        raise ValueError("clusters are empty or contain an empty cluster")
    if n_resamples < 1:
        raise ValueError(f"n_resamples must be at least 1, got {n_resamples}")
    _z(conf)
    rng = random.Random(seed)
    values = sorted(
        stat([item for c in rng.choices(clusters, k=len(clusters)) for item in c])
        for _ in range(n_resamples)
    )
    alpha = 1 - conf
    # shortcut: nearest-rank percentiles, no interpolation; fine at thousands of resamples
    return (
        values[math.floor(alpha / 2 * (n_resamples - 1))],
        values[math.ceil((1 - alpha / 2) * (n_resamples - 1))],
    )
