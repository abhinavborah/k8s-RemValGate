"""The primary analysis of the fine-tuning ablation (story E9-S5, #56).

The fine-tuned planner (3cft) is compared with the untuned planner (3c) by whether each one's top
pick recovers each pool problem. Both ran on the same server, interleaved, at the same seeds
(#165 step 3, #159). A problem counts as recovered only if it recovers at every seed (the rule of
#56, combine_seeds). The paired test is the two-sided McNemar mid-p of #21, paired by problem,
with the Bonett-Price interval for the difference (3cft minus 3c, as the second system minus the
first). The threshold was committed with gate G3 before the runs (#53, R6.6): the claim is
supported only if 3cft wins more problems than it loses and the mid-p is below 0.05, which at
n = 16 takes 5 wins with 0 losses or 7 with 1.

The candidates of each planner, labelled by execution, are summarised beside it: per problem the
top pick's outcome, the working candidates of K, mixed outcomes and whether the problem is
recoverable (top pick fails, another candidate works).
"""

from collections.abc import Mapping, Sequence

from remvalgate.harness.g3 import _complete, combine_seeds, top_pick_solved
from remvalgate.harness.oracle import ProblemLabels
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.stats import bonett_price, mcnemar_midp

UNTUNED, FINE_TUNED = "3c", "3cft"
ALPHA = 0.05


def _recovered(rows: Sequence[Mapping]) -> dict[str, bool]:
    return {r["problem_id"]: Outcome(r["outcome"]) is Outcome.RECOVERED for r in rows}


def check_folds(manifests: Sequence[Mapping], folds: Mapping[str, int]) -> None:
    """Refuse a 3cft run served by another fold's model, or a 3c run served by a fold's model.

    Reads `sampling.fold` and the served build's `fold` from each manifest. Raises ValueError."""
    for m in manifests:
        arm = m["sampling"]["baseline"]
        served = (m.get("model_build") or {}).get("fold")
        if arm == FINE_TUNED:
            want = folds[m["problem_id"]]
            if m["sampling"].get("fold") != want or served != want:
                raise ValueError(f"{m['run_id']}: served fold {served}, its problem's fold {want}")
        elif arm == UNTUNED and served is not None:
            raise ValueError(f"{m['run_id']}: the control was served by fold {served}'s model")


def _pair(untuned: dict[str, bool], tuned: dict[str, bool], problems: Sequence[str]) -> dict:
    n12 = sum(untuned[p] and not tuned[p] for p in problems)
    n21 = sum(tuned[p] and not untuned[p] for p in problems)
    n = len(problems)
    midp = mcnemar_midp(n12, n21)
    low, high = bonett_price(n21, n12, n)
    return {
        UNTUNED: sum(untuned[p] for p in problems),
        FINE_TUNED: sum(tuned[p] for p in problems),
        "n": n,
        # n12: only 3c recovered; n21: only 3cft did
        "n12": n12,
        "n21": n21,
        "mcnemar_midp": midp,
        "difference_bonett_price95": [low, high],
        "supported": n21 > n12 and midp < ALPHA,
    }


def compare(rows: Sequence[Mapping], pool: Sequence[str]) -> dict:
    """The paired comparison of 3cft with 3c, per seed and with the seeds combined.

    rows are result rows of the two arms; every pool problem must have one row per arm and seed,
    and both arms the same seeds. Raises ValueError otherwise."""
    rows = [r for r in rows if r["baseline"] in (UNTUNED, FINE_TUNED)]
    seeds = sorted({r["seed"] for r in rows})
    out: dict = {"seeds": {}}
    for seed in seeds:
        by_arm = {}
        for arm in (UNTUNED, FINE_TUNED):
            mine = [r for r in rows if r["baseline"] == arm and r["seed"] == seed]
            _complete([r["problem_id"] for r in mine], pool, f"{arm} at seed {seed}")
            by_arm[arm] = _recovered(mine)
        out["seeds"][str(seed)] = _pair(by_arm[UNTUNED], by_arm[FINE_TUNED], sorted(pool))
    combined = combine_seeds(rows)
    by_arm = {
        arm: _recovered([r for r in combined if r["baseline"] == arm])
        for arm in (UNTUNED, FINE_TUNED)
    }
    out["both_seeds"] = _pair(by_arm[UNTUNED], by_arm[FINE_TUNED], sorted(pool))
    out["claim"] = "supported" if out["both_seeds"]["supported"] else "falsified"
    out["per_problem"] = {
        p: {UNTUNED: by_arm[UNTUNED][p], FINE_TUNED: by_arm[FINE_TUNED][p]} for p in sorted(pool)
    }
    return out


def _yield(labels: Sequence[ProblemLabels], pool: Sequence[str], what: str) -> dict:
    _complete([p.problem_id for p in labels], pool, what)
    per = {}
    for p in labels:
        per[p.problem_id] = {
            "top_pick_solved": top_pick_solved(p),
            "working": p.c,
            "k": p.n,
            "mixed": p.mixed,
            "recoverable": not top_pick_solved(p) and p.any_working_candidate,
        }
    return {
        "top_pick_solved": sum(v["top_pick_solved"] for v in per.values()),
        "with_working_candidate": sum(v["working"] > 0 for v in per.values()),
        "mixed": sum(v["mixed"] for v in per.values()),
        "recoverable": sorted(k for k, v in per.items() if v["recoverable"]),
        "per_problem": per,
    }


def candidate_yield(
    untuned: Sequence[ProblemLabels], tuned: Sequence[ProblemLabels], pool: Sequence[str]
) -> dict:
    """The oracle labels of the untuned planner's candidates beside the fine-tuned planner's."""
    return {
        UNTUNED: _yield(untuned, pool, "untuned labels"),
        FINE_TUNED: _yield(tuned, pool, "fine-tuned labels"),
    }
