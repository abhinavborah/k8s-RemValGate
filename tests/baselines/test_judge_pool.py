import dataclasses
import hashlib
import json
import subprocess
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import count

import pytest

from remvalgate.baselines.judge_pool import score_problem, score_runs, total
from remvalgate.harness.manifest import (
    SOCIAL_NETWORK,
    Manifest,
    Slice,
    load_registry,
    read_touches,
    start_run,
)
from remvalgate.harness.oracle import Label, ProblemLabels
from remvalgate.harness.outcome import Outcome
from remvalgate.planner.chat import Completion
from remvalgate.planner.models import MODEL

NS = SOCIAL_NETWORK
FIX = f"kubectl -n {NS} scale deployment media-service --replicas=1"
OTHER = f"kubectl -n {NS} rollout restart deployment media-service"
POOL_A, POOL_B, DEV, CONF = "p-synth-pool-a", "p-synth-pool-b", "p-synth-dev", "p-synth-conf"
PIN = "b" * 40
NOW = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
SEALED = "2026-10-27T09:00:00Z"


class FakeChat:
    def __init__(self, scores):
        self._replies, self.seen = iter(json.dumps({"score": s}) for s in scores), []

    def complete(self, messages, seed=None, logprobs=False):
        self.seen.append(messages)
        return Completion(next(self._replies), 5, 0.1, "stop")


@dataclasses.dataclass
class FakeSettings:
    # stands in for OpenAIChat in the recorded settings
    temperature: float = 0.0
    top_p: float = 0.8


class RecordedChat(FakeChat, FakeSettings):
    def __init__(self, scores):
        FakeChat.__init__(self, scores)
        FakeSettings.__init__(self)


def transcript(*cands, diagnosis=True, draw=1, baseline="3c"):
    return {
        "diagnosis": {"fault_type": "ScalePod", "service": "media-service", "reason": "zero"}
        if diagnosis
        else None,
        "context": [{"role": "system", "content": "s"}, {"role": "user", "content": "task"}],
        "candidates": [
            {"seed": s, "command": c, "rejection": r, "draw": draw, "reply": "", "verdict": None}
            for s, c, r in cands
        ],
        "draws": draw,
        "config": {"baseline": baseline},
    }


def labels(problem_id, *rows, run_id=None):
    """A labels file as the oracle writes it, from (seed, command, rejection, outcome) rows."""
    cands = tuple(
        Label(seed, command, rejection, None, None if outcome is None else Outcome(outcome))
        for seed, command, rejection, outcome in rows
    )
    return {**ProblemLabels(problem_id, cands, None).record(), "run_id": run_id}


R, F = "RECOVERED", "APPLIED_INEFFECTIVE"


def test_the_top_score_is_picked_and_its_label_decides():
    t = transcript((0, OTHER, None), (1, FIX, None))
    chat = FakeChat([0.2, 0.9])
    _, rec = score_problem(t, labels("p", (0, OTHER, None, F), (1, FIX, None, R)), NS, chat=chat)
    assert (rec["scored"], rec["pick_seed"], rec["escalated"], rec["solved"]) == (2, 1, False, True)
    # the judge never sees a label
    assert all(R not in m[1]["content"] and "worked" not in m[1]["content"] for m in chat.seen)


def test_a_worked_field_that_disagrees_with_the_outcome_is_refused():
    bad = labels("p", (0, FIX, None, F))
    bad["candidates"][0]["worked"] = True
    with pytest.raises(ValueError, match="disagree"):
        score_problem(transcript((0, FIX, None)), bad, NS, chat=FakeChat([0.9]))


def test_a_refused_candidate_is_not_scored_and_a_tie_keeps_the_earlier():
    t = transcript((0, OTHER, None), (1, "kubectl edit x", "VERB_NOT_ALLOWED"), (2, FIX, None))
    lab = labels(
        "p", (0, OTHER, None, F), (1, "kubectl edit x", "VERB_NOT_ALLOWED", None), (2, FIX, None, R)
    )
    chat = FakeChat([0.5, 0.5])
    _, rec = score_problem(t, lab, NS, chat=chat)
    assert len(chat.seen) == 2 and rec["pick_seed"] == 0 and rec["solved"] is False


def test_no_score_escalates_and_nothing_to_judge_is_refused():
    chat = FakeChat([])
    chat._replies = iter(["no number"])
    _, rec = score_problem(
        transcript((0, FIX, None)), labels("p", (0, FIX, None, R)), NS, chat=chat
    )
    assert rec["escalated"] and not rec["solved"]
    with pytest.raises(ValueError, match="nothing to judge"):
        score_problem(
            transcript((0, FIX, None), diagnosis=False),
            labels("p", (0, FIX, None, R)),
            NS,
            chat=FakeChat([]),
        )


def test_the_last_draw_is_judged():
    t = transcript((0, OTHER, None))
    t["candidates"] += [{"seed": 1, "command": FIX, "rejection": None, "draw": 2, "reply": ""}]
    chat = FakeChat([0.9])
    _, rec = score_problem(t, labels("p", (1, FIX, None, R)), NS, chat=chat)
    assert len(chat.seen) == 1 and FIX in chat.seen[0][1]["content"] and rec["pick_seed"] == 1


def test_seeds_that_disagree_with_the_labels_are_refused():
    with pytest.raises(ValueError, match="seeds differ"):
        score_problem(
            transcript((0, FIX, None)), labels("p", (1, FIX, None, R)), NS, chat=FakeChat([0.9])
        )


def test_the_total_needs_every_pool_problem_once():
    recs = [
        {"problem_id": "a", "solved": True, "escalated": False},
        {"problem_id": "b", "solved": False, "escalated": True},
    ]
    assert total(recs, ["a", "b"]) == {"problems": 2, "solved": 1, "escalated": 1}
    with pytest.raises(ValueError):
        total(recs[:1], ["a", "b"])


# the run loop, with a committed repository, a sealed confirmation slice and a registry


def _sum(problem_id, aiopslab_commit):
    return hashlib.sha256(f"{problem_id}@{aiopslab_commit}".encode()).hexdigest()


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    seal_touch = {
        "problem_id": CONF,
        "run_id": "seal-p-synth-conf-20261027t090000z",
        "slice": "confirmation",
        "timestamp": SEALED,
    }
    (repo / "runs" / "touch_log.jsonl").write_text(json.dumps(seal_touch, sort_keys=True) + "\n")
    seal = {"aiopslab": PIN, "checksums": {CONF: _sum(CONF, PIN)}, "commit": "c" * 40}
    (repo / "runs" / "confirmation_seal.json").write_text(json.dumps({**seal, "sealed": SEALED}))
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    registry = tmp_path / "slices.json"
    registry.write_text(
        json.dumps(
            {
                "pool": [POOL_A, POOL_B],
                "confirmation": [CONF],
                "development": [DEV],
                "singleton": [],
            }
        )
    )
    (tmp_path / "in").mkdir()
    return {
        "repo": repo,
        "log": repo / "runs" / "touch_log.jsonl",
        "registry": load_registry(registry),
        "in": tmp_path / "in",
        "out": tmp_path / "out",
    }


def _run_files(
    env,
    problem_id,
    slice_,
    cands,
    outcomes,
    *,
    write_labels=True,
    seeds=None,
    model=MODEL,
    baseline="3c",
):
    run_id = f"oracle-{problem_id}-20261113t100000z"
    t = transcript(*cands, baseline=baseline)
    manifest = Manifest(
        run_id=run_id,
        problem_id=problem_id,
        slice=Slice(slice_),
        commit="a" * 40,
        dirty=False,
        model_name=model,
        model_version="1.0",
        model_date="2026-11-13",
        seed=0,
        k=2,
        r=1,
        aiopslab_commit=PIN,
        cluster={"context": "kind-kind", "namespace": NS, "nodes": 2},
        timestamp="2026-11-13T10:00:00Z",
        sampling={"seeds": [c[0] for c in cands] if seeds is None else seeds},
    )
    path = env["in"] / f"{run_id}.json"
    path.write_text(json.dumps(dataclasses.asdict(manifest)))
    path.with_suffix(".transcript.json").write_text(json.dumps(t))
    if write_labels:
        rows = [(s, c, r, o) for (s, c, r), o in zip(cands, outcomes, strict=True)]
        path.with_suffix(".labels.json").write_text(
            json.dumps(labels(problem_id, *rows, run_id=run_id))
        )
    return path


def _score(env, paths, chat, model=MODEL):
    clock = count()
    return score_runs(
        paths,
        registry=env["registry"],
        chat=chat,
        model=model,
        model_version="q8",
        family="qwen3.5",
        build={"server": "fake"},
        touch_log=env["log"],
        repo=env["repo"],
        aiopslab_commit=PIN,
        out_dir=env["out"],
        now=lambda: NOW + timedelta(seconds=next(clock)),
        start=partial(start_run, checksum=_sum),
    )


def _b6_touches(env):
    return [t for t in read_touches(env["log"]) if t["run_id"].startswith("baseline6-")]


def test_a_pool_run_touches_each_problem_and_records_the_judge(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R])
    b = _run_files(env, POOL_B, "pool", [(0, OTHER, None), (1, FIX, None)], [F, R])
    summary, out = _score(env, [a, b], RecordedChat([0.9, 0.8, 0.1]))
    assert [t["problem_id"] for t in _b6_touches(env)] == [POOL_A, POOL_B]
    assert summary["total"] == {"problems": 2, "solved": 1, "escalated": 0}
    m = json.loads(next(env["out"].glob(f"baseline6-{POOL_A}-*z.json")).read_text())
    judge = m["sampling"]["judge"]
    assert judge["shared_family"] is True and judge["chat"]["top_p"] == 0.8
    assert judge["date"] == "2026-11-15" and m["model_build"] == {"server": "fake"}
    assert out.exists()


def test_bad_inputs_are_refused_before_any_touch(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R])
    b = _run_files(env, POOL_B, "pool", [(0, FIX, None)], [R], write_labels=False)
    with pytest.raises(ValueError, match="every pool problem"):
        _score(env, [a], FakeChat([0.9]))
    with pytest.raises(ValueError, match="no labels file"):
        _score(env, [a, b], FakeChat([0.9]))
    with pytest.raises(ValueError, match="development problems only"):
        _score(env, [a, b], FakeChat([0.9]), model="qwen/qwen3.5-9b")
    assert _b6_touches(env) == []


def test_another_model_than_the_candidates_is_refused_before_any_touch(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R], model="other")
    b = _run_files(env, POOL_B, "pool", [(0, FIX, None)], [R], model="other")
    with pytest.raises(ValueError, match="candidates are of other"):
        _score(env, [a, b], FakeChat([0.9]))
    assert _b6_touches(env) == []


def test_a_transcript_not_of_3c_is_refused_after_its_touch(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R], baseline="3cft")
    b = _run_files(env, POOL_B, "pool", [(0, FIX, None)], [R])
    with pytest.raises(ValueError, match="not of baseline 3c"):
        _score(env, [a, b], FakeChat([0.9, 0.9]))
    assert [t["problem_id"] for t in _b6_touches(env)] == [POOL_A]


def test_a_run_that_sampled_nothing_is_not_touched(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R])
    b = _run_files(env, POOL_B, "pool", [(0, FIX, None)], [R], seeds=[], write_labels=False)
    summary, _ = _score(env, [a, b], RecordedChat([0.9]))
    assert [t["problem_id"] for t in _b6_touches(env)] == [POOL_A]
    rec = {r["problem_id"]: r for r in summary["records"]}
    assert rec[POOL_B]["escalated"] and not rec[POOL_B]["solved"]


def test_seeds_that_disagree_with_the_manifest_keep_the_touch_and_write_no_summary(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R], seeds=[5])
    b = _run_files(env, POOL_B, "pool", [(0, FIX, None)], [R])
    with pytest.raises(ValueError, match="differ from the manifest"):
        _score(env, [a, b], FakeChat([0.9, 0.9]))
    assert [t["problem_id"] for t in _b6_touches(env)] == [POOL_A]
    assert not env["out"].exists() or not list(env["out"].glob("baseline6-summary-*"))


def test_each_result_names_the_bytes_it_read(env):
    a = _run_files(env, POOL_A, "pool", [(0, FIX, None)], [R])
    b = _run_files(env, POOL_B, "pool", [(0, FIX, None)], [R])
    summary, _ = _score(env, [a, b], RecordedChat([0.9, 0.9]))
    rec = {r["problem_id"]: r for r in summary["records"]}[POOL_A]
    assert (
        rec["transcript_sha256"]
        == hashlib.sha256(a.with_suffix(".transcript.json").read_bytes()).hexdigest()
    )
    assert (
        rec["labels_sha256"]
        == hashlib.sha256(a.with_suffix(".labels.json").read_bytes()).hexdigest()
    )
