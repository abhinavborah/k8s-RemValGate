"""Known fixes of the overlay problems, used by scripts/validate_overlay_problems.py.

Kept apart from that script, which imports aiopslab, so a test checks the keys in CI.
"""

import shlex

# problem id -> the known fix, as kubectl argument lists run in order
FIXES = {
    "dev-k8s_target_port-misconfig-mitigation-1": [
        [
            "patch", "service", "user-mention-service", "--type=json",
            "-p", '[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 9090}]',
        ],
    ],
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
}  # fmt: skip


def action(call: str) -> str:
    return f"```\n{call}\n```"


def exec_shell(command: str) -> str:
    # AIOpsLab's parser strips every double quote at both ends of the command, so a command that
    # ends in one would lose its last character when parsed
    if command.endswith('"'):
        raise ValueError(
            f"a command ending in a double quote does not survive the parser: {command}"
        )
    return action('exec_shell("' + command.replace('"', '\\"') + '")')


def known_fix(problem_id: str, namespace: str) -> list[str]:
    # the command runs inside the kind control plane node, where the context is the default one
    return [shlex.join(["kubectl", "-n", namespace, *args]) for args in FIXES[problem_id]]
