"""Baseline 6 on the pool (#44, #55, BRD R6.5): the prompted judge ranks labelled candidates.

Reads the labelling runs of baseline 3c, each given as its manifest with the transcript and labels
beside it (scripts/run_oracle_labels.py), and for each pool problem has the served model score the
admitted candidates at temperature 0 (src/remvalgate/baselines/judge_pool.py). The pick's outcome
is read from the labels, so nothing runs on a cluster; it needs only the model server (default this
host's LM Studio). The judge must be the model that wrote the candidates, so judge and planner
share a model family and every result carries that flag.

Each pool problem whose run sampled candidates is a touch: a baseline6- run is logged before its
transcript and labels are read, so it needs gate G1 and a clean checkout. The model, the whole pool
and the labels files are checked before any touch. A run that sampled nothing is not read and logs
no touch; it counts as escalated and not recovered. A manifest and a .baseline6.json result per
problem, and a summary, go to runs/baseline6/, which git ignores.

    uv run python scripts/score_baseline6.py --model-version "<format and quantization>" \
        runs/oracle/<run id>.json ...
"""

import argparse
import os
import sys
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.baselines.judge_pool import score_runs
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, RunRefused, load_registry
from remvalgate.harness.model_build import build_for
from remvalgate.planner.chat import API_KEY_ENV, OpenAIChat
from remvalgate.planner.models import add_model_argument

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "baseline6"
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
# the planner's family; the judge is the same served model, so the two are shared by design
FAMILY = "qwen3.5"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifests", nargs="+", type=Path, help="labelling run manifests")
    add_model_argument(parser)
    parser.add_argument("--model-version", required=True, help="format and quantization served")
    parser.add_argument("--base-url", default=OpenAIChat.base_url, help="the server's /v1 URL")
    args = parser.parse_args()
    # the judge settings of scripts/run_planner_dev.py --judge: thinking on, temperature 0
    chat = OpenAIChat(
        model=args.model, base_url=args.base_url, thinking=True, temperature=0.0, max_tokens=4096
    )
    try:
        # read before any touch, so a model that is not loaded stops with no touch (#161)
        build = build_for(args.model, args.base_url, None, None, os.environ.get(API_KEY_ENV))
        summary, out = score_runs(
            args.manifests,
            registry=load_registry(DEFAULT_REGISTRY_PATH),
            chat=chat,
            model=args.model,
            model_version=args.model_version,
            family=FAMILY,
            build=build,
            touch_log=TOUCH_LOG,
            repo=ROOT,
            aiopslab_commit=pinned_commit(ROOT),
            out_dir=OUT_DIR,
        )
    except (ValueError, RunRefused) as error:
        raise SystemExit(f"refused: {error}") from error
    for r in summary["records"]:
        print(
            f"{r['problem_id']}: scored {r['scored']}, pick {r['pick_seed']}, solved {r['solved']}"
        )
    t = summary["total"]
    print(f"baseline 6: solved {t['solved']} of {t['problems']}, escalated {t['escalated']}")
    print(f"summary {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
