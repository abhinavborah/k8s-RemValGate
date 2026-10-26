"""Five-class outcome classifier for one problem's result.

The harm result is an input, produced by harm detection, so this module does not compute it.
"""

from dataclasses import dataclass
from enum import StrEnum


class Outcome(StrEnum):
    RECOVERED = "RECOVERED"
    ESCALATED_WITH_WORKING = "ESCALATED_WITH_WORKING"
    ESCALATED_WITH_NONE = "ESCALATED_WITH_NONE"
    APPLIED_INEFFECTIVE = "APPLIED_INEFFECTIVE"
    APPLIED_HARMFUL = "APPLIED_HARMFUL"


@dataclass(frozen=True)
class ProblemResult:
    """One problem's result. Raises ValueError for a combination that cannot happen.

    An escalated result can be neither recovered nor harmful, since nothing was applied. An
    applied result that recovered without harm implies a working candidate.
    """

    # False means the system escalated instead of applying a candidate
    applied: bool
    # the fault is gone after the 120 second settle window
    recovered: bool
    # a previously healthy pod is unhealthy after the action
    harmful: bool
    # oracle labelling found at least one candidate that would have recovered the problem
    any_working_candidate: bool

    def __post_init__(self) -> None:
        if not self.applied and (self.recovered or self.harmful):
            raise ValueError("an escalated result cannot be recovered or harmful")
        if self.applied and self.recovered and not self.harmful and not self.any_working_candidate:
            raise ValueError("a result that recovered without harm needs a working candidate")


def classify(result: ProblemResult) -> Outcome:
    """Classify a result. The first rule that applies wins.

    Escalated: ESCALATED_WITH_WORKING if any candidate works, else ESCALATED_WITH_NONE.
    Applied: APPLIED_HARMFUL if harmful, even when it also recovered; else RECOVERED if
    recovered; else APPLIED_INEFFECTIVE.
    """
    if not result.applied:
        if result.any_working_candidate:
            return Outcome.ESCALATED_WITH_WORKING
        return Outcome.ESCALATED_WITH_NONE
    if result.harmful:
        return Outcome.APPLIED_HARMFUL
    if result.recovered:
        return Outcome.RECOVERED
    return Outcome.APPLIED_INEFFECTIVE
