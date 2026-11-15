"""Which problems one run of scripts/run_baselines_pool.py takes (#59, #73).

Checked in main before any touch, so a refused selection never touches the held-out problems.
"""

from collections.abc import Iterable

from remvalgate.harness.manifest import Registry, Slice
from remvalgate.harness.oracle import problem_fault_type

# the confirmation slice and the singleton types are in no fold (BRD 1.20)
HELD_OUT = frozenset({Slice.CONFIRMATION, Slice.SINGLETON})
HELD_OUT_BASELINES = frozenset({"2", "3c"})


def selection(
    problems: Iterable[str],
    baselines: Iterable[str],
    registry: Registry,
    *,
    open_confirmation: bool = False,
) -> tuple[list[str], Slice]:
    """Return the selected ids, the pool when none is given, and their one slice.

    Raises ValueError for an id not in the registry, ids of more than one slice, a baseline other
    than 2 and 3c on a held-out slice, an id with no fault type, and open_confirmation on a slice
    other than the confirmation slice.
    """
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    selected = list(dict.fromkeys(problems)) or pool
    unknown = [i for i in selected if i not in registry.slices]
    if unknown:
        raise ValueError(f"not in the slice registry: {', '.join(unknown)}")
    slices = {registry.slices[i] for i in selected}
    if len(slices) > 1:
        # one slice per run, so a summary is never mixed
        raise ValueError(f"one slice per run, got {sorted(slices)}")
    (slice_,) = slices
    if slice_ in HELD_OUT and set(baselines) - HELD_OUT_BASELINES:
        raise ValueError(f"{slice_} problems run baselines {sorted(HELD_OUT_BASELINES)} only")
    untyped = []
    for i in selected:
        try:
            problem_fault_type(i)
        except ValueError:
            untyped.append(i)
    if untyped:
        # redeploy_without_PV and the Astronomy Shop problem are not run (#73)
        raise ValueError(f"no fault type, so not run: {', '.join(untyped)}")
    if open_confirmation and slice_ is not Slice.CONFIRMATION:
        raise ValueError("--open-confirmation is for confirmation problems only")
    return selected, slice_
