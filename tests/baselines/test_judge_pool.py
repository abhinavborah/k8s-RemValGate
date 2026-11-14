import json

import pytest

from remvalgate.baselines.judge_pool import score_problem, total
from remvalgate.planner.chat import Completion

NS = "test-social-network"
FIX = f"kubectl -n {NS} scale deployment media-service --replicas=1"
OTHER = f"kubectl -n {NS} rollout restart deployment media-service"


class FakeChat:
    def __init__(self, scores):
        self._replies, self.seen = iter(json.dumps({"score": s}) for s in scores), []

    def complete(self, messages, seed=None, logprobs=False):
        self.seen.append(messages)
        return Completion(next(self._replies), 5, 0.1, "stop")


def transcript(*cands, diagnosis=True):
    return {
        "diagnosis": {"fault_type": "ScalePod", "service": "media-service", "reason": "zero"}
        if diagnosis
        else None,
        "context": [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}],
        "candidates": [
            {"seed": s, "command": c, "rejection": r, "draw": 0, "reply": ""} for s, c, r in cands
        ],
    }


def labels(*worked):
    return {
        "problem_id": "p",
        "candidates": [{"seed": s, "worked": w} for s, w in worked],
    }


def test_the_top_score_is_picked_and_its_label_decides():
    t = transcript((0, OTHER, None), (1, FIX, None))
    chat = FakeChat([0.2, 0.9])
    scores, rec = score_problem(t, labels((0, False), (1, True)), NS, chat=chat)
    assert rec == {
        "problem_id": "p",
        "scored": 2,
        "pick_seed": 1,
        "escalated": False,
        "solved": True,
    }
    # the judge never sees a label
    assert all(
        "worked" not in m[1]["content"] and "RECOVERED" not in m[1]["content"] for m in chat.seen
    )


def test_a_refused_candidate_is_not_scored_and_a_tie_keeps_the_earlier():
    t = transcript((0, OTHER, None), (1, "kubectl edit x", "VERB_NOT_ALLOWED"), (2, FIX, None))
    chat = FakeChat([0.5, 0.5])
    _, rec = score_problem(t, labels((0, False), (1, True), (2, True)), NS, chat=chat)
    assert len(chat.seen) == 2 and rec["pick_seed"] == 0 and rec["solved"] is False


def test_no_score_or_no_diagnosis_escalates_and_is_not_solved():
    t = transcript((0, FIX, None))
    chat = FakeChat([])
    chat._replies = iter(["no number"])
    _, rec = score_problem(t, labels((0, True)), NS, chat=chat)
    assert rec["escalated"] and not rec["solved"]
    _, rec = score_problem(
        transcript((0, FIX, None), diagnosis=False), labels((0, True)), NS, chat=FakeChat([])
    )
    assert rec["escalated"] and not rec["solved"] and rec["scored"] == 0


def test_seeds_that_disagree_with_the_labels_are_refused():
    with pytest.raises(ValueError, match="seeds differ"):
        score_problem(transcript((0, FIX, None)), labels((1, True)), NS, chat=FakeChat([0.9]))


def test_the_total_needs_every_pool_problem_once():
    recs = [
        {"problem_id": "a", "solved": True, "escalated": False},
        {"problem_id": "b", "solved": False, "escalated": True},
    ]
    assert total(recs, ["a", "b"]) == {"problems": 2, "solved": 1, "escalated": 1}
    with pytest.raises(ValueError):
        total(recs[:1], ["a", "b"])
