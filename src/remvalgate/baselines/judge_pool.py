"""Baseline 6 on the pool (#44, #55, BRD R6.5): the prompted judge ranks labelled candidates.

The judge of baselines/judge.py scores the admitted candidates of one labelling run of baseline 3c
per pool problem, from the run's transcript, at temperature 0. Its pick is the top score with
threshold 0 (#55): a tie keeps the earlier candidate, a candidate with no score is never picked,
and when no candidate has a score the system escalates and the problem counts as not recovered.
The pick's outcome is read from the run's oracle labels by seed, so nothing is executed. The
judge's input holds no label: it is the plan's task prompt, diagnosis and one command.

score_runs checks every input it can from the manifests and file names before any touch (the
model, the whole pool once, a labels file beside each transcript), logs a touch per pool problem
before its transcript and labels are read, checks the three files against each other with
training_data.check_run, then scores. A run that sampled nothing is not read and logs no touch.
"""

import dataclasses
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

from remvalgate.baselines.judge import JudgeModel, Score, judge, pick
from remvalgate.harness.manifest import Registry, Slice, start_run, write_manifest
from remvalgate.harness.no_check import check_transcript, read_transcript, sources
from remvalgate.harness.oracle import read_labels
from remvalgate.harness.training_data import Run, check_run
from remvalgate.planner.chat import Chat
from remvalgate.planner.loop import Candidate, Diagnosis
from remvalgate.planner.models import check_pool_model

THRESHOLD = 0.0


def _plan(transcript: Mapping) -> SimpleNamespace:
    """The two fields judge reads: the diagnosis and the conversation before the final answer."""
    d = transcript.get("diagnosis")
    diagnosis = Diagnosis(d["fault_type"], d["service"], d["reason"]) if d else None
    return SimpleNamespace(diagnosis=diagnosis, context=tuple(transcript.get("context") or ()))


def _candidates(transcript: Mapping) -> list[Candidate]:
    """The candidates of the last draw, as the gate scores the last draw."""
    rows = transcript.get("candidates") or []
    last = max((r.get("draw", 0) for r in rows), default=0)
    return [
        Candidate(r["seed"], r.get("reply", ""), r.get("command"), None, r.get("rejection"), 0, 0.0)
        for r in rows
        if r.get("draw", 0) == last
    ]


def score_problem(
    transcript: Mapping, labels: Mapping, namespace: str, *, chat: Chat
) -> tuple[tuple[Score, ...], dict]:
    """The judge's scores for one labelled problem, and its pick and outcome.

    labels is a labels file as read; `worked` comes from each label's outcome (oracle.read_labels),
    not from the file's own field. Raises ValueError for a transcript with no diagnosis, context or
    candidates, and when the transcript's seeds differ from the labels'."""
    plan = _plan(transcript)
    candidates = _candidates(transcript)
    if plan.diagnosis is None or not plan.context or not candidates:
        raise ValueError(f"{labels['problem_id']}: the transcript has nothing to judge")
    worked = {lab.seed: lab.worked for lab in read_labels(dict(labels)).candidates}
    if sorted(c.seed for c in candidates) != sorted(worked):
        raise ValueError(f"{labels['problem_id']}: the transcript's seeds differ from the labels'")
    scores = judge(plan, candidates, namespace, chat=chat)
    best = pick(scores, THRESHOLD)
    record = {
        "problem_id": labels["problem_id"],
        "scored": sum(s.score is not None for s in scores),
        "pick_seed": None if best is None else best.seed,
        "escalated": best is None,
        "solved": best is not None and worked[best.seed],
    }
    return scores, record


def total(records: Sequence[Mapping], pool: Sequence[str]) -> dict:
    """Problems solved over the pool; every pool problem must have exactly one record."""
    ids = [r["problem_id"] for r in records]
    if sorted(ids) != sorted(pool):
        raise ValueError("baseline 6 needs one record per pool problem")
    return {
        "problems": len(ids),
        "solved": sum(r["solved"] for r in records),
        "escalated": sum(r["escalated"] for r in records),
    }


def _pool(registry: Registry) -> list[str]:
    return [i for i, s in registry.slices.items() if s is Slice.POOL]


def _labels_path(transcript_path: Path) -> Path:
    return transcript_path.with_name(
        transcript_path.name.replace(".transcript.json", ".labels.json")
    )


def score_runs(
    paths: Sequence[Path],
    *,
    registry: Registry,
    chat: Chat,
    model: str,
    model_version: str,
    family: str,
    build: dict,
    touch_log: Path,
    repo: Path,
    aiopslab_commit: str | None,
    out_dir: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    start: Callable = start_run,
) -> tuple[dict, Path]:
    """Score every run and write a manifest and a .baseline6.json result per problem, then a
    summary, under out_dir. Returns the summary and its path.

    Raises ValueError, before any touch, for another model than the runs' or than the pool model,
    for a pool run that does not name every pool problem once, and for a run with candidates and no
    labels file; after its touch, for files that disagree (check_run, the manifest's seeds) or a
    transcript with nothing to judge. start is start_run, replaceable in tests."""
    found = sources(paths, registry)
    on_pool = found[0][0].slice is Slice.POOL
    check_pool_model(model, on_pool)
    for source, _ in found:
        if source.model_name != model:
            raise ValueError(
                f"{source.run_id}: its candidates are of {source.model_name}, not {model}"
            )
    if on_pool:
        named = [s.problem_id for s, _ in found]
        if sorted(named) != sorted(_pool(registry)):
            raise ValueError("a pool run names every pool problem once")
    for _, transcript_path in found:
        if transcript_path is not None and not _labels_path(transcript_path).exists():
            raise ValueError(f"no labels file beside {transcript_path}")
    judge_model = JudgeModel(model, model_version, family, family)
    records = []
    for source, transcript_path in found:
        if transcript_path is None:
            # sampled nothing: no transcript is read and no touch logged
            records.append(
                {
                    "problem_id": source.problem_id,
                    "scored": 0,
                    "pick_seed": None,
                    "escalated": True,
                    "solved": False,
                    "source_run_id": source.run_id,
                }
            )
            continue
        stamp = now()
        run_id = f"baseline6-{source.problem_id}-{stamp.strftime('%Y%m%dt%H%M%Sz')}".lower()
        namespace = source.cluster["namespace"]
        # the touch comes before the transcript and the labels are read
        run = start(
            run_id,
            source.problem_id,
            registry,
            touch_log,
            repo,
            aiopslab_commit,
            stamp,
            namespace=namespace,
        )
        transcript = read_transcript(transcript_path)
        check_transcript(transcript, transcript_path)
        if [c["seed"] for c in transcript["candidates"]] != source.sampling["seeds"]:
            raise ValueError(f"{transcript_path}: the seeds differ from the manifest's")
        labels_bytes = _labels_path(transcript_path).read_bytes()
        labels = json.loads(labels_bytes)
        check_run(
            Run(dataclasses.asdict(source), transcript, labels, sha256(labels_bytes).hexdigest())
        )
        scores, record = score_problem(transcript, labels, namespace, chat=chat)
        judged_at = now()
        manifest = dataclasses.replace(
            source,
            run_id=run_id,
            slice=run.slice,
            commit=run.commit,
            dirty=run.dirty,
            aiopslab_commit=run.aiopslab_commit,
            timestamp=run.timestamp,
            model_build=build,
            sampling={
                "source_run_id": source.run_id,
                "judge": dataclasses.asdict(judge_model)
                | {
                    "shared_family": judge_model.shared_family,
                    "chat": dataclasses.asdict(chat),
                    "date": judged_at.strftime("%Y-%m-%d"),
                },
                "threshold": THRESHOLD,
            },
        )
        path = write_manifest(manifest, run, out_dir, registry, touch_log)
        record |= {"run_id": run_id, "source_run_id": source.run_id}
        out = {"record": record, "scores": [dataclasses.asdict(s) for s in scores]}
        path.with_suffix(".baseline6.json").write_text(json.dumps(out, indent=2))
        records.append(record)
    ids = _pool(registry) if on_pool else [r["problem_id"] for r in records]
    summary = {
        "judge": dataclasses.asdict(judge_model)
        | {"shared_family": judge_model.shared_family, "chat": dataclasses.asdict(chat)},
        "model_build": build,
        "threshold": THRESHOLD,
        "total": total(records, ids),
        "records": records,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"baseline6-summary-{now().strftime('%Y%m%dt%H%M%S%fz')}.json"
    with out.open("x", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    return summary, out
