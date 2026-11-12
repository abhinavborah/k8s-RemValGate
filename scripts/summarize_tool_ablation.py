r"""The ablation without interactive tools (story E5-S3, #38, BRD R5.3), with no cluster and no
model.

Reads the result files of baseline 3c from the baseline-only run (#53) and of the arm notools
(scripts/run_baselines_pool.py --baseline notools), each next to its transcript and its manifest.
Prints for each arm the diagnosis accuracy, the fault-type accuracy and the single-action recovery
over the problems it ran, the totals of its run diagnostics, the seeds, model, version, date and
commit read from the result files and the manifests; the paired comparison of the two arms on the
problems both ran; and one row per problem with its diagnostics. The scoring rules are those of
the RAG ablation (src/remvalgate/harness/rag_ablation.py), and the diagnostics are in
src/remvalgate/harness/tool_ablation.py. The truth is the problem's fault type
(harness.folds.fault_type) and the target service of its known fix
(retrieval.pool_corpus.incident). No rollout runs, so no touch is logged, as in
scripts/summarize_rag_ablation.py: the touches were logged when the runs ran.

A result file given twice, or found in two folders with the same run id, is read once, and the
result files of any other baseline are skipped. Refuses a problem outside the pool, a manifest
with uncommitted changes, a second run of one arm on one problem, an arm with no run, and an arm
whose runs used more than one model build. The comparison is paired on one seed and one model
build, so it also refuses runs of the two arms at more than one seed or with more than one model
build between them.

    uv run python scripts/summarize_tool_ablation.py \
        --results <run folder>/baseline3c-*.result.json <run folder>/notools-*.result.json
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from remvalgate.harness.folds import fault_type
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.rag_ablation import FIELDS, paired, score, summary
from remvalgate.harness.tool_ablation import diagnostics
from remvalgate.retrieval.pool_corpus import incident

ARMS = {"3c": "interactive tools", "notools": "fixed telemetry bundle"}


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _beside(path: Path, suffix: str) -> Path:
    # <run id>.result.json sits next to <run id>.json and <run id>.transcript.json
    return path.with_name(path.name.removesuffix(".result.json") + suffix)


def _totals(found: list[dict]) -> dict:
    """An arm's diagnostics summed over its problems, with its largest prompt and bundle."""
    calls = Counter()
    for d in found:
        calls.update(d["tool_calls"])
    prompts = [d["max_prompt_tokens"] for d in found if d["max_prompt_tokens"] is not None]
    return {
        **{k: sum(d[k] for d in found) for k in ("turns", "refused_turns", "completion_tokens")},
        # rounded again, since a sum of rounded floats carries float noise
        "model_seconds": round(sum(d["model_seconds"] for d in found), 1),
        "tool_calls": dict(sorted(calls.items())),
        "largest_prompt_tokens": max(prompts, default=None),
        "largest_bundle_chars": max(d["bundle_chars"] for d in found),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", nargs="+", type=Path, required=True)
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = {i for i, s in registry.slices.items() if s is Slice.POOL}

    runs: dict[str, tuple[dict, dict, dict]] = {}
    for path in args.results:
        result = _read(path)
        if result["baseline"] not in ARMS or result["run_id"] in runs:
            continue
        runs[result["run_id"]] = (
            result,
            _read(_beside(path, ".json")),
            _read(_beside(path, ".transcript.json")),
        )

    rows: dict[str, list] = {a: [] for a in ARMS}
    # each arm's diagnostics by problem id
    found: dict[str, dict[str, dict]] = {a: {} for a in ARMS}
    builds: dict[str, set] = {a: set() for a in ARMS}
    seeds: dict[str, set] = {a: set() for a in ARMS}
    for result, manifest, transcript in runs.values():
        arm, problem_id = result["baseline"], result["problem_id"]
        if problem_id not in pool:
            parser.error(f"{problem_id} is not a pool problem")
        if manifest["dirty"]:
            parser.error(f"{result['run_id']}: the manifest records uncommitted changes")
        if problem_id in found[arm]:
            parser.error(f"{arm}: {problem_id} has more than one run")
        true_type = fault_type(problem_id)
        rows[arm].append(
            score(
                problem_id,
                transcript["diagnosis"],
                Outcome(result["outcome"]),
                true_type,
                incident(problem_id).target_service,
            )
        )
        found[arm][problem_id] = diagnostics(transcript)
        seeds[arm].add(result["seed"])
        builds[arm].add(
            (
                manifest["model_name"],
                manifest["model_version"],
                manifest["model_date"],
                manifest["commit"],
            )
        )
    models = {a: {(name, version) for name, version, _, _ in builds[a]} for a in ARMS}
    for arm in ARMS:
        if not rows[arm]:
            parser.error(f"{arm}: no result file was given")
        if len(models[arm]) > 1:
            parser.error(f"{arm}: runs used more than one model build")
    # the comparison is paired on one seed and one model build
    for what, by_arm in (("seed", seeds), ("model build", models)):
        if len(set().union(*by_arm.values())) > 1:
            got = ", ".join(f"{a} {sorted(by_arm[a])}" for a in ARMS)
            parser.error(f"the arms need one {what} between them, got {got}")

    shared = found["3c"].keys() & found["notools"].keys()
    out = {
        "arms": {
            a: {
                "label": ARMS[a],
                "missing": sorted(pool.difference(found[a])),
                "seeds": sorted(seeds[a]),
                "runs": [
                    dict(
                        zip(("model_name", "model_version", "model_date", "commit"), x, strict=True)
                    )
                    for x in sorted(builds[a])
                ],
                **summary(rows[a]),
                "diagnostics": _totals(list(found[a].values())),
            }
            for a in ARMS
        },
        "paired_left_out": sorted(pool - shared),
        # paired compares the problems both arms ran
        "paired": {
            "3c vs notools": {f: paired(rows["3c"], rows["notools"], f) for f in FIELDS},
        },
        "problems": {},
    }
    for a in ARMS:
        for r in rows[a]:
            # an arm that did not run the problem stays None
            entry = out["problems"].setdefault(r.problem_id, dict.fromkeys(ARMS))
            entry[a] = {
                "has_diagnosis": r.has_diagnosis,
                **{f: getattr(r, f) for f in FIELDS},
                "diagnostics": found[a][r.problem_id],
            }
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
