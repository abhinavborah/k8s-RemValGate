"""Export the held-out scoring input of baseline 5, one file per fold (story E6-S4).

Reads the 16 labels files of the pool run, the same files the training data export reads
(scripts/export_training_data.py), each with its manifest and transcript beside it, and writes
per fold f runs/baseline5/heldout-baseline5-fold<f>-<UTC time>.json: per held-out problem the
sampling messages and the K candidates' replies and labels (src/remvalgate/baselines/baseline5.py).
Reading pool labels is an evaluation-set touch: one touch per pool problem is logged first, so
it needs a clean checkout, the pinned AIOpsLab commit and the sealed confirmation slice. No
cluster and no model are needed.

    uv run python scripts/export_baseline5_heldout.py runs/oracle/<run id>.labels.json ...
"""

import argparse
import sys
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.baselines.baseline5 import export_heldout
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "baseline5"
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="+", help="the pool's <run id>.labels.json files")
    args = parser.parse_args()
    written = export_heldout(
        args.labels,
        OUT_DIR,
        registry=load_registry(DEFAULT_REGISTRY_PATH),
        log_path=TOUCH_LOG,
        repo=ROOT,
        aiopslab_commit=pinned_commit(ROOT),
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
