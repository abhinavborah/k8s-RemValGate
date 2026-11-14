from remvalgate.harness.failures import breakdown, classify, diagnosis_class
from remvalgate.harness.outcome import Outcome

R, E, F = (
    o.value for o in (Outcome.RECOVERED, Outcome.ESCALATED_UNLABELLED, Outcome.APPLIED_INEFFECTIVE)
)


def test_each_run_gets_one_class_in_order():
    assert classify({"outcome": R, "stop": "turn_cap"}) == "recovered"
    assert classify({"outcome": E, "stop": "escalate", "command": None}) == "escalated"
    assert classify({"outcome": E, "stop": "turn_cap", "command": None}) == "turn cap"
    assert classify({"outcome": E, "stop": "final", "command": None}) == "no command"
    refused = {"outcome": E, "stop": "final", "command": "kubectl x", "allow_list": "FLAG_DENIED"}
    assert classify(refused) == "refused"
    applied = {"outcome": F, "stop": "final", "command": "kubectl x", "allow_list": "admitted"}
    assert classify(applied) == "applied, not recovered"


def test_the_diagnosis_is_scored_against_type_and_service():
    def d(fault_type, service):
        return {"diagnosis": {"fault_type": fault_type, "service": service, "reason": ""}}

    args = (Outcome.APPLIED_INEFFECTIVE, "TargetPortMisconfig", "user-service")
    assert diagnosis_class("p", {"diagnosis": None}, *args) == "none"
    assert diagnosis_class("p", d("ScalePod", "user-service"), *args) == "wrong fault type"
    assert diagnosis_class("p", d("TargetPortMisconfig", "text-service"), *args) == "wrong service"
    assert diagnosis_class("p", d("TargetPortMisconfig", "user-service"), *args) == "right"


def test_breakdown_counts_classes_reasons_and_diagnoses_per_arm():
    cases = [
        {"arm": "3c", "class": "refused", "reason": "FLAG_DENIED", "diagnosis": "right"},
        {"arm": "3c", "class": "refused", "reason": "FLAG_DENIED", "diagnosis": "right"},
        {"arm": "3c", "class": "recovered", "reason": "admitted", "diagnosis": "right"},
        {"arm": "7", "class": "escalated", "reason": None, "diagnosis": "none"},
    ]
    out = breakdown(cases)
    assert out["3c"] == {
        "runs": 3,
        "class": {"refused": 2, "recovered": 1},
        "refusal": {"FLAG_DENIED": 2},
        "diagnosis": {"right": 3},
    }
    assert out["7"]["class"] == {"escalated": 1} and out["7"]["refusal"] == {}
