"""The primary analysis of the fine-tuning ablation (story E9-S5, #56).

The fine-tuned planner (3cft) is compared with the untuned planner (3c) by whether each one's top
pick recovers each pool problem. Both ran on the same server, interleaved, at the same seeds
(#165 step 3, #159). A problem counts as recovered only if it recovers at every seed (the rule of
#56, combine_seeds). The paired test is the two-sided McNemar mid-p of #21, paired by problem,
with the Bonett-Price interval for the difference (3cft minus 3c, as the second system minus the
first). The threshold is the minimum gain of gate G3 (#53, R6.6, `g3.meets_min_gain`), applied
to the combined counts at n = 16: the claim is supported only with 5 wins and 0 losses, or 7 wins
and 1 loss. It is falsified by two or more losses whatever the wins, or by no more wins than
losses. Anything else (1 to 4 wins with no loss, 2 to 6 wins with 1 loss) is "not supported": a
gain too small for 16 problems to separate from noise, which is not evidence of no effect. The
rule and its labels were confirmed on #56 before the result was read. The mid-p and the interval
are reported beside it, and each seed's counts are descriptive.

The candidates of each planner, labelled by execution, are summarised beside it: per problem the
top pick's outcome, the working candidates of K, mixed outcomes and whether the problem is
recoverable (top pick fails, another candidate works).
"""

import json
from collections.abc import Mapping, Sequence

from remvalgate.harness.g3 import _complete, combine_seeds, meets_min_gain, top_pick_solved
from remvalgate.harness.oracle import ProblemLabels
from remvalgate.harness.outcome import Outcome
from remvalgate.harness.stats import bonett_price, mcnemar_midp

UNTUNED, FINE_TUNED = "3c", "3cft"
SEEDS = (1000, 1001)
# the build fields that differ between the fold servers and the control by design; the rest
# (server, host, GPU, llama.cpp, quantization, context, arguments) must match, as #161 asks
FOLD_BUILD = frozenset({"fold", "adapter_sha256", "model_file_name", "model_sha256"})
PER_RUN = frozenset({"endpoint", "model_file"})


def _recovered(rows: Sequence[Mapping]) -> dict[str, bool]:
    return {r["problem_id"]: Outcome(r["outcome"]) is Outcome.RECOVERED for r in rows}


def check_folds(manifests: Sequence[Mapping], folds: Mapping[str, int]) -> None:
    """Refuse a 3cft run served by another fold's model, a 3c run served by a fold's model, and
    any run whose build differs from the others' apart from the fold fields (another server, such as
    an LM Studio control, is never paired with 3cft).

    Reads `sampling.fold` and `model_build` from each manifest. Raises ValueError."""
    shared: dict[str, str] = {}
    for m in manifests:
        build = m.get("model_build") or {}
        key = json.dumps(
            {k: v for k, v in build.items() if k not in FOLD_BUILD | PER_RUN}, sort_keys=True
        )
        shared.setdefault(key, m["run_id"])
    if len(shared) > 1:
        raise ValueError(f"the runs were served by different builds: {sorted(shared.values())}")
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
        "meets_min_gain": meets_min_gain(n21, n12),
    }


def claim(wins: int, losses: int) -> str:
    """supported, falsified or not supported, from the combined wins and losses (#56)."""
    if meets_min_gain(wins, losses):
        return "supported"
    if losses >= 2 or wins <= losses:
        return "falsified"
    return "not supported"


def compare(rows: Sequence[Mapping], pool: Sequence[str]) -> dict:
    """The paired comparison of 3cft with 3c, per seed and with the seeds combined.

    rows are result rows of the two arms; every pool problem must have one row per arm and seed,
    and both arms must hold seeds 1000 and 1001 (#161), no more and no fewer. Raises ValueError
    otherwise."""
    rows = [r for r in rows if r["baseline"] in (UNTUNED, FINE_TUNED)]
    for arm in (UNTUNED, FINE_TUNED):
        have = tuple(sorted({r["seed"] for r in rows if r["baseline"] == arm}))
        if have != SEEDS:
            raise ValueError(f"{arm}: expected seeds {SEEDS}, got {have or 'no rows'}")
    seeds = list(SEEDS)
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
    out["claim"] = claim(out["both_seeds"]["n21"], out["both_seeds"]["n12"])
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
