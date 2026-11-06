import dataclasses
import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from remvalgate import overlay
from remvalgate.harness.manifest import (
    ASTRONOMY_SHOP,
    DEFAULT_REGISTRY_PATH,
    HOTEL_RESERVATION,
    SOCIAL_NETWORK,
    Manifest,
    RunRefused,
    RunStart,
    Slice,
    check_seal,
    git_state,
    load_registry,
    log_authoring,
    log_creation,
    log_fixture,
    logged_namespace,
    read_manifest,
    read_touches,
    require_touch,
    seal_confirmation,
    start_run,
    write_manifest,
)

POOL, CONF, DEV, SINGLE = "p-synth-pool", "p-synth-conf", "p-synth-dev", "p-synth-single"
PIN = "b" * 40
NOW = datetime(2026, 10, 27, 10, 15, tzinfo=UTC)
SEAL = "confirmation_seal.json"


def _sum(problem_id, aiopslab_commit):
    """A stand-in for the confirmation checksum, so no test builds a confirmation problem."""
    return hashlib.sha256(f"{problem_id}@{aiopslab_commit}".encode()).hexdigest()


FIELDS = {
    "run_id",
    "problem_id",
    "slice",
    "commit",
    "dirty",
    "model_name",
    "model_version",
    "model_date",
    "seed",
    "k",
    "r",
    "aiopslab_commit",
    "cluster",
    "timestamp",
    "sampling",
}


def _registry_data():
    return {"pool": [POOL], "confirmation": [CONF], "development": [DEV], "singleton": [SINGLE]}


def _write_registry(tmp_path, data):
    path = tmp_path / "slices.json"
    path.write_text(json.dumps(data))
    return path


def _registry(tmp_path):
    return load_registry(_write_registry(tmp_path, _registry_data()))


def _manifest(**changes):
    values = dict(
        run_id="run-0001",
        problem_id=POOL,
        slice=Slice.POOL,
        commit="a" * 40,
        dirty=False,
        model_name="synthetic-planner",
        model_version="1.0",
        model_date="2026-10-01",
        seed=7,
        k=8,
        r=1,
        aiopslab_commit=PIN,
        cluster={"kind": "kind", "nodes": 1, "namespace": SOCIAL_NETWORK},
        timestamp="2026-10-27T10:15:00Z",
    )
    values.update(changes)
    return Manifest(**values)


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        check=True,
        capture_output=True,
    )


SEALED = "2026-10-27T09:00:00Z"
SEAL_TOUCH = {
    "problem_id": CONF,
    "run_id": "seal-p-synth-conf-20261027t090000z",
    "slice": "confirmation",
    "timestamp": SEALED,
}


def _runs(log):
    """The touches after the fixture's seal touch."""
    return [e for e in read_touches(log) if not e["run_id"].startswith("seal-")]


def _repo(tmp_path):
    """A git repository with one commit holding a file, a sealed confirmation slice and a touch
    log holding only its seal touch."""
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    (repo / "code.py").write_text("x = 1\n")
    line = json.dumps(SEAL_TOUCH, sort_keys=True) + "\n"
    (repo / "runs" / "touch_log.jsonl").write_bytes(line.encode())
    seal = {"aiopslab": PIN, "checksums": {CONF: _sum(CONF, PIN)}, "commit": "c" * 40}
    (repo / "runs" / SEAL).write_text(json.dumps({**seal, "sealed": SEALED}))
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _start(
    tmp_path,
    problem_id,
    run_id="run-0001",
    aiopslab_commit=PIN,
    repo=None,
    namespace=SOCIAL_NETWORK,
):
    repo = repo or _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    start = start_run(
        run_id,
        problem_id,
        _registry(tmp_path),
        log,
        repo,
        aiopslab_commit,
        NOW,
        checksum=_sum,
        namespace=namespace,
    )
    return start, log, repo


def _for(start, **changes):
    """A manifest that agrees with what start_run recorded."""
    recorded = dict(
        slice=start.slice,
        commit=start.commit,
        dirty=start.dirty,
        aiopslab_commit=start.aiopslab_commit,
        timestamp=start.timestamp,
    )
    return _manifest(**{**recorded, **changes})


def _recorded(manifest):
    return RunStart(
        slice=manifest.slice,
        commit=manifest.commit,
        dirty=manifest.dirty,
        aiopslab_commit=manifest.aiopslab_commit,
        timestamp=manifest.timestamp,
        namespace=manifest.cluster.get("namespace"),
    )


# registry


def test_default_registry_counts():
    # the loader already refuses an id in two slices, so loading checks disjointness
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    counts = Counter(registry.slices.values())
    assert counts == {
        Slice.POOL: 16,
        Slice.CONFIRMATION: 3,
        Slice.DEVELOPMENT: 3,
        Slice.SINGLETON: 5,
    }
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    assert sum(i.removeprefix("pool-").startswith("k8s_target_port") for i in pool) == 6
    assert sum(i.removeprefix("pool-").startswith("scale_pod_zero") for i in pool) == 3
    assert sum(i.removeprefix("pool-").startswith("assign_to_non_existent_node") for i in pool) == 3


def test_development_ids_cannot_clash_with_upstream():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    for problem_id, slice_ in registry.slices.items():
        assert problem_id.startswith("dev-") == (slice_ is Slice.DEVELOPMENT)


def test_registry_maps_problems_to_slices(tmp_path):
    registry = _registry(tmp_path)
    assert registry.slices[POOL] is Slice.POOL
    assert registry.slices[CONF] is Slice.CONFIRMATION
    assert registry.slices[DEV] is Slice.DEVELOPMENT
    assert registry.slices[SINGLE] is Slice.SINGLETON


def test_registry_overlap_rejected(tmp_path):
    data = _registry_data()
    data["development"].append(POOL)
    with pytest.raises(ValueError, match="more than one slice"):
        load_registry(_write_registry(tmp_path, data))


@pytest.mark.parametrize(
    "data",
    [
        {"pool": [], "confirmation": [], "development": []},
        {**_registry_data(), "extra": []},
        {**_registry_data(), "pool": "p-synth-pool"},
        {**_registry_data(), "pool": [1]},
        {**_registry_data(), "placeholder": 1},
    ],
)
def test_registry_bad_keys_rejected(tmp_path, data):
    with pytest.raises(ValueError, match="registry"):
        load_registry(_write_registry(tmp_path, data))


# scenario: manifest written for each rollout


def test_manifest_holds_required_fields(tmp_path):
    start, log, _ = _start(tmp_path, POOL)
    path = write_manifest(_for(start), start, tmp_path / "out", _registry(tmp_path), log)
    data = json.loads(path.read_text())
    assert data.keys() == FIELDS
    assert path.name == "run-0001.json"


def test_manifest_json_sorted_keys(tmp_path):
    start, log, _ = _start(tmp_path, POOL)
    path = write_manifest(_for(start), start, tmp_path / "out", _registry(tmp_path), log)
    keys = list(json.loads(path.read_text()))
    assert keys == sorted(keys)


@pytest.mark.parametrize(
    "changes",
    [
        {"k": 0},
        {"k": True},
        {"k": 8.0},
        {"r": 0},
        {"r": 4},
        {"seed": -1},
        {"seed": False},
        {"model_date": "27-10-2026"},
        {"model_date": "2026-02-30"},
        {"model_date": "2026-W43-2"},
        {"timestamp": "2026-1-27T10:15:00Z"},
        {"commit": "abc"},
        {"commit": "A" * 40},
        {"aiopslab_commit": "unpinned"},
        {"timestamp": "2026-10-27T10:15:00"},
        {"timestamp": "2026-10-27T10:15:00+05:30"},
        {"model_name": ""},
        {"model_version": ""},
        {"problem_id": ""},
        {"run_id": "../escape"},
        {"run_id": "Run-0001"},
        {"run_id": "con"},
        {"run_id": "nul.x"},
        {"run_id": ".hidden"},
        {"slice": "pool"},
        {"dirty": 0},
        {"cluster": []},
        {"cluster": {"cpu": float("nan")}},
        {"cluster": {"nodes": (1, 2)}},
        {"sampling": None},
        {"sampling": {"temperature": float("inf")}},
    ],
)
def test_invalid_manifest_raises(changes):
    with pytest.raises(ValueError, match="manifest"):
        _manifest(**changes)


def test_write_refuses_overwrite(tmp_path):
    start, log, _ = _start(tmp_path, POOL)
    write_manifest(_for(start), start, tmp_path / "out", _registry(tmp_path), log)
    with pytest.raises(FileExistsError):
        write_manifest(_for(start, seed=8), start, tmp_path / "out", _registry(tmp_path), log)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_read_rejects_missing_or_extra_keys(tmp_path, change):
    data = json.loads(json.dumps(dataclasses.asdict(_manifest())))
    if change == "missing":
        del data["seed"]
    else:
        data["notes"] = "x"
    path = tmp_path / "m.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="manifest keys"):
        read_manifest(path)


def test_sampling_round_trips_and_an_older_manifest_without_it_still_reads(tmp_path):
    sampled = _manifest(sampling={"temperature": 0.7, "seeds": [0, 1]})
    path = tmp_path / "m.json"
    path.write_text(json.dumps(dataclasses.asdict(sampled)))
    assert read_manifest(path) == sampled
    data = dataclasses.asdict(_manifest())
    del data["sampling"]
    path.write_text(json.dumps(data))
    assert read_manifest(path).sampling == {}


def test_read_rejects_unknown_slice(tmp_path):
    data = json.loads(json.dumps(dataclasses.asdict(_manifest())))
    data["slice"] = "blind"
    path = tmp_path / "m.json"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        read_manifest(path)


def test_git_state_reads_head_and_dirty(tmp_path):
    repo = _repo(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert git_state(repo) == (head, False)
    (repo / "code.py").write_text("x = 2\n")
    assert git_state(repo) == (head, True)


def test_git_state_counts_untracked_files(tmp_path):
    repo = _repo(tmp_path)
    (repo / "new_module.py").write_text("not added")
    assert git_state(repo)[1] is True


def test_git_state_checks_whole_work_tree_from_a_subfolder(tmp_path):
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    assert git_state(repo / "runs")[1] is True


def test_git_state_counts_rewritten_touch_log(tmp_path):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    # bytes, so Windows text mode does not turn the newlines into CRLF
    log.write_bytes(b'{"a": 1}\n')
    _git(repo, "commit", "-q", "-am", "one touch")
    log.write_bytes(b"")
    assert git_state(repo, log)[1] is True
    log.write_bytes(b'{"a": 1}\n{"a": 2}\n')
    assert git_state(repo, log)[1] is False


json_value = st.recursive(
    st.none() | st.booleans() | st.integers() | st.text(),
    lambda inner: st.lists(inner, max_size=3) | st.dictionaries(st.text(), inner, max_size=3),
    max_leaves=8,
)
hex40 = st.text("0123456789abcdef", min_size=40, max_size=40)
manifests = st.builds(
    Manifest,
    run_id=st.from_regex(r"run-[a-z0-9._-]{0,16}", fullmatch=True),
    problem_id=st.text(min_size=1),
    slice=st.just(Slice.DEVELOPMENT),
    commit=hex40,
    dirty=st.booleans(),
    model_name=st.text(min_size=1),
    model_version=st.text(min_size=1),
    model_date=st.dates().map(lambda d: d.isoformat()).filter(lambda s: len(s) == 10),
    seed=st.integers(min_value=0),
    k=st.integers(min_value=1),
    r=st.integers(min_value=1, max_value=3),
    aiopslab_commit=st.none() | hex40,
    cluster=st.dictionaries(st.text(), json_value, max_size=4),
    timestamp=st.datetimes(min_value=datetime(2000, 1, 1)).map(
        lambda d: d.strftime("%Y-%m-%dT%H:%M:%SZ")
    ),
)


@given(manifests)
def test_manifest_round_trip(manifest):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        data = {
            "pool": [],
            "confirmation": [],
            "development": [manifest.problem_id],
            "singleton": [],
        }
        registry = load_registry(_write_registry(tmp, data))
        out, log = tmp / "out", tmp / "touch_log.jsonl"
        path = write_manifest(manifest, _recorded(manifest), out, registry, log)
        assert read_manifest(path) == manifest


# scenario: touch is logged


def test_logged_start_appends_dated_entry(tmp_path):
    start, log, _ = _start(tmp_path, POOL)
    assert _runs(log) == [
        {
            "problem_id": POOL,
            "run_id": "run-0001",
            "slice": "pool",
            "timestamp": "2026-10-27T10:15:00Z",
        }
    ]
    assert start.timestamp == "2026-10-27T10:15:00Z"
    assert start.slice is Slice.POOL


def test_unlogged_start_writes_no_entry(tmp_path):
    _, log, _ = _start(tmp_path, DEV)
    assert _runs(log) == []


def test_singleton_start_is_logged(tmp_path):
    start, log, _ = _start(tmp_path, SINGLE)
    assert start.slice is Slice.SINGLETON
    assert [(e["problem_id"], e["slice"]) for e in _runs(log)] == [(SINGLE, "singleton")]


def test_singleton_run_without_a_pin_refused(tmp_path):
    with pytest.raises(RunRefused, match="pinned"):
        _start(tmp_path, SINGLE, aiopslab_commit=None)


def test_touch_line_format(tmp_path):
    _, log, _ = _start(tmp_path, POOL)
    raw = log.read_bytes().split(b"\n", 1)[1]
    assert raw == (
        b'{"problem_id": "p-synth-pool", "run_id": "run-0001", "slice": "pool", '
        b'"timestamp": "2026-10-27T10:15:00Z"}\n'
    )


def test_unknown_problem_refused(tmp_path):
    with pytest.raises(RunRefused, match="not in the slice registry"):
        _start(tmp_path, "p-synth-unknown")


def test_pool_run_refused_without_confirmation_slice(tmp_path):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    registry = load_registry(_write_registry(tmp_path, {**_registry_data(), "confirmation": []}))
    with pytest.raises(RunRefused, match="gate G1"):
        start_run("run-0001", POOL, registry, log, repo, PIN, NOW)
    assert _runs(log) == []


def test_unpinned_aiopslab_refused_for_logged_slice(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RunRefused, match="AIOpsLab commit"):
        _start(tmp_path, POOL, aiopslab_commit=None, repo=repo)
    assert _runs(repo / "runs" / "touch_log.jsonl") == []


@pytest.mark.parametrize("problem_id", [POOL, DEV])
@pytest.mark.parametrize("pin", ["main", "B" * 40, "b" * 39])
def test_malformed_aiopslab_pin_refused_before_touch(tmp_path, problem_id, pin):
    repo = _repo(tmp_path)
    with pytest.raises(RunRefused, match="not a pinned commit"):
        _start(tmp_path, problem_id, aiopslab_commit=pin, repo=repo)
    assert _runs(repo / "runs" / "touch_log.jsonl") == []


def test_unpinned_aiopslab_allowed_for_development(tmp_path):
    start, _, _ = _start(tmp_path, DEV, aiopslab_commit=None)
    assert start.slice is Slice.DEVELOPMENT


@pytest.mark.parametrize("problem_id", [POOL, SINGLE])
def test_dirty_tree_refused_for_logged_slice(tmp_path, problem_id):
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    with pytest.raises(RunRefused, match="uncommitted"):
        _start(tmp_path, problem_id, repo=repo)
    assert _runs(repo / "runs" / "touch_log.jsonl") == []


def test_dirty_tree_recorded_for_development(tmp_path):
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    start, _, _ = _start(tmp_path, DEV, repo=repo)
    assert start.dirty is True


def test_touch_log_append_does_not_make_tree_dirty(tmp_path):
    start, log, repo = _start(tmp_path, POOL)
    second = start_run(
        "run-0002",
        POOL,
        _registry(tmp_path),
        log,
        repo,
        PIN,
        NOW,
        checksum=_sum,
        namespace=SOCIAL_NETWORK,
    )
    assert (start.dirty, second.dirty) == (False, False)
    assert second.commit == start.commit


def test_naive_now_rejected(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="UTC"):
        start_run(
            "run-0001",
            DEV,
            _registry(tmp_path),
            repo / "runs" / "touch_log.jsonl",
            repo,
            PIN,
            datetime(2026, 10, 27),
        )


def test_require_touch_refuses_without_entry(tmp_path):
    with pytest.raises(RunRefused, match="no touch log entry"):
        require_touch("run-0001", POOL, _registry(tmp_path), tmp_path / "touch_log.jsonl")


def test_require_touch_passes_after_start(tmp_path):
    _, log, _ = _start(tmp_path, POOL)
    require_touch("run-0001", POOL, _registry(tmp_path), log)


def test_require_touch_ignores_development(tmp_path):
    require_touch("run-0001", DEV, _registry(tmp_path), tmp_path / "touch_log.jsonl")


def test_log_creation_logs_development(tmp_path):
    log = tmp_path / "runs" / "touch_log.jsonl"
    entry = log_creation(DEV, _registry(tmp_path), log, NOW)
    assert read_touches(log) == [
        {
            "problem_id": DEV,
            "run_id": "create-p-synth-dev-20261027t101500z",
            "slice": "development",
            "timestamp": "2026-10-27T10:15:00Z",
        }
    ]
    assert entry["slice"] is Slice.DEVELOPMENT


def test_log_creation_accepts_every_overlay_id(tmp_path):
    # the validation script logs a creation touch for each overlay problem: the development
    # problems, the pool variant and the confirmation problems
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    log = tmp_path / "touch_log.jsonl"
    ids = list(overlay.PROBLEMS)
    for problem_id in ids:
        log_creation(problem_id, registry, log, NOW)
    assert [e["problem_id"] for e in read_touches(log)] == ids


def test_log_creation_refuses_a_repeat_in_the_same_second(tmp_path):
    log = tmp_path / "touch_log.jsonl"
    log_creation(POOL, _registry(tmp_path), log, NOW)
    with pytest.raises(RunRefused, match="already in the touch log"):
        log_creation(POOL, _registry(tmp_path), log, NOW)
    assert len(read_touches(log)) == 1


def test_log_creation_refuses_a_second_creation_of_a_confirmation_problem(tmp_path):
    # created once: a second creation would touch the slice again
    log = tmp_path / "touch_log.jsonl"
    log_creation(CONF, _registry(tmp_path), log, NOW)
    before = log.read_bytes()
    with pytest.raises(RunRefused, match="already created"):
        log_creation(CONF, _registry(tmp_path), log, NOW + timedelta(seconds=1))
    assert log.read_bytes() == before


@pytest.mark.parametrize("problem_id", [POOL, DEV])
def test_log_creation_allows_a_second_creation_outside_the_confirmation_slice(tmp_path, problem_id):
    log = tmp_path / "touch_log.jsonl"
    log_creation(problem_id, _registry(tmp_path), log, NOW)
    log_creation(problem_id, _registry(tmp_path), log, NOW + timedelta(seconds=1))
    assert len(read_touches(log)) == 2


def test_log_creation_refuses_an_unregistered_id_and_a_naive_time(tmp_path):
    log = tmp_path / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="not in the slice registry"):
        log_creation("p-unknown", _registry(tmp_path), log, NOW)
    with pytest.raises(ValueError, match="aware"):
        log_creation(DEV, _registry(tmp_path), log, datetime(2026, 10, 27))
    assert not log.exists()


def test_start_run_refuses_a_creation_run_id(tmp_path):
    with pytest.raises(RunRefused, match="creation touches"):
        _start(tmp_path, DEV, run_id="create-run-0001")


def test_start_run_refuses_an_authoring_run_id(tmp_path):
    with pytest.raises(RunRefused, match="authoring touches"):
        _start(tmp_path, DEV, run_id="corpus-run-0001")


def test_log_authoring_appends_a_corpus_touch(tmp_path):
    log = tmp_path / "touch_log.jsonl"
    entry = log_authoring(POOL, _registry(tmp_path), log, NOW)
    assert entry["run_id"] == f"corpus-{POOL}-20261027t101500z"
    assert read_touches(log) == [entry]
    with pytest.raises(RunRefused):
        log_authoring(POOL, _registry(tmp_path), log, NOW)


@pytest.mark.parametrize(("problem_id", "slice_"), [(CONF, "confirmation"), (SINGLE, "singleton")])
def test_log_authoring_refuses_a_confirmation_or_singleton_problem(tmp_path, problem_id, slice_):
    log = tmp_path / "touch_log.jsonl"
    with pytest.raises(RunRefused, match=f"{slice_} problem"):
        log_authoring(problem_id, _registry(tmp_path), log, NOW)
    assert not log.exists()


def test_start_run_refuses_a_fixture_run_id(tmp_path):
    with pytest.raises(RunRefused, match="fixture touches"):
        _start(tmp_path, DEV, run_id="fixture-run-0001")


def test_log_fixture_appends_a_fixture_touch(tmp_path):
    log = tmp_path / "touch_log.jsonl"
    entry = log_fixture(POOL, _registry(tmp_path), log, NOW)
    assert entry["run_id"] == f"fixture-{POOL}-20261027t101500z"
    assert read_touches(log) == [entry]
    with pytest.raises(RunRefused):
        log_fixture(POOL, _registry(tmp_path), log, NOW)


def test_log_fixture_refuses_a_confirmation_problem(tmp_path):
    log = tmp_path / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="confirmation problem"):
        log_fixture(CONF, _registry(tmp_path), log, NOW)
    assert not log.exists()


def test_manifest_refused_without_touch(tmp_path):
    manifest = _manifest()
    log = tmp_path / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="no touch log entry"):
        write_manifest(manifest, _recorded(manifest), tmp_path / "out", _registry(tmp_path), log)
    assert not (tmp_path / "out" / "run-0001.json").exists()


def test_manifest_slice_mismatch_refused(tmp_path):
    start, log, _ = _start(tmp_path, POOL)
    manifest = _for(start, slice=Slice.DEVELOPMENT)
    with pytest.raises(RunRefused, match="registry"):
        write_manifest(manifest, start, tmp_path / "out", _registry(tmp_path), log)


@pytest.mark.parametrize(
    "changes",
    [
        {"dirty": True},
        {"aiopslab_commit": None},
        {"commit": "c" * 40},
        {"timestamp": "2026-10-27T11:00:00Z"},
    ],
)
def test_manifest_refused_when_it_disagrees_with_run_start(tmp_path, changes):
    start, log, _ = _start(tmp_path, POOL)
    with pytest.raises(RunRefused, match="start_run recorded"):
        write_manifest(_for(start, **changes), start, tmp_path / "out", _registry(tmp_path), log)


def test_reused_run_id_refused(tmp_path):
    _, log, repo = _start(tmp_path, POOL)
    with pytest.raises(RunRefused, match="already in the touch log"):
        _start(tmp_path, POOL, repo=repo)
    assert len(_runs(log)) == 1


def test_invalid_run_id_rejected_at_start(tmp_path):
    with pytest.raises(ValueError, match="run_id"):
        _start(tmp_path, POOL, run_id="CON")


def test_git_failure_refused(tmp_path):
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    log = not_a_repo / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="git state"):
        start_run("run-0001", POOL, _registry(tmp_path), log, not_a_repo, PIN, NOW)


@pytest.mark.parametrize(
    "content, match",
    [
        ('{"problem_id": "a", "run_id": "b", "slice": "pool", "timestamp": "t"}', "newline"),
        ("not json\n", "damaged"),
    ],
)
def test_damaged_touch_log_refused_before_append(tmp_path, content, match):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    # appended after the committed lines, so the tree stays clean
    damaged = log.read_bytes() + content.encode()
    log.write_bytes(damaged)
    with pytest.raises(RunRefused, match=match):
        _start(tmp_path, POOL, repo=repo)
    # log_creation reads the log for a confirmation id first, and refuses it the same way
    with pytest.raises(RunRefused, match=match):
        log_creation(CONF, _registry(tmp_path), log, NOW)
    assert log.read_bytes() == damaged


def test_missing_touch_log_reads_empty(tmp_path):
    assert read_touches(tmp_path / "none.jsonl") == []


def test_malformed_line_reports_line_number(tmp_path):
    log = tmp_path / "touch_log.jsonl"
    log.write_text(
        '{"problem_id": "a", "run_id": "b", "slice": "pool", "timestamp": "t"}\nnot json\n'
    )
    with pytest.raises(ValueError, match="line 2"):
        read_touches(log)


# each example builds a git repository, so keep the count small and the deadline off
@settings(max_examples=20, deadline=None)
@given(st.lists(st.sampled_from([POOL, DEV]), min_size=1, max_size=8))
def test_appends_never_change_earlier_lines(problem_ids):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = _repo(tmp)
        log = repo / "runs" / "touch_log.jsonl"
        registry = load_registry(_write_registry(tmp, _registry_data()))
        snapshots = []
        for i, problem_id in enumerate(problem_ids):
            start_run(
                f"run-{i}",
                problem_id,
                registry,
                log,
                repo,
                PIN,
                NOW,
                checksum=_sum,
                namespace=SOCIAL_NETWORK,
            )
            snapshots.append(log.read_bytes())
        logged = [p for p in problem_ids if p != DEV]
        assert len(_runs(log)) == len(logged)
        assert len(log.read_bytes().splitlines()) == len(logged) + 1
        for earlier, later in zip(snapshots, snapshots[1:], strict=False):
            assert later.startswith(earlier)


# scenario: gate G1, the confirmation slice is sealed before any pool run


def _unsealed(tmp_path, data=None):
    """A clean repository with no seal record, a registry and the touch log path."""
    repo = _repo(tmp_path)
    _git(repo, "rm", "-q", f"runs/{SEAL}")
    (repo / "runs" / "touch_log.jsonl").write_text("")
    _git(repo, "add", "runs")
    _git(repo, "commit", "-q", "-m", "unseal")
    registry = load_registry(_write_registry(tmp_path, data or _registry_data()))
    return repo, registry, repo / "runs" / "touch_log.jsonl"


def _create(log, problem_id):
    log_creation(problem_id, load_registry(log.parents[2] / "slices.json"), log, NOW)


@pytest.mark.parametrize("problem_id", [POOL, SINGLE])
def test_pool_run_refused_before_the_seal(tmp_path, problem_id):
    repo, registry, log = _unsealed(tmp_path)
    with pytest.raises(RunRefused, match="not sealed .gate G1"):
        start_run("run-0001", problem_id, registry, log, repo, PIN, NOW, checksum=_sum)
    assert read_touches(log) == []


def test_pool_run_refused_when_a_definition_changed_after_the_seal(tmp_path):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="changed since the seal"):
        start_run(
            "run-0001",
            POOL,
            _registry(tmp_path),
            log,
            repo,
            PIN,
            NOW,
            checksum=lambda i, c: "0" * 64,
        )
    assert _runs(log) == []


def test_pool_run_refused_under_another_aiopslab_pin(tmp_path):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="changed since the seal"):
        start_run("run-0001", POOL, _registry(tmp_path), log, repo, "d" * 40, NOW, checksum=_sum)


def test_pool_run_refused_when_the_seal_and_registry_differ(tmp_path):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    data = {**_registry_data(), "confirmation": [CONF, "p-synth-conf-2"]}
    registry = load_registry(_write_registry(tmp_path, data))
    with pytest.raises(RunRefused, match="does not cover"):
        start_run("run-0001", POOL, registry, log, repo, PIN, NOW, checksum=_sum)


@pytest.mark.parametrize("content", ["{", "[]", '{"checksums": {}}'])
def test_damaged_seal_refused(tmp_path, content):
    repo, registry, log = _unsealed(tmp_path)
    (log.parent / SEAL).write_text(content)
    with pytest.raises(RunRefused, match="seal record"):
        check_seal(registry, log, PIN, _sum)


def test_start_run_refuses_a_seal_run_id(tmp_path):
    with pytest.raises(RunRefused, match="seal touches"):
        _start(tmp_path, DEV, run_id="seal-run-0001")


def test_seal_records_checksums_and_logs_a_seal_touch(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    seal = seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    commit, _ = git_state(repo)
    assert seal == {
        "aiopslab": PIN,
        "checksums": {CONF: _sum(CONF, PIN)},
        "commit": commit,
        "sealed": "2026-10-27T10:15:00Z",
    }
    assert json.loads((log.parent / SEAL).read_text(encoding="utf-8")) == seal
    assert read_touches(log)[-1] == {
        "problem_id": CONF,
        "run_id": "seal-p-synth-conf-20261027t101500z",
        "slice": "confirmation",
        "timestamp": "2026-10-27T10:15:00Z",
    }
    # the sealed slice, once committed, opens gate G1
    _git(repo, "add", "runs")
    _git(repo, "commit", "-q", "-m", "seal")
    start_run(
        "run-0001", POOL, registry, log, repo, PIN, NOW, checksum=_sum, namespace=SOCIAL_NETWORK
    )


def test_seal_refused_twice(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    before = log.read_bytes()
    with pytest.raises(RunRefused, match="already sealed"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    assert log.read_bytes() == before


def test_seal_refused_without_a_creation_touch(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    with pytest.raises(RunRefused, match="no creation touch"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    assert not (log.parent / SEAL).exists()


def test_seal_refused_after_a_run_touched_the_slice(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    # written by hand: start_run refuses a confirmation run before the seal
    entry = {"problem_id": CONF, "run_id": "run-0001", "slice": "confirmation", "timestamp": "t"}
    with log.open("ab") as out:
        out.write((json.dumps(entry, sort_keys=True) + "\n").encode())
    with pytest.raises(RunRefused, match="already run"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    assert not (log.parent / SEAL).exists()


def test_seal_refused_without_confirmation_ids(tmp_path):
    repo, registry, log = _unsealed(tmp_path, {**_registry_data(), "confirmation": []})
    with pytest.raises(RunRefused, match="confirmation slice in the registry"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW)


def test_seal_refused_on_a_dirty_tree(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    (repo / "code.py").write_text("x = 2\n")
    with pytest.raises(RunRefused, match="uncommitted"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW)


def test_seal_refused_on_a_damaged_touch_log_writes_nothing(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    # committed without its final newline, so the tree is clean
    log.write_bytes(log.read_bytes().rstrip(b"\n"))
    _git(repo, "add", "runs")
    _git(repo, "commit", "-q", "-m", "created")
    with pytest.raises(RunRefused, match="newline"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    assert not (log.parent / SEAL).exists()


@pytest.mark.parametrize(
    "touches",
    [[], [{**SEAL_TOUCH, "timestamp": "2026-10-27T09:00:01Z"}], [SEAL_TOUCH, SEAL_TOUCH]],
)
def test_seal_record_without_its_seal_touches_refused(tmp_path, touches):
    repo, registry, log = _unsealed(tmp_path)
    seal = {"aiopslab": PIN, "checksums": {CONF: _sum(CONF, PIN)}, "commit": "c" * 40}
    (log.parent / SEAL).write_text(json.dumps({**seal, "sealed": SEALED}))
    log.write_bytes("".join(json.dumps(t, sort_keys=True) + "\n" for t in touches).encode())
    with pytest.raises(RunRefused, match="does not show the slice as sealed"):
        check_seal(registry, log, PIN, _sum)


def test_confirmation_run_refused_even_when_sealed(tmp_path):
    # opened once, at the end (R9.6): until that path exists, no run touches the slice
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    with pytest.raises(RunRefused, match="opened once"):
        start_run("run-0001", CONF, _registry(tmp_path), log, repo, PIN, NOW, checksum=_sum)
    assert _runs(log) == []


def _sealed(tmp_path):
    """A clean repository whose slice was sealed by seal_confirmation and committed."""
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    seal_confirmation(registry, log, repo, PIN, _sum, NOW)
    _git(repo, "add", "runs")
    _git(repo, "commit", "-q", "-m", "seal")
    return repo, registry, log


def test_reseal_after_a_new_pin_reopens_the_gate(tmp_path):
    repo, registry, log = _sealed(tmp_path)
    new_pin, later = "d" * 40, NOW + timedelta(hours=1)
    with pytest.raises(RunRefused, match="changed since the seal"):
        check_seal(registry, log, new_pin, _sum)
    seal = seal_confirmation(registry, log, repo, new_pin, _sum, later, reseal=True)
    assert seal["aiopslab"] == new_pin
    assert seal["checksums"] == {CONF: _sum(CONF, new_pin)}
    assert [e["run_id"] for e in read_touches(log) if e["run_id"].startswith("seal-")] == [
        "seal-p-synth-conf-20261027t101500z",
        "seal-p-synth-conf-20261027t111500z",
    ]
    check_seal(registry, log, new_pin, _sum)
    with pytest.raises(RunRefused, match="changed since the seal"):
        check_seal(registry, log, PIN, _sum)


def test_reseal_refused_when_nothing_is_sealed(tmp_path):
    repo, registry, log = _unsealed(tmp_path)
    _create(log, CONF)
    with pytest.raises(RunRefused, match="nothing to reseal"):
        seal_confirmation(registry, log, repo, PIN, _sum, NOW, reseal=True)
    assert not (log.parent / SEAL).exists()


def test_reseal_refused_after_a_run_touched_the_slice(tmp_path):
    repo, registry, log = _sealed(tmp_path)
    entry = {"problem_id": CONF, "run_id": "run-0001", "slice": "confirmation", "timestamp": "t"}
    with log.open("ab") as out:
        out.write((json.dumps(entry, sort_keys=True) + "\n").encode())
    before = (log.parent / SEAL).read_bytes()
    with pytest.raises(RunRefused, match="already run"):
        seal_confirmation(
            registry, log, repo, "d" * 40, _sum, NOW + timedelta(hours=1), reseal=True
        )
    assert (log.parent / SEAL).read_bytes() == before


def test_reseal_refused_at_the_sealed_time(tmp_path):
    repo, registry, log = _sealed(tmp_path)
    before = log.read_bytes()
    with pytest.raises(RunRefused, match="sealed time"):
        seal_confirmation(registry, log, repo, "d" * 40, _sum, NOW, reseal=True)
    assert log.read_bytes() == before


# namespace


def test_logged_namespace_maps_the_database_pool_problems_to_hotel_reservation():
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    hotel = {i for i in pool if logged_namespace(i) == HOTEL_RESERVATION}
    assert hotel == {i for i in pool if i.startswith(("revoke_auth_", "user_unregistered_"))}
    assert len(hotel) == 4
    assert {logged_namespace(i) for i in pool} - {HOTEL_RESERVATION} == {SOCIAL_NETWORK}
    confirmation = [i for i, s in registry.slices.items() if s is Slice.CONFIRMATION]
    assert {logged_namespace(i) for i in confirmation} == {SOCIAL_NETWORK}


def test_logged_namespace_maps_each_singleton_to_its_inventory_namespace():
    # docs/problem_inventory.md, rows 8, 9 and 12 to 14
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    singletons = {i for i, s in registry.slices.items() if s is Slice.SINGLETON}
    assert {i: logged_namespace(i) for i in singletons} == {
        "auth_miss_mongodb-mitigation-1": SOCIAL_NETWORK,
        "misconfig_app_hotel_res-mitigation-1": HOTEL_RESERVATION,
        "astronomy_shop_kafka_queue_problems-mitigation-1": ASTRONOMY_SHOP,
        "redeploy_without_PV-mitigation-1": HOTEL_RESERVATION,
        "wrong_bin_usage-mitigation-1": HOTEL_RESERVATION,
    }


HOTEL = "revoke_auth_mongodb-mitigation-1"


@pytest.mark.parametrize(
    ("namespace", "refused"), [(SOCIAL_NETWORK, True), (HOTEL_RESERVATION, False)]
)
def test_a_hotel_reservation_pool_problem_runs_only_in_its_namespace(tmp_path, namespace, refused):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    registry = load_registry(_write_registry(tmp_path, {**_registry_data(), "pool": [POOL, HOTEL]}))

    def start():
        return start_run(
            "run-0001", HOTEL, registry, log, repo, PIN, NOW, checksum=_sum, namespace=namespace
        )

    if refused:
        with pytest.raises(RunRefused, match="runs in 'test-hotel-reservation'"):
            start()
        assert _runs(log) == []
    else:
        assert start().namespace == HOTEL_RESERVATION
        assert [e["problem_id"] for e in _runs(log)] == [HOTEL]


KAFKA = "astronomy_shop_kafka_queue_problems-mitigation-1"


@pytest.mark.parametrize(
    ("namespace", "refused"), [(SOCIAL_NETWORK, True), (ASTRONOMY_SHOP, False)]
)
def test_the_kafka_singleton_runs_only_in_astronomy_shop(tmp_path, namespace, refused):
    repo = _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    registry = load_registry(_write_registry(tmp_path, {**_registry_data(), "singleton": [KAFKA]}))

    def start():
        return start_run(
            "run-0001", KAFKA, registry, log, repo, PIN, NOW, checksum=_sum, namespace=namespace
        )

    if refused:
        with pytest.raises(RunRefused, match="runs in 'astronomy-shop'"):
            start()
        assert _runs(log) == []
    else:
        assert start().namespace == ASTRONOMY_SHOP
        assert [e["problem_id"] for e in _runs(log)] == [KAFKA]


@pytest.mark.parametrize("problem_id", [POOL, SINGLE])
@pytest.mark.parametrize("namespace", [None, HOTEL_RESERVATION])
def test_logged_run_in_another_namespace_refused_before_the_touch(tmp_path, namespace, problem_id):
    repo = _repo(tmp_path)
    with pytest.raises(RunRefused, match="runs in 'test-social-network'"):
        _start(tmp_path, problem_id, repo=repo, namespace=namespace)
    assert _runs(repo / "runs" / "touch_log.jsonl") == []


def test_unlogged_run_needs_no_namespace(tmp_path):
    start, _, _ = _start(tmp_path, DEV, namespace=None)
    assert start.namespace is None


@pytest.mark.parametrize("problem_id", [POOL, SINGLE])
def test_manifest_naming_another_namespace_refused(tmp_path, problem_id):
    start, log, repo = _start(tmp_path, problem_id)
    cluster = {"kind": "kind", "nodes": 1, "namespace": HOTEL_RESERVATION}
    manifest = _for(start, problem_id=problem_id, cluster=cluster)
    with pytest.raises(RunRefused, match="cluster namespace"):
        write_manifest(manifest, start, repo / "m", _registry(tmp_path), log)


def test_logged_manifest_without_a_cluster_namespace_refused(tmp_path):
    start, log, repo = _start(tmp_path, POOL)
    with pytest.raises(RunRefused, match="cluster namespace"):
        write_manifest(
            _for(start, cluster={"kind": "kind", "nodes": 1}),
            start,
            repo / "m",
            _registry(tmp_path),
            log,
        )
