"""Export the per-fold training data of the gate and of baseline 5 from the oracle labels (E7-S1).

Reads the labels files of the oracle labelling run (scripts/run_oracle_labels.py), one per pool
problem, each with its manifest and transcript beside it, and writes per fold f two files to
runs/training_data/ (git ignores runs/, so the tree stays clean for the touches):

    training-gate-fold<f>-<UTC time>.json       the gate records of fold f
    training-baseline5-fold<f>-<UTC time>.json  the baseline 5 records of fold f

The rules and the format are in docs/training_data.md (src/remvalgate/harness/training_data.py).
Reading pool labels is an evaluation-set touch: before any file is read the script appends one
touch per fold and training problem through start_run, so it needs a clean checkout, the pinned
AIOpsLab commit and the sealed confirmation slice. No cluster and no model are needed.

Lengths are counted with the tokenizer saved at --tokenizer (loaded from local files only, never
downloaded), or else with the tokenizer-free length proxy.

    uv run python scripts/export_training_data.py [--tokenizer DIR] \
        runs/oracle/<run id>.labels.json ...
"""

import argparse
import sys
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, load_registry
from remvalgate.harness.training_data import export, length_proxy
from remvalgate.retrieval.pool_corpus import pool_incidents

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "training_data"
# the touch log of every run script (scripts/run_dev_end_to_end.py)
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("labels", nargs="+", help="the pool's <run id>.labels.json files")
    parser.add_argument("--tokenizer", help="a local tokenizer folder, for exact token counts")
    args = parser.parse_args()
    count, counter = length_proxy, "length_proxy"
    if args.tokenizer:
        from transformers import AutoTokenizer

        tokenizer = AutoTokenizer.from_pretrained(args.tokenizer, local_files_only=True)
        count = lambda text: len(tokenizer(text, add_special_tokens=False)["input_ids"])  # noqa: E731
        counter = f"tokenizer {Path(args.tokenizer).name}"
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    written = export(
        args.labels,
        OUT_DIR,
        registry=registry,
        log_path=TOUCH_LOG,
        repo=ROOT,
        aiopslab_commit=pinned_commit(ROOT),
        recorded_fixes={e.entry_id: e.fix_command for e in pool_incidents(registry)},
        recorded_summaries={e.entry_id: e.summary for e in pool_incidents(registry)},
        policy=load_policy(DEFAULT_POLICY_PATH),
        count=count,
        counter=counter,
    )
    for path in written:
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
