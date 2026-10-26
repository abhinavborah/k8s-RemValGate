from itertools import pairwise, permutations

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from sklearn.metrics import brier_score_loss, roc_auc_score

from remvalgate.harness.metrics import (
    aurc,
    auroc,
    brier,
    harmful_rate_at_coverage,
    hit_at_k,
    mrr,
    recall_at_k,
    top1_accuracy,
)

# synthetic fixtures only: three queries by fault type, results tagged by fault type
R = [("a", ["a", "b", "c"]), ("b", ["a", "c", "b"]), ("c", ["a", "b", "a"])]
TOTALS = {"a": 2, "b": 1, "c": 1}


def _match(query, result):
    return query == result


def _total(query):
    return TOTALS[query]


# AC: Hit@k is the share of queries with a relevant result in the first k
def test_hit_at_k():
    # k=1: (1 + 0 + 0) / 3
    assert hit_at_k(R, _match, 1) == pytest.approx(1 / 3, abs=1e-9)
    # k=3: (1 + 1 + 0) / 3
    assert hit_at_k(R, _match, 3) == pytest.approx(2 / 3, abs=1e-9)


def test_hit_at_k_beyond_the_list_uses_the_whole_list():
    assert hit_at_k(R, _match, 10) == hit_at_k(R, _match, 3)


def test_hit_at_k_counts_a_miss_as_zero():
    # c's list has no "c": the query stays in the denominator
    assert hit_at_k([("c", ["a", "b"])], _match, 2) == 0.0


# AC: MRR is the mean reciprocal rank of the first relevant result
def test_mrr():
    # (1/1 + 1/3 + 0) / 3 = 4/9
    assert mrr(R, _match) == pytest.approx(4 / 9, abs=1e-9)


def test_mrr_uses_the_first_relevant_result_only():
    # "a" at rank 2 and 3: 1/2, not 1/2 + 1/3
    assert mrr([("a", ["b", "a", "a"])], _match) == pytest.approx(0.5, abs=1e-9)


# AC: Recall@k divides by the total relevant count from total_relevant
def test_recall_at_k():
    # k=3: (1/2 + 1/1 + 0/1) / 3 = 1/2
    assert recall_at_k(R, _match, _total, 3) == pytest.approx(0.5, abs=1e-9)


def test_recall_at_k_counts_every_hit_in_the_first_k():
    # query "a" finds "a" twice: it has 2 hits of 2 in k=3, 1 of 2 in k=1
    assert recall_at_k([("a", ["a", "b", "a"])], _match, _total, 3) == 1.0
    assert recall_at_k([("a", ["a", "b", "a"])], _match, _total, 1) == 0.5


@pytest.mark.parametrize(
    "call",
    [
        lambda: hit_at_k([], _match, 1),
        lambda: mrr([], _match),
        lambda: recall_at_k([], _match, _total, 1),
    ],
)
def test_empty_results_raise(call):
    with pytest.raises(ValueError, match="empty"):
        call()


@pytest.mark.parametrize("k", [0, -1])
def test_k_below_one_raises(k):
    with pytest.raises(ValueError, match="k must be"):
        hit_at_k(R, _match, k)
    with pytest.raises(ValueError, match="k must be"):
        recall_at_k(R, _match, _total, k)


def test_total_relevant_zero_raises():
    with pytest.raises(ValueError, match="total_relevant"):
        recall_at_k(R, _match, lambda q: 0, 3)


def test_total_relevant_below_hits_found_raises():
    # "a" is found twice in the first 3, so a total of 1 is inconsistent
    with pytest.raises(ValueError, match="total_relevant"):
        recall_at_k([("a", ["a", "b", "a"])], _match, lambda q: 1, 3)


@given(
    results=st.lists(st.lists(st.booleans(), min_size=1, max_size=8), min_size=1, max_size=6),
    k=st.integers(min_value=1, max_value=10),
)
def test_hit_at_k_monotone_and_retrieval_metrics_bounded(results, k):
    rows = [(i, flags) for i, flags in enumerate(results)]

    def relevant(_, flag):
        return flag

    low, high = hit_at_k(rows, relevant, k), hit_at_k(rows, relevant, k + 1)
    assert 0.0 <= low <= high <= 1.0
    assert 0.0 <= mrr(rows, relevant) <= 1.0


# AUROC and Brier: scikit-learn is the reference, in tests only
SCORES = [0.9, 0.5, 0.5, 0.1]
LABELS = [True, True, False, False]


# AC: AUROC is the probability a positive outscores a negative, ties one half
def test_auroc_hand_check():
    # pairs (pos, neg): (0.9,0.5)=1, (0.9,0.1)=1, (0.5,0.5)=0.5, (0.5,0.1)=1 -> 3.5 / 4
    assert auroc(SCORES, LABELS) == pytest.approx(0.875, abs=1e-9)


def test_auroc_matches_sklearn_with_a_tie_and_bool_or_int_labels():
    expected = roc_auc_score([1, 1, 0, 0], SCORES)
    assert auroc(SCORES, LABELS) == pytest.approx(expected, abs=1e-9)
    assert auroc(SCORES, [1, 1, 0, 0]) == pytest.approx(expected, abs=1e-9)


# AC: Brier is the mean squared gap between probability and label
def test_brier_hand_check():
    # ((0.9-1)^2 + (0.5-1)^2 + (0.5-0)^2 + (0.1-0)^2) / 4 = (0.01 + 0.25 + 0.25 + 0.01) / 4
    assert brier(SCORES, LABELS) == pytest.approx(0.13, abs=1e-9)


def test_brier_matches_sklearn():
    expected = brier_score_loss([1, 1, 0, 0], SCORES)
    assert brier(SCORES, LABELS) == pytest.approx(expected, abs=1e-9)


@pytest.mark.parametrize(
    "call",
    [
        lambda: auroc([], []),
        lambda: brier([], []),
    ],
)
def test_score_metrics_reject_empty_input(call):
    with pytest.raises(ValueError, match="empty"):
        call()


@pytest.mark.parametrize(
    "call",
    [
        lambda: auroc([0.1, 0.2], [True]),
        lambda: brier([0.1, 0.2], [True]),
    ],
)
def test_score_metrics_reject_mismatched_input(call):
    # match the length message: one-class labels would raise too
    with pytest.raises(ValueError, match="but"):
        call()


@pytest.mark.parametrize("labels", [[True, True], [False, False]])
def test_auroc_one_class_raises(labels):
    with pytest.raises(ValueError, match="both"):
        auroc([0.3, 0.7], labels)


def test_brier_probability_outside_unit_interval_raises():
    with pytest.raises(ValueError, match="probabilit"):
        brier([1.5, 0.5], [True, False])


finite = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False)
prob = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(rows=st.lists(st.tuples(finite, st.booleans()), min_size=2, max_size=40))
def test_auroc_properties(rows):
    scores = [s for s, _ in rows]
    labels = [y for _, y in rows]
    assume(any(labels) and not all(labels))
    value = auroc(scores, labels)
    assert value == pytest.approx(roc_auc_score([int(y) for y in labels], scores), abs=1e-9)
    assert auroc([-s for s in scores], labels) == pytest.approx(1 - value, abs=1e-9)


@given(rows=st.lists(st.tuples(prob, st.booleans()), min_size=1, max_size=40))
def test_brier_properties(rows):
    probs = [p for p, _ in rows]
    labels = [y for _, y in rows]
    value = brier(probs, labels)
    assert 0.0 <= value <= 1.0
    expected = brier_score_loss([int(y) for y in labels], probs)
    assert value == pytest.approx(expected, abs=1e-9)


# Selective metrics: synthetic fixtures, hand-worked values
S4 = [0.9, 0.8, 0.7, 0.6]
H4 = [False, True, False, True]


# AC: AURC is the mean over m = 1..n of the risky share in the top m by score
def test_aurc_hand_check():
    # risks at m=1..4: 0, 1/2, 1/3, 1/2 -> (0 + 1/2 + 1/3 + 1/2) / 4 = 1/3
    assert aurc(S4, H4) == pytest.approx(1 / 3, abs=1e-9)


def test_aurc_ties_count_in_expectation():
    # m=1: one of the two tied items, 1/2; m=2: both, 1/2 -> 1/2
    assert aurc([1, 1], [True, False]) == pytest.approx(0.5, abs=1e-9)


# AC: harmful rate at coverage c is the harmful share among the top ceil(c * n) by score
@pytest.mark.parametrize(
    ("coverage", "expected"),
    [
        (0.25, 0.0),  # m=1: the top item is fine
        (0.5, 0.5),  # m=2: one harmful of two
        (1.0, 0.5),  # m=4: two harmful of four
    ],
)
def test_harmful_rate_at_coverage_hand_check(coverage, expected):
    assert harmful_rate_at_coverage(S4, H4, coverage) == pytest.approx(expected, abs=1e-9)


def test_harmful_rate_ties_at_the_cut_count_in_expectation():
    # m=1 of two tied items, one harmful: 1/2
    assert harmful_rate_at_coverage([1, 1], [True, False], 0.5) == pytest.approx(0.5, abs=1e-9)


def test_harmful_rate_float_cut_keeps_exactly_seven_of_a_hundred():
    # 0.07 * 100 is 7.000000000000001 in floating point; a plain ceil would keep 8.
    # Harmful at ranks 2, 5 and 8: top 7 gives 2/7, top 8 would give 3/8.
    scores = [100 - i for i in range(100)]
    harmful = [i in (1, 4, 7) for i in range(100)]
    assert harmful_rate_at_coverage(scores, harmful, 0.07) == pytest.approx(2 / 7, abs=1e-9)


@pytest.mark.parametrize("coverage", [1e-6, 1e-12])
def test_harmful_rate_tiny_coverage_keeps_one_item(coverage):
    # 1e-12 * 4 falls below the epsilon, so the cut must still keep the top item
    assert harmful_rate_at_coverage(S4, H4, coverage) == 0.0


@pytest.mark.parametrize("coverage", [0, 1.5, -0.1])
def test_harmful_rate_coverage_outside_unit_interval_raises(coverage):
    with pytest.raises(ValueError, match="coverage"):
        harmful_rate_at_coverage(S4, H4, coverage)


# AC: top-1 accuracy is the working share of the highest-scored candidate, ties shared
def test_top1_accuracy_hand_check():
    problems = [
        [(0.9, True), (0.1, False)],  # top works: 1
        [(0.8, False), (0.3, True)],  # top fails: 0
        [(0.5, True), (0.5, False)],  # tie, one of two works: 1/2
    ]
    # (1 + 0 + 1/2) / 3 = 1/2
    assert top1_accuracy(problems) == pytest.approx(0.5, abs=1e-9)


@pytest.mark.parametrize(
    "call",
    [
        lambda: aurc([], []),
        lambda: harmful_rate_at_coverage([], [], 0.5),
        lambda: top1_accuracy([]),
    ],
)
def test_selective_metrics_reject_empty_input(call):
    with pytest.raises(ValueError, match="empty"):
        call()


@pytest.mark.parametrize(
    "call",
    [
        lambda: aurc([0.1, 0.2], [True]),
        lambda: harmful_rate_at_coverage([0.1, 0.2], [True], 0.5),
    ],
)
def test_selective_metrics_reject_mismatched_input(call):
    with pytest.raises(ValueError, match="but"):
        call()


def test_top1_accuracy_problem_without_candidates_raises():
    with pytest.raises(ValueError, match="no candidates"):
        top1_accuracy([[(0.5, True)], []])


@given(rows=st.lists(st.tuples(finite, st.booleans()), min_size=1, max_size=40))
def test_selective_metric_properties(rows):
    scores = [s for s, _ in rows]
    risky = [y for _, y in rows]
    n = len(rows)
    value = aurc(scores, risky)
    assert 0.0 <= value <= 1.0
    rates = [harmful_rate_at_coverage(scores, risky, m / n) for m in range(1, n + 1)]
    assert all(0.0 <= r <= 1.0 for r in rates)
    assert value == pytest.approx(sum(rates) / n, abs=1e-9)


def _orderings(scores):
    # every ordering of the items that ranks them by score, highest first; each ordering of a
    # tied group appears once, so all are equally likely
    return [
        order
        for order in permutations(range(len(scores)))
        if all(scores[a] >= scores[b] for a, b in pairwise(order))
    ]


@given(rows=st.lists(st.tuples(st.sampled_from([1, 2, 3]), st.booleans()), min_size=1, max_size=6))
def test_selective_metrics_match_brute_force_over_tie_orderings(rows):
    # independent reference: average the plain metric over every score-consistent ordering
    scores = [s for s, _ in rows]
    flags = [f for _, f in rows]
    n = len(rows)
    orders = _orderings(scores)
    risks = [
        sum(sum(flags[i] for i in o[:m]) / m for o in orders) / len(orders) for m in range(1, n + 1)
    ]
    for m, risk in enumerate(risks, 1):
        assert harmful_rate_at_coverage(scores, flags, m / n) == pytest.approx(risk, abs=1e-9)
    assert aurc(scores, flags) == pytest.approx(sum(risks) / n, abs=1e-9)
    first = sum(flags[o[0]] for o in orders) / len(orders)
    assert top1_accuracy([list(zip(scores, flags, strict=True))]) == pytest.approx(first, abs=1e-9)
