"""Hand run for stories E5-S1, E5-S5 and E5-S2: the planner on development problems, through
AIOpsLab's interface, then K candidate commands sampled from its diagnosis, and K more once if
every one of the first K is rejected.

Needs aiopslab/config.yml, the kind cluster with Social Network Ready (README), and an
OpenAI-compatible server with the planner model loaded (default LM Studio on port 1234), so it is
run by hand and never by CI. Development problems only: the run is not an evaluation-set touch and
appends nothing to runs/touch_log.jsonl. Retrieval is configuration (iii) over the synthetic stub
corpus (story E4-S6), so it needs the retrieval dependency group (uv sync --group retrieval).

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

Story E6-S5 adds two flags. --escalate-when-unsure runs baseline 7: the planner is told when to
escalate, and its action is its own command or its escalation, so no candidates are sampled.
--judge runs baseline 6: the served model scores each admitted candidate at temperature 0, and the
transcript records the judge's model and whether it shares the planner's family. --base-url
points the client at a server that is not on localhost, for example LM Studio on the Windows host
seen from WSL.

Story E6-S2 adds --baseline 3a, 3b or 3c: the untuned planner with retrieval configuration (i),
(ii) or (iii). Its action is the planner's own command, so no candidates are sampled and the
--candidates, --judge and --escalate-when-unsure flags are refused. Here the command is applied:
an admitted command goes through the Orchestrator's exec_shell handler after the diagnosis check,
the outcome is classified after the settle window as for baselines 1 and 2, and the reset is
compared with the clean state. A turn-capped plan or a rejected command is classified as not
applied.

    uv run python scripts/run_planner_dev.py --model-version "<format and quantization>" \
        [--no-thinking] [--problem ID ...] [--candidates K] [--base-url URL] \
        [--escalate-when-unsure] [--judge] [--baseline 3a|3b|3c]
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
from run_baselines_smoke import act
from run_baselines_smoke import reset as reset_and_compare
from run_dev_end_to_end import NS, TOUCH_LOG, check, kubectl, reset, snapshot

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.baselines.judge import JudgeModel, judge
from remvalgate.baselines.untuned import CONFIGURATIONS, conflicting_flags, untuned_retriever
from remvalgate.harness.allowlist import (
    DEFAULT_POLICY_PATH,
    Reason,
    load_policy,
    names_no_namespace,
)
from remvalgate.harness.cluster_state import changed
from remvalgate.harness.harm import SETTLE_SECONDS, kubectl_pods, parse_pods
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
from remvalgate.planner.loop import draw_record, plan, resample_once, task_text
from remvalgate.planner.telemetry import preview
from remvalgate.retrieval.fixed import development_retriever

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "manifests"
SCRATCH = ROOT / "runs" / "planner_scratch"
MODEL = "qwen/qwen3.5-4b"
# the judge is the served planner model, one member's choice, so the families are the same
FAMILY = "qwen3.5"
PROBLEMS = (
    "dev-k8s_target_port-misconfig-mitigation-1",
    "dev-scale_pod_zero_social_net-mitigation-1",
    "dev-assign_to_non_existent_node_social_net-mitigation-1",
)
# the problem's own workload runs 10 requests per second for 10 s, which left no traces
WORKLOAD = {"rate": 100, "duration": 30}
# the Jaeger client flushes spans every 10 s (jaeger-config.yml in the application)
TRACE_FLUSH_SECONDS = 12
PROMETHEUS_PORT = 32000


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
    orch,
    problem_id: str,
    clean: dict,
    chat,
    results_dir: Path,
    k: int,
    seed: int,
    retriever,
    escalate_when_unsure: bool = False,
    judge_chat=None,
    baseline: str | None = None,
) -> dict:
    prob = orch.probs.get_problem_instance(problem_id)
    # the snapshot, reset, observation and fixes below are for Social Network only
    check(prob.namespace == NS, f"{problem_id} runs in {prob.namespace}, not {NS}")
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
            task_text(prob.app.name, prob.namespace),
            first,
            problem_id,
            NS,
            chat=chat,
            run_tool=run_tool,
            retriever=retriever,
            policy=load_policy(DEFAULT_POLICY_PATH),
            schemas=load_schemas(DEFAULT_SCHEMAS_PATH),
            escalate_when_unsure=escalate_when_unsure,
        )
        draws = None
        scores = ()
        applied = {}
        # an escalated or turn-capped plan has no diagnosis, so nothing is sampled or judged.
        # Baseline 7's action is the plan's own command or its escalation, so it samples none:
        # draws under its prompt could escalate, and sample would count that as malformed.
        # Baselines 3a to 3c act on the plan's own command, so they sample none either
        if result.diagnosis is not None and not escalate_when_unsure and baseline is None:
            draws = await asyncio.to_thread(
                resample_once,
                result,
                NS,
                chat=chat,
                policy=load_policy(DEFAULT_POLICY_PATH),
                k=k,
                seed=seed,
            )
            if judge_chat is not None:
                # the gate scores the last draw, so the judge does too
                scores = await asyncio.to_thread(
                    judge, result, draws.candidates, NS, chat=judge_chat
                )
        orch.session.end()
        left = changed(injected, snapshot())
        check(not left, f"the diagnosis changed the cluster: {left}")
        if baseline is not None:
            # the one action: admitted or not, applied through the Orchestrator and classified
            # after the settle window, as baselines 1 and 2 are
            pre = parse_pods(kubectl_pods(NS, "kind-kind"), NS)
            policy = load_policy(DEFAULT_POLICY_PATH)
            # the command is free-form: one kubectl rejects or the parser cannot carry is
            # classified, not raised
            verdict, outcome, sent, note = await act(
                orch,
                prob,
                problem_id,
                result.command,
                policy,
                SETTLE_SECONDS,
                pre,
                results_dir,
                strict=False,
            )
            applied = {
                "baseline": baseline,
                "allow_list": None if verdict is None else (verdict.reason or "admitted"),
                "outcome": str(outcome),
                # an admitted command the parser cannot carry is not sent: a harness limit
                "sent": sent,
                "action_note": note,
            }
            # written before the compared reset, which stops the run if the action changed
            # something the recover step does not undo, so that case keeps its record
            results_dir.mkdir(parents=True, exist_ok=True)
            (results_dir / "action.json").write_text(
                json.dumps(
                    {"problem_id": problem_id, "command": result.command, **applied},
                    indent=2,
                    sort_keys=True,
                )
            )
    finally:
        remove_workload()
        # an applied action can change more than the fault, so the reset is compared too
        if baseline is not None:
            reset_and_compare(prob, clean)
        else:
            reset(prob)
    return (
        dataclasses.asdict(result)
        | {"first_observation": first}
        | draw_record(result, draws)
        | {"judge_scores": [dataclasses.asdict(s) for s in scores]}
        | applied
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-version", required=True, help="format and quantization served")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--problem", action="append", choices=PROBLEMS)
    parser.add_argument("--seed", type=int, default=0)
    # K = 8 is the submitted proposal's value, not group-confirmed; the timing pilot may change it
    parser.add_argument("--candidates", type=int, help="K, candidates per problem (default 8)")
    parser.add_argument("--base-url", default=OpenAIChat.base_url, help="the server's /v1 URL")
    parser.add_argument("--escalate-when-unsure", action="store_true", help="baseline 7")
    parser.add_argument("--judge", action="store_true", help="baseline 6")
    parser.add_argument(
        "--baseline", choices=sorted(CONFIGURATIONS), help="untuned planner 3a, 3b or 3c"
    )
    args = parser.parse_args()
    if args.baseline:
        conflicts = conflicting_flags(
            judge=args.judge,
            escalate_when_unsure=args.escalate_when_unsure,
            candidates=args.candidates,
        )
        if conflicts:
            parser.error(f"--baseline acts on the planner's own command; drop {conflicts}")
    elif args.candidates is None:
        args.candidates = 8
    if args.candidates is not None and args.candidates < 1:
        parser.error("--candidates must be at least 1")
    # the judge would score candidates of the escalate-prompted planner, which is neither baseline
    if args.judge and args.escalate_when_unsure:
        parser.error("--judge and --escalate-when-unsure are separate baselines; run them apart")
    if config.get("qualitative_eval"):
        raise AssertionError("set qualitative_eval: false in aiopslab/config.yml")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    policy = load_policy(DEFAULT_POLICY_PATH)
    # configuration (iii), or the baseline's, loaded once: the models take a while to load
    if args.baseline:
        retriever = untuned_retriever(args.baseline, registry)
    else:
        retriever = development_retriever(registry)
    # thinking can use most of a turn's tokens before any content appears, so it gets more room
    chat = OpenAIChat(
        model=MODEL,
        base_url=args.base_url,
        thinking=args.thinking,
        seed=args.seed,
        max_tokens=4096 if args.thinking else 1024,
    )
    # the judge scores at temperature 0, so a candidate's score does not depend on a draw
    judge_chat = dataclasses.replace(chat, temperature=0.0) if args.judge else None
    judge_model = JudgeModel(MODEL, args.model_version, FAMILY, FAMILY)
    SCRATCH.mkdir(parents=True, exist_ok=True)
    os.chdir(SCRATCH)
    kubectl("-n", NS, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = snapshot()
    forward = prometheus_port_forward()
    try:
        for problem_id in args.problem or PROBLEMS:
            now = datetime.now(UTC)
            mode = "think" if args.thinking else "nothink"
            mode += "-escalate" if args.escalate_when_unsure else ""
            mode += "-judge" if args.judge else ""
            mode += f"-baseline{args.baseline}" if args.baseline else ""
            run_id = f"planner-{problem_id}-{mode}-{now.strftime('%Y%m%dt%H%M%Sz')}".lower()
            run = start_run(run_id, problem_id, registry, TOUCH_LOG, ROOT, pinned_commit(ROOT), now)
            orch = new_orchestrator(results_dir=OUT_DIR / run_id)
            transcript = asyncio.run(
                run_problem(
                    orch,
                    problem_id,
                    clean,
                    chat,
                    OUT_DIR / run_id,
                    args.candidates,
                    args.seed,
                    retriever,
                    args.escalate_when_unsure,
                    judge_chat,
                    args.baseline,
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
                # a 3a to 3c run applies one action and samples no candidate
                k=1 if args.baseline else args.candidates,
                r=1,
                aiopslab_commit=run.aiopslab_commit,
                cluster={"context": "kind-kind", "namespace": NS, "nodes": 2},
                timestamp=run.timestamp,
                # the seeds of the draws made, empty when the plan had no diagnosis and 2K after a
                # resample, so k is the K asked for and not always the number drawn; the
                # diagnosis turns use seed
                sampling={
                    **{
                        n: getattr(chat, n)
                        for n in ("temperature", "top_p", "top_k", "max_tokens", "thinking")
                    },
                    "seeds": [c["seed"] for c in transcript["candidates"]],
                    # which baseline ran and how the judge scored, so the manifest alone says so
                    "escalate_when_unsure": args.escalate_when_unsure,
                    "baseline": args.baseline,
                    **(
                        {"judge": dataclasses.asdict(judge_model) | dataclasses.asdict(judge_chat)}
                        if judge_chat is not None
                        else {}
                    ),
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
                "escalate_when_unsure": args.escalate_when_unsure,
                "baseline": args.baseline,
            }
            if judge_chat is not None:
                # the shared family is a confound of every judge score in this run
                transcript["judge"] = {
                    **dataclasses.asdict(judge_model),
                    "shared_family": judge_model.shared_family,
                    "date": now.strftime("%Y-%m-%d"),
                    "chat": dataclasses.asdict(judge_chat),
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
            # rejected as OUT_OF_SANDBOX because the command names no namespace at all (#129)
            no_namespace = sum(
                (c["verdict"] or {}).get("reason") == Reason.OUT_OF_SANDBOX
                and names_no_namespace(c["command"], policy)
                for c in drawn
            )
            print(f"  tokens={tokens} seconds={seconds:.1f}")
            # apart from the diagnosis, so the cost of K can be read from a run
            print(
                f"  candidates={len(drawn)} admitted={admitted} no_namespace={no_namespace}"
                f" tokens={sum(c['completion_tokens'] for c in drawn)}"
                f" seconds={sum(c['seconds'] for c in drawn):.1f}"
                f" draws={transcript['draws']} escalated={transcript['escalated']}"
            )
            if transcript["escalation"] is not None:
                print(f"  escalated: {transcript['escalation']}")
            if args.baseline:
                print(
                    f"  baseline {args.baseline}: allow_list={transcript['allow_list']}"
                    f" outcome={transcript['outcome']} note={transcript['action_note']!r:.200}"
                )
            if judge_chat is not None:
                judged = transcript["judge_scores"]
                print(
                    f"  judge scores={[s['score'] for s in judged]}"
                    f" shared_family={judge_model.shared_family}"
                    f" tokens={sum(s['completion_tokens'] for s in judged)}"
                    f" seconds={sum(s['seconds'] for s in judged):.1f}"
                )
            print(f"  manifest {path}")
    finally:
        forward.terminate()
    return 0


if __name__ == "__main__":
    sys.exit(main())
