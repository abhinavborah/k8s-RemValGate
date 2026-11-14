"""Baseline 6 on the pool (#44, #55, BRD R6.5): the prompted judge ranks labelled candidates.

The judge of baselines/judge.py scores the admitted candidates of one labelling run per pool
problem, from the run's transcript, at temperature 0. Its pick is the top score with threshold 0
(#55): a tie keeps the earlier candidate, a candidate with no score is never picked, and when no
candidate has a score, or the plan had no diagnosis, the system escalates and the problem counts
as not recovered. The pick's outcome is read from the run's oracle labels by seed, so nothing is
executed. The judge sees no label: labels are read only after every score of the problem is in.
"""

from collections.abc import Mapping, Sequence
from types import SimpleNamespace

from remvalgate.baselines.judge import Score, judge, pick
from remvalgate.planner.chat import Chat
from remvalgate.planner.loop import Candidate, Diagnosis

THRESHOLD = 0.0


def _plan(transcript: Mapping) -> SimpleNamespace:
    """The two fields judge reads: the diagnosis and the conversation before the final answer."""
    d = transcript.get("diagnosis")
    diagnosis = Diagnosis(d["fault_type"], d["service"], d["reason"]) if d else None
    return SimpleNamespace(diagnosis=diagnosis, context=tuple(transcript.get("context") or ()))


def _candidates(transcript: Mapping) -> list[Candidate]:
    """The candidates of the last draw, as the gate scores the last draw."""
    rows = transcript.get("candidates") or []
    last = max((r.get("draw", 0) for r in rows), default=0)
    return [
        Candidate(r["seed"], r.get("reply", ""), r.get("command"), None, r.get("rejection"), 0, 0.0)
        for r in rows
        if r.get("draw", 0) == last
    ]


def score_problem(
    transcript: Mapping, labels: Mapping, namespace: str, *, chat: Chat
) -> tuple[tuple[Score, ...], dict]:
    """The judge's scores for one labelled problem, and its pick and outcome.

    Raises ValueError when the transcript's candidates and the labels' disagree in seeds."""
    plan = _plan(transcript)
    candidates = _candidates(transcript)
    worked = {c["seed"]: bool(c["worked"]) for c in labels["candidates"]}
    if candidates and sorted(c.seed for c in candidates) != sorted(worked):
        raise ValueError(f"{labels['problem_id']}: the transcript's seeds differ from the labels'")
    if plan.diagnosis is None or not plan.context or not candidates:
        scores = ()
    else:
        scores = judge(plan, candidates, namespace, chat=chat)
    best = pick(scores, THRESHOLD)
    record = {
        "problem_id": labels["problem_id"],
        "scored": sum(s.score is not None for s in scores),
        "pick_seed": None if best is None else best.seed,
        "escalated": best is None,
        "solved": best is not None and worked[best.seed],
    }
    return scores, record


def total(records: Sequence[Mapping], pool: Sequence[str]) -> dict:
    """Problems solved over the pool; every pool problem must have exactly one record."""
    ids = [r["problem_id"] for r in records]
    if sorted(ids) != sorted(pool):
        raise ValueError("baseline 6 needs one record per pool problem")
    return {
        "problems": len(ids),
        "solved": sum(r["solved"] for r in records),
        "escalated": sum(r["escalated"] for r in records),
    }
