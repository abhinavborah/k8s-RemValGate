import pytest

from remvalgate.harness.outcome import Outcome, ProblemResult, classify

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
        # the applied candidate recovered the problem, so a working candidate existed
        (True, True, False, False),
    ],
)
def test_impossible_input_raises(fields):
    with pytest.raises(ValueError):
        ProblemResult(*fields)
