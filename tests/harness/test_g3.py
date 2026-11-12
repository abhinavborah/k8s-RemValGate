import pytest

from remvalgate.harness.g3 import (
    HEADROOM_STOP,
    MIN_GAIN,
    check_rerun_settings,
    g3,
    headroom,
    meets_min_gain,
    r_check,
    solved_by_baseline,
    top_pick_solved,
)
from remvalgate.harness.oracle import Label, ProblemLabels
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.stats import mcnemar_midp

# synthetic problem ids only: no pool problem is read
POOL = [f"synth-{i}" for i in range(16)]
CMD = "kubectl -n sandbox-a scale deployment svc-a --replicas=1"


def _label(outcome: Outcome | None, seed: int | None = 0) -> Label:
    rejection = None if outcome is not None else "allow-list: OUT_OF_SANDBOX"
    return Label(seed, CMD, rejection, None, outcome)


def _problem(problem_id: str, plan: Outcome | None | str, *candidates: Outcome | None):
    """plan is the top pick's outcome, None for a rejected command, or "none" for no command."""
    return ProblemLabels(
        problem_id,
        tuple(_label(o, seed) for seed, o in enumerate(candidates)),
        None if plan == "none" else _label(plan, None),
    )


def _pool(solved: int, recoverable: int) -> list[ProblemLabels]:
    """solved problems whose top pick recovered, then recoverable ones, then ones with nothing."""
    problems = []
    for i, problem_id in enumerate(POOL):
        if i < solved:
            problems.append(_problem(problem_id, Outcome.RECOVERED, Outcome.RECOVERED))
        elif i < solved + recoverable:
            problems.append(_problem(problem_id, Outcome.APPLIED_INEFFECTIVE, Outcome.RECOVERED))
        else:
            problems.append(
                _problem(problem_id, Outcome.APPLIED_INEFFECTIVE, Outcome.APPLIED_HARMFUL)
            )
    return problems


def test_twelve_solved_stops_the_project():
    result = headroom(_pool(HEADROOM_STOP, 4), POOL)
    assert result["solved"] == 12 and result["stop"] and not result["passed"]


def test_eleven_solved_with_five_recoverable_passes():
    result = headroom(_pool(11, 5), POOL)
    assert (result["solved"], result["recoverable"]) == (11, 5)
    assert not result["stop"] and result["passed"]
    assert result["recoverable_ids"] == sorted(POOL[11:16])


def test_four_recoverable_fails():
    result = headroom(_pool(3, 4), POOL)
    assert not result["stop"] and not result["passed"]


def test_a_solved_problem_is_never_recoverable_even_with_a_working_candidate():
    result = headroom(_pool(16, 0), POOL)
    assert result["recoverable"] == 0 and result["stop"]


@pytest.mark.parametrize("plan", ["none", None, Outcome.APPLIED_HARMFUL])
def test_a_top_pick_with_no_command_a_rejected_one_or_harm_does_not_solve(plan):
    problem = _problem("synth-0", plan, Outcome.RECOVERED)
    assert not top_pick_solved(problem)


def test_a_plan_that_duplicates_a_candidate_takes_its_label():
    plan = Label(None, CMD, None, 0, Outcome.RECOVERED)
    problem = ProblemLabels("synth-0", (_label(Outcome.RECOVERED),), plan)
    assert top_pick_solved(problem)


def test_a_problem_with_no_candidates_is_not_recoverable():
    problems = _pool(0, 0)
    problems[0] = _problem(POOL[0], "none")
    assert headroom(problems, POOL)["recoverable"] == 0


@pytest.mark.parametrize(
    "problems, match",
    [
        (_pool(0, 0)[:15], "missing: synth-15"),
        (_pool(0, 0) + [_problem("synth-dev", "none")], "not pool problems: synth-dev"),
        (_pool(0, 0) + [_problem("synth-3", "none")], "more than once: synth-3"),
    ],
)
def test_labels_that_do_not_cover_the_pool_once_are_refused(problems, match):
    with pytest.raises(ValueError, match=match):
        headroom(problems, POOL)


def _rerun(labels: list[ProblemLabels], flip: int) -> dict[str, Outcome]:
    """The labelling run's top-pick outcomes, with the first flip problems flipped."""
    rerun = {}
    for i, p in enumerate(labels):
        solved = top_pick_solved(p) != (i < flip)
        rerun[p.problem_id] = Outcome.RECOVERED if solved else Outcome.ESCALATED_UNLABELLED
    return rerun


@pytest.mark.parametrize("flip, r", [(0, 1), (1, 1), (2, 3), (16, 3)])
def test_r_is_1_with_at_most_one_flip_else_3(flip, r):
    labels = _pool(6, 4)
    result = r_check(labels, _rerun(labels, flip), POOL)
    assert (result["flips"], result["r"]) == (flip, r)
    assert result["flip_ids"] == sorted(POOL[:flip])


def test_r_compares_recovered_against_any_other_outcome():
    labels = _pool(0, 0)
    # neither run recovered: different failures are no flip
    rerun = dict.fromkeys(POOL, Outcome.APPLIED_HARMFUL)
    assert r_check(labels, rerun, POOL)["flips"] == 0


def test_r_refuses_a_partial_rerun_or_a_string_outcome():
    labels = _pool(6, 4)
    rerun = _rerun(labels, 0)
    with pytest.raises(ValueError, match="3c rerun: pool problems missing"):
        r_check(labels, {k: v for k, v in rerun.items() if k != POOL[0]}, POOL)
    with pytest.raises(ValueError, match="not an outcome"):
        r_check(labels, rerun | {POOL[0]: "RECOVERED"}, POOL)


@pytest.mark.parametrize("losses", sorted(MIN_GAIN))
def test_min_gain_is_the_smallest_win_count_below_mid_p_0_05(losses):
    wins = MIN_GAIN[losses]
    assert mcnemar_midp(wins, losses) < 0.05 <= mcnemar_midp(wins - 1, losses)


def test_min_gain_values():
    assert MIN_GAIN == {0: 5, 1: 7}
    assert meets_min_gain(5, 0) and meets_min_gain(7, 1)
    assert not meets_min_gain(4, 0) and not meets_min_gain(6, 1)
    # two losses fail the falsifier whatever the wins
    assert not meets_min_gain(16, 2)


def test_g3_bundles_the_three_values():
    labels = _pool(11, 5)
    result = g3(labels, _rerun(labels, 1), POOL)
    assert result["headroom"]["passed"] and result["r"]["r"] == 1
    assert result["min_gain"]["wins_needed_by_losses"] == MIN_GAIN


SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "max_tokens": 2048, "thinking": True}


def _manifest(seed: int, version: str = "gguf q8_0; thinking=on", **sampling) -> dict:
    return {
        "model_name": "qwen3-4b",
        "model_version": version,
        "seed": seed,
        "sampling": SAMPLING | sampling,
    }


def test_rerun_settings_pass_with_one_new_seed_and_the_same_build():
    check_rerun_settings([_manifest(0)] * 16, [_manifest(1000)] * 16)


@pytest.mark.parametrize(
    "rerun, match",
    [
        ([_manifest(0)], "repeats the labelling run's seed 0"),
        ([_manifest(1000), _manifest(1001)], "more than one seed"),
        ([_manifest(1000, "gguf q8_0; thinking=off")], "differ in model_version"),
        ([_manifest(1000, temperature=1.0)], "differ in sampling temperature"),
        ([_manifest(1000, max_tokens=4096)], "differ in sampling max_tokens"),
    ],
)
def test_rerun_settings_refuse_a_repeated_seed_or_another_build(rerun, match):
    with pytest.raises(ValueError, match=match):
        check_rerun_settings([_manifest(0)] * 16, rerun)


def _row(problem_id: str, baseline: str, outcome: Outcome) -> dict:
    return {"problem_id": problem_id, "baseline": baseline, "outcome": str(outcome)}


def test_solved_by_baseline_counts_recovered_rows_per_baseline():
    rows = [
        _row(i, "1", Outcome.RECOVERED if n < 3 else Outcome.APPLIED_INEFFECTIVE)
        for n, i in enumerate(POOL)
    ]
    rows += [_row(i, "3a", Outcome.APPLIED_INEFFECTIVE) for i in POOL]
    assert solved_by_baseline(rows, POOL) == {"1": 3, "3a": 0}


@pytest.mark.parametrize(
    "rows, match",
    [
        # a resumed run that left two rows for one problem
        (
            [_row(i, "2", Outcome.RECOVERED) for i in [*POOL, POOL[0]]],
            "baseline 2: a problem is given more than once",
        ),
        ([_row(i, "2", Outcome.RECOVERED) for i in POOL[:-1]], "baseline 2: pool problems missing"),
    ],
)
def test_solved_by_baseline_refuses_a_partial_or_repeated_run(rows, match):
    with pytest.raises(ValueError, match=match):
        solved_by_baseline(rows, POOL)


def test_seeds_combine_to_recovered_only_when_every_seed_recovered():
    from remvalgate.harness.g3 import combine_seeds

    def row(problem, seed, outcome):
        return {"baseline": "3c", "problem_id": problem, "seed": seed, "outcome": outcome}

    rec, fail = Outcome.RECOVERED.value, Outcome.APPLIED_INEFFECTIVE.value
    out = combine_seeds(
        [row("p1", 1000, rec), row("p1", 1001, rec), row("p2", 1001, fail), row("p2", 1000, rec)]
    )
    assert [(r["problem_id"], r["outcome"], r["seeds"]) for r in out] == [
        ("p1", rec, [1000, 1001]),
        ("p2", fail, [1000, 1001]),
    ]
    # one seed passes through unchanged apart from the seed list
    assert combine_seeds([row("p1", 1000, fail)])[0]["outcome"] == fail
    with pytest.raises(ValueError, match="two rows at one seed"):
        combine_seeds([row("p1", 1000, rec), row("p1", 1000, rec)])
    with pytest.raises(ValueError, match="different seeds"):
        combine_seeds([row("p1", 1000, rec), row("p1", 1001, rec), row("p2", 1000, rec)])
