"""The confirmation slice (BRD 7.1, R3.3): what each problem is, and the checksum that records it.

Imports nothing from aiopslab, so CI recomputes the checksums. Nothing may be tuned or
prompt-iterated on these problems; they are opened once, at the end (R9.6).

The checksum covers the declared definition below, the overlay factory each id builds, the source
of that factory and of its base classes in dev_problems.py, the source of checks.py, the known fix
and the AIOpsLab pin. Code under the pin is covered by the pin alone.
"""

import ast
import hashlib
import json
from pathlib import Path

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


_HERE = Path(__file__).parent


def _sources(name: str) -> list[str]:
    """Source of a dev_problems.py definition and of each base class defined there, first base
    first; the chain stops at the first base imported from aiopslab."""
    # read_text uses universal newlines, so the source is the same on every system
    text = (_HERE / "dev_problems.py").read_text(encoding="utf-8")
    defs = {
        n.name: n for n in ast.parse(text).body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
    }
    sources = []
    while name in defs:
        node = defs[name]
        sources.append(ast.get_source_segment(text, node))
        # ponytail: follows the first base only; every overlay class here has a single base
        bases = [b.id for b in getattr(node, "bases", []) if isinstance(b, ast.Name)]
        name = bases[0] if bases else ""
    return sources


def definition(problem_id: str, aiopslab_commit: str) -> dict:
    factory = PROBLEMS[problem_id].problem
    return {
        "problem_id": problem_id,
        "slice": "confirmation",
        "factory": factory,
        "source": _sources(factory),
        "checks": (_HERE / "checks.py").read_text(encoding="utf-8"),
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
