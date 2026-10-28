"""Development problems designated in docs/development_problems.md (E3-S8), authored in E3-S9.

Imports aiopslab at module level, so only the overlay factories import this module.
"""

from aiopslab.orchestrator.problems.assign_non_existent_node import (
    AssignNonExistentNodeSocialNetMitigation,
)
from aiopslab.orchestrator.problems.k8s_target_port_misconfig import (
    K8STargetPortMisconfigMitigation,
)
from aiopslab.orchestrator.problems.scale_pod import ScalePodSocialNetMitigation


class ScalePodMediaMitigation(ScalePodSocialNetMitigation):
    # the base task hard-codes user-service, which the pool uses
    def __init__(self):
        super().__init__()
        self.faulty_service = "media-service"


class AssignNonExistentNodeUniqueIdMitigation(AssignNonExistentNodeSocialNetMitigation):
    def __init__(self):
        super().__init__()
        self.faulty_service = "unique-id-service"

    def eval(self, soln, trace, duration):
        # a Pending pod has no container statuses, so the base eval passes with the fault in place
        results = super().eval(soln, trace, duration)
        if results["success"]:
            dep = self.kubectl.get_deployment(self.faulty_service, self.namespace)
            results["success"] = bool(dep.status.available_replicas)
        return results


def target_port_user_mention() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service="user-mention-service")
