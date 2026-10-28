"""Known fixes of the development problems, used by scripts/validate_dev_problems.py.

Kept apart from that script, which imports aiopslab, so a test checks the keys in CI.
"""

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
}  # fmt: skip
