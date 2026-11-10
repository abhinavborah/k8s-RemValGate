"""Hand run for story E3-S5: oracle labels of baseline 3c's K candidates (R3.5).

Needs aiopslab/config.yml, the kind cluster with Social Network and Hotel Reservation Ready
(README; the four database problems run on Hotel Reservation), Prometheus installed (README,
Telemetry), and an OpenAI-compatible server with the planner model loaded (default LM Studio on
port 1234), so it is run by hand and never by CI. Retrieval is configuration (iii), so it needs the
retrieval dependency group (uv sync --group retrieval). The method and the rules are in
docs/oracle_labels.md.

Each problem is one run: start_run appends a touch for a pool problem and refuses it unless the
confirmation slice is sealed (gate G1) and the tree is clean, so the run needs a clean checkout.
Per problem:

1. inject, save the Services and Deployments as the injection left them, run the problem's
   workload, wait until the fault shows in pod status, run baseline 3c (the planner with retrieval
   configuration (iii), with the settings of scripts/run_planner_dev.py), sample K candidates from
   its diagnosis in one draw, check that the diagnosis changed nothing, reset;
2. merge near-identical commands (src/remvalgate/harness/oracle.py); the plan's own command, the
   action of 3c, is labelled apart and executed only when it duplicates no candidate;
3. for each command to execute, the candidates in draw order and then the plan's: inject, check
   that the objects equal the saved post-injection state, wait until the fault shows in pod
   status and take that status, send the command through the Orchestrator's exec_shell handler,
   wait the settle window, difference the pod status, ask whether the fault remains, classify,
   reset.

The reset is AIOpsLab's recover step, then each Service and Deployment that still differs from the
clean state put back with kubectl replace, then every workload healthy, then the comparison with
the clean state; for RevokeAuth and UserUnregistered, whose user and role state no Kubernetes
object holds, also the problem's mitigation eval. The system's own rollback
(src/remvalgate/harness/execute.py) is never used. A failed check stops the run: the files of the
problems already labelled stay, and the rest are run again by id, each with a new touch.

Development problems run the same way with the synthetic stub corpus and no touch, as a dry run;
one run takes pool or development ids, never both. The tools write files to the current directory
(get_traces and get_metrics do), so the script changes into a scratch folder under runs/.

Outputs go to runs/oracle/, which git ignores, and to the shared drive:

    <run id>.json                the run manifest, written once the candidates are sampled
    <run id>.transcript.json     the plan, its retrieved entries and the K candidates
    <run id>.labels.json         the labels, and one record per execution
    oracle_summary-<stamp>.json  the summary of this run's problems

A run finished in two parts is summarized with scripts/summarize_oracle_labels.py, which needs no
cluster.

    uv run python scripts/run_oracle_labels.py --model-version "<format and quantization>" \
        [--no-thinking] [--candidates K] [--seed S] [--base-url URL] [problem id ...]
"""

import argparse
import asyncio
import dataclasses
import functools
import inspect
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from aiopslab.paths import config
from aiopslab.session import Session
from run_baselines_smoke import apply, fault_remains
from run_dev_end_to_end import TOUCH_LOG, check, kubectl
from run_planner_dev import prometheus_port_forward, remove_workload, start_workload
from summarize_oracle_labels import summarize

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.harness import credentials
from remvalgate.harness.allowlist import DEFAULT_POLICY_PATH, check_chain, load_policy
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.execute import kubectl_rolled_out
from remvalgate.harness.folds import fault_type
from remvalgate.harness.harm import detect_harm, kubectl_pods, parse_pods
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    Manifest,
    Slice,
    load_registry,
    logged_namespace,
    start_run,
    write_manifest,
)
from remvalgate.harness.oracle import (
    DATABASE_TYPES,
    Clean,
    fault_shows,
    label_problem,
    reset_namespace,
)
from remvalgate.harness.outcome import Outcome, classify, single_action_result
from remvalgate.harness.snapshot import kubectl_put, strip
from remvalgate.harness.toolcall import DEFAULT_SCHEMAS_PATH, load_schemas
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import carried_action, exec_shell, tool_call
from remvalgate.planner.chat import OpenAIChat
from remvalgate.planner.loop import Draws, draw_record, plan, sample, task_text
from remvalgate.planner.models import add_model_argument
from remvalgate.planner.telemetry import preview
from remvalgate.retrieval.fixed import development_retriever, pool_retriever

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "oracle"
SCRATCH = ROOT / "runs" / "oracle_scratch"
CONTEXT = "kind-kind"
POLICY = load_policy(DEFAULT_POLICY_PATH)
SCHEMAS = load_schemas(DEFAULT_SCHEMAS_PATH)
# the pool queries (docs/pool_queries.md), taken 47 to 49 s after each injection, show the
# database faults as a failing client pod, so a fault that has not shown in 180 s is an error
SHOWS_TIMEOUT = 180.0
HEALTHY_TIMEOUT = 300.0


def listing(namespace: str) -> dict:
    return json.loads(kubectl("-n", namespace, "get", "svc,deploy", "-o", "json").stdout)


def objects(namespace: str) -> dict:
    return normalise(listing(namespace))


def pods(namespace: str) -> dict:
    return parse_pods(kubectl_pods(namespace, CONTEXT), namespace)


def clean_state(namespace: str) -> Clean:
    items = listing(namespace)["items"]
    status = pods(namespace)
    check(all(s.healthy for s in status.values()), f"{namespace}: a workload is unhealthy")
    return Clean(
        objects=normalise({"items": items}),
        sent={f"{i['kind']}/{i['metadata']['name']}": strip(i) for i in items},
        workloads=frozenset(status),
    )


def wait_until_healthy(namespace: str, workloads: frozenset[str]) -> None:
    # parse_pods drops terminating pods, so an old pod cannot stand in for a missing new one
    deadline = time.monotonic() + HEALTHY_TIMEOUT
    while True:
        status = pods(namespace)
        waiting = sorted(k for k in workloads if k not in status or not status[k].healthy)
        if not waiting:
            return
        if time.monotonic() > deadline:
            raise AssertionError(f"{namespace}: not healthy after {HEALTHY_TIMEOUT} s: {waiting}")
        time.sleep(2)


def wait_for_fault(prob, problem_id: str) -> dict:
    """The pod status once the fault shows in it. The same reading is the status before the
    action, so a workload that recovers for a moment between two readings is not taken as
    healthy."""
    deadline = time.monotonic() + SHOWS_TIMEOUT
    while True:
        status = pods(prob.namespace)
        if fault_shows(fault_type(problem_id), prob.faulty_service, status):
            return status
        if time.monotonic() > deadline:
            raise AssertionError(f"{problem_id}: the fault does not show in pod status")
        time.sleep(2)


def reset(prob, problem_id: str, clean: Clean) -> None:
    """AIOpsLab's recover step, then what a command left changed put back, then the checks
    (reset_namespace in src/remvalgate/harness/oracle.py)."""
    namespace = prob.namespace
    reset_namespace(
        problem_id,
        clean,
        recover=prob.recover_fault,
        live=lambda: objects(namespace),
        put=kubectl_put(namespace, CONTEXT),
        rolled_out=kubectl_rolled_out(namespace, CONTEXT),
        wait_healthy=lambda: wait_until_healthy(namespace, clean.workloads),
        eval_succeeds=lambda: prob.eval("", [], 0.0)["success"],
    )


async def diagnose(orch, prob, problem_id, clean, chat, retriever, k, seed, results_dir):
    """Inject, run 3c's planner and sample K candidates, check the diagnosis changed nothing, reset.

    Returns the plan, the candidates (none when the plan has no diagnosis or k is 0), the first
    observation and the objects as the injection left them.
    """
    namespace = prob.namespace
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent("planner")
    loop = asyncio.get_running_loop()
    try:
        prob.inject_fault()
        # the trace export carries spans from before its window, so the summary keeps later ones
        injected_at = int(time.time() * 1e6)
        # the database faults change MongoDB users, which these objects do not record
        if fault_type(problem_id) not in DATABASE_TYPES:
            took = bool(changed(clean.objects, objects(namespace)))
            check(took, f"{problem_id}: the injection left the cluster unchanged")
        injected = objects(namespace)
        await asyncio.to_thread(start_workload, prob)
        # as before each execution; for the database faults the only check, since their injector
        # deletes the client's pods whatever its script did (fault_shows)
        wait_for_fault(prob, problem_id)
        orch.session.start()

        def run_tool(tool, arguments):
            # plan() runs in a worker thread, so the coroutine goes back to this loop
            future = asyncio.run_coroutine_threadsafe(
                orch.ask_env(tool_call(tool, arguments)), loop
            )
            return preview(tool, future.result(), 4000, since=injected_at)

        first = await orch.ask_env(exec_shell(f"kubectl -n {namespace} get deployments,pods"))
        result = await asyncio.to_thread(
            plan,
            task_text(prob.app.name, namespace),
            first,
            problem_id,
            namespace,
            chat=chat,
            run_tool=run_tool,
            retriever=retriever,
            policy=POLICY,
            schemas=SCHEMAS,
        )
        candidates = ()
        # one draw of K: 3c is the untuned planner, which does not resample (R5.2 is the system's);
        # k = 0 runs the plan only, as the baseline-only run does (scripts/run_baselines_pool.py)
        if k and result.diagnosis is not None:
            candidates = await asyncio.to_thread(
                sample, result, namespace, chat=chat, policy=POLICY, k=k, seed=seed
            )
        orch.session.end()
        left = changed(injected, objects(namespace))
        check(not left, f"{problem_id}: the diagnosis changed the cluster: {left}")
    finally:
        remove_workload()
        reset(prob, problem_id, clean)
    return result, candidates, first, injected


def execute(orch, prob, problem_id, command, clean, injected, results_dir, records) -> Outcome:
    """Execute one command on a fresh injection and classify it, then reset."""
    namespace = prob.namespace
    # raises CannotSend before anything is injected
    carried_action(command, orch.parser.parse, strict=False)
    start = time.perf_counter()
    try:
        prob.inject_fault()
        left = changed(injected, objects(namespace))
        check(not left, f"{problem_id}: the injection differs from the saved state: {left}")
        pre = wait_for_fault(prob, problem_id)
        response = asyncio.run(apply(orch, prob, problem_id, command, results_dir, strict=False))
        harm = detect_harm(
            pre,
            lambda: kubectl_pods(namespace, CONTEXT),
            fault_remains(prob, problem_id),
            namespace,
        )
    finally:
        reset(prob, problem_id, clean)
    outcome = classify(single_action_result(harm, None))
    records.append(
        {
            "command": command,
            "response": response,
            "outcome": str(outcome),
            "harmful": harm.harmful,
            "newly_unhealthy": [f"{s.key}: {s.reason} ({s.detail})" for s in harm.newly_unhealthy],
            "fault_remains": harm.fault_remains,
            "seconds": round(time.perf_counter() - start, 1),
        }
    )
    print(f"  {outcome}: {command}", flush=True)
    return outcome


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("problems", nargs="*", help="pool or development ids (default: the pool)")
    add_model_argument(parser)
    parser.add_argument("--model-version", required=True, help="format and quantization served")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--seed", type=int, default=0)
    # K = 8 is the submitted proposal's value, not group-confirmed
    parser.add_argument("--candidates", type=int, default=8, help="K, candidates per problem")
    parser.add_argument("--base-url", default=OpenAIChat.base_url, help="the server's /v1 URL")
    args = parser.parse_args()
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    development = [i for i, s in registry.slices.items() if s is Slice.DEVELOPMENT]
    selected = list(dict.fromkeys(args.problems)) or pool
    others = [i for i in selected if i not in pool and i not in development]
    if others:
        parser.error(f"not pool or development problems: {', '.join(others)}")
    if any(i in pool for i in selected) and any(i in development for i in selected):
        parser.error("pool and development problems run apart, so a summary is never mixed")
    if args.candidates < 1:
        parser.error("--candidates must be at least 1")
    # AIOpsLab's injectors run kubectl in the current context, the checks here in kind-kind
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    check(current == CONTEXT, f"current kubectl context is {current!r}, expected {CONTEXT!r}")
    check(not config.get("qualitative_eval"), "set qualitative_eval: false in aiopslab/config.yml")
    check(config.get("k8s_host") == "kind", "set k8s_host: kind in aiopslab/config.yml")
    # configuration (iii), loaded once: the models take a while to load
    retriever = (pool_retriever if selected[0] in pool else development_retriever)(registry)
    # the settings of baseline 3c in scripts/run_planner_dev.py
    chat = OpenAIChat(
        model=args.model,
        base_url=args.base_url,
        thinking=args.thinking,
        seed=args.seed,
        max_tokens=4096 if args.thinking else 1024,
    )
    namespaces = sorted({logged_namespace(i) for i in selected})
    if credentials.AUTH_NAMESPACES & set(namespaces):
        # a missing or unsafe password variable stops the run before anything is touched
        credentials.values()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    os.chdir(SCRATCH)
    for namespace in namespaces:
        kubectl("-n", namespace, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = {namespace: clean_state(namespace) for namespace in namespaces}
    nodes = len(kubectl("get", "nodes", "-o", "name").stdout.split())
    print(f"cluster {CONTEXT}: {nodes} nodes, {len(selected)} problems, K = {args.candidates}")
    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    written = []
    forward = prometheus_port_forward()
    try:
        for problem_id in selected:
            now = datetime.now(UTC)
            run_id = f"oracle-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}"
            namespace = logged_namespace(problem_id)
            # start_run refuses a pool run before its touch unless this is the problem's namespace
            run = start_run(
                run_id,
                problem_id,
                registry,
                TOUCH_LOG,
                ROOT,
                pinned_commit(ROOT),
                now,
                namespace=namespace,
            )
            orch = new_orchestrator(results_dir=OUT_DIR / run_id)
            prob = orch.probs.get_problem_instance(problem_id)
            check(prob.namespace == namespace, f"{problem_id} runs in {prob.namespace}")
            result, candidates, first, injected = asyncio.run(
                diagnose(
                    orch,
                    prob,
                    problem_id,
                    clean[namespace],
                    chat,
                    retriever,
                    args.candidates,
                    args.seed,
                    OUT_DIR / run_id,
                )
            )
            manifest = Manifest(
                run_id=run_id,
                problem_id=problem_id,
                slice=run.slice,
                commit=run.commit,
                dirty=run.dirty,
                model_name=args.model,
                model_version=f"{args.model_version}; thinking={'on' if args.thinking else 'off'}",
                model_date=now.strftime("%Y-%m-%d"),
                seed=args.seed,
                k=args.candidates,
                r=1,
                aiopslab_commit=run.aiopslab_commit,
                cluster={"context": CONTEXT, "namespace": namespace, "nodes": nodes},
                timestamp=run.timestamp,
                # the seeds of the draws, empty when the plan had no diagnosis; the diagnosis
                # turns use seed
                sampling={
                    **{
                        n: getattr(chat, n)
                        for n in ("temperature", "top_p", "top_k", "max_tokens", "thinking")
                    },
                    "seeds": [c.seed for c in candidates],
                    "baseline": "3c",
                },
            )
            path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
            # the planner's defaults and the chat settings; plan's k is the retrieval depth, not K
            defaults = inspect.signature(plan).parameters
            transcript = (
                dataclasses.asdict(result)
                | {"first_observation": first}
                | draw_record(result, Draws((candidates,)) if candidates else None)
            )
            # candidates whose stray closing brace was stripped before the check (#146)
            transcript["stripped"] = sum(c.raw_command is not None for c in candidates)
            transcript["config"] = {
                **dataclasses.asdict(chat),
                **{n: defaults[n].default for n in ("max_turns", "max_observation_chars")},
                "retrieval_k": defaults["k"].default,
                "baseline": "3c",
            }
            path.with_suffix(".transcript.json").write_text(
                json.dumps(transcript, indent=2, sort_keys=True, default=str)
            )
            print(f"{problem_id}: stop={result.stop} diagnosis={result.diagnosis}")
            print(f"  plan's command: {result.command} stripped={result.raw_command is not None}")
            admitted = sum(c.rejection is None for c in candidates)
            print(
                f"  candidates={len(candidates)} admitted={admitted}"
                f" stripped={transcript['stripped']}",
                flush=True,
            )
            own = None
            if result.command is not None:
                # admitted as 3c's action path admits it (act in run_baselines_smoke.py), so a
                # chain of commands is executed; the K are admitted by sample with check
                verdict = check_chain(result.command, POLICY, namespace)
                own = (
                    result.command,
                    None if verdict.admitted else f"allow-list: {verdict.reason}",
                )
            records = []
            run_command = functools.partial(
                execute,
                orch,
                prob,
                problem_id,
                clean=clean[namespace],
                injected=injected,
                results_dir=OUT_DIR / run_id,
                records=records,
            )
            labels = label_problem(
                problem_id,
                [(c.seed, c.command, c.rejection) for c in candidates],
                own,
                run_command,
                POLICY,
            )
            labels_path = path.with_suffix(".labels.json")
            labels_path.write_text(
                json.dumps(
                    {"run_id": run_id, **labels.record(), "executions": records},
                    indent=2,
                    sort_keys=True,
                )
            )
            written.append(labels_path)
            own_outcome = None if labels.plan is None else labels.plan.outcome
            print(
                f"  n={labels.n} c={labels.c} any_working={labels.any_working_candidate}"
                f" mixed={labels.mixed} plan's command={own_outcome}"
            )
            # every namespace of the run must be as it was before the run
            for ns in namespaces:
                left = changed(clean[ns].objects, objects(ns))
                check(not left, f"{problem_id}: {ns} changed: {left}")
    finally:
        forward.terminate()
    totals = summarize(written)
    summary_path = OUT_DIR / f"oracle_summary-{stamp}.json"
    summary_path.write_text(json.dumps(totals, indent=2, sort_keys=True))
    print(json.dumps(totals, indent=2, sort_keys=True))
    print(f"summary {summary_path}")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
