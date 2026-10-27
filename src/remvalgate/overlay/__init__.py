"""Problem definitions that live in this repository, registered into AIOpsLab's registry.

AIOpsLab builds its problem registry per Orchestrator instance and has no hook for outside
entries, so the overlay is added to the instance's dict after construction. Nothing here imports
aiopslab at module level: aiopslab reads its config.yml on import, which CI does not have.
"""

from collections.abc import Callable, Mapping, MutableMapping

Factory = Callable[[], object]

# problem id -> zero argument factory; later stories add entries, and each factory imports
# aiopslab inside its own body
PROBLEMS: dict[str, Factory] = {}


def register(
    registry: MutableMapping[str, Factory], problems: Mapping[str, Factory] | None = None
) -> list[str]:
    """Add the overlay problems to a registry dict and return the ids added.

    An id the registry already has is refused and nothing is added, so an overlay can never
    shadow an upstream problem.
    """
    problems = PROBLEMS if problems is None else problems
    clash = sorted(problems.keys() & registry.keys())
    if clash:
        raise ValueError(f"overlay ids already in the registry: {', '.join(clash)}")
    registry.update(problems)
    return list(problems)


def new_orchestrator(problems: Mapping[str, Factory] | None = None, **kwargs):
    """Build an AIOpsLab Orchestrator whose problem registry includes the overlay."""
    from aiopslab.orchestrator import Orchestrator

    orch = Orchestrator(**kwargs)
    register(orch.probs.PROBLEM_REGISTRY, problems)
    return orch
