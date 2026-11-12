import pytest

from remvalgate.harness.outcome import Outcome
from remvalgate.harness.rag_ablation import Row, paired, score, service_matches, summary
from remvalgate.harness.stats import mcnemar_midp, wilson

# synthetic problem ids and services only: no pool problem is read


def _diag(fault_type: str, service: str) -> dict:
    return {"fault_type": fault_type, "service": service, "reason": "r"}


def test_service_matches_ignores_case_and_space():
    assert service_matches(" Svc-A ", "svc-a", "ScalePod")
    assert not service_matches("svc-b", "svc-a", "ScalePod")


def test_mongodb_client_counts_only_for_mongodb_faults():
    assert service_matches("geo", "mongodb-geo", "RevokeAuth")
    assert service_matches("mongodb-geo", "mongodb-geo", "UserUnregistered")
    assert not service_matches("rate", "mongodb-geo", "RevokeAuth")
    # the client rule is not applied to other fault types
    assert not service_matches("geo", "mongodb-geo", "ScalePod")


def test_score_needs_type_and_service():
    right = score("p", _diag("ScalePod", "svc-a"), Outcome.RECOVERED, "ScalePod", "svc-a")
    assert (right.fault_type_correct, right.diagnosis_correct, right.recovered) == (1, 1, 1)
    wrong_service = score(
        "p", _diag("ScalePod", "svc-b"), Outcome.APPLIED_INEFFECTIVE, "ScalePod", "svc-a"
    )
    assert (wrong_service.fault_type_correct, wrong_service.diagnosis_correct) == (True, False)
    assert not wrong_service.recovered
    wrong_type = score("p", _diag("RevokeAuth", "svc-a"), Outcome.RECOVERED, "ScalePod", "svc-a")
    assert (wrong_type.fault_type_correct, wrong_type.diagnosis_correct) == (False, False)
    assert wrong_type.recovered


def test_no_diagnosis_is_wrong_but_keeps_its_outcome():
    row = score("p", None, Outcome.ESCALATED_UNLABELLED, "ScalePod", "svc-a")
    assert row == Row("p", False, False, False, False)


def test_summary_counts_over_every_problem_run():
    rows = [
        Row("a", True, True, True, True),
        Row("b", True, True, False, False),
        Row("c", False, False, False, False),
    ]
    s = summary(rows)
    assert (s["n"], s["with_diagnosis"]) == (3, 2)
    assert s["fault_type_correct"]["count"] == 2
    assert s["diagnosis_correct"]["count"] == 1
    assert s["recovered"]["rate"] == pytest.approx(1 / 3, abs=1e-9)
    assert s["diagnosis_correct"]["wilson95"] == list(wilson(1, 3))


def test_summary_refuses_empty_and_repeated_problems():
    with pytest.raises(ValueError):
        summary([])
    with pytest.raises(ValueError):
        summary([Row("a", True, True, True, True)] * 2)


def test_paired_uses_shared_problems_and_discordant_counts():
    first = [Row(p, True, True, ok, ok) for p, ok in [("a", 1), ("b", 1), ("c", 0), ("d", 1)]]
    second = [Row(p, True, True, ok, ok) for p, ok in [("a", 0), ("b", 1), ("c", 1)]]
    out = paired(first, second, "diagnosis_correct")
    assert (out["n"], out["first"], out["second"], out["n12"], out["n21"]) == (3, 2, 2, 1, 1)
    assert out["mcnemar_midp"] == mcnemar_midp(1, 1)
    assert out["not_shared"] == ["d"]


def test_paired_refuses_bad_input():
    rows = [Row("a", True, True, True, True)]
    with pytest.raises(ValueError):
        paired(rows, rows, "nope")
    with pytest.raises(ValueError):
        paired(rows, [Row("b", True, True, True, True)], "recovered")
    with pytest.raises(ValueError):
        paired(rows * 2, rows, "recovered")


def test_runs_split_by_seed_and_arms_must_share_their_seeds():
    from remvalgate.harness.rag_ablation import split_by_seed

    def run(arm, seed):
        return ({"baseline": arm, "seed": seed},)

    runs = {
        "a1": run("3a", 1000),
        "a2": run("3a", 1001),
        "c1": run("3c", 1000),
        "c2": run("3c", 1001),
    }
    assert {s: sorted(p) for s, p in split_by_seed(runs, ["3a", "3c"]).items()} == {
        1000: ["a1", "c1"],
        1001: ["a2", "c2"],
    }
    # an arm absent from the runs takes no part
    assert list(split_by_seed(runs, ["3a", "3b", "3c"])) == [1000, 1001]
    with pytest.raises(ValueError, match="different seeds"):
        split_by_seed({"a1": run("3a", 1000), "c2": run("3c", 1001)}, ["3a", "3c"])
