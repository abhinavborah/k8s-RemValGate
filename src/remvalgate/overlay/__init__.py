"""Problem definitions that live in this repository, registered into AIOpsLab's registry.

AIOpsLab builds its problem registry per Orchestrator instance and has no hook for outside
entries, so the overlay is added to the instance's dict after construction. Nothing here imports
aiopslab at module level: aiopslab reads its config.yml on import, which CI does not have.
"""

from collections.abc import Callable, Mapping, MutableMapping

Factory = Callable[[], object]


def _local(name: str) -> Factory:
    # dev_problems imports aiopslab, so it is imported only when a problem is built
    def factory():
        from remvalgate.overlay import dev_problems

        return getattr(dev_problems, name)()

    factory.problem = name
    return factory


# problem id -> zero argument factory; each factory imports aiopslab inside its own body
PROBLEMS: dict[str, Factory] = {
    "dev-k8s_target_port-misconfig-mitigation-1": _local("target_port_user_mention"),
    "dev-scale_pod_zero_social_net-mitigation-1": _local("ScalePodMediaMitigation"),
    "dev-assign_to_non_existent_node_social_net-mitigation-1": _local(
        "AssignNonExistentNodeUniqueIdMitigation"
    ),
    # stands in for the upstream id in the pool, whose eval passes with the fault in place
    "pool-assign_to_non_existent_node_social_net-mitigation-1": _local(
        "AssignNonExistentNodeAvailableMitigation"
    ),
    # the pool extension (docs/pool_extension.md)
    "pool-k8s_target_port-misconfig-mitigation-4": _local("target_port_compose_post"),
    "pool-k8s_target_port-misconfig-mitigation-5": _local("target_port_home_timeline"),
    "pool-k8s_target_port-misconfig-mitigation-6": _local("target_port_user_timeline"),
    # the confirmation slice (docs/confirmation_problems.md); never tuned on, opened once
    "conf-k8s_target_port-misconfig-mitigation-1": _local("target_port_social_graph"),
    "conf-scale_pod_zero_social_net-mitigation-1": _local("ScalePodSocialGraphMitigation"),
    "conf-assign_to_non_existent_node_social_net-mitigation-1": _local(
        "AssignNonExistentNodeSocialGraphMitigation"
    ),
}


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
