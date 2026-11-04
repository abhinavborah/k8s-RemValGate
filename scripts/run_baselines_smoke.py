"""Hand run for story E6-S1: baselines 1 and 2 on each development problem.

Needs aiopslab/config.yml, the kind cluster and Social Network deployed and Ready (README,
Deploy and health check), so it is run by hand and never by CI. No language model runs and the
LLM judge must be off. Baseline 2 retrieves with configuration (iii) over the synthetic stub corpus
(story E4-S6), so the script needs the retrieval dependency group (uv sync --group retrieval).
Development problems only: nothing is appended to runs/touch_log.jsonl, and nothing here is a
reported result, so no manifest is written.

For each development problem and each baseline:

1. inject the fault and wait until the pod and object state shows it,
2. take the pod status before the action, and read the state with observe,
3. ask the baseline for its single action and apply the allow-list to it,
4. if admitted, send it through the Orchestrator's own parser and action handlers as exec_shell,
5. wait the settle window, difference the pod status and ask whether the fault remains,
6. classify the outcome, then reset and check the cluster equals the clean snapshot.

A baseline that escalates, or whose command the allow-list rejects, applies nothing. The
development problems have a known fix, so the oracle label of an unapplied result is True.

    uv run python scripts/run_baselines_smoke.py [--settle SECONDS]
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from aiopslab.paths import config
from aiopslab.session import Session

from remvalgate.baselines import BaselineAction
from remvalgate.baselines.observe import observe
from remvalgate.baselines.retrieval_only import retrieval_only
from remvalgate.baselines.rule import fault_type_rule
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.allowlist import check_chain as admit
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.harm import SETTLE_SECONDS, detect_harm, kubectl_pods, parse_pods
from remvalgate.harness.manifest import DEFAULT_REGISTRY_PATH, Slice, load_registry
from remvalgate.harness.outcome import Outcome, classify, single_action_result
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import TARGET_PORT, CannotSend, carried_action
from remvalgate.retrieval.fixed import development_retriever

NS = "test-social-network"
CONTEXT = "kind-kind"
SETTLE_ENV = "AIOPSLAB_MITIGATION_SETTLE_SECONDS"
OUT_DIR = Path(__file__).resolve().parents[1] / "runs" / "manifests"
AGENT = "scripted-baseline"
SHOWS_TIMEOUT = 90.0


def require(condition, message: str) -> None:
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


def listing() -> dict:
    return json.loads(kubectl("get", "deploy,svc,pods", "-o", "json").stdout)


def snapshot() -> dict:
    return normalise(json.loads(kubectl("get", "svc,deploy", "-o", "json").stdout))


def wait_for_observation(service: str, timeout: float = SHOWS_TIMEOUT):
    """Poll until an observation names the injected service, so a state that was already there
    cannot stand in for the injection. A target-port fault is never observed when the containers
    declare no ports, and this then times out."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        found = observe(listing())
        if any(o.service == service for o in found):
            return found
        time.sleep(2)
    raise AssertionError(f"no observation naming {service} within {timeout} s of the injection")


def fault_remains(prob, problem_id: str):
    def eval_remains() -> bool:
        # the settle window was spent by detect_harm, so the eval needs none
        previous = os.environ.get(SETTLE_ENV)
        os.environ[SETTLE_ENV] = "0"
        try:
            return not prob.eval("", [], 0.0)["success"]
        finally:
            if previous is None:
                del os.environ[SETTLE_ENV]
            else:
                os.environ[SETTLE_ENV] = previous

    def target_port_remains() -> bool:
        # the target-port eval also needs every pod healthy, so it fails after a harmful action
        # whether or not the fault is still there; the Service spec alone answers the question
        svc = json.loads(kubectl("get", "svc", prob.faulty_service, "-o", "json").stdout)
        return svc["spec"]["ports"][0]["targetPort"] != TARGET_PORT

    return target_port_remains if "target_port" in problem_id else eval_remains


class ScriptedAgent:
    def __init__(self, actions: list[str]):
        self._actions = iter(actions)

    async def get_action(self, _observation: str) -> str:
        return next(self._actions)


async def apply(
    orch, prob, problem_id: str, command: str, results_dir: Path, strict: bool = True
) -> str:
    """Send one exec_shell action through the Orchestrator's parser and action handlers and
    return the response.

    strict suits the template baselines, whose commands are known to carry and to succeed: a
    command the parser would change, or an error response, raises. A free-form command (the
    planner's) is not strict: a command that cannot be sent unchanged raises CannotSend before
    anything runs, and an error response from kubectl is returned, since the command was applied.
    """
    action = carried_action(command, orch.parser.parse, strict=strict)
    orch.register_agent(ScriptedAgent([action]), name=AGENT)
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent(AGENT)
    orch.session.start()
    reply = await orch.ask_agent("Please take the next action")
    require(reply == action, f"the agent replied {reply!r}")
    response = await orch.ask_env(reply)
    orch.session.end()
    if strict:
        require(not response.startswith(("[ERROR]", "Error", "Failed")), response)
    return response


def reset(prob, clean: dict) -> None:
    # AIOpsLab's recover step, then the README health check, then the clean-state comparison
    prob.recover_fault()
    kubectl("rollout", "status", f"deployment/{prob.faulty_service}", "--timeout=300s")
    kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s")
    left = changed(clean, snapshot())
    require(not left, f"the reset left the cluster changed: {left}")


async def act(orch, prob, problem_id, command, policy, settle, pre, results_dir, strict=True):
    """Admit one command, apply it if admitted, wait the settle window and classify the outcome.

    pre is the pod status taken after the fault shows and before the action. strict is apply's.
    Returns the allow-list verdict (None when there is no command), the outcome, whether the
    command was sent, and a note: the response to a sent command, or why an admitted free-form
    command could not be sent, which is then classified as not applied. Also used by the untuned
    planner baselines in run_planner_dev.py, with strict off.
    """
    verdict = admit(command, policy, NS) if command else None
    note = None
    sent = False
    if verdict is not None and verdict.admitted:
        try:
            note = await apply(orch, prob, problem_id, command, results_dir, strict)
            sent = True
        except CannotSend as error:
            note = str(error)
    harm = None
    if sent:
        harm = detect_harm(
            pre,
            lambda: kubectl_pods(NS, CONTEXT),
            fault_remains(prob, problem_id),
            NS,
            settle_seconds=settle,
        )
    # the development problems have a known fix, so a working candidate exists
    outcome = classify(single_action_result(harm, True))
    require(isinstance(outcome, Outcome), f"{command!r}: no outcome")
    return verdict, outcome, sent, note


def run_one(orch, problem_id, name, baseline, clean, policy, settle) -> dict:
    prob = orch.probs.get_problem_instance(problem_id)
    # a fault already visible would be taken for the injection
    before = observe(listing())
    require(not before, f"the clean cluster already shows {before}")
    try:
        prob.inject_fault()
        observations = wait_for_observation(prob.faulty_service)
        pre = parse_pods(kubectl_pods(NS, CONTEXT), NS)
        action: BaselineAction = baseline(observations, problem_id)
        verdict, outcome, _, _ = asyncio.run(
            act(orch, prob, problem_id, action.command, policy, settle, pre, OUT_DIR / problem_id)
        )
        return {
            "problem": problem_id,
            "baseline": name,
            "command": action.command,
            "reason": action.reason,
            "allow_list": None if verdict is None else (verdict.reason or "admitted"),
            "outcome": str(outcome),
        }
    finally:
        reset(prob, clean)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settle", type=float, default=SETTLE_SECONDS)
    settle = parser.parse_args().settle

    # AIOpsLab's injectors run kubectl in the current context, the checks here in kind-kind
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    require(current == CONTEXT, f"current kubectl context is {current!r}, expected {CONTEXT!r}")
    require(
        not config.get("qualitative_eval"), "set qualitative_eval: false in aiopslab/config.yml"
    )
    require(config.get("k8s_host") == "kind", "set k8s_host: kind in aiopslab/config.yml")

    registry = load_registry(DEFAULT_REGISTRY_PATH)
    development = sorted(i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT)
    require(development, "the slice registry holds no development problem")
    retriever = development_retriever(registry)
    policy = load_policy(DEFAULT_POLICY_PATH)
    baselines = {
        "fault-type rule": lambda obs, pid: fault_type_rule(obs, NS),
        "retrieval only": lambda obs, pid: retrieval_only(retriever, obs, pid, NS),
    }

    orch = new_orchestrator(results_dir=OUT_DIR / "baselines-smoke")
    kubectl("wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = snapshot()
    rows = []
    for problem_id in development:
        for name, baseline in baselines.items():
            row = run_one(orch, problem_id, name, baseline, clean, policy, settle)
            rows.append(row)
            # printed as it returns, so a later failure keeps the rows already classified
            print(json.dumps(row, sort_keys=True), flush=True)
    print(f"result: {len(rows)} runs classified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
