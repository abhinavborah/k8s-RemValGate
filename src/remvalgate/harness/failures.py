"""Why each planner run failed, for the failure analysis (story E9-S7, #58).

A run is put in one class by its result row and transcript, in this order: recovered; escalated
by the planner (baseline 7); turn cap reached with no final answer; a final answer with no
command; the command refused by the allow-list (with the reason); the command applied and not
recovering (ineffective or harmful). Beside the class, the diagnosis is scored against the
problem's fault type and target service with rag_ablation.score: none, wrong fault type, wrong
service, or right. The classes are derived, not judged; the qualitative examples in
docs/failure_analysis.md are read from the transcripts by hand.
"""

from collections import Counter
from collections.abc import Mapping, Sequence

from remvalgate.harness.outcome import Outcome
from remvalgate.harness.rag_ablation import score

CLASSES = (
    "recovered",
    "escalated",
    "turn cap",
    "no command",
    "refused",
    "applied, not recovered",
)


def classify(row: Mapping) -> str:
    """The class of one result row of scripts/run_baselines_pool.py."""
    if Outcome(row["outcome"]) is Outcome.RECOVERED:
        return "recovered"
    if row.get("stop") == "escalate":
        return "escalated"
    if row.get("stop") == "turn_cap":
        return "turn cap"
    if not row.get("command"):
        return "no command"
    if row.get("allow_list") not in (None, "admitted"):
        return "refused"
    return "applied, not recovered"


def diagnosis_class(
    problem_id: str, transcript: Mapping, outcome: Outcome, true_type: str, true_service: str
) -> str:
    """none, wrong fault type, wrong service or right, for a planner transcript."""
    row = score(problem_id, transcript.get("diagnosis"), outcome, true_type, true_service)
    if not row.has_diagnosis:
        return "none"
    if not row.fault_type_correct:
        return "wrong fault type"
    if not row.diagnosis_correct:
        return "wrong service"
    return "right"


def breakdown(cases: Sequence[Mapping]) -> dict:
    """Counts per arm of run class, refusal reason and diagnosis class.

    cases are dicts with arm, class, reason (the allow-list reason or None) and diagnosis."""
    out: dict = {}
    for arm in sorted({c["arm"] for c in cases}):
        mine = [c for c in cases if c["arm"] == arm]
        out[arm] = {
            "runs": len(mine),
            "class": {k: v for k, v in Counter(c["class"] for c in mine).items()},
            "refusal": dict(Counter(c["reason"] for c in mine if c["class"] == "refused")),
            "diagnosis": dict(Counter(c["diagnosis"] for c in mine)),
        }
    return out
