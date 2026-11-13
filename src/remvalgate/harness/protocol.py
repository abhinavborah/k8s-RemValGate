"""The run order of the reruns (#161)."""


def interleaved(seeds: list[int], problems: list[str], arms: list[str]) -> list[tuple]:
    """(seed, problem, arm) in run order: seed by seed, every arm on one problem before the next
    problem, with the first arm rotated from problem to problem, so cluster drift does not fall on
    one arm."""
    return [
        (seed, problem, arm)
        for seed in seeds
        for i, problem in enumerate(problems)
        for arm in arms[i % len(arms) :] + arms[: i % len(arms)]
    ]
