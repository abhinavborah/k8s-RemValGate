"""The incident records and runbook entries of the primary pool (E4-S8, R4.1).

Each pool problem is one incident record, built by a fixed rule so that the retrieval metrics do
not depend on hand wording:

- fault_type is read from the problem id (harness.folds.fault_type);
- target_service and the fix come from the problem's known fix: overlay.fixes.FIXES for the
  overlay problems, UPSTREAM_FIXES below for the AIOpsLab problems, which were taken from the
  mitigation definitions at the pinned AIOpsLab commit;
- summary is one fixed template per fault type, filled with the target service;
- fix_command is the fix by one stated rule (fix_command below): commands that only wait, kubectl
  rollout status, are dropped, since the harness waits its settle window before it checks; each
  remaining command is kubectl -n <namespace> <args> joined by shlex.join; several commands are
  joined with " && ", which baselines.retrieval_only splits again so that each part is checked
  against the allow-list.

The templates and the runbook entries were written from the problem definitions and the
documentation listed below, before any recorded pool query was read.

The four MongoDB fixes (RevokeAuth, UserUnregistered) are the upstream mitigation scripts'
MongoDB calls written as one kubectl exec of mongo --eval, not the scripts themselves. They were
not run on a cluster. They name deploy/<mongodb deployment> as the exec target, because pod names
are random, so the allow-list refuses them as written: replayed by the retrieval-only baseline
they are rejected, and a planner that follows one must name the pod itself. The password
createUser needs is the placeholder <password>, so no credential is committed. The upstream
scripts delete the client service's pods so that it reconnects; the records restart its
deployment instead, which the allow-list admits.

Two more ways in which they are not the upstream fix. The scripts authenticate (mongo -u admin
-p admin, and -u root to recreate the user), and grantRolesToUser needs the grantRole action on
the database, so the calls as recorded, with no user, would be refused where authorization is
enforced; the allow-list's exec shape leaves no room for -u and -p. And the recreated user's
password is the placeholder, not the one the client connects with, so the client would not
authenticate after the restart. The summaries say what fixed the incident upstream, not what
these commands would do.

Sources:
- https://kubernetes.io/docs/concepts/services-networking/service/ (port and targetPort)
- https://kubernetes.io/docs/reference/kubectl/generated/kubectl_scale/
- https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/ (nodeSelector)
- https://kubernetes.io/docs/tasks/debug/debug-application/debug-pods/ (Pending, targetPort)
- https://kubernetes.io/docs/reference/kubectl/generated/kubectl_rollout/kubectl_rollout_restart/
- https://www.mongodb.com/docs/manual/reference/method/db.grantRolesToUser/
- https://www.mongodb.com/docs/manual/reference/method/db.createUser/
"""

import json
import shlex
from collections.abc import Iterable, Sequence

from remvalgate.harness.folds import fault_type, pool_ids
from remvalgate.harness.manifest import Registry, logged_namespace
from remvalgate.overlay.fixes import FIXES, TARGET_PORT
from remvalgate.retrieval import PastFix, Source

_REMOVE_SELECTOR = json.dumps([{"op": "remove", "path": "/spec/template/spec/nodeSelector"}])
_RESTORE_PORT = json.dumps(
    [{"op": "replace", "path": "/spec/ports/0/targetPort", "value": TARGET_PORT}]
)


def _grant(mongodb: str, client: str) -> list[list[str]]:
    script = f'db.grantRolesToUser("admin", [{{role: "readWrite", db: "{client}-db"}}])'
    return [
        ["exec", f"deploy/{mongodb}", "--", "mongo", "admin", "--eval", script],
        ["rollout", "restart", f"deployment/{client}"],
    ]


def _recreate(mongodb: str, client: str) -> list[list[str]]:
    script = (
        'db.createUser({user: "admin", pwd: "<password>", roles: '
        '[{role: "userAdminAnyDatabase", db: "admin"}, '
        f'{{role: "readWrite", db: "{client}-db"}}]}})'
    )
    return [
        ["exec", f"deploy/{mongodb}", "--", "mongo", "admin", "--eval", script],
        ["rollout", "restart", f"deployment/{client}"],
    ]


# the AIOpsLab pool problems, which overlay.fixes does not cover: problem id -> (target service,
# the fix as kubectl argument lists). Services from aiopslab/orchestrator/problems/registry.py and
# scale_pod/scale_pod_social_net.py; fixes from the problems' recover_fault and the mitigation
# scripts in aiopslab/generators/fault/script/.
UPSTREAM_FIXES: dict[str, tuple[str, list[list[str]]]] = {
    "k8s_target_port-misconfig-mitigation-1": (
        "user-service",
        [["patch", "service", "user-service", "--type=json", "-p", _RESTORE_PORT]],
    ),
    "k8s_target_port-misconfig-mitigation-2": (
        "text-service",
        [["patch", "service", "text-service", "--type=json", "-p", _RESTORE_PORT]],
    ),
    "k8s_target_port-misconfig-mitigation-3": (
        "post-storage-service",
        [["patch", "service", "post-storage-service", "--type=json", "-p", _RESTORE_PORT]],
    ),
    "scale_pod_zero_social_net-mitigation-1": (
        "user-service",
        [["scale", "deployment", "user-service", "--replicas=1"]],
    ),
    "revoke_auth_mongodb-mitigation-1": ("mongodb-geo", _grant("mongodb-geo", "geo")),
    "revoke_auth_mongodb-mitigation-2": ("mongodb-rate", _grant("mongodb-rate", "rate")),
    "user_unregistered_mongodb-mitigation-1": ("mongodb-geo", _recreate("mongodb-geo", "geo")),
    "user_unregistered_mongodb-mitigation-2": ("mongodb-rate", _recreate("mongodb-rate", "rate")),
}

# one summary per fault type, filled with the target service; {client} is the service that reads
# the MongoDB database
TEMPLATES = {
    "TargetPortMisconfig": (
        "the {service} Service forwards to a targetPort the container does not listen on, so "
        "traffic to {service} is not forwarded to its pods while they stay Running; setting the "
        "targetPort back to the container port fixed it"
    ),
    "ScalePod": (
        "the {service} deployment was scaled to zero replicas, so no pod serves {service}; "
        "scaling it back to one replica fixed it"
    ),
    "AssignNonExistentNode": (
        "the {service} pod stays Pending because its nodeSelector names a node that does not "
        "exist; removing the nodeSelector from the deployment fixed it"
    ),
    "RevokeAuth": (
        "the admin user of {service} lost its readWrite role on the {client} database, so "
        "{client} fails with an authorization error; granting the role again and restarting "
        "{client} fixed it"
    ),
    "UserUnregistered": (
        "the admin user was dropped from {service}, so {client} cannot authenticate to its "
        "database; recreating the user with its roles and restarting {client} fixed it"
    ),
}


def fix_command(commands: Iterable[Sequence[str]], namespace: str | None) -> str:
    """The one-string form of a fix: waits dropped, each command shlex-joined, " && " between.

    namespace None leaves -n out, for a runbook entry that names no namespace.
    """
    prefix = ["kubectl"] if namespace is None else ["kubectl", "-n", namespace]
    kept = [list(c) for c in commands if list(c[:2]) != ["rollout", "status"]]
    if not kept:
        raise ValueError("a fix needs at least one command that changes state")
    return " && ".join(shlex.join([*prefix, *c]) for c in kept)


def _known_fix(problem_id: str) -> tuple[str, list[list[str]]]:
    if problem_id in UPSTREAM_FIXES:
        return UPSTREAM_FIXES[problem_id]
    commands = FIXES[problem_id]
    # the overlay fixes name their service as the third argument: patch service <name>,
    # scale deployment <name>, patch deployment <name>
    return commands[0][2], commands


def incident(problem_id: str) -> PastFix:
    service, commands = _known_fix(problem_id)
    type_ = fault_type(problem_id)
    summary = TEMPLATES[type_].format(service=service, client=service.removeprefix("mongodb-"))
    return PastFix(
        entry_id=problem_id,
        source=Source.INCIDENT,
        fault_type=type_,
        target_service=service,
        summary=summary,
        fix_command=fix_command(commands, logged_namespace(problem_id)),
    )


def pool_incidents(registry: Registry) -> list[PastFix]:
    """One incident record per problem of the pool slice, in registry order."""
    return [incident(problem_id) for problem_id in pool_ids(registry)]


def _runbook(type_: str, summary: str, commands: list[list[str]]) -> PastFix:
    return PastFix(
        entry_id=f"runbook-{type_}",
        source=Source.RUNBOOK,
        fault_type=type_,
        target_service="<service>",
        summary=summary,
        fix_command=fix_command(commands, None),
    )


# general steps per fault type from the sources above; the ids carry no fault type stem, and the
# fixes name placeholders, so no runbook fix equals the fix of an incident
RUNBOOK: tuple[PastFix, ...] = (
    _runbook(
        "TargetPortMisconfig",
        "a Service forwards traffic to its targetPort on the selected pods; when the targetPort "
        "does not match the containerPort, traffic is not forwarded although the pods are Running. "
        "Patch the Service so that targetPort is the port the container listens on",
        [["patch", "service", "<service>", "--type=json", "-p", _RESTORE_PORT]],
    ),
    _runbook(
        "ScalePod",
        "a Deployment with zero replicas runs no pods, so its Service has no pod to send traffic "
        "to. Scale the Deployment back to at least one replica",
        [["scale", "deployment", "<service>", "--replicas=1"]],
    ),
    _runbook(
        "AssignNonExistentNode",
        "a pod whose nodeSelector matches no node cannot be scheduled onto a node and stays "
        "Pending. Remove or correct the nodeSelector in the pod template",
        [["patch", "deployment", "<service>", "--type=json", "-p", _REMOVE_SELECTOR]],
    ),
    _runbook(
        "RevokeAuth",
        "a MongoDB user that lost a role on a database gets an unauthorized error on every "
        "request, and the service using that database fails. Grant the role back with "
        "db.grantRolesToUser, then restart the service so that it reconnects",
        [
            [
                "exec", "deploy/<service>", "--", "mongo", "admin", "--eval",
                'db.grantRolesToUser("<user>", [{role: "readWrite", db: "<database>"}])',
            ],
            ["rollout", "restart", "deployment/<client>"],
        ],
    ),
    _runbook(
        "UserUnregistered",
        "a MongoDB user that was dropped cannot authenticate, and the service that connects with "
        "it fails. Recreate the user with db.createUser and its roles, then restart the service "
        "so that it reconnects",
        [
            [
                "exec", "deploy/<service>", "--", "mongo", "admin", "--eval",
                'db.createUser({user: "<user>", pwd: "<password>", '
                'roles: [{role: "readWrite", db: "<database>"}]})',
            ],
            ["rollout", "restart", "deployment/<client>"],
        ],
    ),
)  # fmt: skip
