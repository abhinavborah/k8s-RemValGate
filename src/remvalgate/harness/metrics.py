"""Retrieval and score metrics for the evaluation harness.

Retrieval metrics (Hit@k, MRR, Recall@k) take each query with its results, best first.
Relevance is passed as a function of the query and a result, so the relevance label stays the
caller's choice: a fault-type match, or a stricter fault type plus target service label.
Score metrics (AUROC, Brier) take candidate scores with binary labels.
"""

from collections.abc import Callable, Sequence
from typing import TypeVar

Q = TypeVar("Q")
T = TypeVar("T")


def _check(results: Sequence[object], k: int | None = None) -> None:
    if not results:
        raise ValueError("results are empty")
    if k is not None and k < 1:
        raise ValueError(f"k must be at least 1, got {k}")


def hit_at_k(
    results: Sequence[tuple[Q, Sequence[T]]], relevant: Callable[[Q, T], bool], k: int
) -> float:
    """Share of queries with a relevant result in the first k.

    A k larger than a list uses the whole list. A query with no relevant result counts as 0.
    Raises ValueError for empty results or k < 1.
    """
    _check(results, k)
    return sum(any(relevant(q, t) for t in ranked[:k]) for q, ranked in results) / len(results)


def mrr(results: Sequence[tuple[Q, Sequence[T]]], relevant: Callable[[Q, T], bool]) -> float:
    """Mean over queries of 1 / rank of the first relevant result, 0 when there is none.

    Raises ValueError for empty results.
    """
    _check(results)
    total = 0.0
    for q, ranked in results:
        rank = next((i for i, t in enumerate(ranked, 1) if relevant(q, t)), None)
        total += 1 / rank if rank else 0.0
    return total / len(results)


def recall_at_k(
    results: Sequence[tuple[Q, Sequence[T]]],
    relevant: Callable[[Q, T], bool],
    total_relevant: Callable[[Q], int],
    k: int,
) -> float:
    """Mean over queries of (relevant results in the first k) / total_relevant(q).

    The denominator comes from total_relevant, not from the list, since the list may hold
    only part of the relevant items. Results are assumed unique: a repeated result is not
    removed and counts again. Raises ValueError for empty results, k < 1, a
    total_relevant of 0, or a total_relevant below the relevant hits found in the first k.
    """
    _check(results, k)
    total = 0.0
    for q, ranked in results:
        found = sum(relevant(q, t) for t in ranked[:k])
        n = total_relevant(q)
        if n < 1:
            raise ValueError(f"total_relevant must be at least 1, got {n}")
        if found > n:
            raise ValueError(f"total_relevant is {n} but {found} relevant results were found")
        total += found / n
    return total / len(results)


def _check_pairs(scores: Sequence[float], labels: Sequence[bool]) -> None:
    if not scores:
        raise ValueError("scores are empty")
    if len(scores) != len(labels):
        raise ValueError(f"{len(scores)} scores but {len(labels)} labels")


def auroc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Probability that a random positive scores above a random negative, ties one half.

    Mann-Whitney U from average ranks, O(n log n). Raises ValueError for empty input, a length
    mismatch, or labels that do not hold both classes.
    """
    _check_pairs(scores, labels)
    n_pos = sum(bool(y) for y in labels)
    n_neg = len(labels) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("labels must hold both classes")
    order = sorted(range(len(scores)), key=scores.__getitem__)
    rank_sum = 0.0
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average_rank = (i + j) / 2 + 1  # ranks are 1-based; a tied group shares their mean
        rank_sum += average_rank * sum(bool(labels[order[m]]) for m in range(i, j + 1))
        i = j + 1
    return (rank_sum - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def brier(probabilities: Sequence[float], labels: Sequence[bool]) -> float:
    """Mean of (p - y)^2 with y as 1 or 0.

    Raises ValueError for empty input, a length mismatch, or a probability outside [0, 1].
    """
    _check_pairs(probabilities, labels)
    if any(not 0.0 <= p <= 1.0 for p in probabilities):
        raise ValueError("probabilities must lie in [0, 1]")
    return sum((p - bool(y)) ** 2 for p, y in zip(probabilities, labels, strict=True)) / len(labels)
