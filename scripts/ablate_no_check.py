"""Story E5-S4 (BRD R5.4): the ablation without the deterministic check, applied offline.

Reads planner runs that sampled candidates, each given as its manifest under runs/manifests/ with
the transcript beside it, applies the allow-list again to every sampled candidate, and reports the
share removed by reason, for the first draw and for both draws (src/remvalgate/harness/no_check.py).
Nothing is run: no command reaches a cluster, so it needs neither a cluster nor a model server.

Every given run must be of the same slice and of a different problem, so the pooled counts hold one
run per problem. A pool problem's analysis is a touch: start_run logs a nocheck- run per problem
before its transcript is read, so it needs gate G1 and a clean checkout, as any pool run does.
A run refused after a touch, for example by a verdict the policy no longer gives, keeps that
touch, since the transcript was read; the problems before it keep their results. Development runs
log nothing. A manifest per problem, its result and a pooled summary go to
runs/manifests/, which git ignores.

    uv run python scripts/ablate_no_check.py runs/manifests/<run_id>.json [...]
"""

import argparse
import dataclasses
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy, validate_namespace
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    LOGGED,
    load_registry,
    read_manifest,
    start_run,
    write_manifest,
)
from remvalgate.harness.no_check import ablate_problem, pooled, tally_record

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "manifests"
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"


def _sources(paths: list[Path], registry) -> list[tuple]:
    # checked before any touch, so a bad argument logs nothing
    sources = []
    for path in paths:
        manifest = read_manifest(path)
        transcript = path.with_suffix(".transcript.json")
        if not transcript.exists():
            raise SystemExit(f"{path}: no transcript at {transcript}")
        if not manifest.sampling.get("seeds"):
            raise SystemExit(f"{path}: the run sampled no candidates")
        if "namespace" not in manifest.cluster:
            raise SystemExit(f"{path}: the manifest names no namespace")
        try:
            validate_namespace(manifest.cluster["namespace"])
        except ValueError as error:
            raise SystemExit(f"{path}: {error}") from error
        sources.append((manifest, transcript))
    problems = [m.problem_id for m, _ in sources]
    if len(set(problems)) != len(problems):
        raise SystemExit("give one run per problem, so a problem is not counted twice")
    slices = {registry.slices.get(p) for p in problems}
    if len(slices) != 1:
        raise SystemExit(f"give runs of one slice only, got {sorted(map(str, slices))}")
    return sources


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifests", nargs="+", type=Path, help="planner run manifests")
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    policy = load_policy(DEFAULT_POLICY_PATH)
    policy_sha256 = hashlib.sha256(DEFAULT_POLICY_PATH.read_bytes()).hexdigest()
    sources = _sources(args.manifests, registry)
    results = []
    for source, transcript_path in sources:
        now = datetime.now(UTC)
        run_id = f"nocheck-{source.problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}".lower()
        namespace = source.cluster["namespace"]
        # the touch comes before the transcript is read
        run = start_run(
            run_id,
            source.problem_id,
            registry,
            TOUCH_LOG,
            ROOT,
            pinned_commit(ROOT),
            now,
            namespace=namespace,
        )
        transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
        candidates = transcript["candidates"]
        if [c["seed"] for c in candidates] != source.sampling["seeds"]:
            raise SystemExit(f"{transcript_path}: the seeds differ from the manifest's")
        result = ablate_problem(source.problem_id, candidates, policy, namespace)
        if transcript["draws"] != 1 + result.resampled:
            raise SystemExit(
                f"{transcript_path}: draws {transcript['draws']} disagrees with its candidates"
            )
        results.append(result)
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
        path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
        record = {
            "problem_id": result.problem_id,
            "source_run_id": source.run_id,
            "resampled": result.resampled,
            "first_draw": tally_record(result.first_draw),
            "both_draws": tally_record(result.both_draws),
        }
        path.with_suffix(".nocheck.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(
            f"{result.problem_id}: first draw removed {result.first_draw.removed}"
            f"/{result.first_draw.candidates}, both draws removed {result.both_draws.removed}"
            f"/{result.both_draws.candidates}, resampled={result.resampled}  manifest {path}"
        )
    now = datetime.now(UTC)
    summary = {
        "slice": str(registry.slices[results[0].problem_id]),
        "logged": registry.slices[results[0].problem_id] in LOGGED,
        "problems": len(results),
        "resampled": sum(r.resampled for r in results),
        "policy_sha256": policy_sha256,
        "first_draw": tally_record(pooled(results, both=False)),
        "both_draws": tally_record(pooled(results, both=True)),
    }
    out = OUT_DIR / f"nocheck-summary-{now.strftime('%Y%m%dt%H%M%Sz')}.json"
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for view in ("first_draw", "both_draws"):
        t = summary[view]
        print(f"pooled {view}: {t['candidates']} candidates over {len(results)} problems")
        for name in ("admitted", "removed", "no_command"):
            print(f"  {name:<22} {t[name]['count']:>4}  {_pct(t[name]['share'])}")
        for reason, row in t["by_reason"].items():
            print(f"    {reason:<20} {row['count']:>4}  {_pct(row['share'])}")
    print(f"summary {out}")
    return 0


def _pct(share: float | None) -> str:
    return "n/a" if share is None else f"{100 * share:5.1f}%"


if __name__ == "__main__":
    sys.exit(main())
