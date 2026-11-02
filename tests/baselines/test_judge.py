import json

import pytest

from remvalgate.baselines.judge import JudgeModel, Score, judge, pick
from remvalgate.planner.chat import Completion
from remvalgate.planner.loop import Candidate, Diagnosis, Plan, Stop

NS = "test-social-network"
FIX = f"kubectl -n {NS} scale deployment media-service --replicas=1"
TASK = "Users report errors.\n\nCluster state:\nmedia-service has no pods"


class FakeChat:
    def __init__(self, replies, finish=""):
        self._replies, self._finish, self.seen = iter(replies), finish, []

    def complete(self, messages, seed=None, logprobs=False):
        self.seen.append(list(messages))
        return Completion(next(self._replies), 5, 0.1, self._finish)


def plan_with_diagnosis():
    return Plan(
        Diagnosis("ScalePod", "media-service", "zero replicas"),
        FIX,
        None,
        Stop.FINAL,
        (),
        (),
        "system",
        ({"role": "system", "content": "system"}, {"role": "user", "content": TASK}),
    )


def candidate(seed, command=FIX, rejection=None):
    return Candidate(seed, "", command, None, rejection, 1, 0.1)


def score(p):
    return json.dumps({"score": p})


def test_each_admitted_candidate_is_scored_with_the_gates_inputs_and_no_label():
    chat = FakeChat([score(0.9), score(0.2)])
    other = f"kubectl -n {NS} rollout restart deployment media-service"
    scores = judge(
        plan_with_diagnosis(),
        [candidate(0), candidate(1, rejection="allow-list: no"), candidate(2, other)],
        NS,
        chat=chat,
    )
    assert [(s.seed, s.score, s.rejection) for s in scores] == [(0, 0.9, None), (2, 0.2, None)]
    user = chat.seen[0][1]["content"]
    assert TASK in user and "ScalePod on service media-service" in user and FIX in user
    assert other in chat.seen[1][1]["content"]


def test_a_repeated_command_is_judged_once_and_each_repeat_keeps_its_seed():
    chat = FakeChat([score(0.7)])
    scores = judge(plan_with_diagnosis(), [candidate(0), candidate(1)], NS, chat=chat)
    assert len(chat.seen) == 1
    assert [(s.seed, s.score) for s in scores] == [(0, 0.7), (1, 0.7)]
    # the cost of the one call made, not one per repeat
    assert [(s.completion_tokens, s.seconds) for s in scores] == [(5, 0.1), (0, 0.0)]


@pytest.mark.parametrize(
    "reply, reason",
    [
        ("no json here", "no JSON object"),
        (score(1.5), "not in [0, 1]"),
        (score(-0.1), "not in [0, 1]"),
        (json.dumps({"score": True}), "no numeric score"),
        (json.dumps({"score": "0.8"}), "no numeric score"),
        ('{"score": NaN}', "not in [0, 1]"),
    ],
)
def test_a_reply_with_no_probability_gets_no_score_and_a_rejection(reply, reason):
    (s,) = judge(plan_with_diagnosis(), [candidate(0)], NS, chat=FakeChat([reply]))
    assert s.score is None and reason in s.rejection


def test_a_reply_cut_at_the_token_cap_is_named_as_such():
    chat = FakeChat(['{"sco'], finish="length")
    (s,) = judge(plan_with_diagnosis(), [candidate(0)], NS, chat=chat)
    assert s.rejection == "the reply was cut at the token cap" and s.finish_reason == "length"


def test_a_plan_with_no_diagnosis_is_refused_before_any_model_call():
    capped = Plan(None, None, None, Stop.TURN_CAP, (), (), "system")
    with pytest.raises(ValueError, match="no diagnosis"):
        judge(capped, [candidate(0)], NS, chat=FakeChat([]))


def made(seed, p):
    return Score(seed, FIX, p, "", None, 1, 0.1)


def test_pick_takes_the_top_score_at_the_threshold_and_escalates_below_it():
    scores = [made(0, 0.4), made(1, None), made(2, 0.7), made(3, 0.7)]
    assert pick(scores, 0.7).seed == 2
    assert pick(scores, 0.71) is None
    assert pick([made(0, None)], 0.0) is None


def test_the_shared_model_family_is_recorded_as_a_confound():
    assert JudgeModel("qwen/qwen3.5-4b", "q8_0", "qwen3.5", "qwen3.5").shared_family
    assert not JudgeModel("google/gemma", "q8_0", "gemma", "qwen3.5").shared_family
