r"""The values gate G3 commits (story E9-S2, #53), with no cluster.

Reads the oracle labels files of the pool run (scripts/run_oracle_labels.py) and the result files
of the baseline-only run (scripts/run_baselines_pool.py), each next to its manifest, and prints:
the headroom result from the labels, R from the labels' top-pick outcomes against 3c's rerun, the
minimum gain, and the problems each baseline in the result files solved. With no result files it
prints the headroom result only, which needs the labels alone, so a stop is seen before the 3c
rerun spends its touches. Refuses files that do not
cover each pool problem once, a 3c rerun at the labelling run's seed or with another model build,
and a result file read twice (src/remvalgate/harness/g3.py).

    uv run python scripts/summarize_g3.py --labels runs/oracle/<run id>.labels.json ... \
        [--results runs/baselines_pool/<run id>.result.json ...]
"""

import argparse
import json
import sys
from pathlib import Path

from remvalgate.harness.g3 import check_rerun_settings, g3, headroom, solved_by_baseline
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.oracle import read_labels
from remvalgate.harness.outcome import Outcome


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _manifest(path: Path, suffix: str) -> dict:
    # <run id>.labels.json and <run id>.result.json sit next to <run id>.json
    return _read(path.with_name(path.name.removesuffix(suffix) + ".json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", nargs="+", type=Path, required=True)
    parser.add_argument("--results", nargs="+", type=Path, default=[])
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    labels = [read_labels(_read(p)) for p in args.labels]
    if not args.results:
        print(json.dumps({"headroom": headroom(labels, pool)}, indent=2, sort_keys=True))
        return 0
    if len(set(args.results)) != len(args.results):
        parser.error("a result file is given more than once")
    rows = [_read(p) for p in args.results]
    rerun = [(p, r) for p, r in zip(args.results, rows, strict=True) if r["baseline"] == "3c"]
    if not rerun:
        parser.error("R needs the 3c rerun's result files")
    check_rerun_settings(
        [_manifest(p, ".labels.json") for p in args.labels],
        [_manifest(p, ".result.json") for p, _ in rerun],
    )
    outcomes = [(r["problem_id"], Outcome(r["outcome"])) for _, r in rerun]
    if len(dict(outcomes)) != len(outcomes):
        parser.error("3c: a problem has more than one result")
    result = g3(labels, dict(outcomes), pool)
    result["solved_by_baseline"] = solved_by_baseline(rows, pool)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
