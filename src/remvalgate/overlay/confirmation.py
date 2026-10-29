"""The confirmation slice (BRD 7.1, R3.3): what each problem is, and the checksum that records it.

Imports nothing from aiopslab, so CI recomputes the checksums. Nothing may be tuned or
prompt-iterated on these problems; they are opened once, at the end (R9.6).

The checksum covers the declared definition below, the overlay factory each id builds, the known
fix and the AIOpsLab pin. It does not cover the source of an inherited eval, such as
AssignNonExistentNodeAvailableMitigation.eval, which a change in this repository could alter
without changing the checksum.
"""

import hashlib
import json

from remvalgate.overlay import PROBLEMS
from remvalgate.overlay.fixes import FIXES

# one service for all three, as the pool does with user-service, so the pool extension keeps
# enough targetPort 9090 services (docs/confirmation_problems.md)
SERVICE = "social-graph-service"
NAMESPACE = "test-social-network"

# problem id -> its fault type and the class it is built from; all share SERVICE and NAMESPACE
SPEC = {
    "conf-k8s_target_port-misconfig-mitigation-1": {
        "type": "TargetPortMisconfig",
        "base": "K8STargetPortMisconfigMitigation",
    },
    "conf-scale_pod_zero_social_net-mitigation-1": {
        "type": "ScalePod",
        "base": "ScalePodSocialNetMitigation",
    },
    "conf-assign_to_non_existent_node_social_net-mitigation-1": {
        "type": "AssignNonExistentNode",
        "base": "AssignNonExistentNodeAvailableMitigation",
    },
}


def definition(problem_id: str, aiopslab_commit: str) -> dict:
    return {
        "problem_id": problem_id,
        "slice": "confirmation",
        "factory": PROBLEMS[problem_id].problem,
        **SPEC[problem_id],
        "service": SERVICE,
        "namespace": NAMESPACE,
        "fix": FIXES[problem_id],
        "aiopslab": aiopslab_commit,
    }


def digest(record: dict) -> str:
    # canonical JSON, so the checksum does not depend on key order or line endings
    text = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(text.encode("ascii")).hexdigest()


def checksum(problem_id: str, aiopslab_commit: str) -> str:
    return digest(definition(problem_id, aiopslab_commit))
