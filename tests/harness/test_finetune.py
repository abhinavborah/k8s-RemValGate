import pytest

from remvalgate.harness.finetune import candidate_yield, check_folds, claim, compare
from remvalgate.harness.oracle import Label, ProblemLabels
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.stats import mcnemar_midp

POOL = [f"p{i:02d}" for i in range(16)]
R, F = Outcome.RECOVERED.value, Outcome.APPLIED_INEFFECTIVE.value


def _rows(arm: str, recovered: set[str], seeds=(1000, 1001)) -> list[dict]:
    return [
        {"baseline": arm, "problem_id": p, "seed": s, "outcome": R if p in recovered else F}
        for s in seeds
        for p in POOL
    ]


def test_five_wins_and_no_loss_at_both_seeds_supports_the_claim():
    base = set(POOL[:6])
    out = compare(_rows("3c", base) + _rows("3cft", base | set(POOL[6:11])), POOL)
    both = out["both_seeds"]
    assert (both["3c"], both["3cft"], both["n12"], both["n21"]) == (6, 11, 0, 5)
    assert both["mcnemar_midp"] == mcnemar_midp(0, 5) < 0.05
    assert out["claim"] == "supported"
    low, high = both["difference_bonett_price95"]
    assert 0 < low < high


def test_a_tie_is_falsified_and_a_small_gain_is_not_supported():
    base = set(POOL[:6])
    assert compare(_rows("3c", base) + _rows("3cft", base), POOL)["claim"] == "falsified"
    # 4 wins with no loss: below the minimum gain of 5, a gain the test cannot confirm
    gain = compare(_rows("3c", base) + _rows("3cft", base | set(POOL[6:10])), POOL)
    assert gain["both_seeds"]["n21"] == 4 and gain["claim"] == "not supported"


def test_the_three_labels_at_their_edges():
    assert claim(5, 0) == claim(7, 1) == claim(9, 1) == "supported"
    assert claim(1, 0) == claim(4, 0) == claim(2, 1) == claim(6, 1) == "not supported"
    assert claim(0, 0) == claim(1, 1) == claim(0, 1) == claim(9, 2) == claim(3, 5) == "falsified"


def test_two_losses_falsify_whatever_the_wins():
    # 9 wins with 2 losses: mid-p 0.0386 is below 0.05, but the minimum gain allows at most 1 loss
    base = set(POOL[:6])
    out = compare(_rows("3c", base) + _rows("3cft", set(POOL[2:6]) | set(POOL[6:15])), POOL)
    both = out["both_seeds"]
    assert (both["n12"], both["n21"]) == (2, 9)
    assert both["mcnemar_midp"] < 0.05
    assert out["claim"] == "falsified"


def test_seven_wins_with_one_loss_supports_the_claim():
    base = set(POOL[:6])
    out = compare(_rows("3c", base) + _rows("3cft", set(POOL[1:6]) | set(POOL[6:13])), POOL)
    assert (out["both_seeds"]["n12"], out["both_seeds"]["n21"]) == (1, 7)
    assert out["claim"] == "supported"


def test_a_problem_counts_only_when_it_recovers_at_both_seeds():
    base = set(POOL[:6])
    rows = _rows("3c", base) + _rows("3cft", base, seeds=(1000,))
    rows += _rows("3cft", base | {"p10"}, seeds=(1001,))
    out = compare(rows, POOL)
    assert out["seeds"]["1001"]["n21"] == 1
    assert out["both_seeds"]["n21"] == 0
    assert out["per_problem"]["p10"] == {"3c": False, "3cft": False}


def test_a_missing_or_repeated_run_is_refused():
    rows = _rows("3c", set()) + _rows("3cft", set())
    with pytest.raises(ValueError, match="missing"):
        compare(rows[1:], POOL)
    with pytest.raises(ValueError, match="more than once"):
        compare(rows + rows[:1], POOL)


def test_one_seed_or_no_rows_is_refused():
    base = set(POOL[:6])
    one = _rows("3c", base, seeds=(1000,)) + _rows("3cft", base | set(POOL[6:11]), seeds=(1000,))
    with pytest.raises(ValueError, match="expected seeds"):
        compare(one, POOL)
    with pytest.raises(ValueError, match="no rows"):
        compare(_rows("3cft", base), POOL)


def _manifest(arm, problem, asked, served):
    return {
        "run_id": f"{arm}-{problem}",
        "problem_id": problem,
        "sampling": {"baseline": arm, "fold": asked},
        "model_build": {"fold": served},
    }


def test_a_run_served_by_the_wrong_fold_is_refused():
    folds = {"p00": 0, "p01": 1}
    check_folds([_manifest("3cft", "p00", 0, 0), _manifest("3c", "p01", None, None)], folds)
    with pytest.raises(ValueError, match="served fold 1"):
        check_folds([_manifest("3cft", "p00", 0, 1)], folds)
    with pytest.raises(ValueError, match="its problem's fold 0"):
        check_folds([_manifest("3cft", "p00", 1, 1)], folds)
    with pytest.raises(ValueError, match="control was served by fold 2"):
        check_folds([_manifest("3c", "p00", None, 2)], folds)


def _label(outcome, seed=0):
    return Label(seed, "kubectl -n ns get pods", None, None, outcome)


def _outcome(works: bool) -> Outcome:
    return Outcome.RECOVERED if works else Outcome.APPLIED_INEFFECTIVE


def _problem(pid, plan_works, *works):
    plan = Label(None, "kubectl -n ns get pods", None, None, _outcome(plan_works))
    cands = tuple(_label(_outcome(w), i) for i, w in enumerate(works))
    return ProblemLabels(pid, cands, plan)


def test_candidate_yield_counts_working_mixed_and_recoverable():
    untuned = [_problem(p, False, False, False) for p in POOL]
    tuned = [_problem(p, False, False, False) for p in POOL[1:]]
    tuned.append(_problem(POOL[0], False, True, False))
    out = candidate_yield(untuned, tuned, POOL)
    assert out["3c"]["with_working_candidate"] == 0 and out["3c"]["recoverable"] == []
    ft = out["3cft"]
    assert ft["with_working_candidate"] == 1 and ft["mixed"] == 1
    assert ft["recoverable"] == [POOL[0]]
    assert ft["per_problem"][POOL[0]] == {
        "top_pick_solved": False,
        "working": 1,
        "k": 2,
        "mixed": True,
        "recoverable": True,
    }


def test_a_control_from_another_server_is_refused():
    folds = {"p00": 0, "p01": 1}
    pod = {"server": "llama-server", "host": "gpu host", "endpoint": "a"}
    tuned = _manifest("3cft", "p00", 0, 0)
    tuned["model_build"] = {**pod, "fold": 0, "adapter_sha256": "x", "model_sha256": "y"}
    control = _manifest("3c", "p01", None, None)
    control["model_build"] = {**pod, "endpoint": "b", "fold": None, "adapter_sha256": None}
    check_folds([tuned, control], folds)
    control["model_build"] = {"server": "lmstudio", "host": "run host", "fold": None}
    with pytest.raises(ValueError, match="different builds"):
        check_folds([tuned, control], folds)
