import pytest

from remvalgate.harness.outcome import (
    Outcome,
    ProblemResult,
    classify,
    harmful_labels,
    outcome_counts,
    recovered_labels,
)

# fields: applied, recovered, harmful, any_working_candidate


def test_outcome_set_is_fixed():
    assert [outcome.value for outcome in Outcome] == [
        "RECOVERED",
        "ESCALATED_WITH_WORKING",
        "ESCALATED_WITH_NONE",
        "APPLIED_INEFFECTIVE",
        "APPLIED_HARMFUL",
    ]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ((True, True, False, True), Outcome.RECOVERED),
        ((False, False, False, True), Outcome.ESCALATED_WITH_WORKING),
        ((False, False, False, False), Outcome.ESCALATED_WITH_NONE),
        ((True, False, False, True), Outcome.APPLIED_INEFFECTIVE),
        ((True, False, False, False), Outcome.APPLIED_INEFFECTIVE),
        ((True, False, True, False), Outcome.APPLIED_HARMFUL),
    ],
)
def test_classify(fields, expected):
    assert classify(ProblemResult(*fields)) is expected


@pytest.mark.parametrize("any_working", [True, False])
def test_recovered_with_harm_is_harmful(any_working):
    # a harmful candidate is not a working one, so harm with no working candidate is valid
    result = ProblemResult(True, True, True, any_working)
    assert classify(result) is Outcome.APPLIED_HARMFUL


@pytest.mark.parametrize(
    "fields",
    [
        # nothing was applied, so nothing can have recovered or harmed
        (False, True, False, True),
        (False, False, True, True),
        (False, True, True, False),
        # the applied candidate recovered with no harm, so a working candidate existed
        (True, True, False, False),
    ],
)
def test_impossible_input_raises(fields):
    with pytest.raises(ValueError):
        ProblemResult(*fields)


def test_counts_empty():
    counts = outcome_counts([])
    assert counts.total == 0
    assert counts.by_outcome == {outcome: 0 for outcome in Outcome}
    assert counts.recovery_rate == 0.0
    assert recovered_labels([]) == []
    assert harmful_labels([]) == []


def test_recovery_rate_counts_escalations_as_not_recovered():
    outcomes = [
        Outcome.RECOVERED,
        Outcome.ESCALATED_WITH_WORKING,
        Outcome.ESCALATED_WITH_NONE,
        Outcome.APPLIED_INEFFECTIVE,
    ]
    # a generator is read once
    counts = outcome_counts(outcome for outcome in outcomes)
    assert counts.total == 4
    assert counts.by_outcome == {outcome: int(outcome in outcomes) for outcome in Outcome}
    assert counts.recovery_rate == 0.25


def test_labels():
    outcomes = [Outcome.RECOVERED, Outcome.APPLIED_HARMFUL, Outcome.ESCALATED_WITH_NONE]
    assert recovered_labels(outcomes) == [True, False, False]
    assert harmful_labels(outcomes) == [False, True, False]
