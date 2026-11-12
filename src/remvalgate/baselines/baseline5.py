"""Baseline 5: the planner LoRA-fine-tuned per fold, ranking its own candidates by log-probability
(story E6-S4, BRD R6.4).

The parts that need no GPU, so they run and are tested anywhere:

- export_heldout writes, per fold f, the scoring input of fold f's held-out problems: per problem
  the sampling messages the planner saw and its K candidates' replies, rebuilt from the oracle
  run's transcript. A held-out problem retrieved from the corpus of its own fold, which leaves
  out every fold-f problem, so its prompt needs no mask. Reading pool labels is an evaluation-set
  touch, so one touch per problem is logged before any file is read.
- summarize turns the scores the GPU notebook wrote (scripts/baseline5_kaggle.py) into each held-out
  problem's ranking of its K candidates, its coverage curve, and baseline 5's top-1 pick beside
  baseline 3c's.

Training and scoring run in the notebook. The rules are in docs/baseline5.md.
"""

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from remvalgate.harness.folds import pool_folds
from remvalgate.harness.g3 import solved_by_baseline, top_pick_solved
from remvalgate.harness.manifest import Registry, logged_namespace, start_run
from remvalgate.harness.metrics import aurc, harmful_rate_at_coverage, top1_accuracy
from remvalgate.harness.oracle import read_labels
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.training_data import (
    Run,
    check_run,
    check_setup,
    labels_by_problem,
    read_run,
    sampling_messages,
    sha256_json,
)

HELDOUT_FORMAT = "remvalgate-baseline5-heldout/1"
SCORES_FORMAT = "remvalgate-baseline5-scores/1"
_TIMESTAMP = "%Y-%m-%dT%H:%M:%SZ"


def heldout_problem(run: Run) -> dict:
    """One held-out problem's scoring input: the sampling messages, the K candidates in draw
    order with their replies and labels, and whether baseline 3c's top pick solved it.

    Every candidate is kept, duplicates and candidates with no outcome included; one with no
    outcome does not work. A plan with no diagnosis sampled nothing and has no candidates.
    """
    check_run(run)
    labels = read_labels(run.labels)
    transcript = run.transcript
    replies = {c["seed"]: c["reply"] for c in transcript["candidates"]}
    messages = None
    if transcript["diagnosis"] is not None:
        namespace = run.manifest["cluster"]["namespace"]
        messages = sampling_messages(transcript["context"], transcript["diagnosis"], namespace)
    return {
        "problem_id": run.problem_id,
        "run_id": run.run_id,
        "baseline3c_solved": top_pick_solved(labels),
        "messages": messages,
        "candidates": [
            {
                "id": f"{run.problem_id}/{label.seed}",
                "seed": label.seed,
                "command": label.command,
                "outcome": None if label.outcome is None else str(label.outcome),
                "works": label.worked,
                "completion": replies[label.seed],
            }
            for label in labels.candidates
        ],
    }


def export_heldout(
    labels_paths: Sequence[str | Path],
    out_dir: str | Path,
    *,
    registry: Registry,
    log_path: str | Path,
    repo: str | Path,
    aiopslab_commit: str | None,
    now: datetime | None = None,
    checksum=None,
) -> list[Path]:
    """Write heldout-baseline5-fold<f>-<UTC time>.json for every fold and return the paths.

    The labels files are those of the training data export (training_data.export), each with
    its manifest and transcript beside it. Before any file is read the ids are checked from the
    file names and one touch per pool problem is appended through start_run, with run id
    b5-heldout-<fold>-<problem id>-<UTC time>. Every run is then read and the runs must share
    one setup (training_data.check_setup), as in the training data export, before any fold is
    written. An existing output is never overwritten.
    """
    now = (now or datetime.now(UTC)).astimezone(UTC)
    by_problem = labels_by_problem(labels_paths, registry)
    assignment = pool_folds(registry)
    stamp = now.strftime("%Y%m%dt%H%M%Sz")
    touches, start = {}, None
    for pid, fold in sorted(assignment.items()):
        run_id = f"b5-heldout-{fold}-{pid}-{stamp}".lower()
        start = start_run(
            run_id,
            pid,
            registry,
            log_path,
            repo,
            aiopslab_commit,
            now,
            checksum=checksum,
            namespace=logged_namespace(pid),
        )
        touches[pid] = run_id
    read = {p: read_run(path, p) for p, path in sorted(by_problem.items())}
    check_setup([run for run, _ in read.values()])
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for fold in sorted(set(assignment.values())):
        held = sorted(p for p, f in assignment.items() if f == fold)
        runs = [read[p] for p in held]
        body = {
            "format": HELDOUT_FORMAT,
            "fold": fold,
            "created": now.strftime(_TIMESTAMP),
            "commit": start.commit,
            "dirty": start.dirty,
            "aiopslab_commit": start.aiopslab_commit,
            "fold_assignment": dict(sorted(assignment.items())),
            "fold_assignment_sha256": sha256_json(assignment),
            "labels_files": {p: record for p, (_, record) in zip(held, runs, strict=True)},
            "touches": [touches[p] for p in held],
            "problems": [heldout_problem(run) for run, _ in runs],
        }
        path = out_dir / f"heldout-baseline5-fold{fold}-{stamp}.json"
        with path.open("x", encoding="utf-8", newline="\n") as file:
            file.write(json.dumps(body, indent=1, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def problem_result(problem: Mapping, scores: Mapping[str, float]) -> dict:
    """One held-out problem's ranking, coverage curve and top-1 result.

    The ranking orders the K candidates by log-probability, highest first, ties in draw order.
    The coverage curve gives, for m = 1..K, the expected share of the top m that do not work
    (harmful_rate_at_coverage at coverage m/K, ties at the cut shared). top1 is the expected
    working share of the top-scored group (1.0 or 0.0 without ties); a problem with no
    candidates has none, and its top1 is 0.0.
    """
    candidates = problem["candidates"]
    pairs = [(scores[c["id"]], c["works"]) for c in candidates]
    order = sorted(range(len(candidates)), key=lambda i: (-pairs[i][0], candidates[i]["seed"]))
    n = len(candidates)
    failing = [not w for _, w in pairs]
    return {
        "problem_id": problem["problem_id"],
        "baseline3c_solved": problem["baseline3c_solved"],
        "candidates": n,
        "working": sum(w for _, w in pairs),
        "top1": top1_accuracy([pairs]) if pairs else 0.0,
        "ranking": [
            {"seed": candidates[i]["seed"], "logprob": pairs[i][0], "works": pairs[i][1]}
            for i in order
        ],
        "coverage_curve": [
            {"m": m, "risk": harmful_rate_at_coverage([s for s, _ in pairs], failing, m / n)}
            for m in range(1, n + 1)
        ],
        "aurc": aurc([s for s, _ in pairs], failing) if pairs else None,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(
    pairs: Sequence[tuple[str | Path, str | Path]],
    results: Sequence[Mapping] = (),
    pool: Sequence[str] = (),
) -> dict:
    """The per-problem results and the totals of baseline 5 over the folds given.

    Each pair is a held-out file and the scores file the notebook wrote from it. A scores file
    must name its held-out file's SHA-256 and fold, score every candidate of it and have trained
    on no problem of it, and no fold may appear twice; otherwise ValueError.

    results are the pool result rows of the other baselines (scripts/run_baselines_pool.py: rows
    with baseline, problem_id and outcome), which must cover each pool problem once per baseline
    (g3.solved_by_baseline). Each baseline's outcome is then set beside baseline 5's per problem,
    and its problems solved are counted over the same problems.
    """
    problems, runs, folds = [], [], set()
    for heldout_path, scores_path in pairs:
        heldout_path, scores_path = Path(heldout_path), Path(scores_path)
        heldout = json.loads(heldout_path.read_text(encoding="utf-8"))
        scored = json.loads(scores_path.read_text(encoding="utf-8"))
        if scored.get("format") != SCORES_FORMAT or heldout.get("format") != HELDOUT_FORMAT:
            raise ValueError(f"{scores_path}: not a baseline 5 scores and held-out pair")
        if scored["heldout"]["sha256"] != _sha256(heldout_path):
            raise ValueError(f"{scores_path}: scored another held-out file than {heldout_path}")
        fold = heldout["fold"]
        if scored["fold"] != fold or fold in folds:
            raise ValueError(f"{scores_path}: fold {scored['fold']} repeated or not the file's")
        folds.add(fold)
        ids = {c["id"] for p in heldout["problems"] for c in p["candidates"]}
        if set(scored["scores"]) != ids:
            raise ValueError(f"{scores_path}: does not score exactly the held-out candidates")
        # a record id is <problem id>/<seed>
        trained = {i.rsplit("/", 1)[0] for i in scored["trained_ids"]}
        leaked = sorted(trained & {p["problem_id"] for p in heldout["problems"]})
        if leaked:
            raise ValueError(f"{scores_path}: trained on held-out problems {leaked}")
        problems += [problem_result(p, scored["scores"]) for p in heldout["problems"]]
        runs.append(
            {k: v for k, v in scored.items() if k != "scores"}
            | {"heldout_file": heldout_path.name, "heldout_touches": heldout["touches"]}
        )
    summary = {
        "folds": sorted(folds),
        "problems": len(problems),
        # expected number of problems whose top pick works; ties at the top shared
        "baseline5_solved": sum(r["top1"] for r in problems),
        "baseline3c_solved": sum(r["baseline3c_solved"] for r in problems),
        "per_problem": problems,
        "runs": runs,
    }
    if results:
        solved_by_baseline(results, pool)
        outcome = {(r["baseline"], r["problem_id"]): r["outcome"] for r in results}
        baselines = sorted({r["baseline"] for r in results})
        for r in problems:
            r["other_baselines"] = {b: outcome[b, r["problem_id"]] for b in baselines}
        summary["other_baselines_solved"] = {
            b: sum(Outcome(r["other_baselines"][b]) is Outcome.RECOVERED for r in problems)
            for b in baselines
        }
    return summary
