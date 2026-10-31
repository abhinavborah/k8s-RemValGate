"""Hand run for stories E5-S1 and E5-S5: the planner on development problems, through AIOpsLab's
interface, then K candidate commands sampled from its diagnosis.

Needs aiopslab/config.yml, the kind cluster with Social Network Ready (README), and an
OpenAI-compatible server with the planner model loaded (default LM Studio on port 1234), so it is
run by hand and never by CI. Development problems only: the run is not an evaluation-set touch and
appends nothing to runs/touch_log.jsonl.

Per problem: inject, run the planner (tool calls go through the Orchestrator's parser and action
handlers, so quoting is the one a real agent meets), check the cluster is unchanged by the
diagnosis, reset. The planner's command and the K candidates are checked by the allow-list and
never run; a run that ends at the turn cap has no diagnosis and samples none. The tools
write files to the current directory (get_traces and get_metrics do), so the script changes into a
scratch folder under runs/. The transcript and a manifest go to runs/manifests/, which git ignores.

get_metrics needs Prometheus in the cluster (README, Telemetry) and a port-forward to 32000, which
the script starts. get_traces needs requests: after the injection the script runs the problem's
wrk2 workload, waits for it to finish and for the tracer's flush, and deletes the job afterwards.
The Jaeger sampler keeps 1 percent of requests, so a short workload leaves no traces.

    uv run python scripts/run_planner_dev.py --model-version "<format and quantization>" \
        [--no-thinking] [--problem ID ...] [--candidates K]
"""

import argparse
import asyncio
import dataclasses
import inspect
import json
import os
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from aiopslab.generators.workload.wrk import Wrk
from aiopslab.orchestrator.problems.k8s_target_port_misconfig.helpers import get_frontend_url
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
from remvalgate.overlay.fixes import exec_shell, tool_call
from remvalgate.planner.chat import OpenAIChat
from remvalgate.planner.loop import plan, sample
from remvalgate.planner.telemetry import preview
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
# the problem's own workload runs 10 requests per second for 10 s, which left no traces
WORKLOAD = {"rate": 100, "duration": 30}
# the Jaeger client flushes spans every 10 s (jaeger-config.yml in the application)
TRACE_FLUSH_SECONDS = 12
PROMETHEUS_PORT = 32000
TASK = (
    f"Users report errors from the social network application in namespace {NS}. "
    "Find the faulty service and give one command that fixes it."
)


def start_workload(prob) -> None:
    # Wrk returns once the job's pod is ready, not when the job is complete
    url = get_frontend_url(prob.app)
    # Hotel Reservation's payload script picks its own paths, as in AIOpsLab's own workload
    if prob.namespace == NS:
        url += "/wrk2-api/post/compose"
    Wrk(
        rate=WORKLOAD["rate"], dist="exp", connections=4, duration=WORKLOAD["duration"], threads=2
    ).start_workload(payload_script=prob.payload_script, url=url)
    subprocess.run(
        [
            "kubectl",
            "--context",
            "kind-kind",
            "-n",
            "default",
            "wait",
            "--for=condition=complete",
            "job/wrk2-job",
            f"--timeout={WORKLOAD['duration'] + 120}s",
        ],
        check=True,
        capture_output=True,
    )
    time.sleep(TRACE_FLUSH_SECONDS)


def remove_workload() -> None:
    for kind, name in (("job", "wrk2-job"), ("configmap", "wrk2-payload-script")):
        subprocess.run(
            [
                "kubectl",
                "--context",
                "kind-kind",
                "-n",
                "default",
                "delete",
                kind,
                name,
                "--ignore-not-found",
            ],
            check=True,
            capture_output=True,
        )


def prometheus_port_forward() -> subprocess.Popen:
    # the kind config maps no host ports, and get_metrics queries localhost:32000
    kubectl("-n", "observe", "get", "svc", "prometheus-server")
    process = subprocess.Popen(
        [
            "kubectl",
            "--context",
            "kind-kind",
            "-n",
            "observe",
            "port-forward",
            "svc/prometheus-server",
            f"{PROMETHEUS_PORT}:80",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):
        with socket.socket() as probe:
            if probe.connect_ex(("localhost", PROMETHEUS_PORT)) == 0:
                return process
        time.sleep(0.5)
    process.terminate()
    raise AssertionError("the Prometheus port-forward did not open")


async def run_problem(
    orch, problem_id: str, clean: dict, chat, results_dir: Path, k: int, seed: int
) -> dict:
    prob = orch.probs.get_problem_instance(problem_id)
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent("planner")
    loop = asyncio.get_running_loop()
    try:
        prob.inject_fault()
        # the trace export carries spans from before its window, so the summary keeps later ones
        injected_at = int(time.time() * 1e6)
        check(changed(clean, snapshot()), "the injection left the cluster unchanged")
        await asyncio.to_thread(start_workload, prob)
        orch.session.start()
        injected = snapshot()

        def run_tool(tool, arguments):
            # plan() runs in a worker thread, so the coroutine goes back to this loop
            future = asyncio.run_coroutine_threadsafe(
                orch.ask_env(tool_call(tool, arguments)), loop
            )
            # the tools save files and return a path: the model gets a bounded summary too
            return preview(tool, future.result(), 4000, since=injected_at)

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
        candidates = ()
        if result.diagnosis is not None:
            candidates = await asyncio.to_thread(
                sample,
                result,
                NS,
                chat=chat,
                policy=load_policy(DEFAULT_POLICY_PATH),
                k=k,
                seed=seed,
            )
        orch.session.end()
        left = changed(injected, snapshot())
        check(not left, f"the diagnosis changed the cluster: {left}")
    finally:
        remove_workload()
        reset(prob)
    return dataclasses.asdict(result) | {
        "first_observation": first,
        "candidates": [dataclasses.asdict(c) for c in candidates],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-version", required=True, help="format and quantization served")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--problem", action="append", choices=PROBLEMS)
    parser.add_argument("--seed", type=int, default=0)
    # K = 8 is the submitted proposal's value, not group-confirmed; the timing pilot may change it
    parser.add_argument("--candidates", type=int, default=8, help="K, candidates per problem")
    args = parser.parse_args()
    if args.candidates < 1:
        parser.error("--candidates must be at least 1")
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
    forward = prometheus_port_forward()
    try:
        for problem_id in args.problem or PROBLEMS:
            now = datetime.now(UTC)
            mode = "think" if args.thinking else "nothink"
            run_id = f"planner-{problem_id}-{mode}-{now.strftime('%Y%m%dt%H%M%Sz')}".lower()
            run = start_run(run_id, problem_id, registry, TOUCH_LOG, ROOT, pinned_commit(ROOT), now)
            orch = new_orchestrator(results_dir=OUT_DIR / run_id)
            transcript = asyncio.run(
                run_problem(
                    orch, problem_id, clean, chat, OUT_DIR / run_id, args.candidates, args.seed
                )
            )
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
                k=args.candidates,
                r=1,
                aiopslab_commit=run.aiopslab_commit,
                cluster={"context": "kind-kind", "namespace": NS, "nodes": 2},
                timestamp=run.timestamp,
                # the seeds of the draws made, empty when the plan had no diagnosis, so k is the
                # K asked for and not always the number drawn; the diagnosis turns use seed
                sampling={
                    **{
                        n: getattr(chat, n)
                        for n in ("temperature", "top_p", "top_k", "max_tokens", "thinking")
                    },
                    "seeds": [c["seed"] for c in transcript["candidates"]],
                },
            )
            path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
            # the planner's defaults and the chat settings, so two runs can be told apart; plan's
            # k is the retrieval depth, not K
            defaults = inspect.signature(plan).parameters
            transcript["config"] = {
                **dataclasses.asdict(chat),
                **{n: defaults[n].default for n in ("max_turns", "max_observation_chars")},
                "retrieval_k": defaults["k"].default,
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
            drawn = transcript["candidates"]
            admitted = sum(c["rejection"] is None for c in drawn)
            print(f"  tokens={tokens} seconds={seconds:.1f}")
            # apart from the diagnosis, so the cost of K can be read from a run
            print(
                f"  candidates={len(drawn)} admitted={admitted}"
                f" tokens={sum(c['completion_tokens'] for c in drawn)}"
                f" seconds={sum(c['seconds'] for c in drawn):.1f}"
            )
            print(f"  manifest {path}")
    finally:
        forward.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
