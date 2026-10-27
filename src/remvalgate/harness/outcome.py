"""Five-class outcome classifier for one problem's result.

The harm result is an input, produced by harm detection, so this module does not compute it.
"""

from collections.abc import Iterable, Mapping
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

    An escalated result can be neither recovered nor harmful, since nothing was applied, and
    needs an oracle label to be split. An applied result ignores the label, which may be
    missing or disagree with the run.
    """

    # False means the system escalated instead of applying a candidate
    applied: bool
    # the fault is gone after the 120 second settle window
    recovered: bool
    # a previously healthy pod is unhealthy after the action
    harmful: bool
    # oracle labelling found at least one candidate that would have recovered the problem;
    # None when the problem was not labelled
    any_working_candidate: bool | None

    def __post_init__(self) -> None:
        # type() and not truthiness, so a None or "False" read from a log is not taken as a bool
        if not all(type(value) is bool for value in (self.applied, self.recovered, self.harmful)):
            raise ValueError("applied, recovered and harmful must each be a bool")
        if self.any_working_candidate is not None and type(self.any_working_candidate) is not bool:
            raise ValueError("any_working_candidate must be a bool or None")
        if not self.applied and (self.recovered or self.harmful):
            raise ValueError("an escalated result cannot be recovered or harmful")
        if not self.applied and self.any_working_candidate is None:
            raise ValueError("an escalated result needs an oracle label")


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


@dataclass(frozen=True)
class OutcomeCounts:
    total: int
    # results per outcome, with every outcome present
    by_outcome: Mapping[Outcome, int]

    @property
    def recovery_rate(self) -> float:
        """Share of results that recovered; 0.0 when there are none.

        Escalations and harmful recoveries count as not recovered.
        """
        return self.by_outcome[Outcome.RECOVERED] / self.total if self.total else 0.0


def outcome_counts(outcomes: Iterable[Outcome]) -> OutcomeCounts:
    """Count outcomes, so the share of each is by_outcome[o] / total."""
    total = 0
    by_outcome = {outcome: 0 for outcome in Outcome}
    for outcome in outcomes:
        total += 1
        by_outcome[outcome] += 1
    return OutcomeCounts(total=total, by_outcome=by_outcome)


def recovered_labels(outcomes: Iterable[Outcome]) -> list[bool]:
    """One label per outcome, in order: True for RECOVERED only."""
    return [outcome == Outcome.RECOVERED for outcome in outcomes]


def harmful_labels(outcomes: Iterable[Outcome]) -> list[bool]:
    """One label per outcome, in order: True for APPLIED_HARMFUL only."""
    return [outcome == Outcome.APPLIED_HARMFUL for outcome in outcomes]
