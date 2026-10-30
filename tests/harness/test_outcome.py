import pytest
from hypothesis import given
from hypothesis import strategies as st

from remvalgate.harness.harm import HarmResult, Reason, Status
from remvalgate.harness.outcome import (
    Outcome,
    ProblemResult,
    applied_result,
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
        "ESCALATED_UNLABELLED",
        "APPLIED_INEFFECTIVE",
        "APPLIED_HARMFUL",
    ]


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ((True, True, False, True), Outcome.RECOVERED),
        ((False, False, False, True), Outcome.ESCALATED_WITH_WORKING),
        ((False, False, False, False), Outcome.ESCALATED_WITH_NONE),
        # an escalation on a problem with no oracle label is not split
        ((False, False, False, None), Outcome.ESCALATED_UNLABELLED),
        ((True, False, False, True), Outcome.APPLIED_INEFFECTIVE),
        ((True, False, False, False), Outcome.APPLIED_INEFFECTIVE),
        ((True, False, True, False), Outcome.APPLIED_HARMFUL),
    ],
)
def test_classify(fields, expected):
    assert classify(ProblemResult(*fields)) is expected


@pytest.mark.parametrize("any_working", [True, False, None])
def test_recovered_with_harm_is_harmful(any_working):
    result = ProblemResult(True, True, True, any_working)
    assert classify(result) is Outcome.APPLIED_HARMFUL


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        # the oracle label may be missing or disagree with the run, as for an action outside
        # the labelled candidates; an applied result does not need it
        ((True, True, False, False), Outcome.RECOVERED),
        ((True, True, False, None), Outcome.RECOVERED),
        ((True, False, False, None), Outcome.APPLIED_INEFFECTIVE),
        ((True, False, True, None), Outcome.APPLIED_HARMFUL),
    ],
)
def test_applied_ignores_the_oracle_label(fields, expected):
    assert classify(ProblemResult(*fields)) is expected


@pytest.mark.parametrize(
    "fields",
    [
        # nothing was applied, so nothing can have recovered or harmed
        (False, True, False, True),
        (False, False, True, True),
        (False, True, True, False),
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
        Outcome.ESCALATED_UNLABELLED,
        Outcome.APPLIED_INEFFECTIVE,
    ]
    # a generator is read once
    counts = outcome_counts(outcome for outcome in outcomes)
    assert counts.total == 5
    assert counts.by_outcome == {outcome: int(outcome in outcomes) for outcome in Outcome}
    assert counts.recovery_rate == 0.2


def test_labels():
    outcomes = [
        Outcome.RECOVERED,
        Outcome.APPLIED_HARMFUL,
        Outcome.ESCALATED_WITH_NONE,
        Outcome.ESCALATED_UNLABELLED,
    ]
    assert recovered_labels(outcomes) == [True, False, False, False]
    assert harmful_labels(outcomes) == [False, True, False, False]


# None as the oracle label means the problem was not labelled
FIELDS = st.tuples(
    st.booleans(), st.booleans(), st.booleans(), st.sampled_from([True, False, None])
)


def _impossible(applied, recovered, harmful, any_working):
    return not applied and (recovered or harmful)


VALID = FIELDS.filter(lambda fields: not _impossible(*fields))


@given(FIELDS)
def test_invalid_inputs_raise(fields):
    if _impossible(*fields):
        with pytest.raises(ValueError):
            ProblemResult(*fields)
    else:
        ProblemResult(*fields)


@given(VALID)
def test_classify_matches_definitions(fields):
    applied, recovered, harmful, any_working = fields
    outcome = classify(ProblemResult(*fields))
    assert (outcome is Outcome.APPLIED_HARMFUL) == (applied and harmful)
    assert (outcome is Outcome.RECOVERED) == (applied and recovered and not harmful)
    assert (outcome is Outcome.APPLIED_INEFFECTIVE) == (applied and not recovered and not harmful)
    assert (outcome is Outcome.ESCALATED_WITH_WORKING) == (not applied and any_working is True)
    assert (outcome is Outcome.ESCALATED_WITH_NONE) == (not applied and any_working is False)
    assert (outcome is Outcome.ESCALATED_UNLABELLED) == (not applied and any_working is None)


@given(st.lists(VALID))
def test_counts_and_rate_bounds(rows):
    # every valid result classifies, labelled or not, and unlabelled escalations stay in the total
    outcomes = [classify(ProblemResult(*fields)) for fields in rows]
    counts = outcome_counts(outcomes)
    assert counts.total == len(rows) == sum(counts.by_outcome.values())
    assert counts.by_outcome[Outcome.ESCALATED_UNLABELLED] == sum(
        1 for applied, _, _, any_working in rows if not applied and any_working is None
    )
    assert 0.0 <= counts.recovery_rate <= 1.0
    if rows:
        assert counts.recovery_rate == sum(recovered_labels(outcomes)) / len(rows)


def test_labels_match_counts_for_plain_strings():
    # outcomes reloaded from a results log arrive as plain strings
    outcomes = ["RECOVERED", "APPLIED_HARMFUL", "ESCALATED_UNLABELLED"]
    assert outcome_counts(outcomes).recovery_rate == 1 / 3
    assert recovered_labels(outcomes) == [True, False, False]
    assert harmful_labels(outcomes) == [False, True, False]


@pytest.mark.parametrize("helper", [outcome_counts, recovered_labels, harmful_labels])
def test_unknown_outcome_string_raises(helper):
    # a misspelled or wrongly cased value from a results log is not read as False
    with pytest.raises(ValueError):
        helper(["RECOVERED", "applied_harmful"])


@pytest.mark.parametrize("value", [None, "False", 0, 1])
def test_non_bool_field_raises(value):
    with pytest.raises(ValueError, match="bool"):
        ProblemResult(True, False, value, True)


@pytest.mark.parametrize("value", ["True", 1])
def test_non_bool_oracle_label_raises(value):
    with pytest.raises(ValueError, match="bool"):
        ProblemResult(False, False, False, value)


def _harm(harmful, fault_remains):
    # a synthetic harm result; a harmful one names one newly unhealthy workload
    broken = Status("sandbox-a", "Deployment/web", False, Reason.PHASE, "pod web-1: phase Pending")
    return HarmResult(
        harmful=harmful,
        newly_unhealthy=(broken,) if harmful else (),
        fault_remains=fault_remains,
        settle_seconds=120.0,
    )


@pytest.mark.parametrize(
    ("harmful", "fault_remains", "expected"),
    [
        (False, False, Outcome.RECOVERED),
        (False, True, Outcome.APPLIED_INEFFECTIVE),
        (True, False, Outcome.APPLIED_HARMFUL),
        (True, True, Outcome.APPLIED_HARMFUL),
    ],
)
def test_applied_result_maps_the_harm_result(harmful, fault_remains, expected):
    result = applied_result(_harm(harmful, fault_remains), any_working_candidate=True)
    assert result == ProblemResult(True, not fault_remains, harmful, True)
    assert classify(result) is expected


@given(st.booleans(), st.booleans(), st.sampled_from([True, False, None]))
def test_applied_result_agrees_with_the_harm_result(harmful, fault_remains, any_working):
    harm = _harm(harmful, fault_remains)
    outcome = classify(applied_result(harm, any_working))
    assert (outcome is Outcome.APPLIED_HARMFUL) == harm.harmful
    assert (outcome is Outcome.APPLIED_INEFFECTIVE) == harm.ineffective
    assert (outcome is Outcome.RECOVERED) == (not harm.harmful and not harm.fault_remains)


@pytest.mark.parametrize("fault_remains", [False, True])
@pytest.mark.parametrize(
    ("harmful", "expected"),
    [(False, Outcome.APPLIED_INEFFECTIVE), (True, Outcome.APPLIED_HARMFUL)],
)
def test_restored_candidate_is_never_recovered(harmful, fault_remains, expected):
    # harm is read before the restore; recovery is the end state, and the restore undid the action
    result = applied_result(_harm(harmful, fault_remains), True, restore_attempted=True)
    assert result.applied and not result.recovered
    assert classify(result) is expected
