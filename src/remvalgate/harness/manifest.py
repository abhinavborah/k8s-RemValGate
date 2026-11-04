"""Run manifest per rollout, and the evaluation-set touch log.

A run calls start_run before it starts. For a primary-pool or confirmation-slice problem it
appends a dated entry to the touch log, and it refuses the run if the entry cannot be written,
the AIOpsLab commit is not pinned, the work tree has changes git does not ignore, or the run id
is already in the log. write_manifest checks the entry again and compares the manifest with what
start_run recorded, so a run without an entry gets no manifest.

Every confirmation-slice run is refused: the slice is opened once, at the end (R9.6), by a run
path not added yet. A pool run, and that path when it exists, is refused until the confirmation
slice is sealed (gate G1):
seal_confirmation writes the seal record beside the touch log and a seal touch per id, and
start_run checks that both cover the registry's confirmation ids and that each checksum still
matches.

A logged run also names the namespace it runs in, and start_run refuses it before the touch
unless that is the problem's own: the 4 RevokeAuth and UserUnregistered pool problems run on Hotel
Reservation, the rest of the pool on Social Network (docs/problem_inventory.md). write_manifest
then refuses a manifest whose cluster names another namespace. This catches a run script that names
the wrong namespace, not one that names the right one and then acts in another.

log_creation appends a creation touch for a problem validated with a known fix, in any slice.
log_authoring appends an authoring touch for a problem whose definition and known fix were read
to write its retrieval corpus record.
"""

import dataclasses
import json
import re
import subprocess
from collections.abc import Callable, Mapping
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
# the seal record of the confirmation slice, beside the touch log (BRD 7.1, R3.3)
SEAL_NAME = "confirmation_seal.json"
_SEAL_KEYS = {"aiopslab", "checksums", "commit", "sealed"}
_HEX64 = re.compile(r"[0-9a-f]{64}")
# (problem id, AIOpsLab commit) -> sha256 of the problem's definition
Checksum = Callable[[str, str], str]
# BRD terms: R is 1 unless baseline outcomes vary, at most 3
_MAX_R = 3


class Slice(StrEnum):
    POOL = "pool"
    CONFIRMATION = "confirmation"
    DEVELOPMENT = "development"
    SINGLETON = "singleton"


# uses of these slices are evaluation-set touches (BRD 7.3)
LOGGED = frozenset({Slice.POOL, Slice.CONFIRMATION})

SOCIAL_NETWORK = "test-social-network"
HOTEL_RESERVATION = "test-hotel-reservation"
# the pool problems on Hotel Reservation (docs/problem_inventory.md, rows 4 to 7)
_HOTEL_POOL = frozenset(
    {
        "revoke_auth_mongodb-mitigation-1",
        "revoke_auth_mongodb-mitigation-2",
        "user_unregistered_mongodb-mitigation-1",
        "user_unregistered_mongodb-mitigation-2",
    }
)


def logged_namespace(problem_id: str) -> str:
    """The namespace a pool or confirmation problem runs in."""
    return HOTEL_RESERVATION if problem_id in _HOTEL_POOL else SOCIAL_NETWORK


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
    # the planner's sampling settings (temperature, seeds and so on); empty for a run with no
    # sampling step, and absent from manifests written before it existed
    sampling: dict = dataclasses.field(default_factory=dict)

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
            raise ValueError(
                "manifest aiopslab_commit: expected 40 lowercase hex characters or null"
            )
        for name in ("cluster", "sampling"):
            value = getattr(self, name)
            try:
                # the round trip rejects NaN, tuples and other values JSON does not keep
                exact = json.loads(json.dumps(value, allow_nan=False)) == value
            except (TypeError, ValueError):
                exact = False
            if not isinstance(value, dict) or not exact:
                raise ValueError(f"manifest {name}: expected a JSON object")
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
    # the namespace a logged run named; None for an unlogged run that named none
    namespace: str | None = None


def _sound_touches(log_path: Path) -> list[dict]:
    """The touch log's entries, refusing a log that cannot take another line."""
    try:
        touches = read_touches(log_path)
    except ValueError as error:
        raise RunRefused(f"the touch log is damaged, repair it first: {error}") from error
    if log_path.exists() and log_path.read_bytes()[-1:] not in (b"", b"\n"):
        raise RunRefused("the touch log does not end in a newline, repair it first")
    return touches


def _append_touch(log_path: Path, entry: dict) -> None:
    touches = _sound_touches(log_path)
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
    checksum: Checksum | None = None,
    namespace: str | None = None,
) -> RunStart:
    """The entry point every run calls before it starts. Raises RunRefused to stop it.

    checksum defaults to the confirmation slice's own (overlay/confirmation.py). namespace is the
    one the run acts in, required for a logged run and checked against logged_namespace.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("start_run now: expected an aware datetime, converted to UTC")
    _check_run_id(run_id, "start_run")
    if run_id.startswith("create-"):
        raise RunRefused("run ids starting with create- are kept for creation touches")
    if run_id.startswith("seal-"):
        raise RunRefused("run ids starting with seal- are kept for seal touches")
    if run_id.startswith("corpus-"):
        raise RunRefused("run ids starting with corpus- are kept for authoring touches")
    slice_ = _slice_of(problem_id, registry)
    # the slice is opened once, at the end (R9.6); no run path opens it yet
    if slice_ is Slice.CONFIRMATION:
        raise RunRefused("the confirmation slice is opened once, at the end, by its own run path")
    log_path = Path(log_path)
    try:
        commit, dirty = git_state(repo, log_path)
    except (OSError, subprocess.CalledProcessError) as error:
        raise RunRefused(f"cannot read the git state of {repo}: {error}") from error
    # checked before any touch entry, so a value Manifest would reject never takes a run id
    if aiopslab_commit is not None and (
        not isinstance(aiopslab_commit, str) or not _HEX40.fullmatch(aiopslab_commit)
    ):
        raise RunRefused(
            f"AIOpsLab commit {aiopslab_commit!r} is not a pinned commit: "
            "expected 40 lowercase hex characters"
        )
    if slice_ in LOGGED and aiopslab_commit is None:
        raise RunRefused(f"a {slice_} run needs a pinned AIOpsLab commit")
    if slice_ in LOGGED and dirty:
        raise RunRefused(f"a {slice_} run needs a tree with no uncommitted changes")
    if slice_ in LOGGED:
        check_seal(registry, log_path, aiopslab_commit, checksum)
    if slice_ in LOGGED and namespace != logged_namespace(problem_id):
        raise RunRefused(
            f"{problem_id!r} runs in {logged_namespace(problem_id)!r}, "
            f"but the run names {namespace!r}"
        )
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
        namespace=namespace,
    )


def log_creation(
    problem_id: str, registry: Registry, log_path: str | Path, now: datetime | None = None
) -> dict:
    """Append a creation touch: a problem was created and validated with a known fix, which is
    not a system run. Any slice is logged, development included (R3.3, R3.8).

    The run id is create-<problem id>-<UTC time>, so a creation entry is told apart from a run
    entry by its prefix and a repeated validation gets a new id. A confirmation problem is created
    once: a second creation is refused.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("log_creation now: expected an aware datetime, converted to UTC")
    slice_ = _slice_of(problem_id, registry)
    now = now.astimezone(UTC)
    run_id = f"create-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}"
    _check_run_id(run_id, "log_creation")
    if slice_ is Slice.CONFIRMATION and any(
        e["problem_id"] == problem_id and e["run_id"].startswith("create-")
        for e in _sound_touches(Path(log_path))
    ):
        raise RunRefused(f"{problem_id!r} is a confirmation problem already created")
    entry = {
        "problem_id": problem_id,
        "run_id": run_id,
        "slice": slice_,
        "timestamp": now.strftime(_TIMESTAMP),
    }
    _append_touch(Path(log_path), entry)
    return entry


def log_authoring(
    problem_id: str, registry: Registry, log_path: str | Path, now: datetime | None = None
) -> dict:
    """Append an authoring touch: the problem's definition and known fix were read to write its
    retrieval corpus record (E4-S8), which is not a run. A confirmation problem is refused.

    The run id is corpus-<problem id>-<UTC time>, so the entry is told apart from a run entry by
    its prefix.
    """
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("log_authoring now: expected an aware datetime, converted to UTC")
    slice_ = _slice_of(problem_id, registry)
    # reading a confirmation problem to write a record is the leak the seal guards against
    if slice_ is Slice.CONFIRMATION:
        raise RunRefused(f"{problem_id!r} is a confirmation problem: no corpus record is written")
    now = now.astimezone(UTC)
    run_id = f"corpus-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}"
    _check_run_id(run_id, "log_authoring")
    entry = {
        "problem_id": problem_id,
        "run_id": run_id,
        "slice": slice_,
        "timestamp": now.strftime(_TIMESTAMP),
    }
    _append_touch(Path(log_path), entry)
    return entry


def _confirmation_ids(registry: Registry) -> list[str]:
    ids = [i for i, s in registry.slices.items() if s is Slice.CONFIRMATION]
    if not ids:
        raise RunRefused(
            "a pool or confirmation run needs the confirmation slice in the registry (gate G1)"
        )
    return ids


def _default_checksum() -> Checksum:
    # imported here: the overlay registers problems, which the rest of the harness never needs
    from remvalgate.overlay.confirmation import checksum

    return checksum


def check_seal(
    registry: Registry,
    log_path: str | Path,
    aiopslab_commit: str | None,
    checksum: Checksum | None = None,
) -> None:
    """Gate G1 (BRD 7.1, R3.3): refuse unless the confirmation slice is sealed and unchanged.

    The seal must cover exactly the registry's confirmation ids, the touch log must hold one
    seal touch per id at the sealed time, and each checksum recomputed under this AIOpsLab
    commit must equal the sealed one, so an edit to a definition, or a new pin, after the seal
    closes the gate again.
    """
    ids = _confirmation_ids(registry)
    path = Path(log_path).parent / SEAL_NAME
    if not path.exists():
        raise RunRefused("the confirmation slice is not sealed (gate G1)")
    try:
        seal = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        seal = None
    if (
        not isinstance(seal, dict)
        or seal.keys() != _SEAL_KEYS
        or not isinstance(seal["checksums"], dict)
    ):
        raise RunRefused(f"the seal record {path} is damaged (gate G1)")
    if seal["checksums"].keys() != set(ids):
        raise RunRefused("the seal does not cover the registry's confirmation ids (gate G1)")
    # the record alone is not enough: seal_confirmation writes both, after its own checks
    sealed = [
        e["problem_id"]
        for e in _sound_touches(Path(log_path))
        if e["run_id"].startswith("seal-") and e["timestamp"] == seal["sealed"]
    ]
    if sorted(sealed) != sorted(ids):
        raise RunRefused("the touch log does not show the slice as sealed (gate G1)")
    checksum = checksum or _default_checksum()
    changed = [i for i in ids if checksum(i, aiopslab_commit) != seal["checksums"][i]]
    if changed:
        raise RunRefused(f"changed since the seal (gate G1): {', '.join(changed)}")


def seal_confirmation(
    registry: Registry,
    log_path: str | Path,
    repo: str | Path,
    aiopslab_commit: str,
    checksum: Checksum,
    now: datetime | None = None,
    reseal: bool = False,
) -> dict:
    """Seal the confirmation slice once (gate G1): write the seal record and a seal touch per id.

    Refused if the slice is already sealed, the tree has uncommitted changes, or an id has no
    creation touch or has already run. Nothing is written when it is refused.

    reseal replaces the record of a sealed slice, after a group decision such as a new AIOpsLab
    pin. Its earlier seal touches stay in the log and no longer match the record's time; any
    other run touch still refuses it.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    ids = _confirmation_ids(registry)
    log_path = Path(log_path)
    path = log_path.parent / SEAL_NAME
    # checked before anything is written, so a refusal leaves no half seal
    touches = _sound_touches(log_path)
    sealed = path.exists() or any(e["run_id"].startswith("seal-") for e in touches)
    if sealed and not reseal:
        raise RunRefused("the confirmation slice is already sealed")
    if reseal and not path.exists():
        raise RunRefused("the confirmation slice is not sealed, so there is nothing to reseal")
    if reseal and any(e["timestamp"] == now.strftime(_TIMESTAMP) for e in touches):
        raise RunRefused("a reseal needs a time after the sealed time")
    commit, dirty = git_state(repo, log_path)
    if dirty:
        raise RunRefused("sealing needs a tree with no uncommitted changes")
    for problem_id in ids:
        runs = [e["run_id"] for e in touches if e["problem_id"] == problem_id]
        if not any(r.startswith("create-") for r in runs):
            raise RunRefused(f"{problem_id!r} has no creation touch")
        if any(not r.startswith(("create-", "seal-")) for r in runs):
            raise RunRefused(f"{problem_id!r} has already run, so it cannot be sealed")
    sums = {i: checksum(i, aiopslab_commit) for i in ids}
    if not all(isinstance(v, str) and _HEX64.fullmatch(v) for v in sums.values()):
        raise ValueError("seal checksums: expected 64 lowercase hex characters")
    seal = {
        "aiopslab": aiopslab_commit,
        "checksums": sums,
        "commit": commit,
        "sealed": now.strftime(_TIMESTAMP),
    }
    # "x": only an explicit reseal replaces a record; the earlier one stays in git history
    with path.open("w" if reseal else "x", encoding="utf-8", newline="\n") as out:
        out.write(json.dumps(seal, indent=2, sort_keys=True) + "\n")
    for problem_id in ids:
        entry = {
            "problem_id": problem_id,
            "run_id": f"seal-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}",
            "slice": Slice.CONFIRMATION,
            "timestamp": seal["sealed"],
        }
        _append_touch(log_path, entry)
    return seal


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
    expected = logged_namespace(manifest.problem_id)
    if start.slice in LOGGED and manifest.cluster.get("namespace") != expected:
        raise RunRefused(f"the manifest's cluster namespace is not {expected!r}")
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
    # a field with a default may be absent, so older manifests still read
    required = {
        f.name
        for f in dataclasses.fields(Manifest)
        if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
    }
    missing = required - data.keys()
    extra = data.keys() - fields
    if missing or extra:
        raise ValueError(f"manifest keys: missing {sorted(missing)}, unexpected {sorted(extra)}")
    return Manifest(**{**data, "slice": Slice(data["slice"])})
