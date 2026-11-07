"""Per-fold training data for the gate and for baseline 5, built from the oracle labels (E7-S1).

The labels of baseline 3c's K candidates (docs/oracle_labels.md) are reused out of fold, with no
new rollouts: the training set of fold f holds the candidates of the pool problems outside fold
f, so a gate trained on it never sees a problem it scores. build_fold refuses a set that holds a
fold-f problem, or a confirmation, development, singleton or unknown id.

One record per distinct command of a problem. Near-identical candidates were executed once and
share a label (oracle.dedup_key), so they are merged into one record that lists their seeds. A
candidate's label is 1 when its execution was RECOVERED and 0 when it was APPLIED_INEFFECTIVE or
APPLIED_HARMFUL; the outcome is kept as a field. A candidate with no outcome (rejected by the
allow-list, no command in the reply, or not sendable unchanged) is stored with train false and
never trained on. The plan's own command is not one of the K and is left out.

Leakage. A training problem retrieves from the corpus of the fold that holds it out, so its
retrieved past fixes can hold a record of a fold-f problem, fix included. In fold f's records
every such entry is replaced by the marker MASK, in the gate input and in baseline 5's prompt
alike, and a record whose command equals the recorded fix of a fold-f problem (namespaces left
out) is stored with train false. The report counts both. Text the model wrote after reading an
entry, in a later turn of baseline 5's prompt, is not masked.

The gate input is one JSON object in a fixed order: the task, the whole first observation, the
diagnosis, the retrieved past fixes as the planner saw them, and the candidate command. Its
length is checked against TOKEN_BUDGET with a tokenizer when one is given, and otherwise with
length_proxy; rows over the budget are reported, not cut. Baseline 5's prompt is the planner's
sampling messages (sampling_messages, rebuilt here as planner.loop.sample builds them), with the
candidate's reply as the completion.

The files are read by notebooks that do not import this package, so every record holds plain
JSON values only. The format is in docs/training_data.md.
"""

import dataclasses
import hashlib
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.harness.allowlist import Policy, Rejected
from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import Registry, Slice, logged_namespace, start_run
from remvalgate.harness.oracle import dedup_key, read_labels
from remvalgate.harness.outcome import Outcome
from remvalgate.planner.loop import Diagnosis

FORMAT = "remvalgate-training-data/1"
MASK = "[held-out record removed]"
TOKEN_BUDGET = 2048
# the task prompt as planner.loop._task_prompt writes it
_STATE = "\n\nCluster state:\n"
_PAST = "\n\nPast fixes that may be similar:\n"
_TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"
# mirrors planner.loop._sample_prompt, the request every draw of sample sees; a test pins it
_SAMPLE_PROMPT = (
    "Now give one candidate fix for this diagnosis. Reply with exactly one JSON object and "
    'nothing else: {{"command": C}} where C is one kubectl command that fixes the fault and '
    "starts with kubectl -n {namespace}. C is not run here."
)


def length_proxy(text: str) -> int:
    """A tokenizer-free length estimate: one token per digit, as the Qwen tokenizer splits numbers
    into single digits (Qwen technical report, arXiv 2309.16609, section 2.2); not checked for the
    Qwen3.5 tokenizer. One token per three other characters. Deterministic, and meant to
    overestimate English and kubectl listings, but it is an estimate, not a bound."""
    digits = sum(c.isdigit() for c in text)
    return digits + math.ceil((len(text) - digits) / 3)


def sha256_json(value: object) -> str:
    """The SHA-256 of a value's canonical JSON form (sorted keys, no spaces)."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Run:
    """One labelled problem: the three files of one oracle run, read as JSON."""

    manifest: dict
    transcript: dict
    labels: dict
    # the SHA-256 of the labels file's bytes
    labels_sha256: str

    @property
    def problem_id(self) -> str:
        return self.manifest["problem_id"]

    @property
    def run_id(self) -> str:
        return self.manifest["run_id"]


def check_run(run: Run) -> None:
    """Raise ValueError unless the three files belong to one run of baseline 3c and agree.

    The labels' counts are checked by oracle.read_labels; the candidates must be the transcript's
    single draw, seed for seed and command for command.
    """
    pid = run.problem_id
    if run.labels.get("run_id") != run.run_id:
        raise ValueError(f"{pid}: the labels file is of run {run.labels.get('run_id')!r}")
    if run.labels.get("problem_id") != pid:
        raise ValueError(f"{pid}: the labels file is of problem {run.labels.get('problem_id')!r}")
    if run.transcript.get("config", {}).get("baseline") != "3c":
        raise ValueError(f"{pid}: the transcript is not of baseline 3c")
    read_labels(run.labels)
    drawn = run.transcript["candidates"]
    if drawn and run.transcript["draws"] != 1:
        raise ValueError(f"{pid}: baseline 3c draws once, the transcript has more draws")
    pairs = [(c["seed"], c["command"]) for c in drawn]
    labelled = [(c["seed"], c["command"]) for c in run.labels["candidates"]]
    if pairs != labelled:
        raise ValueError(f"{pid}: the labelled candidates are not the transcript's")


def check_ids(
    problem_ids: Sequence[str], fold: int, assignment: Mapping[str, int], registry: Registry
) -> None:
    """Raise ValueError if a training set for fold f would hold a problem that is not in the
    pool, that the assignment does not place, or that fold f holds out."""
    for pid in problem_ids:
        slice_ = registry.slices.get(pid)
        if slice_ is not Slice.POOL:
            raise ValueError(f"{pid}: a {slice_ or 'unregistered'} problem is never trained on")
        if pid not in assignment:
            raise ValueError(f"{pid}: has no fold")
        if assignment[pid] == fold:
            raise ValueError(f"{pid}: is held out by fold {fold}")


def task_and_past(transcript: dict) -> tuple[str, list[str]]:
    """The task line and the retrieved past fixes, one line each without the leading "- ", read
    from the task prompt the planner saw. Raises ValueError when the prompt does not hold the
    first observation, or holds another number of lines than the retrieved entries."""
    prompt = transcript["context"][1]["content"]
    task, _, rest = prompt.partition(_STATE)
    observation, _, past = rest.rpartition(_PAST) if _PAST in rest else (rest, "", "")
    if observation != transcript["first_observation"]:
        raise ValueError("the task prompt does not hold the first observation")
    lines = past.split("\n") if past else []
    if len(lines) != len(transcript["retrieved"]) or not all(x.startswith("- ") for x in lines):
        raise ValueError("the past fixes in the task prompt do not match the retrieved entries")
    return task, [line.removeprefix("- ") for line in lines]


def masked_past(transcript: dict, held_out: frozenset[str]) -> tuple[str, list[str], int]:
    """The task, the past fixes with each held-out record replaced by MASK, and the number
    masked. An incident entry's id is its problem id; runbook entries are never masked."""
    task, lines = task_and_past(transcript)
    ids = [entry_id for entry_id, _ in transcript["retrieved"]]
    kept = [MASK if i in held_out else line for i, line in zip(ids, lines, strict=True)]
    return task, kept, sum(entry_id in held_out for entry_id in ids)


def sampling_messages(context: Sequence[dict], diagnosis: dict, namespace: str) -> list[dict]:
    """The messages every draw of planner.loop.sample saw, rebuilt from a transcript: the
    diagnosis conversation, the diagnosis as the model's answer with its command left out, and
    the request for one command. The transcript stores the diagnosis with sorted keys, so it is
    put back in the field order of Diagnosis to give the same text."""
    final = {"final": dataclasses.asdict(Diagnosis(**diagnosis))}
    return [
        *(dict(m) for m in context),
        {"role": "assistant", "content": json.dumps(final)},
        {"role": "user", "content": _SAMPLE_PROMPT.format(namespace=namespace)},
    ]


def _prompt_messages(transcript: dict, namespace: str, past: list[str]) -> list[dict]:
    """Baseline 5's prompt: the sampling messages, with the task prompt's past fixes replaced."""
    context = [dict(m) for m in transcript["context"]]
    if past:
        head = context[1]["content"].rpartition(_PAST)[0]
        context[1]["content"] = head + _PAST + "\n".join(f"- {line}" for line in past)
    return sampling_messages(context, transcript["diagnosis"], namespace)


def _call_shape(command: str, policy: Policy) -> tuple | None:
    """dedup_key with the namespaces left out, so a command and a fix of another namespace
    compare; None for a command the allow-list's tokenizer refuses."""
    try:
        return tuple(part[:1] + part[2:] for part in dedup_key(command, policy))
    except Rejected:
        return None


def _groups(labels: dict) -> list[list[dict]]:
    """The candidates grouped by the execution that labels them, in draw order: a duplicate joins
    the candidate it duplicates, every other candidate starts a group."""
    groups: dict[int, list[dict]] = {}
    for label in labels["candidates"]:
        source = label["duplicate_of"]
        if source is not None and source not in groups:
            raise ValueError(f"seed {label['seed']} duplicates seed {source}, which is not earlier")
        groups.setdefault(label["seed"] if source is None else source, []).append(label)
    return list(groups.values())


def _exclusion(first: dict, shape: tuple | None, fixes: Mapping[tuple, str]) -> str | None:
    if first["outcome"] is None:
        return f"no outcome: {first['rejection']}"
    if shape is not None and shape in fixes:
        return f"equals the recorded fix of held-out problem {fixes[shape]}"
    return None


def problem_records(
    run: Run, held_out: frozenset[str], fixes: Mapping[tuple, str], policy: Policy
) -> tuple[list[dict], list[dict], int]:
    """The gate records, the baseline 5 records and the number of masked entries of one problem.

    held_out is the ids of fold f's problems; fixes maps the call shape of each fold-f recorded
    fix to its problem id.
    """
    transcript = run.transcript
    namespace = run.manifest["cluster"]["namespace"]
    replies = {c["seed"]: c["reply"] for c in transcript["candidates"]}
    if transcript["diagnosis"] is None:
        return [], [], 0
    task, past, masked = masked_past(transcript, held_out)
    diagnosis = Diagnosis(**transcript["diagnosis"])
    messages = _prompt_messages(transcript, namespace, past)
    gate, baseline5 = [], []
    for group in _groups(run.labels):
        first = group[0]
        command = first["command"]
        shape = None if command is None else _call_shape(command, policy)
        excluded = _exclusion(first, shape, fixes)
        outcome = first["outcome"]
        common = {
            "id": f"{run.problem_id}/{first['seed']}",
            "problem_id": run.problem_id,
            "run_id": run.run_id,
            "seeds": [label["seed"] for label in group],
            "candidate": command,
            "outcome": outcome,
            "label": None if outcome is None else int(outcome == str(Outcome.RECOVERED)),
            "train": excluded is None,
            "excluded": excluded,
            "masked_entries": masked,
        }
        compact = {
            "task": task,
            "first_observation": transcript["first_observation"],
            "diagnosis": {
                "fault_type": diagnosis.fault_type,
                "service": diagnosis.service,
                "reason": diagnosis.reason,
            },
            "retrieved": past,
            "candidate": command,
        }
        gate.append({**common, "input": json.dumps(compact, ensure_ascii=False)})
        baseline5.append({**common, "messages": messages, "completion": replies[first["seed"]]})
    return gate, baseline5, masked


def _lengths(records: list[dict], text: Callable[[dict], str], count) -> dict:
    sizes = [count(text(r)) for r in records]
    if not sizes:
        return {"max": None, "p95": None, "over_budget": []}
    p95 = statistics.quantiles(sizes, n=20, method="inclusive")[-1] if len(sizes) > 1 else sizes[0]
    for record, size in zip(records, sizes, strict=True):
        record["length"] = size
    return {
        "max": max(sizes),
        "p95": p95,
        "over_budget": [r["id"] for r, s in zip(records, sizes, strict=True) if s > TOKEN_BUDGET],
    }


def _cause(record: dict) -> str:
    why = record["excluded"].removeprefix("no outcome: ")
    if why.startswith("allow-list:"):
        return "rejected"
    return "not_sendable" if why.startswith("cannot be sent unchanged") else "no_command"


def _counts(records: list[dict], masked: Mapping[str, int]) -> dict:
    trained = [r for r in records if r["train"]]
    no_outcome = [r for r in records for _ in r["seeds"] if r["outcome"] is None]
    return {
        "problems": len({r["problem_id"] for r in records}),
        "records": len(records),
        "candidates": sum(len(r["seeds"]) for r in records),
        "train": len(trained),
        "train_by_outcome": dict(sorted(Counter(r["outcome"] for r in trained).items())),
        "train_label_1": sum(r["label"] for r in trained),
        "no_outcome": sum(r["outcome"] is None for r in records),
        # candidates, not records, left out for want of an outcome, by cause
        "no_outcome_candidates": dict(sorted(Counter(_cause(r) for r in no_outcome).items())),
        "fix_match_excluded": sum(r["outcome"] is not None and not r["train"] for r in records),
        "masked_entries": sum(masked.values()),
        "records_with_masked_entries": sum(r["masked_entries"] > 0 for r in records),
        "train_with_masked_entries": sum(r["masked_entries"] > 0 for r in trained),
    }


def build_fold(
    runs: Sequence[Run],
    fold: int,
    assignment: Mapping[str, int],
    registry: Registry,
    recorded_fixes: Mapping[str, str],
    policy: Policy,
    count: Callable[[str], int] = length_proxy,
) -> tuple[list[dict], list[dict], dict]:
    """The gate records, the baseline 5 records and the report of fold f.

    runs holds the labelled runs of fold f's training problems, each problem once; a missing
    training problem, a repeated one or an id check_ids refuses raises ValueError.
    recorded_fixes maps each pool problem id to its recorded fix (retrieval.pool_corpus).
    count measures a text's length in tokens.
    """
    ids = [run.problem_id for run in runs]
    check_ids(ids, fold, assignment, registry)
    if len(set(ids)) != len(ids):
        raise ValueError("a problem has more than one labelled run")
    expected = {pid for pid, f in assignment.items() if f != fold}
    if set(ids) != expected:
        raise ValueError(f"fold {fold}: no labelled run for {sorted(expected - set(ids))}")
    held_out = frozenset(pid for pid, f in assignment.items() if f == fold)
    fixes = {}
    for pid in sorted(held_out):
        shape = _call_shape(recorded_fixes[pid], policy)
        if shape is not None:
            fixes.setdefault(shape, pid)
    gate, baseline5, masked = [], [], {}
    for run in sorted(runs, key=lambda r: r.problem_id):
        check_run(run)
        g, b, masked[run.problem_id] = problem_records(run, held_out, fixes, policy)
        gate += g
        baseline5 += b
    report = _counts(gate, masked) | {
        "held_out": sorted(held_out),
        "token_budget": TOKEN_BUDGET,
        "gate_length": _lengths(gate, lambda r: r["input"], count),
        "baseline5_length": _lengths(
            baseline5,
            lambda r: "".join(m["content"] for m in r["messages"]) + (r["completion"] or ""),
            count,
        ),
    }
    return gate, baseline5, report


def _labels_run_id(path: Path) -> str:
    if not path.name.endswith(".labels.json"):
        raise ValueError(f"{path}: not a labels file (<run id>.labels.json)")
    return path.name.removesuffix(".labels.json")


def _problem_of(run_id: str, registry: Registry) -> str:
    """The problem id inside an oracle run id, oracle-<problem id>-<UTC time>."""
    head, _, stamp = run_id.rpartition("-")
    pid = head.removeprefix("oracle-")
    if not run_id.startswith("oracle-") or len(stamp) != 16 or pid not in registry.slices:
        raise ValueError(f"{run_id}: not an oracle run id of a registered problem")
    return pid


def export(
    labels_paths: Sequence[str | Path],
    out_dir: str | Path,
    *,
    registry: Registry,
    log_path: str | Path,
    repo: str | Path,
    aiopslab_commit: str | None,
    recorded_fixes: Mapping[str, str],
    policy: Policy,
    now: datetime | None = None,
    count: Callable[[str], int] = length_proxy,
    counter: str = "length_proxy",
    checksum=None,
) -> list[Path]:
    """Write the gate file and the baseline 5 file of every fold, and return their paths.

    Each labels file is <run id>.labels.json, with <run id>.json (the manifest) and
    <run id>.transcript.json beside it. Before any file is read, every id is checked from the
    file names (pool problems only, each once, the whole pool) and one touch is appended through
    start_run per fold and training problem, with run id train-export-<fold>-<problem id>-<UTC
    time>: start_run refuses a run id already in the log, so the problem id is part of it. The
    files are then read, checked and written; an existing output is never overwritten.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    paths = [Path(p) for p in labels_paths]
    by_problem: dict[str, Path] = {}
    for path in paths:
        pid = _problem_of(_labels_run_id(path), registry)
        if registry.slices[pid] is not Slice.POOL:
            raise ValueError(f"{pid}: a {registry.slices[pid]} problem is never trained on")
        if pid in by_problem:
            raise ValueError(f"{pid}: more than one labels file")
        by_problem[pid] = path
    assignment = pool_folds(registry)
    if set(by_problem) != set(assignment):
        raise ValueError(f"no labels file for {sorted(set(assignment) - set(by_problem))}")
    stamp = now.strftime("%Y%m%dt%H%M%Sz")
    touches, start = [], None
    for fold in sorted(set(assignment.values())):
        for pid in sorted(p for p, f in assignment.items() if f != fold):
            run_id = f"train-export-{fold}-{pid}-{stamp}"
            start = start_run(
                run_id,
                pid,
                registry,
                log_path,
                repo,
                aiopslab_commit,
                now,
                checksum=checksum,
                namespace=logged_namespace(pid),
            )
            touches.append({"fold": fold, "problem_id": pid, "run_id": run_id})
    runs, files = {}, {}
    for pid, path in sorted(by_problem.items()):
        run_id = _labels_run_id(path)
        raw = path.read_bytes()
        folder = path.parent
        manifest = json.loads((folder / f"{run_id}.json").read_text(encoding="utf-8"))
        text = (folder / f"{run_id}.transcript.json").read_bytes()
        run = Run(manifest, json.loads(text), json.loads(raw), hashlib.sha256(raw).hexdigest())
        if run.problem_id != pid or run.run_id != run_id:
            raise ValueError(f"{path}: the manifest is of another run")
        runs[pid] = run
        # the labelling run's manifest names the model, its version and date, and the seed
        files[pid] = {
            "run_id": run_id,
            "file": path.name,
            "sha256": run.labels_sha256,
            "transcript_sha256": hashlib.sha256(text).hexdigest(),
            "manifest": manifest,
        }
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fold in sorted(set(assignment.values())):
        training = [runs[p] for p in sorted(runs) if assignment[p] != fold]
        gate, baseline5, report = build_fold(
            training, fold, assignment, registry, recorded_fixes, policy, count
        )
        header = {
            "format": FORMAT,
            "fold": fold,
            "created": now.strftime(_TIMESTAMP),
            "commit": start.commit,
            "dirty": start.dirty,
            "aiopslab_commit": start.aiopslab_commit,
            "fold_assignment": dict(sorted(assignment.items())),
            "fold_assignment_sha256": sha256_json(assignment),
            "labels_files": {p: files[p] for p in sorted(files) if assignment[p] != fold},
            "touches": [t["run_id"] for t in touches if t["fold"] == fold],
            "length_counter": counter,
            "mask": MASK,
        }
        for kind, records in (("gate", gate), ("baseline5", baseline5)):
            path = out_dir / f"training-{kind}-fold{fold}-{stamp}.json"
            body = {**header, "kind": kind, "report": report, "records": records}
            with path.open("x", encoding="utf-8", newline="\n") as file:
                file.write(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
            written.append(path)
    return written
