"""Hand run for story E4-S3: the intrinsic retrieval metrics on the fixed pool queries (R4.3).

Reads the query files of scripts/record_pool_queries.py, which git ignores and which live on the
shared drive, from runs/queries/. Each of the 16 pool problems is one pool run: start_run appends a
touch to runs/touch_log.jsonl and refuses it unless the confirmation slice is sealed (gate G1) and
the tree is clean, so the run needs a clean checkout. The queries and the models are checked before
the first touch. No cluster is used and no planner model runs.

Outputs go to runs/intrinsic/, which git ignores, and to the shared drive:

    <run id>.json                  the run manifest of one problem
    intrinsic-<stamp>.json         metrics, saturation, per-query ranks and query lengths

    uv sync --group retrieval
    uv run python scripts/run_intrinsic_retrieval.py
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import sentence_transformers
import torch
import transformers

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    load_registry,
    logged_namespace,
    start_run,
    write_manifest,
)
from remvalgate.retrieval.configs import DENSE_MODEL, RERANKER_MODEL, load_models
from remvalgate.retrieval.intrinsic import evaluate, query_lengths, read_queries
from remvalgate.retrieval.pool_corpus import pool_incidents

ROOT = Path(__file__).resolve().parents[1]
QUERY_DIR = ROOT / "runs" / "queries"
OUT_DIR = ROOT / "runs" / "intrinsic"
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"


def tokenizers() -> dict:
    return {
        name: transformers.AutoTokenizer.from_pretrained(name, revision=revision)
        for name, revision in (DENSE_MODEL, RERANKER_MODEL)
    }


def lengths(queries: dict[str, str], services: dict[str, str], loaded: dict) -> dict:
    return {
        name: query_lengths(
            queries,
            services,
            lambda text, t=tokenizer: t(text, add_special_tokens=False).input_ids,
            lambda ids, t=tokenizer: t.decode(ids),
        )
        for name, tokenizer in loaded.items()
    }


def main() -> int:
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    records = pool_incidents(registry)
    files = sorted(QUERY_DIR.glob("pool_queries-*.jsonl"))
    queries = read_queries(files)
    if queries.keys() != {r.entry_id for r in records}:
        print(f"FAIL: {QUERY_DIR} does not hold exactly one query per pool problem")
        return 1
    models = load_models(device="cpu")
    loaded = tokenizers()
    services = {r.entry_id: r.target_service for r in records}

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dt%H%M%Sz")
    aiopslab = pinned_commit(ROOT)
    runs = {}
    for problem_id in queries:
        run_id = f"intrinsic-{problem_id}-{stamp}"
        namespace = logged_namespace(problem_id)
        runs[problem_id] = (
            run_id,
            start_run(
                run_id, problem_id, registry, TOUCH_LOG, ROOT, aiopslab, now, namespace=namespace
            ),
        )

    report = evaluate(queries, registry, models)
    token_report = lengths(queries, services, loaded)
    for problem_id, (run_id, run) in runs.items():
        manifest = Manifest(
            run_id=run_id,
            problem_id=problem_id,
            slice=run.slice,
            commit=run.commit,
            dirty=run.dirty,
            model_name=f"{DENSE_MODEL[0]}+{RERANKER_MODEL[0]}",
            model_version=f"{DENSE_MODEL[1]}+{RERANKER_MODEL[1]}",
            model_date=now.strftime("%Y-%m-%d"),
            seed=0,
            k=3,
            r=1,
            aiopslab_commit=run.aiopslab_commit,
            # no cluster is used; the namespace is the problem's own, as a logged run must name
            cluster={"context": None, "namespace": run.namespace, "nodes": 0},
            timestamp=run.timestamp,
        )
        write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)

    output = {
        "commit": next(iter(runs.values()))[1].commit,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "models": {"dense": list(DENSE_MODEL), "reranker": list(RERANKER_MODEL)},
        "versions": {
            "torch": torch.__version__,
            "sentence_transformers": sentence_transformers.__version__,
            "transformers": transformers.__version__,
        },
        "query_files": [f.name for f in files],
        **report,
        "query_lengths": token_report,
    }
    path = OUT_DIR / f"intrinsic-{stamp}.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")

    for config, by_label in report["configurations"].items():
        for label, by_group in by_label.items():
            for group, m in by_group.items():
                recall = m["recall_at_3"]
                print(
                    f"({config}) {label} {group}: "
                    f"Hit@1 {m['hit_at_1']['count']}/{m['queries']} "
                    f"Hit@3 {m['hit_at_3']['count']}/{m['queries']} "
                    f"MRR {m['mrr']:.3f} "
                    f"Recall@3 {'n/a' if recall is None else round(recall['rate'], 3)}"
                )
    print(f"saturation {report['saturation']}")
    print(f"report {path}")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
