"""Hand run for story E1-S3: one development problem end to end through AIOpsLab's interface.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI. No model runs and the LLM judge
must be off. The agent is a script that sends the known fix as exec_shell actions and then
submits, through the Orchestrator's own parser and action handlers (ask_agent and ask_env).

Orchestrator.init_problem and start_problem are not used: they install OpenEBS and Prometheus
from the network, redeploy the application and delete it at the end, which is neither the cost
of a rollout nor a state the next run can start from.

Two cycles on the same problem, each timed by phase and in total (wall, from building the
problem to the last check):

1. run: inject, check the cluster differs from the clean snapshot, send the fix and submit,
   check the evaluator reports success, reset, check the cluster equals the clean snapshot.
2. reset only: inject, reset without the fix, check the cluster equals the clean snapshot.
   Cycle 1 resets a cluster the fix already repaired, so only this cycle tests the reset.

The reset is AIOpsLab's recover_fault, followed by all pods Ready. A development run is not an
evaluation-set touch, so nothing is appended to runs/touch_log.jsonl. The manifest and the
timings go to runs/manifests/, which git ignores.

    uv run python scripts/run_dev_end_to_end.py
"""

import asyncio
import json
import os
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from aiopslab.paths import config
from aiopslab.session import Session
from aiopslab.utils.status import SubmissionStatus

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    load_registry,
    start_run,
    write_manifest,
)
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import FIXES

NS = "test-social-network"
ROOT = Path(__file__).resolve().parents[1]
TOUCH_LOG = ROOT / "runs" / "touch_log.jsonl"
OUT_DIR = ROOT / "runs" / "manifests"
PROBLEM = "dev-k8s_target_port-misconfig-mitigation-1"
AGENT = "scripted-known-fix"
MODEL_VERSION = "1"


def kubectl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["kubectl", "--context", "kind-kind", *args], capture_output=True, text=True, check=True
    )


def snapshot() -> dict:
    return normalise(json.loads(kubectl("-n", NS, "get", "svc,deploy", "-o", "json").stdout))


def reset(prob) -> None:
    # AIOpsLab's recover step, then the README health check
    prob.recover_fault()
    kubectl("-n", NS, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s")


def action(call: str) -> str:
    return f"```\n{call}\n```"


def exec_shell(command: str) -> str:
    return action('exec_shell("' + command.replace('"', '\\"') + '")')


def known_fix(problem_id: str) -> list[str]:
    # the command runs inside the kind control plane node, where the context is the default one
    return [shlex.join(["kubectl", "-n", NS, *args]) for args in FIXES[problem_id]]


class ScriptedAgent:
    def __init__(self, actions: list[str]):
        self._actions = iter(actions)

    async def get_action(self, _observation: str) -> str:
        return next(self._actions)


async def run_cycle(orch, problem_id: str, clean: dict, results_dir: Path) -> tuple[dict, dict]:
    """Inject, fix through the interface, evaluate, reset. Returns the timings and the results."""
    cycle_start = time.perf_counter()
    times: dict[str, float] = {}
    commands = known_fix(problem_id)
    actions = [*map(exec_shell, commands), action("submit()")]
    orch.register_agent(ScriptedAgent(actions), name=AGENT)
    prob = orch.probs.get_problem_instance(problem_id)
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent(AGENT)

    try:
        start = time.perf_counter()
        prob.inject_fault()
        times["inject"] = time.perf_counter() - start
        assert changed(clean, snapshot()), "the injection left the cluster unchanged"

        start = time.perf_counter()
        orch.session.start()
        for expected, command in zip(actions, [*commands, None], strict=True):
            reply = await orch.ask_agent("Please take the next action")
            assert reply == expected
            if command:
                # a parse that changes the command would run something else than the known fix
                assert orch.parser.parse(reply)["args"] == [command]
            response = await orch.ask_env(reply)
            if command:
                assert not response.startswith(("[ERROR]", "Error", "Failed")), response
        assert response == SubmissionStatus.VALID_SUBMISSION, response
        orch.session.end()
        times["fix"] = time.perf_counter() - start

        start = time.perf_counter()
        os.environ["AIOPSLAB_MITIGATION_SETTLE_SECONDS"] = "120"
        session = orch.session
        results = prob.eval(session.solution, session.history, session.get_duration())
        times["eval"] = time.perf_counter() - start
        session.set_results(results)
        session.to_json()
        assert results["success"], "the evaluator reports failure after the known fix"
    finally:
        start = time.perf_counter()
        reset(prob)
        times["reset"] = time.perf_counter() - start
    left = changed(clean, snapshot())
    assert not left, f"the reset left the cluster changed: {left}"
    times["phases"] = sum(times.values())
    times["wall"] = time.perf_counter() - cycle_start
    return times, results


def reset_only(orch, problem_id: str, clean: dict) -> dict:
    cycle_start = time.perf_counter()
    prob = orch.probs.get_problem_instance(problem_id)
    times: dict[str, float] = {}
    try:
        start = time.perf_counter()
        prob.inject_fault()
        times["inject"] = time.perf_counter() - start
        assert changed(clean, snapshot()), "the injection left the cluster unchanged"
    finally:
        start = time.perf_counter()
        reset(prob)
        times["reset"] = time.perf_counter() - start
    left = changed(clean, snapshot())
    assert not left, f"the reset left a faulted cluster changed: {left}"
    times["phases"] = sum(times.values())
    times["wall"] = time.perf_counter() - cycle_start
    return times


def main() -> int:
    problem_id = PROBLEM
    # AIOpsLab's injectors run kubectl in the current context, the checks here in kind-kind
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if current != "kind-kind":
        raise AssertionError(f"current kubectl context is {current!r}, expected 'kind-kind'")
    if config.get("qualitative_eval"):
        raise AssertionError("set qualitative_eval: false in aiopslab/config.yml")
    if config.get("k8s_host") != "kind":
        raise AssertionError("set k8s_host: kind in aiopslab/config.yml")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    now = datetime.now(UTC)
    run_id = f"e2e-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}"
    # a development problem is not logged, so this appends no touch
    run = start_run(run_id, problem_id, registry, TOUCH_LOG, ROOT, pinned_commit(ROOT), now)

    orch = new_orchestrator(results_dir=OUT_DIR / run_id)
    kubectl("-n", NS, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = snapshot()
    times, results = asyncio.run(run_cycle(orch, problem_id, clean, OUT_DIR / run_id))
    reset_times = reset_only(orch, problem_id, clean)

    manifest = Manifest(
        run_id=run_id,
        problem_id=problem_id,
        slice=run.slice,
        commit=run.commit,
        dirty=run.dirty,
        model_name=AGENT,
        model_version=MODEL_VERSION,
        model_date=now.strftime("%Y-%m-%d"),
        seed=0,
        k=1,
        r=1,
        aiopslab_commit=run.aiopslab_commit,
        cluster={"context": "kind-kind", "namespace": NS, "nodes": 2},
        timestamp=run.timestamp,
    )
    path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
    timings = {"run": times, "reset_only": reset_times, "success": results["success"]}
    path.with_suffix(".timings.json").write_text(json.dumps(timings, indent=2, sort_keys=True))
    print(f"{problem_id}: PASS, manifest {path}")
    for name, cycle in (("run", times), ("reset only", reset_times)):
        print(f"{name}: " + ", ".join(f"{k} {v:.1f} s" for k, v in cycle.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
