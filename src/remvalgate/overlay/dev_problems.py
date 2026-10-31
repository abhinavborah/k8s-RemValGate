"""Problems authored in this repository: the development problems designated in
docs/development_problems.md (E3-S8, authored in E3-S9), the pool variant of
AssignNonExistentNode described in docs/problem_inventory.md, the confirmation problems in
docs/confirmation_problems.md (E3-S10) and the pool extension problems in docs/pool_extension.md
(E3-S2).

Imports aiopslab at module level, so only the overlay factories import this module.
"""

import os
import time

from aiopslab.orchestrator.problems.assign_non_existent_node import (
    AssignNonExistentNodeSocialNetMitigation,
)
from aiopslab.orchestrator.problems.k8s_target_port_misconfig import (
    K8STargetPortMisconfigMitigation,
)
from aiopslab.orchestrator.problems.scale_pod import ScalePodSocialNetMitigation
from kubernetes.client.exceptions import ApiException

from remvalgate.overlay import confirmation
from remvalgate.overlay.checks import has_available_replica, poll_until


class ScalePodMediaMitigation(ScalePodSocialNetMitigation):
    # the base task hard-codes user-service, which the pool uses
    def __init__(self):
        super().__init__()
        self.faulty_service = "media-service"


class ScalePodTextMitigation(ScalePodSocialNetMitigation):
    def __init__(self):
        super().__init__()
        self.faulty_service = "text-service"


class ScalePodPostStorageMitigation(ScalePodSocialNetMitigation):
    def __init__(self):
        super().__init__()
        self.faulty_service = "post-storage-service"


class AssignNonExistentNodeAvailableMitigation(AssignNonExistentNodeSocialNetMitigation):
    """The upstream task on user-service, with an eval that also needs an available replica."""

    def eval(self, soln, trace, duration):
        # same settle window as the base eval, which does not wait for a pod with no statuses yet
        settle = float(os.getenv("AIOPSLAB_MITIGATION_SETTLE_SECONDS", "120"))
        deadline = time.monotonic() + max(settle, 0.0)
        # a Pending pod has no container statuses, so the base eval passes with the fault in place
        results = super().eval(soln, trace, duration)
        if results["success"]:
            results["success"] = poll_until(self._has_available_replica, deadline)
        return results

    def _has_available_replica(self) -> bool:
        try:
            dep = self.kubectl.get_deployment(self.faulty_service, self.namespace)
        except ApiException as e:
            if e.status != 404:
                raise
            dep = None
        return has_available_replica(dep)


class AssignNonExistentNodeUniqueIdMitigation(AssignNonExistentNodeAvailableMitigation):
    def __init__(self):
        super().__init__()
        self.faulty_service = "unique-id-service"


def target_port_user_mention() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service="user-mention-service")


class ScalePodSocialGraphMitigation(ScalePodSocialNetMitigation):
    def __init__(self):
        super().__init__()
        self.faulty_service = confirmation.SERVICE


class AssignNonExistentNodeSocialGraphMitigation(AssignNonExistentNodeAvailableMitigation):
    def __init__(self):
        super().__init__()
        self.faulty_service = confirmation.SERVICE


def target_port_social_graph() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service=confirmation.SERVICE)


def target_port_compose_post() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service="compose-post-service")


def target_port_home_timeline() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service="home-timeline-service")


def target_port_user_timeline() -> K8STargetPortMisconfigMitigation:
    return K8STargetPortMisconfigMitigation(faulty_service="user-timeline-service")
