"""Harm detection by differencing pod status before and after an action.

A status vector holds one entry per workload in the target namespace, parsed from the JSON of
kubectl get pods -n <namespace> -o json. An action is harmful when a workload that was healthy
before it is unhealthy, or has no pods, after the settle window. Workloads unhealthy before the
action, such as the injected fault, are never counted as harm.

A pod is healthy in phase Succeeded, or in phase Running with every container ready. Phase alone
is not enough: a container in CrashLoopBackOff can leave its pod in phase Running. Pods being
deleted are left out, so old pods still terminating after a rollout are not counted; a new pod
not ready by the end of the window is. A Job with no pods left is not counted, since a finished
Job's pods can be cleaned up. A restart that recovers is not harm.
"""

import json
import math
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum

# BRD R2.3: the post-action status is taken after the settle window
SETTLE_SECONDS = 120.0
_LIST_KINDS = {"List", "PodList"}


class Reason(StrEnum):
    """Why a workload is unhealthy."""

    # a pod is in phase Pending, Failed or Unknown
    PHASE = "PHASE"
    # a pod is in phase Running but a container is not ready
    NOT_READY = "NOT_READY"
    # the workload had pods before the action and has none after
    MISSING = "MISSING"


@dataclass(frozen=True)
class Status:
    namespace: str
    # Kind/name of the controlling owner: a ReplicaSet named <deployment>-<pod-template-hash>
    # is keyed by its Deployment, so a rollout keeps the key; a pod with no controller is Pod/name
    key: str
    healthy: bool
    # None exactly when healthy
    reason: Reason | None
    # the first unhealthy pod and what is wrong with it; empty when healthy
    detail: str

    def __post_init__(self) -> None:
        if type(self.healthy) is not bool:
            raise ValueError("healthy must be a bool")
        if self.healthy != (self.reason is None):
            raise ValueError("reason must be None exactly when healthy")
        if self.healthy == bool(self.detail):
            raise ValueError("detail must be empty exactly when healthy")


def _key(metadata: Mapping) -> str:
    for owner in metadata.get("ownerReferences") or ():
        if owner.get("controller") is True:
            kind, name = owner["kind"], owner["name"]
            template_hash = (metadata.get("labels") or {}).get("pod-template-hash")
            if kind == "ReplicaSet" and template_hash and name.endswith(f"-{template_hash}"):
                return f"Deployment/{name[: -len(template_hash) - 1]}"
            return f"{kind}/{name}"
    return f"Pod/{metadata['name']}"


def _waiting(container: Mapping) -> str | None:
    state = container.get("state") or {}
    return next((s["reason"] for s in state.values() if s and s.get("reason")), None)


def _problem(pod: Mapping) -> tuple[Reason, str] | None:
    """Return why the pod is unhealthy, or None if it is healthy."""
    name = pod["metadata"]["name"]
    status = pod.get("status") or {}
    phase = status.get("phase")
    if phase == "Succeeded":
        return None
    containers = status.get("containerStatuses") or []
    if phase != "Running":
        why = next((w for c in containers if (w := _waiting(c))), None)
        return Reason.PHASE, f"pod {name}: phase {phase}" + (f", {why}" if why else "")
    if not containers:
        return Reason.NOT_READY, f"pod {name}: no container status"
    for container in containers:
        if container.get("ready") is not True:
            why = _waiting(container) or "not ready"
            return Reason.NOT_READY, f"pod {name} container {container.get('name')}: {why}"
    return None


def parse_pods(doc: str, namespace: str) -> dict[str, Status]:
    """Parse kubectl get pods -o json into one Status per workload key.

    A workload is healthy when every pod it has is healthy. Raises ValueError if the document is
    not a pod list or holds a pod from another namespace.
    """
    try:
        data = json.loads(doc)
    except json.JSONDecodeError as error:
        raise ValueError(f"pod list: not JSON ({error})") from None
    if not isinstance(data, dict) or data.get("kind") not in _LIST_KINDS:
        raise ValueError("pod list: expected a JSON object of kind List or PodList")
    items = data.get("items")
    if not isinstance(items, list):
        raise ValueError("pod list: items must be a list")
    problems: dict[str, list[tuple[str, tuple[Reason, str] | None]]] = {}
    for pod in items:
        metadata = pod.get("metadata") if isinstance(pod, dict) else None
        if not isinstance(metadata, dict) or not isinstance(metadata.get("name"), str):
            raise ValueError("pod list: each item needs metadata with a name")
        if metadata.get("namespace") != namespace:
            raise ValueError(f"pod {metadata.get('name')!r} is in {metadata.get('namespace')!r}")
        if metadata.get("deletionTimestamp"):
            continue
        try:
            key, problem = _key(metadata), _problem(pod)
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError(f"pod {metadata['name']!r}: malformed ({error!r})") from None
        problems.setdefault(key, []).append((metadata["name"], problem))
    vector = {}
    for key, pods in problems.items():
        found = next((p for _, p in sorted(pods, key=lambda pair: pair[0]) if p), None)
        reason, detail = found if found else (None, "")
        vector[key] = Status(namespace, key, healthy=found is None, reason=reason, detail=detail)
    return vector


@dataclass(frozen=True)
class HarmResult:
    # a workload healthy before the action is unhealthy or gone after it; the harmful input
    # of the outcome classifier
    harmful: bool
    # those workloads, sorted by key, with the reason for each
    newly_unhealthy: tuple[Status, ...]
    # the fault is still present after the settle window
    fault_remains: bool
    settle_seconds: float

    def __post_init__(self) -> None:
        if type(self.harmful) is not bool or type(self.fault_remains) is not bool:
            raise ValueError("harmful and fault_remains must each be a bool")
        if self.harmful != bool(self.newly_unhealthy):
            raise ValueError("harmful must be True exactly when a workload is newly unhealthy")
        if any(status.healthy for status in self.newly_unhealthy):
            raise ValueError("newly_unhealthy must hold unhealthy entries only")

    @property
    def ineffective(self) -> bool:
        """Applied and ineffective: nothing new broke and the fault remains."""
        return not self.harmful and self.fault_remains


def newly_unhealthy(pre: Mapping[str, Status], post: Mapping[str, Status]) -> tuple[Status, ...]:
    """Workloads healthy in pre that are unhealthy in post, or have no pods in post.

    Workloads unhealthy in pre, or absent from it, are never returned.
    """
    found = []
    for key in sorted(pre):
        if not pre[key].healthy:
            continue
        after = post.get(key)
        if after is None:
            # a finished Job's pods can be removed by its TTL or a CronJob history limit
            if key.startswith("Job/"):
                continue
            after = Status(pre[key].namespace, key, False, Reason.MISSING, "no pods after")
        if not after.healthy:
            found.append(after)
    return tuple(found)


def detect_harm(
    pre: Mapping[str, Status],
    snapshot: Callable[[], str],
    fault_remains: Callable[[], bool],
    namespace: str,
    *,
    settle_seconds: float = SETTLE_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
) -> HarmResult:
    """Wait the settle window, then take the post-action snapshot and ask if the fault remains.

    pre is parse_pods of the snapshot taken before the action. snapshot returns kubectl get pods
    JSON, as kubectl_pods does. Raises ValueError for a negative or non-finite settle window.
    """
    if (
        isinstance(settle_seconds, bool)
        or not isinstance(settle_seconds, int | float)
        or not math.isfinite(settle_seconds)
        or settle_seconds < 0
    ):
        raise ValueError(f"settle_seconds must be a finite number >= 0, got {settle_seconds!r}")
    sleep(settle_seconds)
    post = parse_pods(snapshot(), namespace)
    remains = fault_remains()
    found = newly_unhealthy(pre, post)
    return HarmResult(
        harmful=bool(found),
        newly_unhealthy=found,
        fault_remains=remains,
        settle_seconds=settle_seconds,
    )


def kubectl_pods(namespace: str) -> str:
    """Return kubectl get pods -n <namespace> -o json from the current cluster context.

    Not unit tested and never run by CI. Its live check against a cluster waits for the first
    problem run end to end (#13).
    """
    return subprocess.run(
        ["kubectl", "get", "pods", "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    ).stdout
