"""Summarize baseline 5 from its held-out files and the scores files the notebook wrote (E6-S4).

Prints, as JSON, each held-out problem's ranking of its K candidates and coverage curve, and
baseline 5's top-1 result beside baseline 3c's (src/remvalgate/baselines/baseline5.py). Give one
--pair per fold. With --results, the pool result files of baselines 1, 2, 3a and 3b
(scripts/run_baselines_pool.py) are set beside baseline 5 per problem; their manifests must name
one model build, and rows at 2 seeds count a problem as recovered only if it recovered at both
(#56, #161). No cluster and no model are needed.

    uv run python scripts/summarize_baseline5.py \
        --pair heldout-baseline5-fold0-<time>.json scores-baseline5-fold0-<time>.json \
        [--results runs/baselines_pool/<run id>.result.json ...]
"""

import argparse
import json
import sys
from pathlib import Path

from remvalgate.baselines.baseline5 import summarize
from remvalgate.harness.g3 import combine_seeds
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.model_build import check_one_build


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pair", nargs=2, action="append", required=True, metavar=("HELDOUT", "SCORES")
    )
    parser.add_argument("--results", nargs="+", type=Path, default=[])
    args = parser.parse_args()
    if len(set(args.results)) != len(args.results):
        parser.error("a result file is given more than once")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    rows = [json.loads(p.read_text(encoding="utf-8")) for p in args.results]
    # the manifest beside each result file: runs served by different builds are not set side by
    # side (#161)
    manifests = [
        json.loads(p.with_name(p.name.removesuffix(".result.json") + ".json").read_text("utf-8"))
        for p in args.results
    ]
    try:
        check_one_build(manifests, "the result files")
        # 2 seeds per problem become one row per problem, by the rule of #56
        rows = combine_seeds(rows)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(summarize(args.pair, rows, pool), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
