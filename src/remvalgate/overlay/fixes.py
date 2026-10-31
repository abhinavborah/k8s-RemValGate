"""Known fixes of the overlay problems, used by scripts/validate_overlay_problems.py.

Kept apart from that script, which imports aiopslab, so a test checks the keys in CI.
"""

import json
import shlex
from collections.abc import Iterable, Mapping, Set

# the targetPort the target-port problem breaks and its known fix restores
TARGET_PORT = 9090


def _restore_target_port(service: str) -> list[list[str]]:
    op = {"op": "replace", "path": "/spec/ports/0/targetPort", "value": TARGET_PORT}
    patch = json.dumps([op])
    return [["patch", "service", service, "--type=json", "-p", patch]]


# problem id -> the known fix, as kubectl argument lists run in order
FIXES = {
    "dev-k8s_target_port-misconfig-mitigation-1": _restore_target_port("user-mention-service"),
    "dev-scale_pod_zero_social_net-mitigation-1": [
        ["scale", "deployment", "media-service", "--replicas=1"],
        ["rollout", "status", "deployment/media-service", "--timeout=300s"],
    ],
    "dev-assign_to_non_existent_node_social_net-mitigation-1": [
        [
            "patch", "deployment", "unique-id-service", "--type=json",
            "-p", '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]',
        ],
        ["rollout", "status", "deployment/unique-id-service", "--timeout=300s"],
    ],
    "pool-assign_to_non_existent_node_social_net-mitigation-1": [
        [
            "patch", "deployment", "user-service", "--type=json",
            "-p", '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]',
        ],
        ["rollout", "status", "deployment/user-service", "--timeout=300s"],
    ],
    "pool-k8s_target_port-misconfig-mitigation-4": _restore_target_port("compose-post-service"),
    "pool-k8s_target_port-misconfig-mitigation-5": _restore_target_port("home-timeline-service"),
    "pool-k8s_target_port-misconfig-mitigation-6": _restore_target_port("user-timeline-service"),
    "pool-scale_pod_zero_social_net-mitigation-2": [
        ["scale", "deployment", "text-service", "--replicas=1"],
        ["rollout", "status", "deployment/text-service", "--timeout=300s"],
    ],
    "pool-scale_pod_zero_social_net-mitigation-3": [
        ["scale", "deployment", "post-storage-service", "--replicas=1"],
        ["rollout", "status", "deployment/post-storage-service", "--timeout=300s"],
    ],
    "conf-k8s_target_port-misconfig-mitigation-1": [
        [
            "patch", "service", "social-graph-service", "--type=json",
            "-p", json.dumps([{"op": "replace", "path": "/spec/ports/0/targetPort",
                               "value": TARGET_PORT}]),
        ],
    ],
    "conf-scale_pod_zero_social_net-mitigation-1": [
        ["scale", "deployment", "social-graph-service", "--replicas=1"],
        ["rollout", "status", "deployment/social-graph-service", "--timeout=300s"],
    ],
    "conf-assign_to_non_existent_node_social_net-mitigation-1": [
        [
            "patch", "deployment", "social-graph-service", "--type=json",
            "-p", '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]',
        ],
        ["rollout", "status", "deployment/social-graph-service", "--timeout=300s"],
    ],
}  # fmt: skip


def action(call: str) -> str:
    return f"```\n{call}\n```"


def exec_shell(command: str, timeout: int | None = None) -> str:
    # AIOpsLab's parser strips every double quote at both ends of the command, so a command that
    # ends in one would lose its last character when parsed
    if command.endswith('"'):
        raise ValueError(
            f"a command ending in a double quote does not survive the parser: {command}"
        )
    extra = "" if timeout is None else f", timeout={int(timeout)}"
    return action('exec_shell("' + command.replace('"', '\\"') + '"' + extra + ")")


def tool_call(tool: str, arguments: Mapping[str, object]) -> str:
    """The action text for a tool call a planner made.

    Arguments go by keyword, so the order of the keys in the model's JSON never decides which
    parameter gets which value (get_logs takes namespace first, then service).
    """
    if tool == "exec_shell":
        return exec_shell(arguments["command"], arguments.get("timeout"))
    args = ", ".join(f"{name}={json.dumps(value)}" for name, value in arguments.items())
    return action(f"{tool}({args})")


def known_fix(problem_id: str, namespace: str) -> list[str]:
    # the command runs inside the kind control plane node, where the context is the default one
    return [shlex.join(["kubectl", "-n", namespace, *args]) for args in FIXES[problem_id]]


def select(requested: Iterable[str], confirmation: Set[str]) -> list[str]:
    """The overlay ids to validate: the requested ones, or with none requested every overlay
    problem outside the confirmation slice, so a routine revalidation never touches it.

    log_creation refuses a second creation of a confirmation problem.
    """
    requested = list(dict.fromkeys(requested))
    if not requested:
        return [i for i in FIXES if i not in confirmation]
    unknown = [i for i in requested if i not in FIXES]
    if unknown:
        raise ValueError(f"not overlay problems: {', '.join(unknown)}")
    return requested
