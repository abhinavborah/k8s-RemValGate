"""Hand check for story E8-S2: snapshot, apply, check and restore against a live cluster.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI. No model runs and the LLM judge
must be off. Development problems only: nothing is appended to runs/touch_log.jsonl.

Each case injects a fault, runs apply_with_check with one command and the real settle window,
and checks the outcome. A case whose check fails must leave the Services and Deployments equal to
what they were after the injection. Every case ends with AIOpsLab's recover_fault and a check
against the clean cluster.

1. ScalePod, the known fix: the check passes, the change is kept, RECOVERED.
2. TargetPortMisconfig, a patch that leaves the wrong port: restored, APPLIED_INEFFECTIVE.
3. AssignNonExistentNode, a patch that makes another Deployment unschedulable: restored,
   APPLIED_HARMFUL.

The out-of-scope faults have no development problem; the unit tests cover them.

    uv run python scripts/run_execution_check.py [--settle SECONDS]
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import time

from aiopslab.paths import config

from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.execute import apply_with_check, kubectl_rolled_out, kubectl_run
from remvalgate.harness.harm import SETTLE_SECONDS, detect_harm, kubectl_pods, parse_pods
from remvalgate.harness.outcome import Outcome, classify
from remvalgate.harness.snapshot import kubectl_get, kubectl_put
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import FIXES
from remvalgate.overlay.fixes import TARGET_PORT as FIXED_PORT

NS = "test-social-network"
CONTEXT = "kind-kind"
SETTLE_ENV = "AIOPSLAB_MITIGATION_SETTLE_SECONDS"
POLICY = load_policy(DEFAULT_POLICY_PATH)
SCALE = "dev-scale_pod_zero_social_net-mitigation-1"
TARGET_PORT = "dev-k8s_target_port-misconfig-mitigation-1"
ASSIGN = "dev-assign_to_non_existent_node_social_net-mitigation-1"
WRONG_PORT = [
    "patch", "service", "user-mention-service", "--type=json",
    "-p", '[{"op": "replace", "path": "/spec/ports/0/targetPort", "value": 1234}]',
]  # fmt: skip
BREAK_OTHER = [
    "patch", "deployment", "text-service",
    "-p", '{"spec":{"template":{"spec":{"nodeSelector":{"absent":"x"}}}}}',
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


def objects() -> dict:
    return normalise(json.loads(kubectl("get", "svc,deploy", "-o", "json").stdout))


def pods() -> dict:
    return parse_pods(kubectl_pods(NS, CONTEXT), NS)


def eval_passes(prob):
    def passes() -> bool:
        # the settle window was spent by detect_harm, so the eval needs none
        previous = os.environ.get(SETTLE_ENV)
        os.environ[SETTLE_ENV] = "0"
        try:
            return prob.eval("", [], 0.0)["success"]
        finally:
            if previous is None:
                del os.environ[SETTLE_ENV]
            else:
                os.environ[SETTLE_ENV] = previous

    return passes


def target_port_remains() -> bool:
    # as in run_harm_check.py: the target-port eval also fails on an unrelated unhealthy pod
    ports = json.loads(kubectl("get", "svc", "user-mention-service", "-o", "json").stdout)
    return ports["spec"]["ports"][0]["targetPort"] != FIXED_PORT


def wait_until_faulted(key: str, timeout: float = 60) -> None:
    # ScalePod leaves the workload with no pods, so absent counts as faulted
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        status = pods().get(key)
        if status is None or not status.healthy:
            return
        time.sleep(0.5)
    raise AssertionError(f"{key} is still healthy in pod status after {timeout} s")


def case(name, prob, fault_type, args, expected, *, clean, settle, fault_remains=None):
    print(f"== {name}")
    passes = eval_passes(prob)
    try:
        prob.inject_fault()
        if fault_type != "misconfig_k8s":
            # the target-port fault leaves every pod Ready; the others show in pod status
            wait_until_faulted(f"Deployment/{prob.faulty_service}")
        injected = objects()
        pre = pods()
        execution = apply_with_check(
            shlex.join(["kubectl", *args, "-n", NS]),
            fault_type,
            True,
            policy=POLICY,
            namespace=NS,
            get=kubectl_get(NS, CONTEXT),
            put=kubectl_put(NS, CONTEXT),
            run=kubectl_run(CONTEXT),
            measure_harm=lambda: detect_harm(
                pre,
                lambda: kubectl_pods(NS, CONTEXT),
                fault_remains or (lambda: not passes()),
                NS,
                settle_seconds=settle,
            ),
            check=passes,
            rolled_out=kubectl_rolled_out(NS, CONTEXT),
        )
        outcome = classify(execution.result)
        restored = execution.restore and execution.restore.restored
        print(
            f"   exit {execution.exit_code}, check passed {execution.check_passed}, harmful "
            f"{execution.harm.harmful}, restored {restored}, rollout errors "
            f"{list(execution.rollout_errors)}, outcome {outcome}"
        )
        check(execution.exit_code == 0, f"{name}: the command failed")
        check(outcome is expected, f"{name}: outcome {outcome}, expected {expected}")
        check(not execution.rollout_errors, f"{name}: {execution.rollout_errors}")
        if execution.escalated:
            check(restored, f"{name}: restore reports {execution.restore}")
            left = changed(injected, objects())
            check(not left, f"{name}: the restore left the cluster changed: {left}")
    finally:
        prob.recover_fault()
        kubectl("rollout", "status", f"deployment/{prob.faulty_service}", "--timeout=300s")
        kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s")
    left = changed(clean, objects())
    check(not left, f"{name}: the reset left the cluster changed: {left}")


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
    clean = objects()
    check(all(s.healthy for s in pods().values()), "the clean cluster has an unhealthy pod")

    def fresh(problem_id):
        return orch.probs.get_problem_instance(problem_id)

    env = dict(clean=clean, settle=settle)
    case("ScalePod, known fix", fresh(SCALE), "scale_pods_to_zero", FIXES[SCALE][0],
         Outcome.RECOVERED, **env)  # fmt: skip
    case("target port, wrong port", fresh(TARGET_PORT), "misconfig_k8s", WRONG_PORT,
         Outcome.APPLIED_INEFFECTIVE, fault_remains=target_port_remains, **env)  # fmt: skip
    case("assign to a missing node, break another Deployment", fresh(ASSIGN),
         "assign_to_non_existent_node", BREAK_OTHER, Outcome.APPLIED_HARMFUL, **env)  # fmt: skip
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
