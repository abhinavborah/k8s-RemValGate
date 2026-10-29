"""Hand check for story E2-S2: snapshot and restore against a live cluster.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI. No model runs and the LLM judge
must be off. Development problems only: nothing is appended to runs/touch_log.jsonl.

For each of the three object-state problem types (TargetPortMisconfig, ScalePod,
AssignNonExistentNode) the fault is injected, then three commands run in turn on the object the
known fix touches. Each is followed by a restore and a check that the Services and Deployments
equal what they were before the command:

1. the known fix,
2. a command that damages the object,
3. a delete of the object, which the allow-list does not admit but the restore must survive.

Each case ends with AIOpsLab's recover_fault and a check against the clean cluster.

    uv run python scripts/run_restore_check.py
"""

import json
import shlex
import subprocess
import sys

from aiopslab.paths import config

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.snapshot import kubectl_get, kubectl_put, restore, take, touched
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import FIXES

NS = "test-social-network"
CONTEXT = "kind-kind"
POLICY = load_policy(DEFAULT_POLICY_PATH)
NODE_SELECTOR = '{"spec":{"template":{"spec":{"nodeSelector":{"absent":"x"}}}}}'
# problem id, injector fault_type, object the known fix touches, a damaging command on it
CASES = [
    (
        "dev-k8s_target_port-misconfig-mitigation-1",
        "misconfig_k8s",
        "service/user-mention-service",
        ["patch", "service", "user-mention-service", "--type=json", "-p",
         '[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 1234}]'],
    ),
    (
        "dev-scale_pod_zero_social_net-mitigation-1",
        "scale_pods_to_zero",
        "deployment/media-service",
        ["scale", "deployment", "media-service", "--replicas=3"],
    ),
    (
        "dev-assign_to_non_existent_node_social_net-mitigation-1",
        "assign_to_non_existent_node",
        "deployment/unique-id-service",
        ["patch", "deployment", "unique-id-service", "-p", NODE_SELECTOR],
    ),
]  # fmt: skip


def check(condition, message: str) -> None:
    # not assert, which python -O strips
    if not condition:
        raise AssertionError(message)


def kubectl(*args: str, ok: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", NS, *args],
        capture_output=True,
        text=True,
        check=not ok,
    )


# Services the restore created again: they get new addresses, which the restore does not compare
RECREATED: set[str] = set()


def objects() -> dict:
    return normalise(json.loads(kubectl("get", "svc,deploy", "-o", "json").stdout))


def differs(before: dict, after: dict) -> list[str]:
    def view(state: dict) -> dict:
        state = json.loads(json.dumps(state))
        for key in RECREATED & state.keys():
            for field in ("clusterIP", "clusterIPs"):
                state[key]["spec"].pop(field, None)
        return state

    return changed(view(before), view(after))


def settle(target: str) -> None:
    if target.startswith("deployment/"):
        kubectl("rollout", "status", target, "--timeout=300s")


def command(args: list[str]) -> str:
    return shlex.join(["kubectl", *args, "-n", NS])


def run_case(label, fault_type, target, args, get, put, act) -> None:
    print(f"   {label}")
    refs = touched(command(args), POLICY, NS) if args else [_ref(target)]
    check(refs, f"{label}: the command touches nothing")
    snapshot = take(NS, refs, get)
    before = objects()
    act(args)
    result = restore(fault_type, snapshot, get, put)
    RECREATED.update(f"{kind}/{name}" for kind, name in result.recreated)
    left = differs(before, objects())
    print(
        f"      restored {result.restored}, recreated {list(result.recreated)}, left {left}, "
        f"errors {list(result.errors)}"
    )
    check(result.restored, f"{label}: restore reports {result.changed}")
    check(not left, f"{label}: the cluster differs from before the command: {left}")


def _ref(target: str):
    kind, _, name = target.partition("/")
    return kind.capitalize(), name


def main() -> int:
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    check(current == CONTEXT, f"current kubectl context is {current!r}, expected {CONTEXT!r}")
    check(not config.get("qualitative_eval"), "set qualitative_eval: false in aiopslab/config.yml")
    check(config.get("k8s_host") == "kind", "set k8s_host: kind in aiopslab/config.yml")
    orch = new_orchestrator()
    kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = objects()
    get, put = kubectl_get(NS, CONTEXT), kubectl_put(NS, CONTEXT)

    for problem_id, fault_type, target, damage in CASES:
        print(f"== {problem_id}")
        prob = orch.probs.get_problem_instance(problem_id)
        fix = FIXES[problem_id][0]
        try:
            prob.inject_fault()
            for label, args, action in (
                ("known fix", fix, lambda a: kubectl(*a)),
                ("damaging command", damage, lambda a: kubectl(*a)),
                ("delete", None, lambda _, t=target: kubectl("delete", t, "--wait=true")),
            ):
                run_case(label, fault_type, target, args, get, put, action)
        finally:
            prob.recover_fault()
            settle(target)
            kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s")
        left = differs(clean, objects())
        check(not left, f"{problem_id}: the reset left the cluster changed: {left}")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
