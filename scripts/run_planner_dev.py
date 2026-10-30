"""Hand run for story E5-S1: the planner on development problems, through AIOpsLab's interface.

Needs aiopslab/config.yml, the kind cluster with Social Network Ready (README), and an
OpenAI-compatible server with the planner model loaded (default LM Studio on port 1234), so it is
run by hand and never by CI. Development problems only: the run is not an evaluation-set touch and
appends nothing to runs/touch_log.jsonl.

Per problem: inject, run the planner (tool calls go through the Orchestrator's parser and action
handlers, so quoting is the one a real agent meets), check the cluster is unchanged by the
diagnosis, reset. The planner's command is checked by the allow-list and never run. The tools
write files to the current directory (get_traces does), so the script changes into a scratch
folder under runs/. The transcript and a manifest go to runs/manifests/, which git ignores.

    uv run python scripts/run_planner_dev.py --model-version "<format and quantization>" \
        [--no-thinking] [--problem ID ...]
"""

import argparse
import asyncio
import dataclasses
import inspect
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from aiopslab.paths import config
from aiopslab.session import Session
from run_dev_end_to_end import NS, TOUCH_LOG, check, kubectl, reset, snapshot

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, load_policy
from remvalgate.harness.cluster_state import changed
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    load_registry,
    start_run,
    write_manifest,
)
from remvalgate.harness.toolcall import DEFAULT_SCHEMAS_PATH, load_schemas
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import action, exec_shell
from remvalgate.planner.chat import OpenAIChat
from remvalgate.planner.loop import plan
from remvalgate.retrieval.stub import DevStubRetriever

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "manifests"
SCRATCH = ROOT / "runs" / "planner_scratch"
MODEL = "qwen/qwen3.5-4b"
PROBLEMS = (
    "dev-k8s_target_port-misconfig-mitigation-1",
    "dev-scale_pod_zero_social_net-mitigation-1",
    "dev-assign_to_non_existent_node_social_net-mitigation-1",
)
# the task text a planner gets: the symptom, not the fault, as an incident ticket would
TASK = (
    f"Users report errors from the social network application in namespace {NS}. "
    "Find the faulty service and give one command that fixes it."
)


def tool_action(tool: str, arguments: dict) -> str:
    if tool == "exec_shell":
        return exec_shell(arguments["command"])
    args = ", ".join(json.dumps(v) for v in arguments.values())
    return action(f"{tool}({args})")


async def run_problem(orch, problem_id: str, clean: dict, chat, results_dir: Path) -> dict:
    prob = orch.probs.get_problem_instance(problem_id)
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent("planner")
    loop = asyncio.get_running_loop()
    try:
        prob.inject_fault()
        check(changed(clean, snapshot()), "the injection left the cluster unchanged")
        orch.session.start()
        injected = snapshot()

        def run_tool(tool, arguments):
            # plan() runs in a worker thread, so the coroutine goes back to this loop
            future = asyncio.run_coroutine_threadsafe(
                orch.ask_env(tool_action(tool, arguments)), loop
            )
            return future.result()

        first = await orch.ask_env(exec_shell(f"kubectl -n {NS} get deployments,pods"))
        result = await asyncio.to_thread(
            plan,
            TASK,
            first,
            problem_id,
            NS,
            chat=chat,
            run_tool=run_tool,
            retriever=DevStubRetriever(load_registry(DEFAULT_REGISTRY_PATH)),
            policy=load_policy(DEFAULT_POLICY_PATH),
            schemas=load_schemas(DEFAULT_SCHEMAS_PATH),
        )
        orch.session.end()
        left = changed(injected, snapshot())
        check(not left, f"the diagnosis changed the cluster: {left}")
    finally:
        reset(prob)
    return dataclasses.asdict(result) | {"first_observation": first}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-version", required=True, help="format and quantization served")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--problem", action="append", choices=PROBLEMS)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()
    if config.get("qualitative_eval"):
        raise AssertionError("set qualitative_eval: false in aiopslab/config.yml")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    # thinking can use most of a turn's tokens before any content appears, so it gets more room
    chat = OpenAIChat(
        model=MODEL,
        thinking=args.thinking,
        seed=args.seed,
        max_tokens=4096 if args.thinking else 1024,
    )
    SCRATCH.mkdir(parents=True, exist_ok=True)
    os.chdir(SCRATCH)
    kubectl("-n", NS, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = snapshot()
    for problem_id in args.problem or PROBLEMS:
        now = datetime.now(UTC)
        mode = "think" if args.thinking else "nothink"
        run_id = f"planner-{problem_id}-{mode}-{now.strftime('%Y%m%dt%H%M%Sz')}".lower()
        run = start_run(run_id, problem_id, registry, TOUCH_LOG, ROOT, pinned_commit(ROOT), now)
        orch = new_orchestrator(results_dir=OUT_DIR / run_id)
        transcript = asyncio.run(run_problem(orch, problem_id, clean, chat, OUT_DIR / run_id))
        manifest = Manifest(
            run_id=run_id,
            problem_id=problem_id,
            slice=run.slice,
            commit=run.commit,
            dirty=run.dirty,
            model_name=MODEL,
            model_version=f"{args.model_version}; thinking={'on' if args.thinking else 'off'}",
            model_date=now.strftime("%Y-%m-%d"),
            seed=args.seed,
            k=1,
            r=1,
            aiopslab_commit=run.aiopslab_commit,
            cluster={"context": "kind-kind", "namespace": NS, "nodes": 2},
            timestamp=run.timestamp,
        )
        path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
        # the planner's defaults and the chat settings, so two runs can be told apart
        defaults = inspect.signature(plan).parameters
        transcript["config"] = {
            **dataclasses.asdict(chat),
            **{n: defaults[n].default for n in ("max_turns", "k", "max_observation_chars")},
        }
        path.with_suffix(".transcript.json").write_text(
            json.dumps(transcript, indent=2, sort_keys=True, default=str)
        )
        steps = transcript["steps"]
        verdict = transcript["verdict"] or {}
        tokens = sum(s["completion_tokens"] for s in steps)
        seconds = sum(s["seconds"] for s in steps)
        print(f"{problem_id} [{mode}]: stop={transcript['stop']} turns={len(steps)}")
        print(f"  diagnosis={transcript['diagnosis']}")
        print(f"  command={transcript['command']} admitted={verdict.get('admitted')}")
        print(f"  tokens={tokens} seconds={seconds:.1f}")
        print(f"  manifest {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
