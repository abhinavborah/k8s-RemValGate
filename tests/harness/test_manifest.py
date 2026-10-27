import dataclasses
import json
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    RunRefused,
    RunStart,
    Slice,
    git_state,
    load_registry,
    read_manifest,
    read_touches,
    require_touch,
    start_run,
    write_manifest,
)

POOL, CONF, DEV, SINGLE = "p-synth-pool", "p-synth-conf", "p-synth-dev", "p-synth-single"
PIN = "b" * 40
NOW = datetime(2026, 10, 27, 10, 15, tzinfo=UTC)
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
        cluster={"kind": "kind", "nodes": 1},
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


def _repo(tmp_path):
    """A git repository with one commit holding a file and an empty touch log."""
    repo = tmp_path / "repo"
    (repo / "runs").mkdir(parents=True)
    (repo / "code.py").write_text("x = 1\n")
    (repo / "runs" / "touch_log.jsonl").write_text("")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _start(tmp_path, problem_id, run_id="run-0001", aiopslab_commit=PIN, repo=None):
    repo = repo or _repo(tmp_path)
    log = repo / "runs" / "touch_log.jsonl"
    start = start_run(run_id, problem_id, _registry(tmp_path), log, repo, aiopslab_commit, NOW)
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
    )


# registry


def test_default_registry_counts():
    # the loader already refuses an id in two slices, so loading checks disjointness
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    counts = Counter(registry.slices.values())
    assert counts == {Slice.POOL: 9, Slice.SINGLETON: 5}



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


@pytest.mark.parametrize("problem_id, slice_", [(POOL, "pool"), (CONF, "confirmation")])
def test_logged_start_appends_dated_entry(tmp_path, problem_id, slice_):
    start, log, _ = _start(tmp_path, problem_id)
    assert read_touches(log) == [
        {
            "problem_id": problem_id,
            "run_id": "run-0001",
            "slice": slice_,
            "timestamp": "2026-10-27T10:15:00Z",
        }
    ]
    assert start.timestamp == "2026-10-27T10:15:00Z"
    assert start.slice == Slice(slice_)


@pytest.mark.parametrize("problem_id", [DEV, SINGLE])
def test_unlogged_start_writes_no_entry(tmp_path, problem_id):
    _, log, _ = _start(tmp_path, problem_id)
    assert read_touches(log) == []


def test_touch_line_format(tmp_path):
    _, log, _ = _start(tmp_path, POOL)
    raw = log.read_bytes()
    assert raw == (
        b'{"problem_id": "p-synth-pool", "run_id": "run-0001", "slice": "pool", '
        b'"timestamp": "2026-10-27T10:15:00Z"}\n'
    )


def test_unknown_problem_refused(tmp_path):
    with pytest.raises(RunRefused, match="not in the slice registry"):
        _start(tmp_path, "p-synth-unknown")


def test_unpinned_aiopslab_refused_for_logged_slice(tmp_path):
    repo = _repo(tmp_path)
    with pytest.raises(RunRefused, match="AIOpsLab commit"):
        _start(tmp_path, POOL, aiopslab_commit=None, repo=repo)
    assert read_touches(repo / "runs" / "touch_log.jsonl") == []


@pytest.mark.parametrize("problem_id", [POOL, CONF, DEV])
@pytest.mark.parametrize("pin", ["main", "B" * 40, "b" * 39])
def test_malformed_aiopslab_pin_refused_before_touch(tmp_path, problem_id, pin):
    repo = _repo(tmp_path)
    with pytest.raises(RunRefused, match="not a pinned commit"):
        _start(tmp_path, problem_id, aiopslab_commit=pin, repo=repo)
    assert read_touches(repo / "runs" / "touch_log.jsonl") == []


def test_unpinned_aiopslab_allowed_for_development(tmp_path):
    start, _, _ = _start(tmp_path, DEV, aiopslab_commit=None)
    assert start.slice is Slice.DEVELOPMENT


def test_dirty_tree_refused_for_logged_slice(tmp_path):
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    with pytest.raises(RunRefused, match="uncommitted"):
        _start(tmp_path, CONF, repo=repo)
    assert read_touches(repo / "runs" / "touch_log.jsonl") == []


def test_dirty_tree_recorded_for_development(tmp_path):
    repo = _repo(tmp_path)
    (repo / "code.py").write_text("x = 2\n")
    start, _, _ = _start(tmp_path, DEV, repo=repo)
    assert start.dirty is True


def test_touch_log_append_does_not_make_tree_dirty(tmp_path):
    start, log, repo = _start(tmp_path, POOL)
    second = start_run("run-0002", POOL, _registry(tmp_path), log, repo, PIN, NOW)
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
        start_run("run-0001", CONF, _registry(tmp_path), log, repo, PIN, NOW)
    assert len(read_touches(log)) == 1


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
    log.write_bytes(content.encode())
    with pytest.raises(RunRefused, match=match):
        _start(tmp_path, POOL, repo=repo)
    assert log.read_bytes() == content.encode()


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
@given(st.lists(st.sampled_from([POOL, CONF, DEV]), min_size=1, max_size=8))
def test_appends_never_change_earlier_lines(problem_ids):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        repo = _repo(tmp)
        log = repo / "runs" / "touch_log.jsonl"
        registry = load_registry(_write_registry(tmp, _registry_data()))
        snapshots = []
        for i, problem_id in enumerate(problem_ids):
            start_run(f"run-{i}", problem_id, registry, log, repo, PIN, NOW)
            snapshots.append(log.read_bytes())
        logged = [p for p in problem_ids if p != DEV]
        assert len(read_touches(log)) == len(logged)
        assert len(log.read_bytes().splitlines()) == len(logged)
        for earlier, later in zip(snapshots, snapshots[1:], strict=False):
            assert later.startswith(earlier)
