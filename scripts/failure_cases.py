"""The failure breakdown of the planner runs (story E9-S7, #58), with no cluster.

Reads result files of scripts/run_baselines_pool.py, each next to its manifest and transcript, and
prints per arm the class of every run, the allow-list reasons and the diagnosis classes
(src/remvalgate/harness/failures.py), then one line per pool problem with each arm's classes at
each seed, so the problems where arms disagree can be read by hand.

    uv run python scripts/failure_cases.py runs/baselines_pool/<run id>.result.json ...
"""

import argparse
import json
import sys
from pathlib import Path

from remvalgate.harness.failures import breakdown, classify, diagnosis_class
from remvalgate.harness.folds import fault_type
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.outcome import Outcome
from remvalgate.retrieval.pool_corpus import incident


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="+", type=Path)
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = {i for i, s in registry.slices.items() if s is Slice.POOL}
    cases = []
    for path in args.results:
        row = json.loads(path.read_text("utf-8"))
        problem_id = row["problem_id"]
        if problem_id not in pool:
            parser.error(f"{problem_id} is not a pool problem")
        transcript_path = path.with_name(path.name.replace(".result.json", ".transcript.json"))
        transcript = json.loads(transcript_path.read_text("utf-8"))
        has_diagnosis = "diagnosis" in transcript
        cases.append(
            {
                "arm": row["baseline"],
                "problem_id": problem_id,
                "seed": row["seed"],
                "run_id": row["run_id"],
                "class": classify(row),
                "reason": row.get("allow_list"),
                "diagnosis": diagnosis_class(
                    problem_id,
                    transcript,
                    Outcome(row["outcome"]),
                    fault_type(problem_id),
                    incident(problem_id).target_service,
                )
                if has_diagnosis
                else "no planner",
            }
        )
    seen = [(c["arm"], c["problem_id"], c["seed"]) for c in cases]
    if len(set(seen)) != len(seen):
        parser.error("a run of one arm, problem and seed is given twice")
    per_problem = {}
    for c in sorted(cases, key=lambda c: (c["problem_id"], c["arm"], c["seed"])):
        per_problem.setdefault(c["problem_id"], {}).setdefault(c["arm"], []).append(
            {"seed": c["seed"], "class": c["class"], "reason": c["reason"], "run_id": c["run_id"]}
        )
    print(json.dumps({"arms": breakdown(cases), "per_problem": per_problem}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
