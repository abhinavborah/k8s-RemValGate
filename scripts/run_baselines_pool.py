r"""Hand run for story E9-S2: the baseline-only run on the primary pool (#53, gate G3).

Needs aiopslab/config.yml, the kind cluster with Social Network and Hotel Reservation Ready
(README; the four database problems run on Hotel Reservation), Prometheus installed (README,
Telemetry), the retrieval dependency group (uv sync --group retrieval) and, for 3a, 3b and 3c, an
OpenAI-compatible server with the planner model loaded (default LM Studio on port 1234). So it is
run by hand and never by CI. The run, the R rule and the headroom check are described in
docs/baselines_pool.md.

Each baseline on each problem is one run. start_run appends one touch for a pool problem and
refuses it unless the confirmation slice is sealed (gate G1) and the tree is clean, so the run needs
a clean checkout. Each run executes the baseline's single action once, with the procedure of the
oracle labelling run (scripts/run_oracle_labels.py), so every baseline is paired with the labels
on one procedure:

1. get the command on its own injection, then reset:
   - 3a, 3b and 3c: inject, save the objects as the injection left them, run the workload, wait
     until the fault shows, run the untuned planner with retrieval configuration (i), (ii) or
     (iii) and the settings of the labelling run, check the diagnosis changed nothing (diagnose
     with no candidates);
   - 7: as 3c, with the planner told to escalate when unsure (baseline 7, #44); an escalation
     applies nothing;
   - notools: as 3c, with the telemetry tools replaced by a bundle collected once at injection
     (diagnose_without_tools, below);
   - 1 and 2: inject, save the objects, wait until the fault shows, read the state with observe in
     the problem's namespace, and ask the fault-type rule or retrieval only (configuration (iii))
     for its action;
2. admit the command with check_chain, as 3c's action path admits it;
3. when admitted and sendable: inject, check that the objects equal the saved state, wait until
   the fault shows, send the command through exec_shell, wait the settle window, difference the
   pod status, ask whether the fault remains, classify, reset (execute in the labelling script).

A run with no command, a rejected one, or one that cannot be sent unchanged applies nothing and is
classified ESCALATED_UNLABELLED: this run does not read the oracle labels, so whether a working
candidate exists is left to the summary. Namespaces, observations, workload and health checks come
from the problem (prob.namespace), so the Hotel Reservation problems run in
test-hotel-reservation.

Baseline 3c here is the rerun for the R check (gate G3): the same retrieval path (pool_retriever),
thinking setting and model build as the labelling run, at a pre-stated seed that differs from it
(default 1000). Baselines 1, 2, 3a and 3b take the same seed, so the comparison with 3c is paired
on one seed and one procedure. Run the G3 part with --baseline 3c first; the other four can run
later with the same code and settings.

The arm notools is the ablation without interactive tools (story E5-S3, #38, BRD R5.3): 3c's
planner, retrieval path, seed and settings, with get_logs, get_metrics and get_traces removed and
read-only exec_shell kept. Right after the first observation the harness collects a fixed bundle
once: the metrics and traces of the namespace, summarised as the tools summarise them for 3c, and
the logs of the LOG_SERVICES Deployments with the most error lines logged since the injection
(src/remvalgate/harness/tool_ablation.py). The planner gets the bundle with the task, and its
command is executed as 3c's is. Its run ids start with notools-; the rules and the comparison
with 3c are in docs/tool_ablation.md.

Development problems run the same way with the synthetic stub corpus and no touch, as a dry run;
one run takes the ids of one slice only. A failed check stops the run: the files of the runs
already finished stay, and the rest are run again by id, each with a new touch.

The confirmation slice (#59, R9.6) and the singleton types (#73, R9.5) run baselines 2 and 3c only,
retrieving from every pool incident and the runbook, since they are in no fold (BRD 1.20). Their
outputs go to runs/confirmation/ and runs/singleton/. The first run of the confirmation slice passes
--open-confirmation, which opens it once after every check of the run; a later run of the slice
is refused before the opening and after 3 runs of a problem. Of the 5 singleton types, 3 have a
fault type (oracle.SINGLETON_TYPES) and run; the other 2 are refused before any touch (BRD 1.22).

Outputs go to runs/baselines_pool/, which git ignores, and to the shared drive:

    <run id>.json                the run manifest
    <run id>.transcript.json     the plan or the observations behind the command, and the
                                 bundle of a notools run
    <run id>.result.json         the command, the allow-list verdict, the outcome, the execution

Once the 3c rerun and the oracle labels exist, scripts/summarize_g3.py prints the headroom result,
R and the minimum gain with no cluster.

    uv run python scripts/run_baselines_pool.py --baseline 3c --model-version "<format and \
        quantization>" [--baseline 1 ...] [--no-thinking] [--seed S ...] [--base-url URL] \
        [problem id ...]

Recording for the replay (issue #53): record every pool run with a terminal capture and a
timestamped pod watch, and upload both to the shared drive beside the run's manifests, so the
evaluations can replay it without a rerun.

1. Install asciinema 3.x once (brew install asciinema on macOS; on Linux the distribution package
   or cargo install --locked asciinema). Check with asciinema --version.
2. mkdir -p runs/recordings (under runs/, which git ignores).
3. In a second terminal, before the run, start the pod watch and leave it running until the run
   ends:

       kubectl --context kind-kind get pods -A -w --output-watch-events \
         | while IFS= read -r line; do printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$line"; \
           done > runs/recordings/<run name>.pods.log

4. Start the run inside the recorder:

       asciinema rec --return --title "E9-S2 <run name>" \
         --command "<the run command>" runs/recordings/<run name>.cast

   Do not pass --idle-time-limit when recording, so the file keeps the real timing; shorten the
   waits at playback instead (asciinema play --idle-time-limit 2 --speed 2 <file>).
5. Stop the pod watch with Ctrl-C after the run ends.
6. Rename both files to the run name and upload them to k8s-RemValGate/run-logs/<run name>/ on
   the shared drive, next to the manifests. Do not commit them.

Do not print tokens, passwords or private links in the recorded terminal, and do not pass
--capture-input. A run resumed by problem id gets a new recording. The recording is evidence for
the replay only: outcomes and counts are read from the manifest and result files.
"""

import argparse
import asyncio
import dataclasses
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
from run_dev_end_to_end import TOUCH_LOG, check, kubectl
from run_oracle_labels import (
    POLICY,
    SCHEMAS,
    clean_state,
    diagnose,
    execute,
    objects,
    reset,
    wait_for_fault,
    wait_for_mongod,
)
from run_planner_dev import MODEL, prometheus_port_forward, remove_workload, start_workload

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.baselines.observe import observe
from remvalgate.baselines.retrieval_only import retrieval_only
from remvalgate.baselines.rule import fault_type_rule
from remvalgate.baselines.untuned import pool_untuned_retriever, untuned_retriever
from remvalgate.harness import credentials
from remvalgate.harness.allowlist import check_chain
from remvalgate.harness.cluster_state import changed
from remvalgate.harness.folds import pool_folds
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    MAX_R,
    Manifest,
    Slice,
    load_registry,
    logged_namespace,
    open_confirmation,
    start_run,
    write_manifest,
)
from remvalgate.harness.model_build import build_for
from remvalgate.harness.oracle import DATABASE_TYPES, problem_fault_type
from remvalgate.harness.outcome import classify, single_action_result
from remvalgate.harness.protocol import interleaved
from remvalgate.harness.tool_ablation import LOG_SERVICES, error_lines, noisiest_services
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import CannotSend, exec_shell, tool_call
from remvalgate.planner.chat import API_KEY_ENV, OpenAIChat
from remvalgate.planner.loop import DEFAULT_TOOLS, plan, task_text
from remvalgate.planner.telemetry import preview
from remvalgate.retrieval.fixed import (
    development_retriever,
    held_out_retriever,
    pool_retriever,
)

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "baselines_pool"
SCRATCH = ROOT / "runs" / "baselines_pool_scratch"
# the confirmation slice and the singleton types: in no fold, so no fine-tuned build; one output
# folder each
HELD_OUT = frozenset({Slice.CONFIRMATION, Slice.SINGLETON})
HELD_OUT_BASELINES = frozenset({"2", "3c"})
OUT_DIRS = {
    Slice.CONFIRMATION: ROOT / "runs" / "confirmation",
    Slice.SINGLETON: ROOT / "runs" / "singleton",
}
CONTEXT = "kind-kind"
BASELINES = ("1", "2", "3a", "3b", "3c", "7", "notools", "toolsbundle", "kubectlonly", "3cft")
PLANNER = ("3a", "3b", "3c", "7", "notools", "toolsbundle", "kubectlonly", "3cft")
# the fine-tuned planner (#159): 3c's configuration with the problem's own fold's merged adapter,
# served by llama-server on the GPU host (--fold-servers); the only difference from 3c is the
# model, so a problem is never served by a model trained on it
# the ablation without interactive tools keeps read-only exec_shell (proposal version 2, VI)
NOTOOLS_TOOLS = frozenset({"exec_shell"})
# the arms of the 2 by 2 tool ablation besides 3c (tools, no bundle): their tools and whether the
# harness collects the bundle (#38, #165)
ABLATION = {
    "notools": (NOTOOLS_TOOLS, True),
    "toolsbundle": (DEFAULT_TOOLS, True),
    "kubectlonly": (NOTOOLS_TOOLS, False),
}
# the summary limit the tool outputs of 3c's run get (run_tool in scripts/run_oracle_labels.py)
PREVIEW_CHARS = 4000
# pre-stated before the run: differs from the labelling run's seed 0, so the R check compares two
# draws of the planner and not one draw twice
SEED = 1000


def observe_fault(prob, problem_id: str, clean) -> tuple[list, dict]:
    """Inject, wait until the fault shows, read the state with observe, reset.

    Returns the observations and the objects as the injection left them.
    """
    namespace = prob.namespace
    if problem_fault_type(problem_id) in DATABASE_TYPES:
        wait_for_mongod(namespace)
    try:
        prob.inject_fault()
        # the database faults change MongoDB users, which these objects do not record
        if problem_fault_type(problem_id) not in DATABASE_TYPES:
            took = bool(changed(clean.objects, objects(namespace)))
            check(took, f"{problem_id}: the injection left the cluster unchanged")
        injected = objects(namespace)
        wait_for_fault(prob, problem_id)
        # observe reads pods too: a pod that cannot be scheduled shows only there
        listing = json.loads(
            kubectl("-n", namespace, "get", "deploy,svc,pods", "-o", "json").stdout
        )
        found = observe(listing)
    finally:
        reset(prob, problem_id, clean)
    return found, injected


def logs_since(namespace: str, deployment: str, since: str) -> str:
    """What a pod of the Deployment logged since the given time, or "" when kubectl reads none, as
    for a Deployment scaled to zero or a pod that never started."""
    done = subprocess.run(
        [
            "kubectl",
            "--context",
            CONTEXT,
            "-n",
            namespace,
            "logs",
            f"deploy/{deployment}",
            f"--since-time={since}",
            # kubectl waits 20 s by default for a pod to run, which a Pending pod never does
            "--pod-running-timeout=5s",
        ],
        capture_output=True,
        text=True,
    )
    return done.stdout if done.returncode == 0 else ""


async def collect_bundle(orch, namespace: str, injected_at: int):
    """The fixed telemetry bundle of the notools arm, taken once right after the first observation,
    and the error lines each Deployment logged since the injection, which chose its logs.

    The metrics and traces of the namespace, summarised as run_tool summarises them for 3c, and the
    get_logs output of the LOG_SERVICES Deployments with the most error lines since the injection
    (noisiest_services). Nothing here reads the problem's fault or target. The planner cuts each
    output as it cuts a tool's output.
    """
    since = datetime.fromtimestamp(injected_at / 1e6, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = kubectl(
        "-n", namespace, "get", "deploy", "-o", "jsonpath={.items[*].metadata.name}"
    ).stdout.split()
    counts = {name: error_lines(logs_since(namespace, name, since)) for name in names}
    calls = [("get_metrics", {"namespace": namespace}), ("get_traces", {"namespace": namespace})]
    calls += [
        ("get_logs", {"namespace": namespace, "service": service})
        for service in noisiest_services(counts, LOG_SERVICES)
    ]
    bundle = []
    for tool, arguments in calls:
        output = await orch.ask_env(tool_call(tool, arguments))
        bundle.append((tool, arguments, preview(tool, output, PREVIEW_CHARS, since=injected_at)))
    return bundle, counts


# a copy of diagnose kept apart, so that diagnose, which the oracle labels also run, gains no
# parameter for these arms; a fix to one belongs in both (#165 reruns 3c with this code)
async def diagnose_without_tools(
    orch, prob, problem_id, clean, chat, retriever, results_dir, tools, with_bundle
):
    """diagnose of scripts/run_oracle_labels.py with no candidates and the telemetry tools replaced
    by the bundle: the same injection, workload, wait, first observation, check and reset.

    Returns the plan, the first observation, the bundle, the error lines that chose its logs and the
    objects as the injection left them.
    """
    namespace = prob.namespace
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent("planner")
    loop = asyncio.get_running_loop()
    if problem_fault_type(problem_id) in DATABASE_TYPES:
        wait_for_mongod(namespace)
    try:
        prob.inject_fault()
        # the trace export carries spans from before its window, so the summary keeps later ones
        injected_at = int(time.time() * 1e6)
        # the database faults change MongoDB users, which these objects do not record
        if problem_fault_type(problem_id) not in DATABASE_TYPES:
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
            return preview(tool, future.result(), PREVIEW_CHARS, since=injected_at)

        first = await orch.ask_env(exec_shell(f"kubectl -n {namespace} get deployments,pods"))
        bundle, counts = (
            await collect_bundle(orch, namespace, injected_at) if with_bundle else ([], {})
        )
        result = await asyncio.to_thread(
            plan,
            task_text(prob.app.name, namespace, injected_at),
            first,
            problem_id,
            namespace,
            chat=chat,
            run_tool=run_tool,
            retriever=retriever,
            policy=POLICY,
            schemas=SCHEMAS,
            tools=tools,
            bundle=bundle,
        )
        orch.session.end()
        left = changed(injected, objects(namespace))
        check(not left, f"{problem_id}: the diagnosis changed the cluster: {left}")
    finally:
        remove_workload()
        reset(prob, problem_id, clean)
    return result, first, bundle, counts, injected


def retrievers(baselines: list[str], slice_: Slice, registry) -> dict:
    """The retriever of each baseline that needs one, each model loaded once."""
    if slice_ in HELD_OUT:
        # baselines 2 and 3c only (HELD_OUT_BASELINES), over the whole pool corpus (BRD 1.20)
        iii = held_out_retriever(registry) if {"2", "3c"} & set(baselines) else None
        return {b: iii for b in baselines if b in ("2", "3c")}
    pool = slice_ is Slice.POOL
    iii = None
    chosen = {}
    for b in baselines:
        if b in ("2", "3c", "3cft", "7", *ABLATION):
            iii = iii or (pool_retriever if pool else development_retriever)(registry)
            chosen[b] = iii
        elif b in ("3a", "3b"):
            chosen[b] = (pool_untuned_retriever if pool else untuned_retriever)(b, registry)
    return chosen


def fold_servers(path: Path | None) -> dict[int, dict]:
    """The --fold-servers file: fold to base_url and the Path of its served_build record."""
    if path is None:
        return {}
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    servers = {
        int(f): {"base_url": s["base_url"], "served_build": Path(s["served_build"])}
        for f, s in raw.items()
    }
    check(sorted(servers) == [0, 1, 2, 3], f"{path}: expected folds 0 to 3, got {sorted(servers)}")
    return servers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "problems",
        nargs="*",
        help="pool, development, confirmation or singleton ids, one slice per run (default: the "
        "pool)",
    )
    parser.add_argument(
        "--open-confirmation",
        action="store_true",
        help="open the sealed confirmation slice before the runs (R9.6); refused if it was "
        "opened before, so pass it once, on the first run of the slice",
    )
    parser.add_argument(
        "--baseline", action="append", choices=BASELINES, required=True, help="repeatable"
    )
    parser.add_argument("--model-version", help="format and quantization served (planner arms)")
    parser.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--seed", type=int, action="append", help="repeatable (default 1000 and 1001, #161)"
    )
    parser.add_argument("--base-url", default=OpenAIChat.base_url, help="the server's /v1 URL")
    parser.add_argument(
        "--served-build",
        type=Path,
        help="record file of a llama-server on another host (model_build.read_served_build); "
        "without it the server must be this host's LM Studio",
    )
    parser.add_argument(
        "--fold-servers",
        type=Path,
        help="JSON file mapping each fold to the base_url and served_build record of its "
        "fine-tuned planner, needed by 3cft",
    )
    parser.add_argument(
        "--dev-fold",
        type=int,
        choices=range(4),
        help="the fold whose fine-tuned planner serves 3cft on development problems, which are "
        "in no fold's training data (a dry run before the pool)",
    )
    args = parser.parse_args()
    baselines = list(dict.fromkeys(args.baseline))
    seeds = list(dict.fromkeys(args.seed or [SEED, SEED + 1]))
    if len(seeds) > MAX_R:
        parser.error(f"at most {MAX_R} seeds, the most a manifest records")
    args.seeds = seeds
    args.servers = fold_servers(args.fold_servers) if "3cft" in baselines else {}
    if "3cft" in baselines and not args.servers:
        parser.error("3cft needs --fold-servers")
    uses_model = any(b in PLANNER for b in baselines)
    if uses_model and not args.model_version:
        parser.error("--model-version is needed for the planner arms")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    selected = list(dict.fromkeys(args.problems)) or pool
    unknown = [i for i in selected if i not in registry.slices]
    if unknown:
        parser.error(f"not in the slice registry: {', '.join(unknown)}")
    slices = {registry.slices[i] for i in selected}
    if len(slices) > 1:
        # one slice per run, so a summary is never mixed
        parser.error(f"one slice per run, got {sorted(slices)}")
    (args.slice,) = slices
    development = [i for i in selected if args.slice is Slice.DEVELOPMENT]
    if args.slice in HELD_OUT and set(baselines) - HELD_OUT_BASELINES:
        parser.error(f"{args.slice} problems run baselines {sorted(HELD_OUT_BASELINES)} only")
    untyped = []
    for i in selected:
        try:
            problem_fault_type(i)
        except ValueError:
            untyped.append(i)
    if untyped:
        # redeploy_without_PV and the Astronomy Shop problem are not run (#73)
        parser.error(f"no fault type, so not run: {', '.join(untyped)}")
    if args.open_confirmation and args.slice is not Slice.CONFIRMATION:
        parser.error("--open-confirmation is for confirmation problems only")
    if "3cft" in baselines and development and args.dev_fold is None:
        parser.error("3cft on development problems needs --dev-fold")
    args.folds = pool_folds(registry)
    # AIOpsLab's injectors run kubectl in the current context, the checks here in kind-kind
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    check(current == CONTEXT, f"current kubectl context is {current!r}, expected {CONTEXT!r}")
    check(not config.get("qualitative_eval"), "set qualitative_eval: false in aiopslab/config.yml")
    check(config.get("k8s_host") == "kind", "set k8s_host: kind in aiopslab/config.yml")
    retriever = retrievers(baselines, args.slice, registry)
    # the settings of the labelling run (scripts/run_oracle_labels.py), apart from the seed
    chat = OpenAIChat(
        model=MODEL,
        base_url=args.base_url,
        thinking=args.thinking,
        seed=seeds[0],
        max_tokens=4096 if args.thinking else 1024,
    )
    namespaces = sorted({logged_namespace(i) for i in selected})
    if credentials.AUTH_NAMESPACES & set(namespaces):
        # a missing or unsafe password variable stops the run before anything is touched
        credentials.values()
    SCRATCH.mkdir(parents=True, exist_ok=True)
    args.out = OUT_DIRS.get(args.slice, OUT_DIR)
    args.out.mkdir(parents=True, exist_ok=True)
    # the tools write files to the current directory (get_traces and get_metrics do)
    os.chdir(SCRATCH)
    for namespace in namespaces:
        kubectl("-n", namespace, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = {namespace: clean_state(namespace) for namespace in namespaces}
    nodes = len(kubectl("get", "nodes", "-o", "name").stdout.split())
    print(f"cluster {CONTEXT}: {nodes} nodes, {len(selected)} problems, baselines {baselines}")
    forward = prometheus_port_forward() if uses_model else None
    try:
        if args.open_confirmation:
            # after every check and the port-forward, so a run that cannot start never opens it
            opened = open_confirmation(registry, TOUCH_LOG, ROOT, pinned_commit(ROOT))
            print(f"confirmation slice opened at {opened}", flush=True)
        for seed, problem_id, baseline in interleaved(seeds, selected, baselines):
            row = run_one(
                baseline,
                problem_id,
                registry,
                clean,
                dataclasses.replace(chat, seed=seed),
                retriever,
                args,
                nodes,
            )
            # printed as it returns, so a later stop keeps the rows already classified
            print(json.dumps(row, sort_keys=True), flush=True)
            # every namespace of the run must be as it was before the run
            for ns in namespaces:
                left = changed(clean[ns].objects, objects(ns))
                check(not left, f"{problem_id}: {ns} changed: {left}")
    finally:
        if forward is not None:
            forward.terminate()
    print("result: PASS")
    return 0


def run_one(baseline, problem_id, registry, clean, chat, retriever, args, nodes) -> dict:
    fold = None
    if baseline == "3cft":
        # a pool problem gets its own fold's model; a development problem the --dev-fold one
        fold = args.folds.get(problem_id, args.dev_fold)
        server = args.servers[fold]
        chat = dataclasses.replace(chat, base_url=server["base_url"])
        key = os.environ.get(API_KEY_ENV)
        # read before start_run, so a server that is not the recorded one stops the run first
        build = build_for(MODEL, server["base_url"], server["served_build"], fold, key)
    else:
        # read before start_run, so a model that is not loaded stops the run before its touch
        key = os.environ.get(API_KEY_ENV)
        build = (
            build_for(MODEL, args.base_url, args.served_build, None, key)
            if baseline in PLANNER
            else {}
        )
    now = datetime.now(UTC)
    prefix = baseline if baseline in ABLATION else f"baseline{baseline}"
    run_id = f"{prefix}-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}"
    namespace = logged_namespace(problem_id)
    # start_run refuses a pool run before its touch unless this is the problem's namespace
    run = start_run(
        run_id, problem_id, registry, TOUCH_LOG, ROOT, pinned_commit(ROOT), now, namespace=namespace
    )
    results_dir = args.out / run_id
    orch = new_orchestrator(results_dir=results_dir)
    prob = orch.probs.get_problem_instance(problem_id)
    check(prob.namespace == namespace, f"{problem_id} runs in {prob.namespace}")
    reason = stop = escalation = None
    if baseline in PLANNER:
        bundle = counts = None
        if baseline in ABLATION:
            tools, with_bundle = ABLATION[baseline]
            result, first, bundle, counts, injected = asyncio.run(
                diagnose_without_tools(
                    orch,
                    prob,
                    problem_id,
                    clean[namespace],
                    chat,
                    retriever[baseline],
                    results_dir,
                    tools,
                    with_bundle,
                )
            )
        else:
            result, _, first, injected = asyncio.run(
                diagnose(
                    orch,
                    prob,
                    problem_id,
                    clean[namespace],
                    chat,
                    retriever[baseline],
                    0,
                    chat.seed,
                    results_dir,
                    # baseline 7: 3c told to escalate when unsure (#44), paired with 3a to 3c
                    escalate_when_unsure=baseline == "7",
                )
            )
        command = result.command
        # kept in the row: an escalation, a turn cap and an unparsed reply all leave no command
        stop, escalation = str(result.stop), result.escalation
        defaults = inspect.signature(plan).parameters
        transcript = dataclasses.asdict(result) | {"first_observation": first}
        transcript["config"] = {
            **dataclasses.asdict(chat),
            **{n: defaults[n].default for n in ("max_turns", "max_observation_chars")},
            "retrieval_k": defaults["k"].default,
            "baseline": baseline,
            "escalate_when_unsure": baseline == "7",
        }
        if bundle is not None:
            # the outputs as the planner got them, since Plan.context is empty at the turn cap
            transcript["bundle"] = [
                {"tool": tool, "arguments": arguments, "output": output}
                for tool, arguments, output in bundle
            ]
            # the logs change, so the counts that chose them are kept for the audit
            transcript["bundle_error_lines"] = counts
            transcript["config"] |= {
                "tools": sorted(ABLATION[baseline][0]),
                "bundle_log_services": LOG_SERVICES if ABLATION[baseline][1] else 0,
            }
    else:
        found, injected = observe_fault(prob, problem_id, clean[namespace])
        if baseline == "1":
            action = fault_type_rule(found, namespace)
        else:
            action = retrieval_only(retriever[baseline], found, problem_id, namespace)
        command, reason = action.command, action.reason
        transcript = {
            "observations": [dataclasses.asdict(o) for o in found],
            "command": command,
            "reason": reason,
            "baseline": baseline,
        }
    manifest = Manifest(
        run_id=run_id,
        problem_id=problem_id,
        slice=run.slice,
        commit=run.commit,
        dirty=run.dirty,
        model_name=MODEL if baseline in PLANNER else "none",
        model_version=(
            f"{args.model_version}; thinking={'on' if args.thinking else 'off'}"
            if baseline in PLANNER
            else "no language model"
        ),
        model_date=now.strftime("%Y-%m-%d"),
        seed=chat.seed,
        k=1,
        r=len(args.seeds),
        aiopslab_commit=run.aiopslab_commit,
        cluster={"context": CONTEXT, "namespace": namespace, "nodes": nodes},
        timestamp=run.timestamp,
        model_build=build if baseline in PLANNER else {},
        sampling=(
            {
                **{
                    n: getattr(chat, n)
                    for n in (
                        "temperature",
                        "top_p",
                        "top_k",
                        "presence_penalty",
                        "max_tokens",
                        "thinking",
                    )
                },
                "baseline": baseline,
                # the key run_planner_dev.py records for baseline 7
                "escalate_when_unsure": baseline == "7",
                # 3cft: the fold whose adapter served the run, None for every other arm
                "fold": fold,
            }
            if baseline in PLANNER
            else {"baseline": baseline}
        ),
    )
    path = write_manifest(manifest, run, args.out, registry, TOUCH_LOG)
    path.with_suffix(".transcript.json").write_text(
        json.dumps(transcript, indent=2, sort_keys=True, default=str)
    )
    verdict = check_chain(command, POLICY, namespace) if command else None
    records, outcome, note = [], None, None
    if verdict is not None and verdict.admitted:
        try:
            outcome = execute(
                orch, prob, problem_id, command, clean[namespace], injected, results_dir, records
            )
        except CannotSend as error:
            note = f"cannot be sent unchanged: {error}"
    if outcome is None:
        # nothing applied; whether a working candidate exists is the oracle labels' to say
        outcome = classify(single_action_result(None, None))
    row = {
        "run_id": run_id,
        "problem_id": problem_id,
        "baseline": baseline,
        "seed": chat.seed,
        "command": command,
        "reason": reason,
        "stop": stop,
        "escalation": escalation,
        "allow_list": None if verdict is None else (verdict.reason or "admitted"),
        "note": note,
        "sent": bool(records),
        "outcome": str(outcome),
    }
    path.with_suffix(".result.json").write_text(
        json.dumps(row | {"executions": records}, indent=2, sort_keys=True)
    )
    return row


if __name__ == "__main__":
    sys.exit(main())
