"""Ablation without the deterministic check (story E5-S4, BRD R5.4), applied offline.

The allow-list is applied again to every sampled candidate a run recorded, so its contribution is
measured without running an out-of-policy command: nothing here runs a command or reaches a
cluster. The planner's own command is not a sampled candidate and is left out.

Two views are reported. The headline is the candidates a run without the check would have drawn:
the first draw, plus the second when every candidate of the first had no command. resample_once
draws again when every first-draw candidate is rejected; without the check a candidate with a
command is never rejected, so the second draw is made then only for a first draw with no command
at all. Both draws together count every candidate sampled. A candidate whose reply held no
command is counted apart, since the check never saw it. Shares are of all candidates in the view.

Each recorded verdict is compared with the recomputed one, so a policy changed since the run
refuses the report instead of mixing two policies.

ablate_runs reads the runs: one per problem, all of one slice and one model and sampling setup,
all checked before any touch. A pool problem's run gets a nocheck- touch through start_run before
its transcript is read. A run refused after its touch keeps that touch, since the transcript was
read; the problems before it keep their results, and no summary is written. A run whose manifest
records no seeds, as when its plan had no diagnosis, is listed in the summary and not touched,
since there is no candidate to read.
"""

import dataclasses
import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.harness.allowlist import (
    Policy,
    Reason,
    ReasonCounts,
    Verdict,
    check,
    reason_counts,
    validate_namespace,
)
from remvalgate.harness.manifest import (
    LOGGED,
    Manifest,
    Registry,
    read_manifest,
    start_run,
    write_manifest,
)

# the manifest fields every pooled run must share, besides the sampling settings
_SHARED = ("model_name", "model_version", "k", "r")
# the transcript fields the analysis reads
_CANDIDATE_KEYS = ("seed", "draw", "command", "verdict", "rejection")
_VERDICT_KEYS = ("admitted", "reason")


class VerdictMismatch(ValueError):
    pass


@dataclass(frozen=True)
class Tally:
    # every candidate in the view, with or without a command
    candidates: int
    # replies with no command, or cut at the token cap
    no_command: int
    # the allow-list's verdicts on the candidates that have a command
    checked: ReasonCounts

    @property
    def removed(self) -> int:
        return sum(self.checked.by_reason.values())

    def share(self, count: int) -> float | None:
        """count over all candidates in the view, None when there are none."""
        return count / self.candidates if self.candidates else None


@dataclass(frozen=True)
class ProblemResult:
    problem_id: str
    # the candidates a run without the check would have drawn
    without_check: Tally
    both_draws: Tally
    # True when a second draw was made
    resampled: bool
    # True when the second draw would be made without the check too: no first-draw command
    resampled_without_check: bool


def recheck(candidates: Sequence[Mapping], policy: Policy, namespace: str) -> list[Verdict | None]:
    """The allow-list verdict on each recorded candidate, None for one with no command.

    Raises VerdictMismatch when a recomputed verdict differs from the recorded one.
    """
    verdicts: list[Verdict | None] = []
    for c in candidates:
        command, recorded = c["command"], c["verdict"]
        verdict = None if command is None else check(command, policy, namespace)
        if (recorded is None) != (verdict is None) or (
            verdict is not None
            and (recorded["admitted"], recorded["reason"]) != (verdict.admitted, verdict.reason)
        ):
            raise VerdictMismatch(
                f"seed {c['seed']}: recorded verdict {recorded} but the policy now gives "
                f"{verdict}; rerun with the policy the candidates were checked under"
            )
        verdicts.append(verdict)
    return verdicts


def tally(verdicts: Iterable[Verdict | None]) -> Tally:
    verdicts = list(verdicts)
    return Tally(
        candidates=len(verdicts),
        no_command=sum(v is None for v in verdicts),
        checked=reason_counts(v for v in verdicts if v is not None),
    )


def ablate_problem(
    problem_id: str, candidates: Sequence[Mapping], policy: Policy, namespace: str
) -> ProblemResult:
    """Recheck one problem's recorded candidates (a transcript's "candidates" list).

    Raises ValueError when the draws are not 1, or 1 then 2, or when a second draw follows a first
    with an admitted candidate, and VerdictMismatch as recheck does.
    """
    draws = [c["draw"] for c in candidates]
    if draws and (draws[0] != 1 or draws != sorted(draws) or not set(draws) <= {1, 2}):
        raise ValueError(f"{problem_id}: expected draw 1, then draw 2 if any, got {draws}")
    first = [c for c in candidates if c["draw"] == 1]
    # resample_once draws again only when every candidate of the first draw is rejected
    if 2 in draws and any(c["rejection"] is None for c in first):
        raise ValueError(f"{problem_id}: a second draw after a first with an admitted candidate")
    verdicts = recheck(candidates, policy, namespace)
    resampled = 2 in draws
    again = resampled and all(c["command"] is None for c in first)
    kept = [v for v, d in zip(verdicts, draws, strict=True) if d == 1 or again]
    return ProblemResult(
        problem_id=problem_id,
        without_check=tally(kept),
        both_draws=tally(verdicts),
        resampled=resampled,
        resampled_without_check=again,
    )


def pooled(results: Iterable[ProblemResult], both: bool) -> Tally:
    """The sum over problems of the headline view, or of both draws."""
    tallies = [r.both_draws if both else r.without_check for r in results]
    by_reason = {reason: sum(t.checked.by_reason[reason] for t in tallies) for reason in Reason}
    return Tally(
        candidates=sum(t.candidates for t in tallies),
        no_command=sum(t.no_command for t in tallies),
        checked=ReasonCounts(total=sum(t.checked.total for t in tallies), by_reason=by_reason),
    )


def tally_record(t: Tally) -> dict:
    """A tally as JSON: counts with their shares of all candidates in the view."""

    def row(count: int) -> dict:
        return {"count": count, "share": t.share(count)}

    return {
        "candidates": t.candidates,
        "admitted": row(t.checked.admitted),
        "removed": row(t.removed),
        "no_command": row(t.no_command),
        "by_reason": {reason.value: row(n) for reason, n in t.checked.by_reason.items()},
    }


def _setup(manifest: Manifest) -> dict:
    # the sampling settings apart from the draw seeds, which differ per run
    return {
        **{name: getattr(manifest, name) for name in _SHARED},
        "sampling": {k: v for k, v in manifest.sampling.items() if k != "seeds"},
    }


def sources(paths: Sequence[Path], registry: Registry) -> list[tuple[Manifest, Path | None]]:
    """Each run's manifest and transcript path. Reads no transcript, so it logs no touch.

    A run whose manifest records no seeds sampled nothing, as when its plan had no diagnosis: its
    transcript path is None, since the manifest alone says so and nothing needs to be read.

    Raises ValueError for a run with no seeds field, no transcript or no valid namespace, for two
    runs of one problem, and for runs of mixed slices or of a different model or setup.
    """
    found = []
    for path in paths:
        manifest = read_manifest(path)
        transcript = path.with_suffix(".transcript.json")
        seeds = manifest.sampling.get("seeds")
        if not isinstance(seeds, list):
            raise ValueError(f"{path}: the manifest records no seeds list")
        if not seeds:
            transcript = None
        elif not transcript.exists():
            raise ValueError(f"{path}: no transcript at {transcript}")
        if "namespace" not in manifest.cluster:
            raise ValueError(f"{path}: the manifest names no namespace")
        validate_namespace(manifest.cluster["namespace"])
        found.append((manifest, transcript))
    if not found:
        raise ValueError("give at least one run")
    problems = [m.problem_id for m, _ in found]
    if len(set(problems)) != len(problems):
        raise ValueError("give one run per problem, so a problem is not counted twice")
    slices = {registry.slices.get(p) for p in problems}
    if len(slices) != 1:
        raise ValueError(f"give runs of one slice only, got {sorted(map(str, slices))}")
    setups = [_setup(m) for m, _ in found]
    if any(s != setups[0] for s in setups):
        raise ValueError("give runs of one model and one sampling setup, so the pool is one system")
    return found


def read_transcript(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def check_transcript(transcript: object, path: Path) -> None:
    """Raise ValueError naming the first field the analysis reads that the transcript lacks."""
    if not isinstance(transcript, dict):
        raise ValueError(f"{path}: the transcript is not a JSON object")
    for key in ("candidates", "draws"):
        if key not in transcript:
            raise ValueError(f"{path}: the transcript has no {key} field")
    if not isinstance(transcript["candidates"], list):
        raise ValueError(f"{path}: candidates is not a list")
    for n, c in enumerate(transcript["candidates"]):
        missing = [k for k in _CANDIDATE_KEYS if not isinstance(c, dict) or k not in c]
        if missing:
            raise ValueError(f"{path}: candidate {n} has no {', '.join(missing)} field")
        verdict = c["verdict"]
        if verdict is not None and (
            not isinstance(verdict, dict) or any(k not in verdict for k in _VERDICT_KEYS)
        ):
            raise ValueError(f"{path}: candidate {n} has a verdict without admitted and reason")


def ablate_runs(
    paths: Sequence[Path],
    *,
    registry: Registry,
    policy: Policy,
    policy_sha256: str,
    touch_log: Path,
    repo: Path,
    aiopslab_commit: str | None,
    out_dir: Path,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
    start: Callable = start_run,
) -> tuple[dict, Path]:
    """Analyse the runs and write a manifest and a .nocheck.json result per problem, then a
    pooled summary, all under out_dir. Returns the summary and its path.

    A run that sampled nothing is listed in the summary's no_candidates_run_ids and is not
    touched. problems counts the runs analysed.

    Raises ValueError as sources, ablate_problem or the transcript checks do, and RunRefused when
    start_run or write_manifest refuses. start is start_run, replaceable in tests.
    """
    found = sources(paths, registry)
    results, runs, empty = [], [], []
    for source, transcript_path in found:
        if transcript_path is None:
            # the manifest says nothing was sampled, so no transcript is read and no touch logged
            empty.append(source.run_id)
            continue
        stamp = now()
        run_id = f"nocheck-{source.problem_id}-{stamp.strftime('%Y%m%dt%H%M%Sz')}".lower()
        namespace = source.cluster["namespace"]
        # the touch comes before the transcript is read
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
        candidates = transcript["candidates"]
        if [c["seed"] for c in candidates] != source.sampling["seeds"]:
            raise ValueError(f"{transcript_path}: the seeds differ from the manifest's")
        result = ablate_problem(source.problem_id, candidates, policy, namespace)
        if transcript["draws"] != 1 + result.resampled:
            raise ValueError(
                f"{transcript_path}: draws {transcript['draws']} disagrees with its candidates"
            )
        # the candidates' model, seed and K are the source run's; commit and time are this run's
        manifest = dataclasses.replace(
            source,
            run_id=run_id,
            slice=run.slice,
            commit=run.commit,
            dirty=run.dirty,
            aiopslab_commit=run.aiopslab_commit,
            timestamp=run.timestamp,
            sampling={
                **source.sampling,
                "source_run_id": source.run_id,
                "policy_sha256": policy_sha256,
            },
        )
        path = write_manifest(manifest, run, out_dir, registry, touch_log)
        record = {
            "problem_id": result.problem_id,
            "run_id": run_id,
            "source_run_id": source.run_id,
            "resampled": result.resampled,
            "resampled_without_check": result.resampled_without_check,
            "without_check": tally_record(result.without_check),
            "both_draws": tally_record(result.both_draws),
        }
        path.with_suffix(".nocheck.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        results.append(result)
        runs.append((run_id, source))
    first = found[0][0]
    slice_ = registry.slices[first.problem_id]
    summary = {
        "slice": str(slice_),
        "logged": slice_ in LOGGED,
        "problems": len(results),
        # the runs pooled, so the summary traces to their manifests and touches
        "run_ids": [run_id for run_id, _ in runs],
        "source_run_ids": [s.run_id for _, s in runs],
        # runs whose plan sampled nothing, as with no diagnosis, so none is dropped silently
        "no_candidates_run_ids": empty,
        **_setup(first),
        "model_dates": sorted({s.model_date for s, _ in found}),
        "resampled": sum(r.resampled for r in results),
        "resampled_without_check": sum(r.resampled_without_check for r in results),
        "policy_sha256": policy_sha256,
        "without_check": tally_record(pooled(results, both=False)),
        "both_draws": tally_record(pooled(results, both=True)),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"nocheck-summary-{now().strftime('%Y%m%dt%H%M%Sz')}.json"
    # mode x: a summary is never overwritten
    with out.open("x", encoding="utf-8", newline="\n") as file:
        file.write(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    return summary, out
