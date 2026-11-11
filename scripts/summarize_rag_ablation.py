r"""The extrinsic RAG ablation (story E4-S4, #33, BRD R4.4), with no cluster and no model.

Reads the result files of baselines 3a, 3b and 3c from the baseline-only run
(scripts/run_baselines_pool.py), each next to its transcript and its manifest, and prints for
each configuration the diagnosis accuracy, the fault-type accuracy and the single-action recovery
over the problems it ran, the paired comparisons on the problems every configuration ran, one
row per problem, and the seeds, model, version, date and commit read from the result files and
the manifests. The rules are in
src/remvalgate/harness/rag_ablation.py. The truth is the problem's fault type
(harness.folds.fault_type) and the target service of its known fix
(retrieval.pool_corpus.incident). No rollout runs, so no touch is logged.

A result file given twice, or found in two folders with the same run id, is read once. Refuses a
second run of one configuration on one problem, a problem outside the pool, a manifest with
uncommitted changes, and a configuration whose runs used more than one model build.

    uv run python scripts/summarize_rag_ablation.py --results <run folder>/baseline3*.result.json
"""

import argparse
import json
import sys
from itertools import combinations
from pathlib import Path

from remvalgate.harness.folds import fault_type
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.rag_ablation import CONFIGURATIONS, FIELDS, paired, score, summary
from remvalgate.retrieval.pool_corpus import incident


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _beside(path: Path, suffix: str) -> Path:
    # <run id>.result.json sits next to <run id>.json and <run id>.transcript.json
    return path.with_name(path.name.removesuffix(".result.json") + suffix)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", nargs="+", type=Path, required=True)
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = {i for i, s in registry.slices.items() if s is Slice.POOL}

    runs: dict[str, tuple[dict, dict, dict]] = {}
    for path in args.results:
        result = _read(path)
        if result["baseline"] not in CONFIGURATIONS or result["run_id"] in runs:
            continue
        runs[result["run_id"]] = (
            result,
            _read(_beside(path, ".json")),
            _read(_beside(path, ".transcript.json")),
        )

    rows: dict[str, list] = {b: [] for b in CONFIGURATIONS}
    builds: dict[str, set] = {b: set() for b in CONFIGURATIONS}
    seeds: dict[str, set] = {b: set() for b in CONFIGURATIONS}
    for result, manifest, transcript in runs.values():
        baseline, problem_id = result["baseline"], result["problem_id"]
        if problem_id not in pool:
            parser.error(f"{problem_id} is not a pool problem")
        if manifest["dirty"]:
            parser.error(f"{result['run_id']}: the manifest records uncommitted changes")
        if any(r.problem_id == problem_id for r in rows[baseline]):
            parser.error(f"{baseline}: {problem_id} has more than one run")
        true_type = fault_type(problem_id)
        rows[baseline].append(
            score(
                problem_id,
                transcript["diagnosis"],
                Outcome(result["outcome"]),
                true_type,
                incident(problem_id).target_service,
            )
        )
        seeds[baseline].add(result["seed"])
        builds[baseline].add(
            (
                manifest["model_name"],
                manifest["model_version"],
                manifest["model_date"],
                manifest["commit"],
            )
        )
    for baseline, found in builds.items():
        if len({(name, version) for name, version, _, _ in found}) > 1:
            parser.error(f"{baseline}: runs used more than one model build")

    present = [b for b in CONFIGURATIONS if rows[b]]
    # paired comparisons use the problems every configuration ran, so all pairs share one set
    shared = set.intersection(*({r.problem_id for r in rows[b]} for b in present))
    on_shared = {b: [r for r in rows[b] if r.problem_id in shared] for b in present}
    out = {
        "configurations": {
            b: {
                "retrieval": CONFIGURATIONS[b],
                "missing": sorted(pool - {r.problem_id for r in rows[b]}),
                "seeds": sorted(seeds[b]),
                "runs": [
                    dict(
                        zip(("model_name", "model_version", "model_date", "commit"), x, strict=True)
                    )
                    for x in sorted(builds[b])
                ],
                **summary(rows[b]),
            }
            for b in present
        },
        "paired_left_out": sorted(pool - shared),
        "paired": {
            f"{a} vs {b}": {field: paired(on_shared[a], on_shared[b], field) for field in FIELDS}
            for a, b in combinations(present, 2)
        },
        "problems": {},
    }
    for b in present:
        for r in rows[b]:
            # a configuration that did not run the problem stays None
            entry = out["problems"].setdefault(r.problem_id, dict.fromkeys(present))
            entry[b] = {"has_diagnosis": r.has_diagnosis, **{f: getattr(r, f) for f in FIELDS}}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
