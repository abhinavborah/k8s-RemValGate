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


def target_port_user_mention() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service="user-mention-service")
