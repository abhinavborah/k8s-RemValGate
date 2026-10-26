"""Retrieval metrics for the evaluation harness.

Retrieval metrics (Hit@k, MRR, Recall@k) take each query with its results, best first.
Relevance is passed as a function of the query and a result, so the relevance label stays the
caller's choice: a fault-type match, or a stricter fault type plus target service label.
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
