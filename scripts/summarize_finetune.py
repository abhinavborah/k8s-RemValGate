"""The primary analysis of the fine-tuning ablation (story E9-S5, #56), with no cluster.

Reads the result files of the 3c control and the 3cft arm (scripts/run_baselines_pool.py, #165
step 3), each next to its manifest, and prints the paired comparison per seed and with the seeds
combined by the rule of #56, and whether the claim is supported or falsified at the minimum gain
of gate G3 on the combined counts (src/remvalgate/harness/finetune.py). Refuses a 3cft run served
by another fold's model and a control served by a fold's model.

With --labels (the untuned oracle labels, runs/oracle/) and --ft-labels (the fine-tuned
planner's, runs/oracle_3cft/), it adds the candidates of both planners labelled by execution. With
--baseline5 (the output of scripts/summarize_baseline5.py) it adds baseline 5's top-1 total over
the untuned candidates.

    uv run python scripts/summarize_finetune.py --results runs/baselines_pool/<run id>.result.json \
        ... [--labels runs/oracle/<run id>.labels.json ...] \
        [--ft-labels runs/oracle_3cft/<run id>.labels.json ...] [--baseline5 <summary>.json]
"""

import argparse
import json
import sys
from pathlib import Path

from remvalgate.harness.finetune import candidate_yield, check_folds, compare
from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.oracle import read_labels


def _manifest(path: Path) -> dict:
    return json.loads(
        path.with_name(path.name.removesuffix(".result.json") + ".json").read_text("utf-8")
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", nargs="+", type=Path, required=True)
    parser.add_argument("--labels", nargs="*", type=Path, default=[])
    parser.add_argument("--ft-labels", nargs="*", type=Path, default=[])
    parser.add_argument("--baseline5", type=Path)
    args = parser.parse_args()
    if bool(args.labels) != bool(args.ft_labels):
        parser.error("--labels and --ft-labels go together")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    rows = [json.loads(p.read_text("utf-8")) for p in args.results]
    try:
        check_folds([_manifest(p) for p in args.results], pool_folds(registry))
        out = compare(rows, pool)
        if args.labels:
            read = [read_labels(json.loads(p.read_text("utf-8"))) for p in args.labels]
            ft = [read_labels(json.loads(p.read_text("utf-8"))) for p in args.ft_labels]
            out["candidates"] = candidate_yield(read, ft, pool)
    except ValueError as e:
        sys.exit(str(e))
    if args.baseline5:
        b5 = json.loads(args.baseline5.read_text("utf-8"))
        out["baseline5"] = {"solved": b5["baseline5_solved"], "baseline3c": b5["baseline3c_solved"]}
    print(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
