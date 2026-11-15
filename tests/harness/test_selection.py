import pytest

from remvalgate.harness.manifest import Registry, Slice
from remvalgate.harness.selection import selection

POOL = "k8s_target_port-misconfig-mitigation-1"
DEV = "dev-k8s_target_port-misconfig-mitigation-1"
CONF = "conf-scale_pod_zero_social_net-mitigation-1"
SINGLE = "wrong_bin_usage-mitigation-1"
UNTYPED = "redeploy_without_PV-mitigation-1"
REGISTRY = Registry(
    slices={
        POOL: Slice.POOL,
        DEV: Slice.DEVELOPMENT,
        CONF: Slice.CONFIRMATION,
        SINGLE: Slice.SINGLETON,
        UNTYPED: Slice.SINGLETON,
    },
    placeholder=None,
)


def test_no_ids_select_the_pool_and_held_out_ids_run_2_and_3c():
    assert selection([], ["3c"], REGISTRY) == ([POOL], Slice.POOL)
    assert selection([CONF], ["2", "3c"], REGISTRY, open_confirmation=True) == (
        [CONF],
        Slice.CONFIRMATION,
    )
    assert selection([SINGLE], ["3c"], REGISTRY) == ([SINGLE], Slice.SINGLETON)


@pytest.mark.parametrize(
    ("problems", "baselines", "opening", "message"),
    [
        (["not-a-problem"], ["3c"], False, "not in the slice registry"),
        ([POOL, CONF], ["3c"], False, "one slice per run"),
        ([CONF], ["3c", "notools"], True, "run baselines \\['2', '3c'\\] only"),
        ([SINGLE], ["7"], False, "run baselines"),
        ([UNTYPED], ["3c"], False, "no fault type, so not run"),
        ([SINGLE], ["3c"], True, "for confirmation problems only"),
        ([DEV], ["3c"], True, "for confirmation problems only"),
    ],
)
def test_a_refused_selection_raises(problems, baselines, opening, message):
    with pytest.raises(ValueError, match=message):
        selection(problems, baselines, REGISTRY, open_confirmation=opening)
