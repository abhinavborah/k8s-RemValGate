"""Retrieval, score and selective metrics for the evaluation harness.

Retrieval metrics (Hit@k, MRR, Recall@k) take each query with its results, best first.
Relevance is passed as a function of the query and a result, so the relevance label stays the
caller's choice: a fault-type match, or a stricter fault type plus target service label.
Score metrics (AUROC, Brier) and selective metrics (AURC, harmful rate at coverage, top-1
accuracy) take candidate scores with binary labels: a label must equal 0 or 1, as a bool or
an int; any other label raises ValueError.
"""

import math
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
    # a nan breaks the sort order, so it would give a wrong metric with no error
    if any(math.isnan(s) for s in scores):
        raise ValueError("scores hold nan")
    # "false" or None read from a file would pass bool() as a positive or a negative
    if any(y not in (0, 1) for y in labels):
        raise ValueError("labels must be bool or 0/1")


def auroc(scores: Sequence[float], labels: Sequence[bool]) -> float:
    """Probability that a random positive scores above a random negative, ties one half.

    Mann-Whitney U from average ranks, O(n log n). Raises ValueError for empty input, a length
    mismatch, a nan score, a label not equal to 0 or 1, or labels that do not hold both
    classes.
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

    Raises ValueError for empty input, a length mismatch, a label not equal to 0 or 1, or a nan
    or a probability outside [0, 1].
    """
    _check_pairs(probabilities, labels)
    if any(not 0.0 <= p <= 1.0 for p in probabilities):
        raise ValueError("probabilities must lie in [0, 1]")
    return sum((p - bool(y)) ** 2 for p, y in zip(probabilities, labels, strict=True)) / len(labels)


def _expected_flagged_in_top(scores: Sequence[float], flags: Sequence[bool], m: int) -> float:
    """Expected number of flagged items among the top m by score.

    Equal scores form a group. A group inside the top m counts fully; the group that straddles
    the cut contributes its flagged share times the places it fills, which is the expectation
    over all orderings of the tied items.
    """
    groups: dict[float, list[int]] = {}  # score -> [size, flagged]
    for s, f in zip(scores, flags, strict=True):
        g = groups.setdefault(s, [0, 0])
        g[0] += 1
        g[1] += bool(f)
    total = 0.0
    taken = 0
    for s in sorted(groups, reverse=True):
        size, flagged = groups[s]
        if taken + size <= m:
            total += flagged
        else:
            total += (m - taken) * flagged / size
            break
        taken += size
    return total


def aurc(scores: Sequence[float], risky: Sequence[bool]) -> float:
    """Area under the risk-coverage curve, ranking by score, highest first.

    The mean over m = 1..n of the risky share among the top m. Which risk the label marks is
    a parameter, not fixed here (an open group decision). Ties count in expectation over all
    orderings of the tied group, as in AUROC. Raises ValueError for empty input, a length
    mismatch, a nan score, or a label not equal to 0 or 1.
    """
    _check_pairs(scores, risky)
    n = len(scores)
    return sum(_expected_flagged_in_top(scores, risky, m) / m for m in range(1, n + 1)) / n


def harmful_rate_at_coverage(
    scores: Sequence[float], harmful: Sequence[bool], coverage: float
) -> float:
    """Expected harmful share among the top m = max(1, ceil(coverage * n)) items by score.

    Coverage lies in (0, 1]; a tiny coverage still keeps one item. Ties at the cut count in
    expectation over all orderings of the tied group, as in AUROC. Raises ValueError for empty
    input, a length mismatch, a nan score, a label not equal to 0 or 1, or coverage outside
    (0, 1].
    """
    _check_pairs(scores, harmful)
    if not 0.0 < coverage <= 1.0:
        raise ValueError(f"coverage must lie in (0, 1], got {coverage}")
    # the epsilon stops float noise rounding up: 0.07 * 100 is 7.000000000000001, ceil gives 8
    m = max(1, math.ceil(coverage * len(scores) - 1e-9))
    return _expected_flagged_in_top(scores, harmful, m) / m


def top1_accuracy(problems: Sequence[Sequence[tuple[float, bool]]]) -> float:
    """Share of problems whose highest-scored candidate works.

    Each problem is a list of (score, works) pairs. A tie at the top counts the working share of
    the tied group. Raises ValueError for no problems, a problem with no candidates, a nan
    score, or a works flag not equal to 0 or 1.
    """
    if not problems:
        raise ValueError("problems are empty")
    total = 0.0
    for candidates in problems:
        if not candidates:
            raise ValueError("a problem has no candidates")
        scores = [score for score, _ in candidates]
        works = [w for _, w in candidates]
        _check_pairs(scores, works)
        total += _expected_flagged_in_top(scores, works, 1)
    return total / len(problems)
