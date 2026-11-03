import json
import os
import subprocess

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, Reason, check, load_policy
from remvalgate.harness.no_check import (
    VerdictMismatch,
    ablate_problem,
    pooled,
    recheck,
    tally_record,
)
from remvalgate.planner.loop import Candidate, Draws, draw_record

POLICY = load_policy(DEFAULT_POLICY_PATH)
NS = "sandbox-a"
ADMITTED = f"kubectl -n {NS} scale deployment web --replicas=1"
OUT_OF_SANDBOX = "kubectl -n kube-system scale deployment web --replicas=1"
BINARY = f"rm -rf /tmp/x -n {NS}"
METACHAR = f"kubectl -n {NS} get pods; rm -rf /"


def _candidate(seed: int, command: str | None) -> Candidate:
    verdict = None if command is None else check(command, POLICY, NS)
    rejection = None
    if command is None:
        rejection = "the reply has no non-empty command"
    elif not verdict.admitted:
        rejection = f"allow-list: {verdict.reason}"
    return Candidate(seed, "{}", command, verdict, rejection, 10, 0.1)


def _records(*draws: list[str | None]) -> list[dict]:
    """Candidates as a transcript records them, seeds counted on across draws."""
    seed = iter(range(100))
    built = tuple(tuple(_candidate(next(seed), c) for c in draw) for draw in draws)
    # plan is unused when draws are given
    record = draw_record(None, Draws(built))
    # through JSON, as the transcript is read back: reasons become plain strings
    return json.loads(json.dumps(record, default=str))["candidates"]


def test_first_draw_counts_by_reason():
    records = _records([ADMITTED, OUT_OF_SANDBOX, BINARY, METACHAR, None])
    result = ablate_problem("p", records, POLICY, NS)
    t = result.first_draw
    assert (t.candidates, t.no_command, t.removed, t.checked.admitted) == (5, 1, 3, 1)
    assert t.checked.by_reason[Reason.OUT_OF_SANDBOX] == 1
    assert t.checked.by_reason[Reason.BINARY_NOT_ALLOWED] == 1
    assert t.checked.by_reason[Reason.SHELL_METACHAR] == 1
    assert result.resampled is False
    assert result.both_draws == t


def test_second_draw_counts_in_both_draws_only():
    records = _records([OUT_OF_SANDBOX, BINARY], [ADMITTED, OUT_OF_SANDBOX])
    result = ablate_problem("p", records, POLICY, NS)
    assert result.resampled is True
    assert (result.first_draw.candidates, result.first_draw.removed) == (2, 2)
    assert (result.both_draws.candidates, result.both_draws.removed) == (4, 3)
    assert result.both_draws.checked.by_reason[Reason.OUT_OF_SANDBOX] == 2


def test_shares_are_of_all_candidates_in_the_view():
    record = tally_record(
        ablate_problem("p", _records([ADMITTED, BINARY, None, None]), POLICY, NS).first_draw
    )
    assert record["candidates"] == 4
    assert record["removed"] == {"count": 1, "share": 0.25}
    assert record["no_command"] == {"count": 2, "share": 0.5}
    assert record["admitted"] == {"count": 1, "share": 0.25}
    assert record["by_reason"]["BINARY_NOT_ALLOWED"] == {"count": 1, "share": 0.25}
    assert set(record["by_reason"]) == {r.value for r in Reason}


def test_empty_view_has_no_share():
    record = tally_record(ablate_problem("p", [], POLICY, NS).first_draw)
    assert record["removed"] == {"count": 0, "share": None}


def test_pooled_sums_problems():
    a = ablate_problem("a", _records([ADMITTED, BINARY]), POLICY, NS)
    b = ablate_problem("b", _records([OUT_OF_SANDBOX], [None]), POLICY, NS)
    first, both = pooled([a, b], both=False), pooled([a, b], both=True)
    assert (first.candidates, first.removed, first.no_command) == (3, 2, 0)
    assert (both.candidates, both.removed, both.no_command) == (4, 2, 1)
    assert first.checked.total == 3
    assert first.checked.admitted == 1


def test_changed_verdict_is_refused():
    records = _records([ADMITTED])
    records[0]["verdict"]["admitted"] = False
    records[0]["verdict"]["reason"] = "FLAG_DENIED"
    with pytest.raises(VerdictMismatch, match="seed 0"):
        recheck(records, POLICY, NS)


def test_wrong_namespace_is_a_mismatch():
    # the candidates were checked in NS, so another namespace changes the verdicts
    with pytest.raises(VerdictMismatch):
        recheck(_records([ADMITTED]), POLICY, "sandbox-b")


def test_missing_verdict_is_refused():
    records = _records([ADMITTED])
    records[0]["verdict"] = None
    with pytest.raises(VerdictMismatch):
        recheck(records, POLICY, NS)


@pytest.mark.parametrize("draws", [[2], [1, 2, 1], [1, 3]])
def test_bad_draw_numbers_are_refused(draws):
    records = _records([ADMITTED] * len(draws))
    for record, draw in zip(records, draws, strict=True):
        record["draw"] = draw
    with pytest.raises(ValueError, match="expected draw 1"):
        ablate_problem("p", records, POLICY, NS)


def test_second_draw_after_an_admitted_candidate_is_refused():
    records = _records([ADMITTED], [BINARY])
    with pytest.raises(ValueError, match="second draw"):
        ablate_problem("p", records, POLICY, NS)


def test_nothing_is_executed(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the offline check ran a process")

    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(os, "system", refuse)
    records = _records([OUT_OF_SANDBOX, BINARY, METACHAR, None], [ADMITTED])
    result = ablate_problem("p", records, POLICY, NS)
    assert result.both_draws.candidates == 5
