"""The values gate G3 fixes before any gate or planner training (story E9-S2, BRD R6.6, R9.1).

Pure functions on the oracle labels of the pool run (scripts/run_oracle_labels.py) and on the
outcomes of baseline 3c's rerun (scripts/run_baselines_pool.py), so they need no cluster:

- headroom: from the oracle labels only (R6.6: "Both counts come from one set of executions").
  Baseline 3c's top pick is the plan's own command in the labels file. It solves a problem when
  that command's execution is RECOVERED; a plan with no command, or one whose command was rejected
  or could not be sent, does not. A problem is recoverable when the top pick does not solve it and
  at least one of the K candidates worked. The project stops when the top pick solves 12 or more
  of the 16 problems; otherwise the check passes only with at least 5 recoverable problems. The
  recoverable count is an upper bound on what any selector over the K can gain.
- R: whether 3c's top pick recovered (RECOVERED against any other outcome) is compared per
  problem between the oracle labelling run and the rerun at another, pre-stated seed, both with
  the same retrieval path, thinking setting and model build. R = 1 if the outcome flips on at
  most 1 of the 16 problems, else R = 3 (the cap): with two outcomes, R = 2 cannot break a 1 to 1
  split. With R = 3 each system's per-problem outcome is the majority of its 3 runs. Baselines 1
  and 2 run no model and are not used; 3a and 3b take 3c's R.
- MIN_GAIN: the problem-level minimum gain, below.
- check_rerun_settings: the two runs of the R check differ in the seed only.

Every function takes the pool's ids and refuses records that do not cover each of them exactly
once, so a development dry run or a partial run is never counted.
"""

import json
from collections.abc import Mapping, Sequence

from remvalgate.harness.oracle import ProblemLabels
from remvalgate.harness.outcome import Outcome

# the sampling settings both runs' manifests record, compared by check_rerun_settings
SAMPLING_KEYS = ("temperature", "top_p", "top_k", "max_tokens")

# the project stops when 3c's top pick solves at least this many pool problems
HEADROOM_STOP = 12
# the check passes only with at least this many recoverable problems
MIN_RECOVERABLE = 5
# R = 1 while the rerun flips at most this many problems, else R = R_MAX
R_FLIPS_ALLOWED = 1
R_MAX = 3

# Minimum gain at problem level, n = 16 paired problems: losses -> the fewest wins needed.
# Wins are problems the gate recovers and baseline 5 does not, losses the reverse. The test is the
# two-sided McNemar mid-p (proposal XI; mcnemar_midp in stats.py) below 0.05, which is reached at
# 5 wins with no loss (mid-p 0.0312; 4 wins give 0.0625) and at 7 wins with one loss (mid-p 0.0391;
# 6 wins give 0.0703). Two or more losses fail the falsifier whatever the wins ("adding the gate
# loses more than one problem that baseline 5 already solves"). The exact test would need 6 wins
# with no loss; it is not used because it is conservative on sparse discordant counts. Committed
# before any gate or planner training and never changed using gate results or the sealed slice.
MIN_GAIN = {0: 5, 1: 7}


def meets_min_gain(wins: int, losses: int) -> bool:
    """Whether a gate's problem-level wins and losses against baseline 5 meet MIN_GAIN."""
    if wins < 0 or losses < 0:
        raise ValueError(f"counts must be non-negative, got wins={wins}, losses={losses}")
    return losses in MIN_GAIN and wins >= MIN_GAIN[losses]


def _complete(problem_ids: Sequence[str], pool: Sequence[str], what: str) -> None:
    repeated = sorted({i for i in problem_ids if problem_ids.count(i) > 1})
    if repeated:
        raise ValueError(f"{what}: a problem is given more than once: {', '.join(repeated)}")
    extra = sorted(set(problem_ids) - set(pool))
    if extra:
        raise ValueError(f"{what}: not pool problems: {', '.join(extra)}")
    missing = sorted(set(pool) - set(problem_ids))
    if missing:
        raise ValueError(f"{what}: pool problems missing: {', '.join(missing)}")


def top_pick_solved(labels: ProblemLabels) -> bool:
    """Whether 3c's top pick, the plan's own command, recovered the problem in the labelling run."""
    return labels.plan is not None and labels.plan.worked


def headroom(labels: Sequence[ProblemLabels], pool: Sequence[str]) -> dict:
    """The headroom check of gate G3 from the oracle labels of every pool problem.

    Raises ValueError unless labels cover each pool problem exactly once.
    """
    _complete([p.problem_id for p in labels], pool, "oracle labels")
    solved = sorted(p.problem_id for p in labels if top_pick_solved(p))
    recoverable = sorted(
        p.problem_id for p in labels if not top_pick_solved(p) and p.any_working_candidate
    )
    stop = len(solved) >= HEADROOM_STOP
    return {
        "problems": len(labels),
        "solved": len(solved),
        "solved_ids": solved,
        "recoverable": len(recoverable),
        "recoverable_ids": recoverable,
        "stop": stop,
        "passed": not stop and len(recoverable) >= MIN_RECOVERABLE,
    }


def r_check(
    labels: Sequence[ProblemLabels], rerun: Mapping[str, Outcome], pool: Sequence[str]
) -> dict:
    """R by the stated rule, from the labelling run's top-pick outcomes and the rerun's outcomes.

    rerun maps each pool problem to the outcome of 3c's rerun. Raises ValueError unless both
    cover each pool problem exactly once, or when a rerun outcome is not an Outcome.
    """
    _complete([p.problem_id for p in labels], pool, "oracle labels")
    _complete(list(rerun), pool, "3c rerun")
    for problem_id, outcome in rerun.items():
        # type() and not membership, so a string read from a file is not taken as an Outcome
        if type(outcome) is not Outcome:
            raise ValueError(f"{problem_id}: not an outcome: {outcome!r}")
    first = {p.problem_id: top_pick_solved(p) for p in labels}
    flips = sorted(i for i in pool if first[i] != (rerun[i] is Outcome.RECOVERED))
    return {
        "problems": len(pool),
        "labelling_solved": sum(first.values()),
        "rerun_solved": sum(o is Outcome.RECOVERED for o in rerun.values()),
        "flips": len(flips),
        "flip_ids": flips,
        "r": 1 if len(flips) <= R_FLIPS_ALLOWED else R_MAX,
    }


def g3(labels: Sequence[ProblemLabels], rerun: Mapping[str, Outcome], pool: Sequence[str]) -> dict:
    """The three values gate G3 commits: the headroom result, R and the minimum gain."""
    return {
        "headroom": headroom(labels, pool),
        "r": r_check(labels, rerun, pool),
        "min_gain": {
            "wins_needed_by_losses": dict(MIN_GAIN),
            "test": "two-sided McNemar mid-p < 0.05, n = 16",
        },
    }


def check_rerun_settings(labelling: Sequence[dict], rerun: Sequence[dict]) -> None:
    """Refuse an R check whose two runs differ in anything but the seed.

    labelling and rerun hold the manifests of the labelling run and of 3c's rerun. Both runs must
    name one model and one model version (format, quantization and thinking setting) and the same
    sampling settings, the same in both, and the rerun one seed that the labelling run did not use.
    Raises ValueError otherwise.
    """
    for name in ("model_name", "model_version"):
        values = {m[name] for m in [*labelling, *rerun]}
        if len(values) != 1:
            raise ValueError(f"the two runs differ in {name}: {sorted(values)}")
    for name in SAMPLING_KEYS:
        values = {json.dumps(m.get("sampling", {}).get(name)) for m in [*labelling, *rerun]}
        if len(values) != 1:
            raise ValueError(f"the two runs differ in sampling {name}: {sorted(values)}")
    seeds = {m["seed"] for m in rerun}
    if len(seeds) != 1:
        raise ValueError(f"the rerun has more than one seed: {sorted(seeds)}")
    if seeds & {m["seed"] for m in labelling}:
        raise ValueError(f"the rerun repeats the labelling run's seed {seeds.pop()}")


def solved_by_baseline(rows: Sequence[Mapping], pool: Sequence[str]) -> dict:
    """Per baseline, how many pool problems its result rows solved (RECOVERED), from rows that
    cover each pool problem exactly once. Raises ValueError for a baseline whose rows repeat a
    problem, miss one or name a problem outside the pool, so a partial or resumed run that left
    two rows for one problem is never counted."""
    by_baseline: dict[str, list[Mapping]] = {}
    for row in rows:
        by_baseline.setdefault(row["baseline"], []).append(row)
    counts = {}
    for baseline, mine in sorted(by_baseline.items()):
        _complete([r["problem_id"] for r in mine], pool, f"baseline {baseline}")
        counts[baseline] = sum(Outcome(r["outcome"]) is Outcome.RECOVERED for r in mine)
    return counts
