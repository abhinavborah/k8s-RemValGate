"""Grouped k-fold assignment of the evaluation pool, and the check of its invariants (E3-S4, R3.4).

Each problem is one incident, so a whole problem falls in one fold and its candidates never fall
on two sides. The proposal (section IX) states the rule for the 16-problem pool: no fold holds
out every instance of a fault type, TargetPortMisconfig (6 instances) splits 2, 2, 1, 1, the two
instances of RevokeAuth and of UserUnregistered fall in different folds, and the three instances
of ScalePod and of AssignNonExistentNode fall in three different folds. All of these are one rule:
the instances of a fault type spread over the folds as evenly as possible, so the counts per fold
differ by at most one. The fold sizes differ by at most one as well, which gives 4, 4, 4, 4 for 16
problems.

assign_folds is deterministic and depends only on the set of ids, so the result is the same on
every call. It changes when a problem is added, so the assignment of the committed 16-problem pool
is pinned in tests/harness/test_folds.py, and a change to the pool shows there.
"""

from collections import Counter
from collections.abc import Iterable

from remvalgate.harness.manifest import Registry, Slice

# a stem of the problem id to the fault type of the proposal; a slice prefix such as "pool-" does
# not matter
FAULT_TYPES = {
    "k8s_target_port-misconfig": "TargetPortMisconfig",
    "revoke_auth": "RevokeAuth",
    "user_unregistered": "UserUnregistered",
    "scale_pod_zero": "ScalePod",
    "assign_to_non_existent_node": "AssignNonExistentNode",
}


def fault_type(problem_id: str) -> str:
    """Return the fault type of a pool problem id.

    Raises ValueError for an id that matches no stem, and for one that matches stems of more than
    one fault type, so a later stem that contains an existing one is an error and not a silent
    first match.
    """
    matches = {type_ for stem, type_ in FAULT_TYPES.items() if stem in problem_id}
    if not matches:
        raise ValueError(f"no fault type can be read from the problem id {problem_id!r}")
    if len(matches) > 1:
        raise ValueError(
            f"the problem id {problem_id!r} matches more than one fault type: {sorted(matches)}"
        )
    return matches.pop()


def _check_n_folds(n_folds: int) -> None:
    if type(n_folds) is not int or n_folds < 2:
        raise ValueError(f"need at least 2 folds, got {n_folds!r}")


def assign_folds(problems: Iterable[str], n_folds: int = 4) -> dict[str, int]:
    """Assign each problem to a fold 0..n_folds-1.

    The problems are ordered by fault type, largest type first, then by id, and dealt to the folds
    in turn. The k-th problem goes to fold k mod n_folds, so the fold sizes differ by at most one,
    and the instances of one type are consecutive in the order, so their counts per fold differ by
    at most one as well. Raises ValueError for a duplicate id, an id with no fault type, fewer
    problems than folds, or fewer than 2 folds.
    """
    _check_n_folds(n_folds)
    ids = list(problems)
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate problem ids")
    if len(ids) < n_folds:
        raise ValueError(f"fewer problems ({len(ids)}) than folds ({n_folds})")
    by_type: dict[str, list[str]] = {}
    for problem_id in sorted(ids):
        by_type.setdefault(fault_type(problem_id), []).append(problem_id)
    ordered = [
        problem_id
        for _, members in sorted(by_type.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for problem_id in members
    ]
    return {problem_id: k % n_folds for k, problem_id in enumerate(ordered)}


def pool_ids(registry: Registry) -> list[str]:
    """The problem ids of the pool slice."""
    return [problem_id for problem_id, s in registry.slices.items() if s is Slice.POOL]


def pool_folds(registry: Registry, n_folds: int = 4) -> dict[str, int]:
    """Assign the problems of the pool slice."""
    return assign_folds(pool_ids(registry), n_folds)


def check_folds(
    assignment: dict[str, int], n_folds: int = 4, expected_ids: Iterable[str] | None = None
) -> list[str]:
    """Return one message per broken invariant; an empty list means the folds are valid.

    The checks: every fold index is an integer in 0..n_folds-1, the fold sizes differ by at most
    one, and for each fault type the counts per fold differ by at most one. A fold index that is
    not valid, and an id with no fault type, are reported and left out of the other checks.

    With expected_ids, for example pool_ids(registry), it also reports each expected id that has
    no fold and each assigned id that is not expected, so an assignment made for an older pool is
    caught once the pool has grown.
    """
    _check_n_folds(n_folds)
    messages = []
    if expected_ids is not None:
        expected = set(expected_ids)
        for problem_id in sorted(expected - assignment.keys()):
            messages.append(f"{problem_id}: expected in the folds but has no fold")
        for problem_id in sorted(assignment.keys() - expected):
            messages.append(f"{problem_id}: has a fold but is not an expected problem")
    valid: dict[str, int] = {}
    for problem_id, fold in assignment.items():
        if type(fold) is not int or not 0 <= fold < n_folds:
            messages.append(
                f"{problem_id}: fold index {fold!r} is not an integer in 0..{n_folds - 1}"
            )
        else:
            valid[problem_id] = fold
    sizes = Counter(valid.values())
    per_fold = [sizes[f] for f in range(n_folds)]
    if valid and max(per_fold) - min(per_fold) > 1:
        messages.append(f"fold sizes differ by more than one: {per_fold}")
    by_type: dict[str, Counter] = {}
    for problem_id, fold in valid.items():
        try:
            type_ = fault_type(problem_id)
        except ValueError as error:
            messages.append(str(error))
            continue
        by_type.setdefault(type_, Counter())[fold] += 1
    for type_, counts in sorted(by_type.items()):
        spread = [counts[f] for f in range(n_folds)]
        if max(spread) - min(spread) > 1:
            messages.append(f"{type_}: instances per fold {spread} differ by more than one")
    return messages
