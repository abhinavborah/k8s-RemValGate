import pytest

from remvalgate.harness.finetune import candidate_yield, check_folds, compare
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


def test_a_tie_or_a_small_gain_is_falsified():
    base = set(POOL[:6])
    assert compare(_rows("3c", base) + _rows("3cft", base), POOL)["claim"] == "falsified"
    # 4 wins with no loss: mid-p 0.0625, above the threshold
    gain = compare(_rows("3c", base) + _rows("3cft", base | set(POOL[6:10])), POOL)
    assert gain["both_seeds"]["n21"] == 4 and gain["claim"] == "falsified"


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
