"""Ablation without the deterministic check (story E5-S4, BRD R5.4), applied offline.

The allow-list is applied again to every sampled candidate a run recorded, so its contribution is
measured without running an out-of-policy command: nothing here runs a command or reaches a
cluster. The planner's own command is not a sampled candidate and is left out.

Two views are reported. The first draw is the headline: without the check no candidate is
rejected, so the second draw, made only because every candidate of the first was rejected, would
not be drawn. Both draws together count every candidate sampled. A candidate whose reply held no
command is counted apart, since the check never saw it. Shares are of all candidates in the view.

Each recorded verdict is compared with the recomputed one, so a policy changed since the run
refuses the report instead of mixing two policies.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from remvalgate.harness.allowlist import Policy, Reason, ReasonCounts, Verdict, check, reason_counts


class VerdictMismatch(ValueError):
    pass


@dataclass(frozen=True)
class Tally:
    # every candidate in the view, with or without a command
    candidates: int
    # replies with no command, or cut at the token cap
    no_command: int
    # the allow-list's verdicts on the candidates that have a command
    checked: ReasonCounts

    @property
    def removed(self) -> int:
        return sum(self.checked.by_reason.values())

    def share(self, count: int) -> float | None:
        """count over all candidates in the view, None when there are none."""
        return count / self.candidates if self.candidates else None


@dataclass(frozen=True)
class ProblemResult:
    problem_id: str
    first_draw: Tally
    both_draws: Tally
    # True when every candidate of the first draw was rejected, so a second draw was made
    resampled: bool


def recheck(candidates: Sequence[Mapping], policy: Policy, namespace: str) -> list[Verdict | None]:
    """The allow-list verdict on each recorded candidate, None for one with no command.

    Raises VerdictMismatch when a recomputed verdict differs from the recorded one.
    """
    verdicts: list[Verdict | None] = []
    for c in candidates:
        command, recorded = c["command"], c["verdict"]
        verdict = None if command is None else check(command, policy, namespace)
        if (recorded is None) != (verdict is None) or (
            verdict is not None
            and (recorded["admitted"], recorded["reason"]) != (verdict.admitted, verdict.reason)
        ):
            raise VerdictMismatch(
                f"seed {c['seed']}: recorded verdict {recorded} but the policy now gives "
                f"{verdict}; rerun with the policy the candidates were checked under"
            )
        verdicts.append(verdict)
    return verdicts


def tally(verdicts: Iterable[Verdict | None]) -> Tally:
    verdicts = list(verdicts)
    return Tally(
        candidates=len(verdicts),
        no_command=sum(v is None for v in verdicts),
        checked=reason_counts(v for v in verdicts if v is not None),
    )


def ablate_problem(
    problem_id: str, candidates: Sequence[Mapping], policy: Policy, namespace: str
) -> ProblemResult:
    """Recheck one problem's recorded candidates (a transcript's "candidates" list).

    Raises ValueError when the draws are not 1, or 1 then 2, or when a second draw follows a first
    with an admitted candidate, and VerdictMismatch as recheck does.
    """
    draws = [c["draw"] for c in candidates]
    if draws and (draws[0] != 1 or draws != sorted(draws) or not set(draws) <= {1, 2}):
        raise ValueError(f"{problem_id}: expected draw 1, then draw 2 if any, got {draws}")
    # resample_once draws again only when every candidate of the first draw is rejected
    if 2 in draws and any(c["rejection"] is None for c in candidates if c["draw"] == 1):
        raise ValueError(f"{problem_id}: a second draw after a first with an admitted candidate")
    verdicts = recheck(candidates, policy, namespace)
    first = [v for v, d in zip(verdicts, draws, strict=True) if d == 1]
    return ProblemResult(
        problem_id=problem_id,
        first_draw=tally(first),
        both_draws=tally(verdicts),
        resampled=2 in draws,
    )


def pooled(results: Iterable[ProblemResult], both: bool) -> Tally:
    """The sum over problems of the first draw, or of both draws."""
    tallies = [r.both_draws if both else r.first_draw for r in results]
    by_reason = {reason: sum(t.checked.by_reason[reason] for t in tallies) for reason in Reason}
    return Tally(
        candidates=sum(t.candidates for t in tallies),
        no_command=sum(t.no_command for t in tallies),
        checked=ReasonCounts(total=sum(t.checked.total for t in tallies), by_reason=by_reason),
    )


def tally_record(t: Tally) -> dict:
    """A tally as JSON: counts with their shares of all candidates in the view."""

    def row(count: int) -> dict:
        return {"count": count, "share": t.share(count)}

    return {
        "candidates": t.candidates,
        "admitted": row(t.checked.admitted),
        "removed": row(t.removed),
        "no_command": row(t.no_command),
        "by_reason": {reason.value: row(n) for reason, n in t.checked.by_reason.items()},
    }
