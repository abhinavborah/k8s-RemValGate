import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.metrics import hit_at_k, mrr, recall_at_k

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
