r"""Hand run for #164: which form of the retrieval query configuration (iii) should use.

Reads the recorded pool queries of scripts/record_pool_queries.py from runs/queries/ (git ignores
them; they live on the shared drive) and the first observations of past pool runs from the
transcripts given with --transcripts. For each form in src/remvalgate/retrieval/query_form.py it
reports:
- quality: the intrinsic metrics of #32 on the recorded queries in that form;
- stability: over every past run of a pool problem, whether configuration (iii) retrieves the same
  top 3 for that run's first observation as for the problem's recorded query, both in that form.

The decision rule, fixed in #164 before this ran: keep the forms whose Hit@3 (fault-type label, all
16 queries) is at most 1 query below the raw form's; among those, pick the most stable; on a tie,
keep the form that changes the query least, in FORMS order.

Each of the 16 pool problems is one pool run: start_run appends a touch to runs/touch_log.jsonl and
refuses it unless the confirmation slice is sealed and the tree is clean. No cluster and no planner
model is used.

    uv run python scripts/compare_query_forms.py --transcripts <run folder>/*.transcript.json
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    load_registry,
    logged_namespace,
    start_run,
    write_manifest,
)
from remvalgate.retrieval.configs import DENSE_MODEL, RERANKER_MODEL, load_models
from remvalgate.retrieval.corpus import build_corpus
from remvalgate.retrieval.fixed import configuration_iii
from remvalgate.retrieval.intrinsic import evaluate, read_queries
from remvalgate.retrieval.pool_corpus import RUNBOOK, pool_incidents
from remvalgate.retrieval.query_form import FORMS, MARGIN, choose, query_in_form

ROOT = Path(__file__).resolve().parents[1]
QUERY_DIR = ROOT / "runs" / "queries"
OUT_DIR = ROOT / "runs" / "query_forms"
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
# the primary metric of #32
LABEL, GROUP = "fault_type", "all"


def past_observations(paths: list[Path], pool: set[str]) -> list[tuple[str, str, str]]:
    """(run id, problem id, first observation) of every pool planner run among the transcripts.

    The problem id is read from the manifest beside each transcript; a run whose first
    observation is missing is left out.
    """
    found = {}
    for path in paths:
        manifest = path.with_name(path.name.removesuffix(".transcript.json") + ".json")
        if not manifest.exists():
            continue
        meta = json.loads(manifest.read_text(encoding="utf-8"))
        first = json.loads(path.read_text(encoding="utf-8")).get("first_observation")
        if meta["problem_id"] in pool and isinstance(first, str) and first:
            found[meta["run_id"]] = (meta["run_id"], meta["problem_id"], first)
    return sorted(found.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--transcripts", nargs="+", type=Path, required=True)
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    records = pool_incidents(registry)
    queries = read_queries(sorted(QUERY_DIR.glob("pool_queries-*.jsonl")))
    if queries.keys() != {r.entry_id for r in records}:
        print(f"FAIL: {QUERY_DIR} does not hold exactly one query per pool problem")
        return 1
    past = past_observations(args.transcripts, set(queries))
    if not past:
        print("FAIL: no first observation of a pool run among the transcripts")
        return 1
    models = load_models(device="cpu")

    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%dt%H%M%Sz")
    aiopslab = pinned_commit(ROOT)
    runs = {}
    for problem_id in queries:
        run_id = f"queryform-{problem_id}-{stamp}"
        namespace = logged_namespace(problem_id)
        runs[problem_id] = (
            run_id,
            start_run(
                run_id, problem_id, registry, TOUCH_LOG, ROOT, aiopslab, now, namespace=namespace
            ),
        )

    assignment = pool_folds(registry)
    corpora = {
        fold: build_corpus(fold, assignment=assignment, incidents=records, runbook=RUNBOOK)
        for fold in sorted(set(assignment.values()))
    }
    retriever = configuration_iii(assignment, corpora, models=models)

    def top3(text: str, problem_id: str) -> list[str]:
        return [h.entry.entry_id for h in retriever.retrieve(text, problem_id, 3)]

    report = {}
    for form in FORMS:
        shaped = {p: query_in_form(q, form) for p, q in queries.items()}
        metrics = evaluate(shaped, registry, models, form="raw")["configurations"]["iii"]
        recorded = {p: top3(q, p) for p, q in shaped.items()}
        same = [
            (
                run_id,
                problem_id,
                top3(query_in_form(first, form), problem_id) == recorded[problem_id],
            )
            for run_id, problem_id, first in past
        ]
        report[form] = {
            "hit_at_3": metrics[LABEL][GROUP]["hit_at_3"]["count"],
            "metrics": metrics,
            "stable_runs": sum(s for _, _, s in same),
            "past_runs": len(same),
            "stability": sum(s for _, _, s in same) / len(same),
            "unstable": sorted(f"{p} {r}" for r, p, s in same if not s),
        }
    chosen = choose(
        {f: r["hit_at_3"] for f, r in report.items()},
        {f: r["stability"] for f, r in report.items()},
    )

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
            cluster={"context": None, "namespace": run.namespace, "nodes": 0},
            timestamp=run.timestamp,
        )
        write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)

    output = {
        "commit": next(iter(runs.values()))[1].commit,
        "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "rule": {"label": LABEL, "group": GROUP, "margin": MARGIN},
        "forms": report,
        "chosen": chosen,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"query_forms-{stamp}.json"
    path.write_text(json.dumps(output, indent=2, sort_keys=True), encoding="utf-8")
    for form, r in report.items():
        print(
            f"{form}: Hit@3 {r['hit_at_3']}/16, stable {r['stable_runs']}/{r['past_runs']} "
            f"past runs ({r['stability']:.3f})"
        )
    print(f"chosen: {chosen}\nwritten: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
