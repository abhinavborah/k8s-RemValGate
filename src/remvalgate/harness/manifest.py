"""Run manifest per rollout, and the evaluation-set touch log.

A run calls start_run before it starts. For a primary-pool or confirmation-slice problem it
appends a dated entry to the touch log, and it refuses the run if the entry cannot be written,
the AIOpsLab commit is not pinned, the work tree has changes git does not ignore, or the run id
is already in the log. write_manifest checks the entry again and compares the manifest with what
start_run recorded, so a run without an entry gets no manifest.

The slice registry holds no problem ids until the pool and confirmation slice are defined.
"""

import dataclasses
import json
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

DEFAULT_REGISTRY_PATH = Path(__file__).with_name("slices.json")

_HEX40 = re.compile(r"[0-9a-f]{40}")
# lowercase, so two ids never name one file on a case-insensitive filesystem
_RUN_ID = re.compile(r"[a-z0-9][a-z0-9._-]*")
# Windows device names, which cannot be file names even with an extension
_DEVICES = {"con", "prn", "aux", "nul"} | {f"{d}{i}" for d in ("com", "lpt") for i in range(1, 10)}
_TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
_TOUCH_KEYS = {"problem_id", "run_id", "slice", "timestamp"}
# BRD terms: R is 1 unless baseline outcomes vary, at most 3
_MAX_R = 3


class Slice(StrEnum):
    POOL = "pool"
    CONFIRMATION = "confirmation"
    DEVELOPMENT = "development"
    SINGLETON = "singleton"


# uses of these slices are evaluation-set touches (BRD 7.3)
LOGGED = frozenset({Slice.POOL, Slice.CONFIRMATION})


class RunRefused(Exception):
    pass


@dataclass(frozen=True)
class Registry:
    # problem id to its slice
    slices: Mapping[str, Slice]
    # the note that the ids are not defined yet
    placeholder: str | None


def load_registry(path: str | Path) -> Registry:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("registry: expected a JSON object")
    keys = {s.value for s in Slice}
    missing = keys - data.keys()
    extra = data.keys() - keys - {"placeholder"}
    if missing or extra:
        raise ValueError(f"registry keys: missing {sorted(missing)}, unexpected {sorted(extra)}")
    placeholder = data.get("placeholder")
    if placeholder is not None and not isinstance(placeholder, str):
        raise ValueError("registry placeholder: expected a note")
    slices: dict[str, Slice] = {}
    for slice_ in Slice:
        ids = data[slice_.value]
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError(f"registry {slice_.value}: expected a list of strings")
        for problem_id in ids:
            if problem_id in slices:
                raise ValueError(f"registry: {problem_id!r} is in more than one slice")
            slices[problem_id] = slice_
    return Registry(slices=MappingProxyType(slices), placeholder=placeholder)


def _check_run_id(run_id: object, where: str) -> None:
    # the run id names the manifest file
    if (
        not isinstance(run_id, str)
        or not _RUN_ID.fullmatch(run_id)
        or run_id.split(".")[0] in _DEVICES
    ):
        raise ValueError(
            f"{where} run_id: expected lowercase letters, digits, dot, dash or underscore, "
            "starting with a letter or digit, and not a device name"
        )


def _int(name: str, value: object, low: int, high: int | None = None) -> None:
    if type(value) is not int or value < low or (high is not None and value > high):
        bound = f"from {low} to {high}" if high is not None else f"at least {low}"
        raise ValueError(f"manifest {name}: expected an integer {bound}")


def _matches(value: object, fmt: str, length: int) -> bool:
    # the length check rejects unpadded fields, which strptime accepts
    try:
        return (
            isinstance(value, str) and len(value) == length and bool(datetime.strptime(value, fmt))
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class Manifest:
    run_id: str
    problem_id: str
    slice: Slice
    # commit of this repository when the run started, and whether tracked files had changes
    commit: str
    dirty: bool
    model_name: str
    model_version: str
    model_date: str
    seed: int
    k: int
    r: int
    # None until the AIOpsLab commit is pinned; refused for logged slices
    aiopslab_commit: str | None
    cluster: dict
    # when the run started, UTC
    timestamp: str

    def __post_init__(self):
        _check_run_id(self.run_id, "manifest")
        for name in ("problem_id", "model_name", "model_version"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name):
                raise ValueError(f"manifest {name}: expected a non-empty string")
        if type(self.slice) is not Slice:
            raise ValueError("manifest slice: expected a Slice")
        if not isinstance(self.commit, str) or not _HEX40.fullmatch(self.commit):
            raise ValueError("manifest commit: expected 40 lowercase hex characters")
        if type(self.dirty) is not bool:
            raise ValueError("manifest dirty: expected true or false")
        if not _matches(self.model_date, "%Y-%m-%d", 10):
            raise ValueError("manifest model_date: expected YYYY-MM-DD")
        _int("seed", self.seed, 0)
        _int("k", self.k, 1)
        _int("r", self.r, 1, _MAX_R)
        pin = self.aiopslab_commit
        if pin is not None and (not isinstance(pin, str) or not _HEX40.fullmatch(pin)):
            raise ValueError("manifest aiopslab_commit: expected 40 hex characters or null")
        try:
            # the round trip rejects NaN, tuples and other values JSON does not keep
            exact = json.loads(json.dumps(self.cluster, allow_nan=False)) == self.cluster
        except (TypeError, ValueError):
            exact = False
        if not isinstance(self.cluster, dict) or not exact:
            raise ValueError("manifest cluster: expected a JSON object")
        if not _matches(self.timestamp, _TIMESTAMP, 20):
            raise ValueError("manifest timestamp: expected UTC as YYYY-MM-DDTHH:MM:SSZ")


def _git(cwd: str | Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout


def git_state(repo: str | Path, log_path: str | Path | None = None) -> tuple[str, bool]:
    """Return the HEAD commit and whether the work tree has changes git does not ignore.

    A touch log inside the work tree may differ from HEAD only by lines appended after its
    committed content. Any other change to it counts as a change.
    """
    top = Path(_git(repo, "rev-parse", "--show-toplevel").strip())
    pathspec: list[str] = []
    log_changed = False
    if log_path is not None:
        log_path = Path(log_path)
        try:
            relative = log_path.resolve().relative_to(top.resolve()).as_posix()
        except ValueError:
            relative = None
        if relative:
            pathspec = ["--", ".", f":(exclude){relative}"]
            # empty when the log is not committed yet
            committed = subprocess.run(
                ["git", "show", f"HEAD:{relative}"], cwd=top, capture_output=True
            ).stdout
            current = log_path.read_bytes() if log_path.exists() else b""
            log_changed = not current.startswith(committed)
    status = _git(top, "status", "--porcelain", *pathspec)
    return _git(top, "rev-parse", "HEAD").strip(), bool(status.strip()) or log_changed


def read_touches(log_path: str | Path) -> list[dict]:
    path = Path(log_path)
    if not path.exists():
        return []
    entries = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        try:
            entry = json.loads(line)
        except ValueError:
            entry = None
        if not isinstance(entry, dict) or entry.keys() != _TOUCH_KEYS:
            raise ValueError(
                f"touch log line {number}: expected an entry with {sorted(_TOUCH_KEYS)}"
            )
        entries.append(entry)
    return entries


def _slice_of(problem_id: str, registry: Registry) -> Slice:
    if problem_id not in registry.slices:
        raise RunRefused(f"{problem_id!r} is not in the slice registry")
    return registry.slices[problem_id]


def require_touch(run_id: str, problem_id: str, registry: Registry, log_path: str | Path) -> None:
    """Refuse a pool or confirmation run that has no touch log entry."""
    slice_ = _slice_of(problem_id, registry)
    if slice_ in LOGGED and not any(
        e["run_id"] == run_id and e["problem_id"] == problem_id for e in read_touches(log_path)
    ):
        raise RunRefused(f"no touch log entry for run {run_id!r} on {problem_id!r}")


@dataclass(frozen=True)
class RunStart:
    slice: Slice
    commit: str
    dirty: bool
    aiopslab_commit: str | None
    timestamp: str


def _append_touch(log_path: Path, entry: dict) -> None:
    try:
        touches = read_touches(log_path)
    except ValueError as error:
        raise RunRefused(f"the touch log is damaged, repair it first: {error}") from error
    if log_path.exists() and log_path.read_bytes()[-1:] not in (b"", b"\n"):
        raise RunRefused("the touch log does not end in a newline, repair it first")
    if any(e["run_id"] == entry["run_id"] for e in touches):
        raise RunRefused(f"run id {entry['run_id']!r} is already in the touch log")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # limit: one write call per line in append mode, with no coordination between processes
    # or machines; add a file lock if parallel rollouts share one log file
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write(json.dumps(entry, sort_keys=True) + "\n")


def start_run(
    run_id: str,
    problem_id: str,
    registry: Registry,
    log_path: str | Path,
    repo: str | Path,
    aiopslab_commit: str | None,
    now: datetime | None = None,
) -> RunStart:
    """The entry point every run calls before it starts. Raises RunRefused to stop it."""
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("start_run now: expected an aware datetime, converted to UTC")
    _check_run_id(run_id, "start_run")
    slice_ = _slice_of(problem_id, registry)
    log_path = Path(log_path)
    try:
        commit, dirty = git_state(repo, log_path)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RunRefused(f"cannot read the git state of {repo}: {error}") from error
    if slice_ in LOGGED and aiopslab_commit is None:
        raise RunRefused(f"a {slice_} run needs a pinned AIOpsLab commit")
    if slice_ in LOGGED and dirty:
        raise RunRefused(f"a {slice_} run needs a tree with no uncommitted changes")
    timestamp = now.astimezone(UTC).strftime(_TIMESTAMP)
    if slice_ in LOGGED:
        entry = {
            "problem_id": problem_id,
            "run_id": run_id,
            "slice": slice_,
            "timestamp": timestamp,
        }
        _append_touch(log_path, entry)
        # read back, so a write that did not land refuses the run
        require_touch(run_id, problem_id, registry, log_path)
    return RunStart(
        slice=slice_,
        commit=commit,
        dirty=dirty,
        aiopslab_commit=aiopslab_commit,
        timestamp=timestamp,
    )


def write_manifest(
    manifest: Manifest,
    start: RunStart,
    out_dir: str | Path,
    registry: Registry,
    log_path: str | Path,
) -> Path:
    """Write <run_id>.json under out_dir, refusing an unlogged run or one that disagrees with
    what start_run recorded."""
    if _slice_of(manifest.problem_id, registry) is not manifest.slice:
        raise RunRefused(f"manifest slice {manifest.slice} disagrees with the registry")
    recorded = (start.slice, start.commit, start.dirty, start.aiopslab_commit, start.timestamp)
    if (
        manifest.slice,
        manifest.commit,
        manifest.dirty,
        manifest.aiopslab_commit,
        manifest.timestamp,
    ) != recorded:
        raise RunRefused("the manifest disagrees with what start_run recorded")
    require_touch(manifest.run_id, manifest.problem_id, registry, log_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{manifest.run_id}.json"
    text = json.dumps(dataclasses.asdict(manifest), sort_keys=True, indent=2, allow_nan=False)
    # mode x: a manifest is never overwritten
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(text + "\n")
    return path


def read_manifest(path: str | Path) -> Manifest:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("manifest: expected a JSON object")
    fields = {f.name for f in dataclasses.fields(Manifest)}
    missing = fields - data.keys()
    extra = data.keys() - fields
    if missing or extra:
        raise ValueError(f"manifest keys: missing {sorted(missing)}, unexpected {sorted(extra)}")
    return Manifest(**{**data, "slice": Slice(data["slice"])})
