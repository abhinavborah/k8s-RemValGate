"""Tests of the per-fold training data builder on synthetic labels and transcripts only."""

import hashlib
import json
import subprocess
from datetime import UTC, datetime

import pytest

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    RunRefused,
    Slice,
    load_registry,
    logged_namespace,
    read_touches,
)
from remvalgate.harness.oracle import label_problem
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.training_data import (
    FORMAT,
    MASK,
    TOKEN_BUDGET,
    Run,
    build_fold,
    check_ids,
    check_run,
    export,
    length_proxy,
    sampling_messages,
    sha256_json,
)
from remvalgate.overlay.fixes import CannotSend
from remvalgate.planner.chat import Completion
from remvalgate.planner.loop import Diagnosis, Plan, Stop, _task_prompt, sample, task_text
from remvalgate.retrieval.pool_corpus import pool_incidents

REGISTRY = load_registry(DEFAULT_REGISTRY_PATH)
ASSIGNMENT = pool_folds(REGISTRY)
POLICY = load_policy(DEFAULT_POLICY_PATH)
FIXES = {e.entry_id: e.fix_command for e in pool_incidents(REGISTRY)}
SUMMARIES = {e.entry_id: e.summary for e in pool_incidents(REGISTRY)}
FOLD = 0
HELD = sorted(p for p, f in ASSIGNMENT.items() if f == FOLD)
TRAIN = sorted(p for p, f in ASSIGNMENT.items() if f != FOLD)
STAMP = "20261108t100000z"
SETUP = {
    "model_name": "qwen/qwen3.5-4b",
    "model_version": "gguf q8_0, context 16384; thinking=on",
    "k": 8,
    "seed": 0,
    "sampling": {
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "max_tokens": 2048,
        "thinking": True,
    },
}
PIN = "b" * 40
NOW = datetime(2026, 11, 8, 10, 0, tzinfo=UTC)
SEALED = "2026-11-01T09:00:00Z"


def _commands(namespace):
    scale = f"kubectl -n {namespace} scale deployment synth-service"
    return {
        "works": f"{scale} --replicas=1",
        # the same call as works, flag written apart: merged with it
        "same": f"{scale} --replicas 1",
        "fails": f"{scale} --replicas=2",
        "unsendable": f"{scale} --replicas=3",
    }


def _entry_line(entry_id):
    return f"ScalePod on svc: summary of {entry_id}. Fix: kubectl scale deployment svc --replicas=1"


def _synthetic(problem_id, retrieved=None, extra=()):
    """A transcript and a labels record of one problem, as the oracle run writes them."""
    namespace = logged_namespace(problem_id)
    run_id = f"oracle-{problem_id}-{STAMP}"
    commands = _commands(namespace)
    drawn = [
        (0, commands["works"], None),
        (1, commands["same"], None),
        (2, commands["fails"], None),
        (3, None, "the reply has no non-empty command"),
        (4, commands["unsendable"], None),
        *extra,
    ]
    outcomes = {
        commands["works"]: Outcome.RECOVERED,
        commands["fails"]: Outcome.APPLIED_HARMFUL,
    }

    def execute(command):
        if command not in outcomes:
            raise CannotSend("not sendable")
        return outcomes[command]

    for _, command, _ in extra:
        outcomes.setdefault(command, Outcome.APPLIED_INEFFECTIVE)
    labels = label_problem(problem_id, drawn, None, execute, POLICY)
    retrieved = retrieved if retrieved is not None else ["runbook-ScalePod"]
    observation = "NAME READY STATUS\nsynth-service-1 0/1 Running"
    past = "\n".join(f"- {_entry_line(i)}" for i in retrieved)
    user = _task_prompt(
        task_text("Social Network", namespace, 1_794_475_589_000_000), observation, past
    )
    transcript = {
        "candidates": [
            {"seed": s, "command": c, "reply": json.dumps({"command": c}), "draw": 1}
            for s, c, _ in drawn
        ],
        "draws": 1,
        "config": {"baseline": "3c"},
        "context": [
            {"content": "system prompt", "role": "system"},
            {"content": user, "role": "user"},
        ],
        # sorted keys, as the transcript file holds them
        "diagnosis": {"fault_type": "ScalePod", "reason": "zero replicas", "service": "synth"},
        "first_observation": observation,
        "retrieved": [[entry_id, n] for n, entry_id in enumerate(retrieved, start=1)],
    }
    manifest = {
        "run_id": run_id,
        "problem_id": problem_id,
        "cluster": {"namespace": namespace},
        **SETUP,
    }
    record = {"run_id": run_id, **labels.record(), "executions": []}
    return manifest, transcript, record


def _run(problem_id, **kwargs):
    manifest, transcript, record = _synthetic(problem_id, **kwargs)
    return Run(manifest, transcript, record, "0" * 64)


def _build(runs=None, count=length_proxy):
    runs = runs if runs is not None else [_run(p) for p in TRAIN]
    return build_fold(
        runs, FOLD, ASSIGNMENT, REGISTRY, FIXES, POLICY, count, recorded_summaries=SUMMARIES
    )


def test_a_fold_holds_one_merged_record_per_distinct_command_of_its_training_problems():
    gate, baseline5, report = _build()
    assert {r["problem_id"] for r in gate} == set(TRAIN)
    assert not {r["problem_id"] for r in gate} & set(HELD)
    first = [r for r in gate if r["problem_id"] == TRAIN[0]]
    assert [r["seeds"] for r in first] == [[0, 1], [2], [3], [4]]
    assert [r["label"] for r in first] == [1, 0, None, None]
    assert [r["outcome"] for r in first] == ["RECOVERED", "APPLIED_HARMFUL", None, None]
    assert [r["train"] for r in first] == [True, True, False, False]
    assert first[2]["excluded"] == "no outcome: the reply has no non-empty command"
    assert first[3]["excluded"].startswith("no outcome: cannot be sent unchanged")
    assert report["records"] == 4 * len(TRAIN) and report["candidates"] == 5 * len(TRAIN)
    assert report["train"] == 2 * len(TRAIN) and report["train_label_1"] == len(TRAIN)
    assert report["train_by_outcome"] == {"APPLIED_HARMFUL": len(TRAIN), "RECOVERED": len(TRAIN)}
    assert report["no_outcome"] == 2 * len(TRAIN) and report["held_out"] == HELD
    assert report["no_outcome_candidates"] == {"no_command": len(TRAIN), "not_sendable": len(TRAIN)}
    assert [r["id"] for r in baseline5] == [r["id"] for r in gate]


def test_the_gate_input_holds_the_five_fields_in_order_and_no_label():
    gate, _, _ = _build()
    record = gate[0]
    compact = json.loads(record["input"])
    assert list(compact) == ["task", "first_observation", "diagnosis", "retrieved", "candidate"]
    assert list(compact["diagnosis"]) == ["fault_type", "service", "reason"]
    assert compact["task"].startswith("Users report errors")
    assert compact["retrieved"] == [_entry_line("runbook-ScalePod")]
    assert compact["candidate"] == record["candidate"]
    for leak in ("RECOVERED", "APPLIED", record["problem_id"].split("-mitigation")[0]):
        assert leak not in record["input"]


def test_baseline5_prompt_is_the_sampling_messages_and_the_completion_the_reply():
    _, baseline5, _ = _build()
    record = baseline5[0]
    roles = [m["role"] for m in record["messages"]]
    assert roles == ["system", "user", "assistant", "user"]
    assert json.loads(record["messages"][2]["content"]) == {
        "final": {"fault_type": "ScalePod", "service": "synth", "reason": "zero replicas"}
    }
    assert record["completion"] == json.dumps({"command": record["candidate"]})


def test_retrieved_records_of_the_held_out_fold_are_masked_in_both_files():
    held, other = HELD[0], TRAIN[1]
    runs = [_run(p) for p in TRAIN]
    runs[0] = _run(TRAIN[0], retrieved=[other, held, "runbook-ScalePod"])
    gate, baseline5, report = _build(runs)
    compact = json.loads(gate[0]["input"])
    assert compact["retrieved"] == [_entry_line(other), MASK, _entry_line("runbook-ScalePod")]
    prompt = baseline5[0]["messages"][1]["content"]
    assert f"- {MASK}" in prompt and _entry_line(held) not in prompt
    assert _entry_line(other) in prompt
    assert gate[0]["masked_entries"] == 1 and gate[4]["masked_entries"] == 0
    assert report["masked_entries"] == 1
    assert report["records_with_masked_entries"] == 4 and report["train_with_masked_entries"] == 2


def test_a_command_equal_to_a_held_out_recorded_fix_is_not_trained_on():
    # the recorded fix of a held-out problem, written in the training problem's namespace
    held = next(p for p in HELD if "scale_pod" in p or "target_port" in p)
    target = TRAIN[0]
    copy = FIXES[held].replace(f"-n {logged_namespace(held)}", f"-n {logged_namespace(target)}")
    runs = [_run(p) for p in TRAIN]
    rejected = (6, "kubectl -n x delete pod y", "allow-list: delete is not allowed")
    runs[0] = _run(target, extra=[(5, copy, None), rejected])
    gate, _, report = _build(runs)
    assert report["no_outcome_candidates"]["rejected"] == 1
    (record,) = [r for r in gate if r["candidate"] == copy]
    assert record["outcome"] == "APPLIED_INEFFECTIVE" and record["label"] == 0
    assert not record["train"] and record["excluded"].endswith(held)
    assert report["fix_match_excluded"] == 1


def test_a_held_out_missing_or_repeated_problem_is_refused():
    runs = [_run(p) for p in TRAIN]
    with pytest.raises(ValueError, match="held out by fold 0"):
        _build([*runs, _run(HELD[0])])
    with pytest.raises(ValueError, match="no labelled run"):
        _build(runs[1:])
    with pytest.raises(ValueError, match="more than one labelled run"):
        _build([*runs, runs[0]])


@pytest.mark.parametrize("slice_", [Slice.CONFIRMATION, Slice.DEVELOPMENT, Slice.SINGLETON])
def test_check_ids_refuses_every_slice_but_the_pool(slice_):
    synthetic = type(REGISTRY)(slices={**REGISTRY.slices, "p-synth": slice_}, placeholder=None)
    with pytest.raises(ValueError, match=f"a {slice_} problem"):
        check_ids(["p-synth"], FOLD, ASSIGNMENT, synthetic)
    with pytest.raises(ValueError, match="unregistered"):
        check_ids(["p-unknown"], FOLD, ASSIGNMENT, REGISTRY)


def test_check_run_refuses_labels_that_are_not_the_transcripts_draw():
    manifest, transcript, record = _synthetic(TRAIN[0])
    transcript["candidates"][2]["command"] = "kubectl -n x get pods"
    with pytest.raises(ValueError, match="not the transcript's"):
        check_run(Run(manifest, transcript, record, ""))
    manifest, transcript, record = _synthetic(TRAIN[0])
    transcript["first_observation"] += " changed"
    with pytest.raises(ValueError, match="first observation"):
        _build([Run(manifest, transcript, record, ""), *(_run(p) for p in TRAIN[1:])])
    manifest, transcript, record = _synthetic(TRAIN[0])
    record["c"] = 0
    with pytest.raises(ValueError, match="counts"):
        check_run(Run(manifest, transcript, record, ""))


def test_lengths_are_reported_and_rows_over_the_budget_listed():
    gate, _, report = _build(count=lambda text: TOKEN_BUDGET + 1)
    assert report["gate_length"]["max"] == TOKEN_BUDGET + 1
    assert report["gate_length"]["over_budget"] == [r["id"] for r in gate]
    gate, _, report = _build()
    assert report["gate_length"]["over_budget"] == []
    assert all(r["length"] == length_proxy(r["input"]) for r in gate)


def test_length_proxy_counts_each_digit_and_a_token_per_three_other_characters():
    assert length_proxy("") == 0
    assert length_proxy("1234") == 4
    assert length_proxy("abcd") == 2
    assert length_proxy("pod-7 0/1") == 2 + 3


# export, end to end on files


def _sum(problem_id, aiopslab_commit):
    return hashlib.sha256(f"{problem_id}@{aiopslab_commit}".encode()).hexdigest()


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _repo(tmp_path):
    """A committed repository whose touch log shows the confirmation slice as sealed."""
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    conf = sorted(p for p, s in REGISTRY.slices.items() if s is Slice.CONFIRMATION)
    touches = [
        {"problem_id": p, "run_id": f"seal-{p}-20261101t090000z", "slice": "confirmation"}
        | {"timestamp": SEALED}
        for p in conf
    ]
    log = "".join(json.dumps(t, sort_keys=True) + "\n" for t in touches)
    (repo / "runs" / "touch_log.jsonl").write_text(log)
    seal = {"aiopslab": PIN, "checksums": {p: _sum(p, PIN) for p in conf}, "commit": "c" * 40}
    (repo / "runs" / "confirmation_seal.json").write_text(json.dumps({**seal, "sealed": SEALED}))
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _files(tmp_path, problems):
    folder = tmp_path / "oracle"
    folder.mkdir()
    paths = []
    for pid in problems:
        manifest, transcript, record = _synthetic(pid)
        run_id = manifest["run_id"]
        (folder / f"{run_id}.json").write_text(json.dumps(manifest))
        (folder / f"{run_id}.transcript.json").write_text(json.dumps(transcript, sort_keys=True))
        (folder / f"{run_id}.labels.json").write_text(json.dumps(record, sort_keys=True))
        paths.append(folder / f"{run_id}.labels.json")
    return paths


def _export(tmp_path, paths, repo):
    return export(
        paths,
        tmp_path / "out",
        registry=REGISTRY,
        log_path=repo / "runs" / "touch_log.jsonl",
        repo=repo,
        aiopslab_commit=PIN,
        recorded_fixes=FIXES,
        recorded_summaries=SUMMARIES,
        policy=POLICY,
        now=NOW,
        checksum=_sum,
    )


def test_export_logs_a_touch_per_fold_and_training_problem_and_writes_eight_files(tmp_path):
    repo = _repo(tmp_path)
    paths = _files(tmp_path, sorted(ASSIGNMENT))
    written = _export(tmp_path, paths, repo)
    assert len(written) == 8
    touches = [
        t for t in read_touches(repo / "runs" / "touch_log.jsonl") if t["run_id"][:5] != "seal-"
    ]
    assert len(touches) == 4 * len(TRAIN)
    assert touches[0]["run_id"] == f"train-export-0-{TRAIN[0]}-{STAMP}"
    assert all(t["slice"] == "pool" for t in touches)
    gate = json.loads((tmp_path / "out" / f"training-gate-fold0-{STAMP}.json").read_text())
    assert gate["format"] == FORMAT and gate["kind"] == "gate" and gate["fold"] == 0
    assert gate["fold_assignment_sha256"] == sha256_json(ASSIGNMENT)
    assert sorted(gate["labels_files"]) == TRAIN
    path = next(p for p in paths if TRAIN[0] in p.name)
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    assert gate["labels_files"][TRAIN[0]]["sha256"] == expected
    assert gate["labels_files"][TRAIN[0]]["manifest"]["run_id"] == path.name.split(".")[0]
    transcript = path.with_name(path.name.replace(".labels.", ".transcript."))
    expected = hashlib.sha256(transcript.read_bytes()).hexdigest()
    assert gate["labels_files"][TRAIN[0]]["transcript_sha256"] == expected
    assert len(gate["touches"]) == len(TRAIN) and not gate["dirty"]
    assert {r["problem_id"] for r in gate["records"]} == set(TRAIN)
    baseline5 = json.loads(
        (tmp_path / "out" / f"training-baseline5-fold3-{STAMP}.json").read_text()
    )
    assert baseline5["kind"] == "baseline5" and baseline5["records"][0]["messages"]


def test_export_refuses_a_non_pool_or_incomplete_set_before_any_touch(tmp_path):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    before = log.read_text()
    paths = _files(tmp_path, sorted(ASSIGNMENT))
    dev = next(p for p, s in REGISTRY.slices.items() if s is Slice.DEVELOPMENT)
    stray = paths[0].with_name(f"oracle-{dev}-{STAMP}.labels.json")
    with pytest.raises(ValueError, match="never trained on"):
        _export(tmp_path, [*paths, stray], repo)
    with pytest.raises(ValueError, match="no labels file"):
        _export(tmp_path, paths[1:], repo)
    with pytest.raises(ValueError, match="more than one labels file"):
        _export(tmp_path, [*paths, paths[0]], repo)
    assert log.read_text() == before


def test_export_refuses_a_dirty_tree(tmp_path):
    repo = _repo(tmp_path)
    (repo / "stray.py").write_text("x = 1\n")
    with pytest.raises(RunRefused, match="uncommitted"):
        _export(tmp_path, _files(tmp_path, sorted(ASSIGNMENT)), repo)


def test_rebuilt_sampling_messages_equal_what_the_draws_of_sample_saw():
    # a synthetic plan and its transcript, stored with sorted keys as the transcript file is;
    # the rebuilt messages must equal what planner.loop.sample sent, its sample prompt included
    namespace = "test-social-network"
    user = _task_prompt(
        task_text("Social Network", namespace, 1_794_475_589_000_000), "pods", "- runbook line"
    )
    context = ({"role": "system", "content": "system prompt"}, {"role": "user", "content": user})
    fields = {"fault_type": "ScalePod", "service": "synth", "reason": "zero replicas"}
    diagnosis = Diagnosis(**fields)
    result = Plan(diagnosis, None, None, Stop.FINAL, (), (), "system prompt", context)
    seen = []

    class Chat:
        def complete(self, messages, *, seed=None, logprobs=False):
            seen.append(messages)
            return Completion(json.dumps({"command": f"kubectl -n {namespace} get pods"}), 1, 0.0)

    sample(result, namespace, chat=Chat(), policy=POLICY, k=2, seed=0)
    stored = json.loads(json.dumps({"context": context, "diagnosis": fields}, sort_keys=True))
    rebuilt = sampling_messages(stored["context"], stored["diagnosis"], namespace)
    assert rebuilt == seen[0] == seen[1]


@pytest.mark.parametrize(
    "change, field",
    [
        ({"model_version": "gguf q4_k_m, context 16384; thinking=on"}, "model_version"),
        ({"k": 4}, "k"),
        ({"seed": 1000}, "seed"),
        ({"sampling": {**SETUP["sampling"], "thinking": False}}, "sampling.thinking"),
    ],
)
def test_labelling_runs_of_another_setup_are_not_pooled(change, field):
    runs = [_run(p) for p in TRAIN]
    other = runs[-1]
    runs[-1] = Run(
        {**other.manifest, **change}, other.transcript, other.labels, other.labels_sha256
    )
    with pytest.raises(ValueError, match=f"{field} is .* the labelling runs must share one setup"):
        _build(runs)


def test_held_out_text_the_masking_cannot_reach_is_counted():
    held = sorted(p for p, f in ASSIGNMENT.items() if f == FOLD)[0]
    # a planner reason that quotes a held-out fix in another namespace, after reading it
    fix = FIXES[held].replace(logged_namespace(held), "test-social-network")
    runs = [_run(p) for p in TRAIN]
    first = runs[0]
    transcript = {**first.transcript, "diagnosis": {**first.transcript["diagnosis"], "reason": fix}}
    runs[0] = Run(first.manifest, transcript, first.labels, first.labels_sha256)
    report = _build(runs)[2]
    trained = sum(r["train"] for r in _build(runs)[0] if r["problem_id"] == first.problem_id)
    assert trained and report["held_out_text"] == {
        "gate_train": trained,
        "baseline5_train": trained,
    }
    assert _build()[2]["held_out_text"] == {"gate_train": 0, "baseline5_train": 0}
