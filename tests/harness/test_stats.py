from collections import Counter

import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy.stats import binomtest

from remvalgate.harness.stats import bonett_price, cluster_bootstrap, mcnemar_midp, wilson


def _exact(n12, n21):
    return binomtest(n12, n12 + n21, 0.5).pvalue


# AC: published examples reproduce. Fagerland, Lydersen and Laake 2013, BMC Med Res
# Methodol 13:91, Tables 1, 2 and 6: Bentur (n12=1, n21=7) and Cavo (n12=6, n21=16)
@pytest.mark.parametrize("n12, n21, exact, midp", [(1, 7, 0.0703, 0.0391), (6, 16, 0.0525, 0.0347)])
def test_mcnemar_published(n12, n21, exact, midp):
    assert mcnemar_midp(n12, n21) == pytest.approx(midp, abs=5e-5)
    assert _exact(n12, n21) == pytest.approx(exact, abs=5e-5)


def test_mcnemar_ties_and_empty():
    # equation 6: 1 - f / 2 when the counts are equal
    assert mcnemar_midp(3, 3) == pytest.approx(1 - 20 / 64 / 2)
    assert mcnemar_midp(0, 0) == 1.0


@given(st.integers(0, 40), st.integers(0, 40))
def test_mcnemar_midp_below_exact_and_symmetric(n12, n21):
    p = mcnemar_midp(n12, n21)
    assert 0 < p <= 1
    assert p == mcnemar_midp(n21, n12)
    if n12 + n21:
        assert p <= _exact(n12, n21) + 1e-12


# AC: published examples reproduce. Newcombe 1998, Stat Med 17:857, Table I, method 3
@pytest.mark.parametrize(
    "x, n, low, high",
    [
        (81, 263, 0.2553, 0.3662),
        (15, 148, 0.0624, 0.1605),
        (0, 20, 0.0000, 0.1611),
        (1, 29, 0.0061, 0.1718),
    ],
)
def test_wilson_published(x, n, low, high):
    assert wilson(x, n) == pytest.approx((low, high), abs=5e-5)


# AC: published examples reproduce. Bentur and Cavo with the Bonett-Price adjustment, from
# the contingencytables R package of Fagerland, Lydersen and Laake (Statistical Analysis of
# Contingency Tables, chapter 8); and the example in Bonett's statpsych ci.prop.ps
@pytest.mark.parametrize(
    "n12, n21, n, low, high",
    [
        (1, 7, 21, -0.5084, -0.0134),
        (6, 16, 161, -0.1195, -0.0032),
        (26, 4, 48, 0.2548067, 0.6251933),
    ],
)
def test_bonett_price_published(n12, n21, n, low, high):
    assert bonett_price(n12, n21, n) == pytest.approx((low, high), abs=5e-5)


# AC: published examples reproduce. Bonett and Price 2012, J Educ Behav Stat 37(4), p. 484
# (as reproduced in the NCSS PASS documentation): 50 and 22 discordant of 200, to 3 decimals
def test_bonett_price_paper_example():
    assert bonett_price(50, 22, 200) == pytest.approx((0.057, 0.220), abs=5e-4)


# AC: the 5-wins-0-losses and 7-wins-1-loss thresholds are checked at n = 16
@pytest.mark.parametrize("wins, losses", [(5, 0), (7, 1)])
def test_thresholds_pass_at_16(wins, losses):
    assert mcnemar_midp(wins, losses) < 0.05
    assert _exact(wins, losses) > 0.05  # the exact test would miss both
    assert bonett_price(wins, losses, 16)[0] > 0


@pytest.mark.parametrize("wins, losses", [(4, 0), (6, 1)])
def test_neighbouring_splits_fail_at_16(wins, losses):
    assert mcnemar_midp(wins, losses) > 0.05


@pytest.mark.parametrize(
    "call",
    [
        lambda: mcnemar_midp(-1, 2),
        lambda: mcnemar_midp(1.0, 2),
        lambda: wilson(3, 0),
        lambda: wilson(5, 4),
        lambda: wilson(1, 4, conf=1.0),
        lambda: bonett_price(10, 7, 16),
        lambda: bonett_price(0, 0, 0),
        lambda: bonett_price(True, 0, 16),
    ],
)
def test_invalid_inputs(call):
    with pytest.raises(ValueError):
        call()


# synthetic fixture: three incidents with candidate labels, tagged by incident
INCIDENTS = [[("a", 1), ("a", 0)], [("b", 1)], [("c", 0), ("c", 0), ("c", 1)]]


# AC: the clustered bootstrap resamples whole incidents, no candidate split from its incident
def test_cluster_bootstrap_resamples_whole_incidents():
    sizes = {c[0][0]: len(c) for c in INCIDENTS}

    def stat(items):
        counts = Counter(tag for tag, _ in items)
        # every incident appears a whole number of times, and 3 incidents are drawn
        assert all(counts[tag] % sizes[tag] == 0 for tag in counts)
        assert sum(counts[tag] // sizes[tag] for tag in counts) == len(INCIDENTS)
        return sum(label for _, label in items) / len(items)

    low, high = cluster_bootstrap(INCIDENTS, stat, n_resamples=500, seed=1)
    assert 0 <= low <= high <= 1
    assert cluster_bootstrap(INCIDENTS, stat, n_resamples=500, seed=1) == (low, high)


def test_cluster_bootstrap_invalid():
    with pytest.raises(ValueError):
        cluster_bootstrap([], len)
    with pytest.raises(ValueError):
        cluster_bootstrap(INCIDENTS, len, n_resamples=0)
