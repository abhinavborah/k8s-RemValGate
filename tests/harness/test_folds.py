import random
from collections import Counter

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from remvalgate.harness.folds import (
    FAULT_TYPES,
    assign_folds,
    check_folds,
    fault_type,
    pool_folds,
    pool_ids,
)
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Registry, Slice, load_registry

STEMS = {
    "TargetPortMisconfig": "k8s_target_port-misconfig-mitigation-{}",
    "RevokeAuth": "revoke_auth_mongodb-mitigation-{}",
    "UserUnregistered": "user_unregistered_mongodb-mitigation-{}",
    "ScalePod": "scale_pod_zero_social_net-mitigation-{}",
    "AssignNonExistentNode": "assign_to_non_existent_node_social_net-mitigation-{}",
}
# the extended pool of the plan: 6 + 2 + 2 + 3 + 3
PLAN = {
    "TargetPortMisconfig": 6,
    "RevokeAuth": 2,
    "UserUnregistered": 2,
    "ScalePod": 3,
    "AssignNonExistentNode": 3,
}


def pool(counts: dict[str, int]) -> list[str]:
    return [STEMS[t].format(i) for t, n in counts.items() for i in range(1, n + 1)]


def per_fold(assignment: dict[str, int], type_: str, n_folds: int = 4) -> list[int]:
    counts = Counter(f for p, f in assignment.items() if fault_type(p) == type_)
    return [counts[f] for f in range(n_folds)]


# --- fault_type ------------------------------------------------------------------------------


@pytest.mark.parametrize("type_", sorted(STEMS))
def test_each_pool_problem_id_maps_to_its_fault_type(type_):
    assert fault_type(STEMS[type_].format(1)) == type_


@pytest.mark.parametrize("prefix", ["pool-", "conf-", "dev-"])
def test_a_slice_prefix_does_not_change_the_fault_type(prefix):
    assert fault_type(prefix + STEMS["ScalePod"].format(2)) == "ScalePod"


@pytest.mark.parametrize("problem_id", ["", "auth_miss_mongodb-mitigation-1", "something-else"])
def test_an_unknown_problem_id_is_refused(problem_id):
    with pytest.raises(ValueError, match="fault type"):
        fault_type(problem_id)


def test_the_stems_cover_exactly_the_five_pool_fault_types():
    assert set(FAULT_TYPES.values()) == set(STEMS)


# --- assign_folds on the planned pool --------------------------------------------------------


def test_every_problem_gets_exactly_one_fold_and_each_fold_has_four():
    problems = pool(PLAN)
    assignment = assign_folds(problems)
    assert sorted(assignment) == sorted(problems)
    assert Counter(assignment.values()) == {0: 4, 1: 4, 2: 4, 3: 4}


def test_target_port_splits_two_two_one_one():
    assignment = assign_folds(pool(PLAN))
    assert sorted(per_fold(assignment, "TargetPortMisconfig")) == [1, 1, 2, 2]


@pytest.mark.parametrize("type_", ["RevokeAuth", "UserUnregistered"])
def test_the_two_instances_of_a_database_fault_fall_in_different_folds(type_):
    assignment = assign_folds(pool(PLAN))
    assert sorted(per_fold(assignment, type_)) == [0, 0, 1, 1]


@pytest.mark.parametrize("type_", ["ScalePod", "AssignNonExistentNode"])
def test_the_three_instances_fall_in_three_different_folds(type_):
    assignment = assign_folds(pool(PLAN))
    assert sorted(per_fold(assignment, type_)) == [0, 1, 1, 1]


def test_no_fold_holds_out_every_instance_of_a_fault_type():
    assignment = assign_folds(pool(PLAN))
    for type_ in PLAN:
        assert max(per_fold(assignment, type_)) < PLAN[type_]


def test_the_planned_assignment_passes_the_check():
    assert check_folds(assign_folds(pool(PLAN))) == []


def test_the_result_does_not_depend_on_the_order_of_the_input():
    problems = pool(PLAN)
    shuffled = problems[:]
    random.Random(7).shuffle(shuffled)
    assert assign_folds(shuffled) == assign_folds(problems)


def test_the_result_is_the_same_on_every_call():
    problems = pool(PLAN)
    assert assign_folds(problems) == assign_folds(problems)


# --- assign_folds on other pools and refusals -------------------------------------------------


def test_the_committed_pool_of_nine_assigns_and_passes_the_check():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    assignment = pool_folds(registry)
    pool_ids = [i for i, s in registry.slices.items() if s is Slice.POOL]
    assert sorted(assignment) == sorted(pool_ids)
    assert check_folds(assignment) == []
    sizes = Counter(assignment.values())
    assert max(sizes.values()) - min(sizes.values()) <= 1


def test_pool_folds_takes_only_the_pool_slice():
    registry = Registry(
        slices={
            **dict.fromkeys(pool({"RevokeAuth": 2, "UserUnregistered": 2}), Slice.POOL),
            "dev-scale_pod_zero_social_net-mitigation-1": Slice.DEVELOPMENT,
            "conf-scale_pod_zero_social_net-mitigation-1": Slice.CONFIRMATION,
        },
        placeholder=None,
    )
    assert sorted(pool_folds(registry)) == sorted(pool({"RevokeAuth": 2, "UserUnregistered": 2}))


def test_a_pool_smaller_than_the_number_of_folds_is_refused():
    with pytest.raises(ValueError, match="folds"):
        assign_folds(pool({"RevokeAuth": 2, "ScalePod": 1}))


def test_a_duplicate_problem_id_is_refused():
    problems = pool(PLAN)
    with pytest.raises(ValueError, match="duplicate"):
        assign_folds([*problems, problems[0]])


@pytest.mark.parametrize("n_folds", [0, 1, -3])
def test_fewer_than_two_folds_is_refused(n_folds):
    with pytest.raises(ValueError, match="folds"):
        assign_folds(pool(PLAN), n_folds=n_folds)


def test_a_different_number_of_folds_is_supported():
    assignment = assign_folds(pool(PLAN), n_folds=2)
    assert check_folds(assignment, n_folds=2) == []
    assert Counter(assignment.values()) == {0: 8, 1: 8}


@settings(max_examples=200, deadline=None)
@given(
    counts=st.fixed_dictionaries({t: st.integers(0, 9) for t in STEMS}),
    n_folds=st.integers(2, 5),
)
def test_any_pool_gets_a_balanced_assignment_that_passes_the_check(counts, n_folds):
    problems = pool(counts)
    if len(problems) < n_folds:
        with pytest.raises(ValueError, match="folds"):
            assign_folds(problems, n_folds=n_folds)
        return
    assignment = assign_folds(problems, n_folds=n_folds)
    assert check_folds(assignment, n_folds=n_folds) == []
    for type_, n in counts.items():
        spread = per_fold(assignment, type_, n_folds)
        assert sum(spread) == n
        assert max(spread) - min(spread) <= 1


# --- check_folds: it must catch each broken invariant ----------------------------------------


def good() -> dict[str, int]:
    return dict(assign_folds(pool(PLAN)))


def test_check_catches_a_fold_that_holds_every_instance_of_a_type():
    bad = good()
    for p in pool({"RevokeAuth": 2}):
        bad[p] = 0
    messages = check_folds(bad)
    assert any("RevokeAuth" in m for m in messages)


def test_check_catches_two_database_instances_in_one_fold():
    bad = good()
    first, second = pool({"UserUnregistered": 2})
    bad[second] = bad[first]
    assert any("UserUnregistered" in m for m in check_folds(bad))


def test_check_catches_an_uneven_target_port_split():
    bad = good()
    by_fold = {
        f: [p for p in bad if fault_type(p) == "TargetPortMisconfig" and bad[p] == f]
        for f in range(4)
    }
    one = next(f for f, ps in by_fold.items() if len(ps) == 1)
    two = next(f for f, ps in by_fold.items() if len(ps) == 2)
    # counts 2, 2, 1, 1 become 3, 2, 0, 1 in some order
    bad[by_fold[one][0]] = two
    assert any("TargetPortMisconfig" in m for m in check_folds(bad))


def test_check_catches_uneven_fold_sizes():
    bad = good()
    victim = next(p for p, f in bad.items() if f == 3 and fault_type(p) == "TargetPortMisconfig")
    bad[victim] = 0
    assert any("size" in m for m in check_folds(bad))


def test_check_catches_a_fold_index_out_of_range():
    bad = good()
    bad[next(iter(bad))] = 4
    assert any("fold index" in m for m in check_folds(bad))


def test_check_catches_a_boolean_or_negative_fold():
    bad = good()
    first, second = list(bad)[:2]
    bad[first] = -1
    bad[second] = True
    assert len([m for m in check_folds(bad) if "fold index" in m]) == 2


def test_check_reports_a_problem_it_cannot_type():
    bad = good()
    bad["unknown-problem"] = 0
    assert any("fault type" in m for m in check_folds(bad))


def test_check_returns_a_list_of_strings():
    assert all(
        isinstance(m, str) for m in check_folds({"k8s_target_port-misconfig-mitigation-1": 9})
    )


# --- a stem that contains another ------------------------------------------------------------


def test_a_stem_that_contains_another_of_a_different_type_is_an_error(monkeypatch):
    monkeypatch.setitem(FAULT_TYPES, "revoke_auth_mongodb", "SomethingElse")
    with pytest.raises(ValueError, match="more than one fault type"):
        fault_type("revoke_auth_mongodb-mitigation-1")


def test_two_stems_of_the_same_type_are_not_ambiguous(monkeypatch):
    monkeypatch.setitem(FAULT_TYPES, "revoke_auth_mongodb", "RevokeAuth")
    assert fault_type("revoke_auth_mongodb-mitigation-1") == "RevokeAuth"


# --- check_folds against the expected pool ---------------------------------------------------


def test_the_expected_pool_matches_the_assignment():
    problems = pool(PLAN)
    assert check_folds(assign_folds(problems), expected_ids=problems) == []


def test_check_reports_a_pool_problem_with_no_fold():
    problems = pool(PLAN)
    bad = good()
    del bad[problems[0]]
    messages = check_folds(bad, expected_ids=problems)
    assert any(problems[0] in m and "no fold" in m for m in messages)


def test_check_reports_an_assigned_problem_that_is_not_in_the_pool():
    problems = pool(PLAN)
    bad = good()
    bad["pool-scale_pod_zero_social_net-mitigation-9"] = 0
    messages = check_folds(bad, expected_ids=problems)
    assert any("mitigation-9" in m and "not an expected" in m for m in messages)


def test_an_assignment_made_for_nine_problems_is_caught_when_the_pool_grows_to_sixteen():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    old = pool_folds(registry)
    grown = [*pool_ids(registry), *pool({"TargetPortMisconfig": 6})[3:]]
    # three new TargetPortMisconfig instances join the pool: the old assignment misses them
    messages = check_folds(old, expected_ids=grown)
    assert sum("no fold" in m for m in messages) == 3
    # without the expected ids the stale assignment still looks valid
    assert check_folds(old) == []


def test_pool_ids_lists_only_the_pool_slice():
    registry = Registry(
        slices={"p-pool-scale_pod_zero-1": Slice.POOL, "x-scale_pod_zero-2": Slice.DEVELOPMENT},
        placeholder=None,
    )
    assert pool_ids(registry) == ["p-pool-scale_pod_zero-1"]
