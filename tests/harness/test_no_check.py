import dataclasses
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import count

import pytest

from remvalgate.harness import no_check
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, Reason, check, load_policy
from remvalgate.harness.manifest import (
    SOCIAL_NETWORK,
    Manifest,
    RunRefused,
    Slice,
    load_registry,
    read_manifest,
    read_touches,
    start_run,
)
from remvalgate.harness.no_check import (
    VerdictMismatch,
    ablate_problem,
    ablate_runs,
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


def _candidate(seed: int, command: str | None, namespace: str = NS) -> Candidate:
    verdict = None if command is None else check(command, POLICY, namespace)
    rejection = None
    if command is None:
        rejection = "the reply has no non-empty command"
    elif not verdict.admitted:
        rejection = f"allow-list: {verdict.reason}"
    return Candidate(seed, "{}", command, verdict, rejection, 10, 0.1)


def _transcript(*draws: list[str | None], namespace: str = NS) -> dict:
    """Draws as a transcript records them, seeds counted on across draws, read back from JSON."""
    seed = iter(range(100))
    built = tuple(tuple(_candidate(next(seed), c, namespace) for c in draw) for draw in draws)
    # plan is unused when draws are given
    record = draw_record(None, Draws(built))
    # through JSON, as the transcript is read back: reasons become plain strings
    return json.loads(json.dumps(record, default=str))


def _records(*draws: list[str | None]) -> list[dict]:
    return _transcript(*draws)["candidates"]


def test_first_draw_counts_by_reason():
    records = _records([ADMITTED, OUT_OF_SANDBOX, BINARY, METACHAR, None])
    result = ablate_problem("p", records, POLICY, NS)
    t = result.without_check
    assert (t.candidates, t.no_command, t.removed, t.checked.admitted) == (5, 1, 3, 1)
    assert t.checked.by_reason[Reason.OUT_OF_SANDBOX] == 1
    assert t.checked.by_reason[Reason.BINARY_NOT_ALLOWED] == 1
    assert t.checked.by_reason[Reason.SHELL_METACHAR] == 1
    assert (result.resampled, result.resampled_without_check) == (False, False)
    assert result.both_draws == t


def test_second_draw_after_rejections_counts_in_both_draws_only():
    # without the check the first draw's commands are admitted, so no second draw is made
    records = _records([OUT_OF_SANDBOX, None], [ADMITTED, OUT_OF_SANDBOX])
    result = ablate_problem("p", records, POLICY, NS)
    assert (result.resampled, result.resampled_without_check) == (True, False)
    assert (result.without_check.candidates, result.without_check.removed) == (2, 1)
    assert (result.both_draws.candidates, result.both_draws.removed) == (4, 2)
    assert result.both_draws.checked.by_reason[Reason.OUT_OF_SANDBOX] == 2


def test_second_draw_after_no_command_counts_in_the_headline():
    # a first draw with no command is resampled with or without the check
    records = _records([None, None], [ADMITTED, BINARY])
    result = ablate_problem("p", records, POLICY, NS)
    assert (result.resampled, result.resampled_without_check) == (True, True)
    assert result.without_check == result.both_draws
    assert (result.without_check.candidates, result.without_check.removed) == (4, 1)
    assert result.without_check.checked.by_reason[Reason.BINARY_NOT_ALLOWED] == 1


def test_shares_are_of_all_candidates_in_the_view():
    record = tally_record(
        ablate_problem("p", _records([ADMITTED, BINARY, None, None]), POLICY, NS).without_check
    )
    assert record["candidates"] == 4
    assert record["removed"] == {"count": 1, "share": 0.25}
    assert record["no_command"] == {"count": 2, "share": 0.5}
    assert record["admitted"] == {"count": 1, "share": 0.25}
    assert record["by_reason"]["BINARY_NOT_ALLOWED"] == {"count": 1, "share": 0.25}
    assert set(record["by_reason"]) == {r.value for r in Reason}


def test_empty_view_has_no_share():
    record = tally_record(ablate_problem("p", [], POLICY, NS).without_check)
    assert record["removed"] == {"count": 0, "share": None}


def test_pooled_sums_problems():
    a = ablate_problem("a", _records([ADMITTED, BINARY]), POLICY, NS)
    b = ablate_problem("b", _records([OUT_OF_SANDBOX], [None]), POLICY, NS)
    headline, both = pooled([a, b], both=False), pooled([a, b], both=True)
    assert (headline.candidates, headline.removed, headline.no_command) == (3, 2, 0)
    assert (both.candidates, both.removed, both.no_command) == (4, 2, 1)
    assert headline.checked.total == 3
    assert headline.checked.admitted == 1


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
    records = _records([BINARY] * len(draws))
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


# ablate_runs, on a temporary repository, registry and touch log with synthetic problem ids

POOL_A, POOL_B, DEV, CONF = "p-synth-pool-a", "p-synth-pool-b", "p-synth-dev", "p-synth-conf"
PIN = "b" * 40
NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
SEALED = "2026-10-27T09:00:00Z"
POLICY_SHA = "d" * 64
# the synthetic pool ids are not Hotel Reservation problems, so they run on Social Network
SN = SOCIAL_NETWORK


def _sum(problem_id, aiopslab_commit):
    """A stand-in for the confirmation checksum, so no test builds a confirmation problem."""
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
    """A committed repository with a sealed confirmation slice, its touch log, a registry, and
    input and output folders outside the repository so the tree stays clean."""
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


def _source(env, problem_id, slice_, *draws, transcript=None, **changes):
    """Write a planner run's manifest and transcript; returns the manifest path."""
    record = transcript or _transcript(*draws, namespace=SN)
    run_id = f"planner-{problem_id}-20261102t100000z"
    values = dict(
        run_id=run_id,
        problem_id=problem_id,
        slice=slice_,
        commit="a" * 40,
        dirty=False,
        model_name="synthetic-planner",
        model_version="1.0",
        model_date="2026-11-02",
        seed=0,
        k=2,
        r=1,
        aiopslab_commit=PIN,
        cluster={"context": "kind-kind", "namespace": SN, "nodes": 2},
        timestamp="2026-11-02T10:00:00Z",
        sampling={"temperature": 0.7, "seeds": [c["seed"] for c in record["candidates"]]},
    )
    values.update(changes)
    path = env["in"] / f"{run_id}.json"
    manifest = dataclasses.asdict(Manifest(**values))
    path.write_text(json.dumps(manifest))
    path.with_suffix(".transcript.json").write_text(json.dumps(record))
    return path


def _run(env, paths, start=None):
    clock = count()
    return ablate_runs(
        paths,
        registry=env["registry"],
        policy=POLICY,
        policy_sha256=POLICY_SHA,
        touch_log=env["log"],
        repo=env["repo"],
        aiopslab_commit=PIN,
        out_dir=env["out"],
        now=lambda: NOW + timedelta(seconds=next(clock)),
        start=start or partial(start_run, checksum=_sum),
    )


def _nocheck_touches(env):
    return [e for e in read_touches(env["log"]) if e["run_id"].startswith("nocheck-")]


def test_pool_runs_are_touched_and_traced(env):
    ok = f"kubectl -n {SN} scale deployment web --replicas=1"
    a = _source(env, POOL_A, Slice.POOL, [ok, "kubectl -n default get pods"])
    b = _source(env, POOL_B, Slice.POOL, [None, None], [ok, BINARY])
    summary, out = _run(env, [a, b])
    touches = _nocheck_touches(env)
    assert [(t["problem_id"], t["slice"]) for t in touches] == [(POOL_A, "pool"), (POOL_B, "pool")]
    assert summary["run_ids"] == [t["run_id"] for t in touches]
    assert summary["source_run_ids"] == [
        f"planner-{POOL_A}-20261102t100000z",
        f"planner-{POOL_B}-20261102t100000z",
    ]
    assert (summary["model_name"], summary["model_version"], summary["k"]) == (
        "synthetic-planner",
        "1.0",
        2,
    )
    assert summary["model_dates"] == ["2026-11-02"]
    assert summary["sampling"] == {"temperature": 0.7}
    assert (summary["logged"], summary["resampled"], summary["resampled_without_check"]) == (
        True,
        1,
        1,
    )
    # A: 2 candidates, 1 out of sandbox; B: both draws, as the first had no command
    assert summary["without_check"]["candidates"] == 6
    assert summary["without_check"]["removed"]["count"] == 2
    assert summary["without_check"]["by_reason"]["OUT_OF_SANDBOX"]["count"] == 1
    assert summary["without_check"]["by_reason"]["BINARY_NOT_ALLOWED"]["count"] == 1
    assert json.loads(out.read_text()) == summary
    for touch in touches:
        manifest = read_manifest(env["out"] / f"{touch['run_id']}.json")
        assert manifest.slice is Slice.POOL
        assert manifest.sampling["policy_sha256"] == POLICY_SHA
        assert manifest.sampling["source_run_id"].startswith("planner-")
        result = json.loads((env["out"] / f"{touch['run_id']}.nocheck.json").read_text())
        assert result["run_id"] == touch["run_id"]


def test_touch_precedes_the_transcript_read(env, monkeypatch):
    path = _source(env, POOL_A, Slice.POOL, [BINARY])
    read = no_check.read_transcript

    def checked_read(transcript_path):
        assert [t["problem_id"] for t in _nocheck_touches(env)] == [POOL_A]
        return read(transcript_path)

    monkeypatch.setattr(no_check, "read_transcript", checked_read)
    _run(env, [path])


def test_development_runs_log_no_touch(env):
    before = env["log"].read_bytes()
    summary, _ = _run(env, [_source(env, DEV, Slice.DEVELOPMENT, [BINARY])])
    assert env["log"].read_bytes() == before
    assert (summary["slice"], summary["logged"]) == ("development", False)


def _never(*args, **kwargs):
    raise AssertionError("start_run was called before the inputs were checked")


def _second_pool(env, **changes):
    return _source(env, POOL_B, Slice.POOL, [BINARY], **changes)


@pytest.mark.parametrize(
    ("make", "match"),
    [
        (lambda env: [_source(env, POOL_A, Slice.POOL, [BINARY])] * 2, "one run per problem"),
        (lambda env: [_source(env, DEV, Slice.DEVELOPMENT, [BINARY])], "one slice"),
        (lambda env: [_second_pool(env, model_version="2.0")], "one model"),
        (lambda env: [_second_pool(env, k=3)], "one model"),
        (
            lambda env: [_second_pool(env, sampling={"temperature": 0.0, "seeds": [0]})],
            "one model",
        ),
        (lambda env: [_second_pool(env, cluster={"nodes": 2})], "no namespace"),
        (lambda env: [_second_pool(env, cluster={"namespace": "default"})], "not a sandbox"),
        (lambda env: [_second_pool(env, sampling={"temperature": 0.7})], "no seeds list"),
    ],
)
def test_bad_inputs_are_refused_before_any_touch(env, make, match):
    before = env["log"].read_bytes()
    paths = [_source(env, POOL_A, Slice.POOL, [BINARY]), *make(env)]
    with pytest.raises(ValueError, match=match):
        _run(env, paths, start=_never)
    assert env["log"].read_bytes() == before


def test_missing_transcript_is_refused_before_any_touch(env):
    path = _source(env, POOL_A, Slice.POOL, [BINARY])
    path.with_suffix(".transcript.json").unlink()
    with pytest.raises(ValueError, match="no transcript"):
        _run(env, [path], start=_never)


@pytest.mark.parametrize("field", ["seeds", "draws"])
def test_a_transcript_that_disagrees_keeps_its_touch_and_writes_no_summary(env, field):
    record = _transcript([BINARY], namespace=SN)
    if field == "draws":
        record["draws"] = 2
    path = _source(env, POOL_A, Slice.POOL, transcript=record)
    if field == "seeds":
        manifest = json.loads(path.read_text())
        manifest["sampling"]["seeds"] = [5]
        path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=field):
        _run(env, [path])
    assert [t["problem_id"] for t in _nocheck_touches(env)] == [POOL_A]
    assert not env["out"].exists() or not list(env["out"].glob("nocheck-summary-*"))


def test_a_pool_run_without_the_seal_is_refused(env):
    (env["repo"] / "runs" / "confirmation_seal.json").unlink()
    _git(env["repo"], "commit", "-q", "-am", "unseal")
    with pytest.raises(RunRefused, match="not sealed"):
        _run(env, [_source(env, POOL_A, Slice.POOL, [BINARY])])
    assert _nocheck_touches(env) == []


def test_a_run_that_sampled_nothing_is_listed_and_not_touched(env, monkeypatch):
    a = _source(env, POOL_A, Slice.POOL, [BINARY])
    # as run_planner_dev records a plan with no diagnosis: no seeds, no candidates
    empty = {"candidates": [], "draws": 0, "escalated": True}
    b = _source(env, POOL_B, Slice.POOL, transcript=empty)
    b.with_suffix(".transcript.json").unlink()
    read = no_check.read_transcript

    def only_a(path):
        assert POOL_B not in path.name
        return read(path)

    monkeypatch.setattr(no_check, "read_transcript", only_a)
    summary, _ = _run(env, [a, b])
    assert [t["problem_id"] for t in _nocheck_touches(env)] == [POOL_A]
    assert summary["no_candidates_run_ids"] == [f"planner-{POOL_B}-20261102t100000z"]
    assert summary["source_run_ids"] == [f"planner-{POOL_A}-20261102t100000z"]
    assert (summary["problems"], summary["without_check"]["candidates"]) == (1, 1)


def test_only_runs_that_sampled_nothing_give_an_empty_summary(env):
    empty = {"candidates": [], "draws": 0, "escalated": True}
    summary, out = _run(env, [_source(env, POOL_A, Slice.POOL, transcript=empty)], start=_never)
    assert (summary["problems"], summary["without_check"]["removed"]) == (
        0,
        {"count": 0, "share": None},
    )
    assert out.exists()


def _drop(record, field):
    if field in ("candidates", "draws"):
        del record[field]
    elif field == "reason":
        del record["candidates"][0]["verdict"]["reason"]
    else:
        del record["candidates"][0][field]


@pytest.mark.parametrize(
    "field", ["candidates", "draws", "seed", "draw", "command", "verdict", "rejection", "reason"]
)
def test_a_transcript_missing_a_field_is_refused_by_name(env, field):
    record = _transcript([BINARY], namespace=SN)
    seeds = [c["seed"] for c in record["candidates"]]
    _drop(record, field)
    path = _source(env, POOL_A, Slice.POOL, transcript={"candidates": [{"seed": 0}], "draws": 1})
    path.with_suffix(".transcript.json").write_text(json.dumps(record))
    manifest = json.loads(path.read_text())
    manifest["sampling"]["seeds"] = seeds
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match=f"no {field} field|without admitted and reason"):
        _run(env, [path])
    assert [t["problem_id"] for t in _nocheck_touches(env)] == [POOL_A]


def test_two_runs_in_one_second_write_two_summaries(env):
    # the per-fold runs of #165 step 3 started within one second, and the second summary clashed
    ok = f"kubectl -n {SN} scale deployment web --replicas=1"
    a = _source(env, POOL_A, Slice.POOL, [ok])
    b = _source(env, POOL_B, Slice.POOL, [ok])
    outs = []
    for i, path in enumerate([a, b]):
        clock = count()
        _, out = ablate_runs(
            [path],
            registry=env["registry"],
            policy=POLICY,
            policy_sha256=POLICY_SHA,
            touch_log=env["log"],
            repo=env["repo"],
            aiopslab_commit=PIN,
            out_dir=env["out"],
            now=lambda i=i, c=clock: NOW + timedelta(microseconds=1000 * i + next(c)),
            start=partial(start_run, checksum=_sum),
        )
        outs.append(out)
    assert outs[0] != outs[1] and all(o.exists() for o in outs)
