"""Baseline 6 (E6-S5, BRD R6.5): a prompted model scores each candidate in place of the gate.

The judge's input is a choice made for this baseline, since the gate's input is defined only as an
(incident, candidate) pair (R8.1): the planner's task prompt (task, cluster state and the
retrieved past fixes), its diagnosis and one candidate command, without the planner's
investigation turns. If the gate is given more or less, that difference is recorded where the gate
is compared with this baseline. The judge never sees an oracle label or an execution result. It
replies with a verbalized probability that the command fixes the fault, not a log-probability
score, since LM Studio likely returns no logprobs (see planner/chat.py).

A reply that holds no number in [0, 1] gets the score None and a rejection. How a None score is
treated in the metrics, which raise on nan, is left to the main run (#55), as is the threshold
pick uses. Only candidates the allow-list admits are scored, as the gate scores admissible
candidates only (R8.1).

This judge is a system under test, scored against execution labels like the gate. It is not an
evaluator, so the human-label agreement rule for LLM judges does not apply to it. When the judge
and the planner share a model family, its scores are confounded with the planner's own
preferences; JudgeModel records both families so every result carries the flag.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from remvalgate.planner.chat import Chat
from remvalgate.planner.loop import Candidate, Plan, _json_object


@dataclass(frozen=True)
class JudgeModel:
    name: str
    version: str
    # the families as plain names, for example "qwen3.5"; stated by the caller, not parsed
    family: str
    planner_family: str

    @property
    def shared_family(self) -> bool:
        return self.family == self.planner_family


@dataclass(frozen=True)
class Score:
    # the candidate's sampling seed, which identifies it within its plan
    seed: int
    command: str
    # the probability the judge gave, None when the reply holds none
    score: float | None
    reply: str
    rejection: str | None
    completion_tokens: int
    seconds: float
    finish_reason: str = ""


def _system_prompt(namespace: str) -> str:
    return (
        f"You review a proposed fix for a fault in the Kubernetes namespace {namespace}. "
        "Reply with exactly one JSON object and nothing else: "
        '{"score": P} where P is your probability, from 0 to 1, that running the command fixes '
        "the fault without harming anything else."
    )


def _user_prompt(result: Plan, command: str) -> str:
    d = result.diagnosis
    # context holds the system prompt, then the task prompt
    task = result.context[1]["content"]
    return (
        f"{task}\n\nDiagnosis: {d.fault_type} on service {d.service}. {d.reason}\n\n"
        f"Proposed command: {command}"
    )


def _parse(text: str, finish_reason: str) -> tuple[float | None, str | None]:
    try:
        obj = _json_object(text)
    except ValueError as error:
        if finish_reason == "length":
            return None, "the reply was cut at the token cap"
        return None, str(error)
    score = obj.get("score")
    # bool is an int in Python, and true is not a probability
    if isinstance(score, bool) or not isinstance(score, int | float):
        return None, "the reply has no numeric score"
    if not math.isfinite(score) or not 0 <= score <= 1:
        return None, f"the score {score} is not in [0, 1]"
    return float(score), None


def judge(
    result: Plan, candidates: Sequence[Candidate], namespace: str, *, chat: Chat
) -> tuple[Score, ...]:
    """Score each admitted candidate with one model call, in the order given.

    Raises ValueError for a plan with no diagnosis. The call uses the chat client's own seed, so
    every candidate is judged under the same settings. A command repeated across candidates is
    judged once: each repeat keeps its own seed and the first call's reply, with 0 tokens and 0.0
    seconds, so summing over the scores gives the cost of the calls made.
    """
    if result.diagnosis is None or not result.context:
        raise ValueError("the plan has no diagnosis to judge against")
    scores, seen = [], {}
    for c in candidates:
        if c.rejection is not None or c.command is None:
            continue
        repeat = c.command in seen
        if not repeat:
            messages = [
                {"role": "system", "content": _system_prompt(namespace)},
                {"role": "user", "content": _user_prompt(result, c.command)},
            ]
            seen[c.command] = chat.complete(messages)
        completion = seen[c.command]
        score, rejection = _parse(completion.text, completion.finish_reason)
        scores.append(
            Score(
                c.seed,
                c.command,
                score,
                completion.text,
                rejection,
                0 if repeat else completion.completion_tokens,
                0.0 if repeat else completion.seconds,
                completion.finish_reason,
            )
        )
    return tuple(scores)


def pick(scores: Sequence[Score], threshold: float) -> Score | None:
    """The top-scored candidate if its score reaches the threshold, else None (escalate).

    A tie keeps the earlier candidate. Candidates with no score are never picked.
    """
    scored = [s for s in scores if s.score is not None]
    if not scored:
        return None
    best = max(scored, key=lambda s: s.score)
    return best if best.score >= threshold else None
