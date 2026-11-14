"""Baseline 6 on the pool (#44, #55, BRD R6.5): the prompted judge ranks labelled candidates.

Reads the labelling runs of one planner, each given as its manifest with the transcript and labels
beside it (scripts/run_oracle_labels.py), and for each pool problem has the served model score the
admitted candidates at temperature 0 (src/remvalgate/baselines/judge_pool.py). The pick's outcome
is read from the labels, so nothing runs on a cluster; it needs only the model server (default this
host's LM Studio). The judge is the planner's model, so judge and planner share a model family and
every result carries that flag.

Each pool problem whose run sampled candidates is a touch: start_run logs a baseline6- run before
its transcript is read, so it needs gate G1 and a clean checkout. A run that sampled nothing is
not read and logs no touch; it counts as escalated and not recovered. A manifest and a
.baseline6.json result per problem, and a summary, go to runs/baseline6/, which git ignores.

    uv run python scripts/score_baseline6.py --model-version "<format and quantization>" \
        runs/oracle/<run id>.json ...
"""

import argparse
import dataclasses
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.baselines.judge import JudgeModel
from remvalgate.baselines.judge_pool import THRESHOLD, score_problem, total
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    RunRefused,
    Slice,
    load_registry,
    start_run,
    write_manifest,
)
from remvalgate.harness.model_build import build_for
from remvalgate.harness.no_check import check_transcript, read_transcript, sources
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
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    try:
        found = sources(args.manifests, registry)
    except ValueError as error:
        raise SystemExit(f"refused: {error}") from error
    # read before any touch, so a model that is not loaded stops with no touch (#161)
    build = build_for(args.model, args.base_url, None, None, os.environ.get(API_KEY_ENV))
    # the judge settings of scripts/run_planner_dev.py --judge: thinking on, temperature 0
    chat = OpenAIChat(
        model=args.model, base_url=args.base_url, thinking=True, temperature=0.0, max_tokens=4096
    )
    judge_model = JudgeModel(args.model, args.model_version, FAMILY, FAMILY)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    records = []
    try:
        for source, transcript_path in found:
            if transcript_path is None:
                # sampled nothing: no transcript is read and no touch logged
                records.append(
                    {
                        "problem_id": source.problem_id,
                        "scored": 0,
                        "pick_seed": None,
                        "escalated": True,
                        "solved": False,
                        "source_run_id": source.run_id,
                    }
                )
                print(f"{source.problem_id}: no candidates, escalated", flush=True)
                continue
            now = datetime.now(UTC)
            run_id = f"baseline6-{source.problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}".lower()
            namespace = source.cluster["namespace"]
            run = start_run(
                run_id,
                source.problem_id,
                registry,
                TOUCH_LOG,
                ROOT,
                pinned_commit(ROOT),
                now,
                namespace=namespace,
            )
            transcript = read_transcript(transcript_path)
            check_transcript(transcript, transcript_path)
            labels_path = transcript_path.with_name(
                transcript_path.name.replace(".transcript.json", ".labels.json")
            )
            labels = json.loads(labels_path.read_text(encoding="utf-8"))
            scores, record = score_problem(transcript, labels, namespace, chat=chat)
            manifest = dataclasses.replace(
                source,
                run_id=run_id,
                slice=run.slice,
                commit=run.commit,
                dirty=run.dirty,
                aiopslab_commit=run.aiopslab_commit,
                timestamp=run.timestamp,
                model_build=build,
                sampling={
                    "source_run_id": source.run_id,
                    "judge": dataclasses.asdict(judge_model)
                    | {"shared_family": judge_model.shared_family},
                    "temperature": chat.temperature,
                    "thinking": chat.thinking,
                    "max_tokens": chat.max_tokens,
                    "seed": chat.seed,
                    "threshold": THRESHOLD,
                },
            )
            path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
            record |= {"run_id": run_id, "source_run_id": source.run_id}
            out = {"record": record, "scores": [dataclasses.asdict(s) for s in scores]}
            path.with_suffix(".baseline6.json").write_text(json.dumps(out, indent=2))
            print(
                f"{source.problem_id}: scored {record['scored']}, pick {record['pick_seed']}, "
                f"solved {record['solved']}",
                flush=True,
            )
            records.append(record)
    except (ValueError, RunRefused) as error:
        raise SystemExit(f"refused: {error}") from error
    summary = {
        "judge": dataclasses.asdict(judge_model) | {"shared_family": judge_model.shared_family},
        "model_build": build,
        "threshold": THRESHOLD,
        # a pool run must cover the pool; a development dry run covers what it was given
        "total": total(
            records, pool if found[0][0].slice == "pool" else [r["problem_id"] for r in records]
        ),
        "records": records,
    }
    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%S%fz")
    out = OUT_DIR / f"baseline6-summary-{stamp}.json"
    with out.open("x", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)
    t = summary["total"]
    print(f"baseline 6: solved {t['solved']} of {t['problems']}, escalated {t['escalated']}")
    print(f"summary {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
