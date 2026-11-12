"""Summarize baseline 5 from its held-out files and the scores files the notebook wrote (E6-S4).

Prints, as JSON, each held-out problem's ranking of its K candidates and coverage curve, and
baseline 5's top-1 result beside baseline 3c's (src/remvalgate/baselines/baseline5.py). Give one
--pair per fold. No cluster and no model are needed.

    uv run python scripts/summarize_baseline5.py \
        --pair runs/baseline5/heldout-baseline5-fold0-<time>.json scores-baseline5-fold0-<time>.json
"""

import argparse
import json
import sys

from remvalgate.baselines.baseline5 import summarize


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pair", nargs=2, action="append", required=True, metavar=("HELDOUT", "SCORES")
    )
    args = parser.parse_args()
    print(json.dumps(summarize(args.pair), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
