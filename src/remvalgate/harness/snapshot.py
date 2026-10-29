"""Snapshot and restore of the Kubernetes objects a candidate command touches (story E2-S2, R2.2).

touched reads the targets from the command with the allow-list parser, so a snapshot covers
whatever the command changes and not only what a known fix would. take saves the live objects
and restore puts them back with a full replace. A server-side apply would leave a field added by
another manager in place, so it cannot undo a patch. An object the command deleted is created
again. Only Deployments and Services are snapshotted: a pod the command deletes is recreated by
its controller, and a ReplicaSet scaled by hand is set back by its Deployment.

Faults whose state is not a Kubernetes object (RevokeAuth, UserUnregistered) are out of scope:
restore does nothing for them and says so. The caller escalates and still runs harm detection.

The kubectl calls are injected, as in harm.py, so CI tests the logic without a cluster.
"""

import copy
import itertools
import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from remvalgate.harness.allowlist import (
    Policy,
    check,
    parse_args,
    split_targets,
    split_verb,
    tokenize,
)
from remvalgate.harness.cluster_state import changed, normalise

# the injector fault_type of each problem type, from the problem inventory
IN_SCOPE = frozenset({"misconfig_k8s", "scale_pods_to_zero", "assign_to_non_existent_node"})
OUT_OF_SCOPE = frozenset({"revoke_auth", "storage_user_unregistered"})

_KINDS = {
    **dict.fromkeys(("deployments", "deployment", "deploy"), "Deployment"),
    **dict.fromkeys(("services", "service", "svc"), "Service"),
}
_MUTATING = frozenset({"scale", "patch", "rollout restart", "rollout undo"})
# set by the API server, so a replace must not send them
_SERVER_FIELDS = (
    "uid",
    "resourceVersion",
    "creationTimestamp",
    "managedFields",
    "generation",
    "selfLink",
)

# a Service gets these when it is created, so a created-again Service cannot match them
_ADDRESS_FIELDS = ("clusterIP", "clusterIPs")

ObjectRef = tuple[str, str]  # kind, name


@dataclass(frozen=True)
class Snapshot:
    namespace: str
    # the object as kubectl returned it, with status and server fields removed
    objects: Mapping[ObjectRef, dict]


@dataclass(frozen=True)
class RestoreResult:
    attempted: bool
    # kind/name keys whose spec or labels still differ from the snapshot; empty on success
    changed: tuple[str, ...]
    recreated: tuple[ObjectRef, ...] = ()

    @property
    def restored(self) -> bool:
        return self.attempted and not self.changed


def touched(command: str, policy: Policy, namespace: str) -> list[ObjectRef]:
    """Return the Deployments and Services an admitted command changes, in a stable order.

    Raises ValueError if the allow-list rejects the command: it never runs, so it needs no
    snapshot. A read-only command, a pod delete and a ReplicaSet scale return an empty list.
    """
    verdict = check(command, policy, namespace)
    if not verdict.admitted:
        raise ValueError(f"the allow-list rejects {command!r}: {verdict.reason}")
    parsed = parse_args(tokenize(command)[1:], policy)
    verb = split_verb(parsed.positionals, policy)
    if verb is None or verb[0] not in _MUTATING:
        return []
    types, names = split_targets(verb[1])
    if len(types) == len(names) and "/" in verb[1][0]:
        pairs = zip(types, names, strict=True)
    else:
        pairs = itertools.product(types, names)
    return sorted({(_KINDS[t], n) for t, n in pairs if t in _KINDS})


def strip(obj: dict) -> dict:
    """Remove status and the fields the API server sets, so the rest can be sent back."""
    out = copy.deepcopy(obj)
    out.pop("status", None)
    for field in _SERVER_FIELDS:
        out.get("metadata", {}).pop(field, None)
    return out


def take(namespace: str, refs: list[ObjectRef], get: Callable[[ObjectRef], dict | None]):
    """Save the live objects. A missing one is left out: the command fails on it at kubectl."""
    objects = {ref: strip(live) for ref in refs if (live := get(ref)) is not None}
    return Snapshot(namespace, objects)


def restore(
    fault_type: str,
    snapshot: Snapshot,
    get: Callable[[ObjectRef], dict | None],
    put: Callable[[str, dict], None],
) -> RestoreResult:
    """Put the snapshot back and compare the live objects with it.

    get returns the live object or None if it is gone. put("replace" | "create", obj) writes.
    Raises ValueError for a fault type that is in neither list, so a new type is decided on
    purpose. The caller waits for a restored Deployment to roll out.
    """
    if fault_type in OUT_OF_SCOPE:
        return RestoreResult(attempted=False, changed=())
    if fault_type not in IN_SCOPE:
        raise ValueError(f"unknown fault type {fault_type!r}")
    expected = dict(snapshot.objects)
    recreated = []
    for ref, saved in snapshot.objects.items():
        if get(ref) is None:
            created = copy.deepcopy(saved)
            # a Service that is created again gets new addresses, the old ones may be taken
            for field in _ADDRESS_FIELDS:
                created.get("spec", {}).pop(field, None)
            expected[ref] = created
            put("create", copy.deepcopy(created))
            recreated.append(ref)
        else:
            # a copy, so a put that edits what it is given cannot edit the snapshot
            put("replace", copy.deepcopy(saved))
    live = {}
    for ref in snapshot.objects:
        obj = get(ref)
        if obj is not None and ref in recreated:
            for field in _ADDRESS_FIELDS:
                obj.get("spec", {}).pop(field, None)
        if obj is not None:
            live[ref] = obj
    left = changed(
        normalise({"items": list(expected.values())}),
        normalise({"items": list(live.values())}),
    )
    return RestoreResult(attempted=True, changed=tuple(left), recreated=tuple(recreated))


def kubectl_get(namespace: str, context: str | None = None):
    """Return a get for take and restore: the live object, or None if it is not found.

    Not unit tested and never run by CI; scripts/run_restore_check.py runs it on a cluster.
    """
    base = ["kubectl", *(["--context", context] if context else []), "-n", namespace]

    def get(ref: ObjectRef) -> dict | None:
        run = subprocess.run(
            [*base, "get", ref[0].lower(), ref[1], "-o", "json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if run.returncode == 0:
            return json.loads(run.stdout)
        if "NotFound" in run.stderr:
            return None
        raise RuntimeError(run.stderr.strip())

    return get


def kubectl_put(namespace: str, context: str | None = None):
    """Return a put for restore that runs kubectl replace or create with the object on stdin."""
    base = ["kubectl", *(["--context", context] if context else []), "-n", namespace]

    def put(action: str, obj: dict) -> None:
        subprocess.run(
            [*base, action, "-f", "-"],
            input=json.dumps(obj),
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )

    return put
