"""Hand run for story E9-S1 (gate G2): five timed rollouts on development problems.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI. No model runs and the LLM judge
must be off. Development problems only: the run manifests record the slice and no touch is
appended to runs/touch_log.jsonl.

A rollout is one injection, the known fix sent as exec_shell actions through the Orchestrator's
parser and action handlers, the harm window of detect_harm (one settle window, default 120 s) and
AIOpsLab's recover_fault. The phases are timed apart, so a per-rollout cost can be built from them
under any rollout shape:

    inject, pre (the pod status taken before the action, waiting for the fault to show where it
    shows late), snapshot (the objects the fix touches), execute, settle (detect_harm), recover.

The five rollouts are the three development problems and a repeat of the first two. The manifests
and a summary go to runs/manifests/, which git ignores.

    uv run python scripts/run_pilot.py [--settle SECONDS]
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, stdev

from aiopslab.paths import config
from aiopslab.session import Session

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.harm import SETTLE_SECONDS, detect_harm, kubectl_pods, parse_pods
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    load_registry,
    start_run,
    write_manifest,
)
from remvalgate.harness.snapshot import kubectl_get, take, touched
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import TARGET_PORT, exec_shell, known_fix

NS = "test-social-network"
CONTEXT = "kind-kind"
ROOT = Path(__file__).resolve().parents[1]
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
OUT_DIR = ROOT / "runs" / "manifests"
SETTLE_ENV = "AIOPSLAB_MITIGATION_SETTLE_SECONDS"
AGENT = "scripted-known-fix"
POLICY = load_policy(DEFAULT_POLICY_PATH)
TARGET_PORT_ID = "dev-k8s_target_port-misconfig-mitigation-1"
SCALE_ID = "dev-scale_pod_zero_social_net-mitigation-1"
ASSIGN_ID = "dev-assign_to_non_existent_node_social_net-mitigation-1"
ROLLOUTS = [TARGET_PORT_ID, SCALE_ID, ASSIGN_ID, TARGET_PORT_ID, SCALE_ID]
PHASES = ("inject", "pre", "snapshot", "execute", "settle", "recover")


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


def target_port_remains() -> bool:
    # the target-port eval also needs every pod healthy, so the Service spec answers instead
    svc = json.loads(kubectl("get", "svc", "user-mention-service", "-o", "json").stdout)
    return svc["spec"]["ports"][0]["targetPort"] != TARGET_PORT


def eval_remains(prob):
    def remains() -> bool:
        # detect_harm spent the settle window, so the eval needs none
        previous = os.environ.get(SETTLE_ENV)
        os.environ[SETTLE_ENV] = "0"
        try:
            return not prob.eval("", [], 0.0)["success"]
        finally:
            if previous is None:
                del os.environ[SETTLE_ENV]
            else:
                os.environ[SETTLE_ENV] = previous

    return remains


def wait_until_unhealthy(key: str, timeout: float = 90) -> None:
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        status = pods().get(key)
        if status is not None and not status.healthy:
            return
        time.sleep(0.5)
    raise AssertionError(f"{key} is not unhealthy in pod status after {timeout} s")


class ScriptedAgent:
    def __init__(self, actions: list[str]):
        self._actions = iter(actions)

    async def get_action(self, _observation: str) -> str:
        return next(self._actions)


async def execute(orch, prob, problem_id: str, commands: list[str], results_dir: Path) -> None:
    actions = [exec_shell(command) for command in commands]
    orch.register_agent(ScriptedAgent(actions), name=AGENT)
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent(AGENT)
    orch.session.start()
    for expected, command in zip(actions, commands, strict=True):
        reply = await orch.ask_agent("Please take the next action")
        check(reply == expected, f"the agent replied {reply!r}")
        # a parse that changes the command would run something else than the known fix
        check(orch.parser.parse(reply)["args"] == [command], f"the parser changed {command!r}")
        response = await orch.ask_env(reply)
        check(not response.startswith(("[ERROR]", "Error", "Failed")), response)
    orch.session.end()


async def rollout(orch, problem_id: str, clean: dict, settle: float, results_dir: Path) -> dict:
    start_all = time.perf_counter()
    prob = orch.probs.get_problem_instance(problem_id)
    commands = known_fix(problem_id, NS)
    # rollout status only waits, so it changes no object and needs no snapshot
    mutating = [c for c in commands if " rollout status " not in c]
    remains = target_port_remains if problem_id == TARGET_PORT_ID else eval_remains(prob)
    times: dict[str, float] = {}
    harmful = remaining = None
    try:
        start = time.perf_counter()
        prob.inject_fault()
        times["inject"] = time.perf_counter() - start

        start = time.perf_counter()
        if problem_id == ASSIGN_ID:
            wait_until_unhealthy(f"Deployment/{prob.faulty_service}")
        pre = pods()
        times["pre"] = time.perf_counter() - start

        start = time.perf_counter()
        refs = sorted({ref for c in mutating for ref in touched(c, POLICY, NS)})
        check(refs, f"{problem_id}: the fix touches no object")
        take(NS, refs, kubectl_get(NS, CONTEXT))
        times["snapshot"] = time.perf_counter() - start

        start = time.perf_counter()
        await execute(orch, prob, problem_id, commands, results_dir)
        times["execute"] = time.perf_counter() - start

        start = time.perf_counter()
        result = detect_harm(
            pre, lambda: kubectl_pods(NS, CONTEXT), remains, NS, settle_seconds=settle
        )
        times["settle"] = time.perf_counter() - start
        harmful, remaining = result.harmful, result.fault_remains
    finally:
        start = time.perf_counter()
        prob.recover_fault()
        kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s")
        times["recover"] = time.perf_counter() - start
    left = changed(clean, objects())
    check(not left, f"{problem_id}: the reset left the cluster changed: {left}")
    check(harmful is False, f"{problem_id}: the known fix was harmful")
    check(remaining is False, f"{problem_id}: the fault remains after the known fix")
    times["phases"] = sum(times[p] for p in PHASES)
    times["wall"] = time.perf_counter() - start_all
    return times


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
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    orch = new_orchestrator(results_dir=OUT_DIR)
    kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = objects()
    check(all(s.healthy for s in pods().values()), "the clean cluster has an unhealthy pod")
    nodes = len(kubectl("get", "nodes", "-o", "name").stdout.split())
    print(f"cluster {CONTEXT}: {nodes} nodes, {len(pods())} workloads, settle {settle} s")

    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    rows = []
    for index, problem_id in enumerate(ROLLOUTS, start=1):
        now = datetime.now(UTC)
        run_id = f"pilot-{stamp}-{index}-{problem_id.removeprefix('dev-').split('-')[0]}"
        # a development problem is not logged, so this appends no touch
        run = start_run(run_id, problem_id, registry, TOUCH_LOG, ROOT, pinned_commit(ROOT), now)
        times = asyncio.run(rollout(orch, problem_id, clean, settle, OUT_DIR / run_id))
        manifest = Manifest(
            run_id=run_id,
            problem_id=problem_id,
            slice=run.slice,
            commit=run.commit,
            dirty=run.dirty,
            model_name=AGENT,
            model_version="1",
            model_date=now.strftime("%Y-%m-%d"),
            seed=0,
            k=1,
            r=1,
            aiopslab_commit=run.aiopslab_commit,
            cluster={"context": CONTEXT, "namespace": NS, "nodes": nodes},
            timestamp=run.timestamp,
        )
        path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
        path.with_suffix(".timings.json").write_text(json.dumps(times, indent=2, sort_keys=True))
        rows.append({"run_id": run_id, "problem_id": problem_id, **times})
        print(f"{index} {problem_id}: " + ", ".join(f"{k} {v:.1f}" for k, v in times.items()))

    summary = {"settle_seconds": settle, "rollouts": rows}
    for name in (*PHASES, "phases", "wall"):
        values = [row[name] for row in rows]
        summary[name] = {"mean": mean(values), "sd": stdev(values)}
    path = OUT_DIR / f"pilot-{stamp}.summary.json"
    path.write_text(json.dumps(summary, indent=2, sort_keys=True))
    print(f"summary {path}")
    for name in (*PHASES, "phases", "wall"):
        print(f"{name}: mean {summary[name]['mean']:.1f} s, sd {summary[name]['sd']:.1f} s")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
