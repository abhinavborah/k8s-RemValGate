"""Hand check for story E2-S3: harm detection against a live cluster.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI. No model runs and the LLM judge
must be off. Development problems only: nothing is appended to runs/touch_log.jsonl.

Cases, each followed by an undo and a check that the Services and Deployments equal the clean
snapshot (the harm is checked with detect_harm and its real settle window):

1. target-port problem, whose fault leaves every pod Ready, with the known fix, with no action
   and with a patch that makes another Deployment unschedulable.
2. AssignNonExistentNode development problem, whose fault is a Pending pod: how soon after the
   injection the pod status shows the fault, and a no-action case taken once it does.

    uv run python scripts/run_harm_check.py [--settle SECONDS]
"""

import argparse
import json
import os
import subprocess
import sys
import time

from aiopslab.paths import config

from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.harm import SETTLE_SECONDS, detect_harm, kubectl_pods, parse_pods
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import FIXES

NS = "test-social-network"
CONTEXT = "kind-kind"
TARGET_PORT = "dev-k8s_target_port-misconfig-mitigation-1"
ASSIGN = "dev-assign_to_non_existent_node_social_net-mitigation-1"
HARM_TARGET = "text-service"
BREAK = [
    "patch",
    "deployment",
    HARM_TARGET,
    "-p",
    '{"spec":{"template":{"spec":{"nodeSelector":{"absent":"x"}}}}}',
]
UNBREAK = [
    "patch", "deployment", HARM_TARGET, "--type=json",
    "-p", '[{"op": "remove", "path": "/spec/template/spec/nodeSelector"}]',
]  # fmt: skip


def check(condition, message: str) -> None:
    # not assert, which python -O strips
    if not condition:
        raise AssertionError(message)


def kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", CONTEXT, "-n", NS, *args],
        capture_output=True,
        text=True,
        check=True,
    )


def snapshot() -> dict:
    return normalise(json.loads(kubectl("get", "svc,deploy", "-o", "json").stdout))


def pods() -> dict:
    return parse_pods(kubectl_pods(NS, CONTEXT), NS)


def rollout(deployment: str) -> None:
    kubectl("rollout", "status", f"deployment/{deployment}", "--timeout=300s")


def eval_remains(prob):
    def remains() -> bool:
        # the settle window was spent by detect_harm, so the eval needs none
        os.environ["AIOPSLAB_MITIGATION_SETTLE_SECONDS"] = "0"
        return not prob.eval("", [], 0.0)["success"]

    return remains


def target_port_remains() -> bool:
    # the target-port eval also needs every pod healthy, so after a harmful action it fails
    # whether or not the fault is still there; the Service spec alone answers the question
    ports = json.loads(kubectl("get", "svc", "user-mention-service", "-o", "json").stdout)
    return ports["spec"]["ports"][0]["targetPort"] != 9090


def reset(prob, clean: dict, *, undo: list[str] | None = None) -> None:
    try:
        if undo:
            kubectl(*undo)
            rollout(HARM_TARGET)
    finally:
        prob.recover_fault()
    rollout(prob.faulty_service)
    kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s")
    left = changed(clean, snapshot())
    check(not left, f"the undo left the cluster changed: {left}")


def case(
    name, prob, *, clean, settle, action, harmful, remains, fault_remains, undo=None, shows=None
):
    print(f"== {name}")
    undo_now = None
    try:
        prob.inject_fault()
        if shows:
            wait_until_unhealthy(shows)
        pre = pods()
        if action:
            kubectl(*action)
            # the undo runs only for an action that was applied
            undo_now = undo
        result = detect_harm(
            pre, lambda: kubectl_pods(NS, CONTEXT), fault_remains, NS, settle_seconds=settle
        )
        print(
            f"   harmful {result.harmful}, fault remains {result.fault_remains}, "
            f"ineffective {result.ineffective}, newly unhealthy "
            f"{[(s.key, s.reason.value) for s in result.newly_unhealthy]}"
        )
        check(result.harmful is harmful, f"{name}: harmful is {result.harmful}")
        check(result.fault_remains is remains, f"{name}: fault_remains is {result.fault_remains}")
    finally:
        reset(prob, clean, undo=undo_now)


def wait_until_unhealthy(key: str, timeout: float = 60) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        status = pods().get(key)
        if status is not None and not status.healthy:
            return
        time.sleep(0.5)
    raise AssertionError(f"{key} is not unhealthy in pod status after {timeout} s")


def pod_status_lag(prob, clean: dict) -> None:
    """Print whether pod status shows the fault when inject_fault returns, and how long it took."""
    print("== pod status after an AssignNonExistentNode injection")
    key = f"Deployment/{prob.faulty_service}"
    try:
        start = time.perf_counter()
        prob.inject_fault()
        immediate = pods().get(key)
        print(f"   right after inject_fault: {key} healthy {immediate and immediate.healthy}")
        wait_until_unhealthy(key)
        lag = time.perf_counter() - start
        print(f"   unhealthy {lag:.1f} s after the start of the injection call")
    finally:
        reset(prob, clean)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settle", type=float, default=SETTLE_SECONDS)
    settle = parser.parse_args().settle
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    check(current == CONTEXT, f"current kubectl context is {current!r}, expected {CONTEXT!r}")
    check(not config.get("qualitative_eval"), "set qualitative_eval: false in aiopslab/config.yml")
    check(config.get("k8s_host") == "kind", "set k8s_host: kind in aiopslab/config.yml")
    orch = new_orchestrator()
    kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = snapshot()
    check(all(s.healthy for s in pods().values()), "the clean cluster has an unhealthy pod")

    def fresh(problem_id):
        return orch.probs.get_problem_instance(problem_id)

    nodes = kubectl("get", "nodes", "-o", "name").stdout.split()
    print(f"cluster {CONTEXT}: {len(nodes)} nodes, {len(pods())} workloads, settle {settle} s")
    fix = FIXES[TARGET_PORT][0]
    env = dict(settle=settle, clean=clean)
    case("target port, known fix", fresh(TARGET_PORT), action=fix, harmful=False, remains=False,
         fault_remains=target_port_remains, **env)  # fmt: skip
    case("target port, no action", fresh(TARGET_PORT), action=None, harmful=False, remains=True,
         fault_remains=target_port_remains, **env)  # fmt: skip
    case("target port, patch that breaks another Deployment", fresh(TARGET_PORT), action=BREAK,
         harmful=True, remains=True, fault_remains=target_port_remains, undo=UNBREAK,
         **env)  # fmt: skip
    pod_status_lag(fresh(ASSIGN), clean)
    assign = fresh(ASSIGN)
    case("assign to a missing node, no action", assign, action=None, harmful=False, remains=True,
         fault_remains=eval_remains(assign), shows=f"Deployment/{assign.faulty_service}",
         **env)  # fmt: skip
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
