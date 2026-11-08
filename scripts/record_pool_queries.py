"""Hand run for story E3-S7: record one post-injection telemetry query per pool incident (R3.7).

Needs aiopslab/config.yml, the kind cluster with Social Network and Hotel Reservation Ready
(README; the four database problems run on Hotel Reservation), so it is run by hand and never
by CI. No model runs and the LLM judge must be off. Each problem is a pool run: start_run appends
a touch to runs/touch_log.jsonl and refuses it unless the confirmation slice is sealed (gate G1)
and the tree is clean, so the run needs a clean checkout.

Per problem: inject, run the problem's workload, wait until the fault shows in pod status, take
the planner's first observation through AIOpsLab's interface as scripts/run_planner_dev.py does,
in the problem's own namespace, reset.
That observation is the query: it is what the planner retrieves with, and it names no service, so
it does not give the fault away. The symptom query of the rule baselines is derived from a JSON
listing taken at the same time; the rules read object-state faults only, so it is empty for the
database faults. After the reset the Services and Deployments of both applications must equal
their state before the run.

Outputs go to runs/queries/, which git ignores, and to the shared drive:

    <run id>.json          the run manifest
    <run id>.listing.json  kubectl get deploy,svc,pods -o json, taken after the observation
    pool_queries-<stamp>.jsonl  one line per problem: problem id, run id, query, symptom query
                                and the seconds from the injection to the observation

Each problem is injected once. With no ids every pool problem is recorded; ids can be named to
finish a run that stopped, since a problem recorded again takes a second touch.

    uv run python scripts/record_pool_queries.py [problem id ...]
"""

import asyncio
import json
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from aiopslab.paths import config
from aiopslab.session import Session
from run_dev_end_to_end import TOUCH_LOG, check, kubectl
from run_oracle_labels import wait_for_fault
from run_planner_dev import remove_workload, start_workload

from remvalgate.aiopslab_pin import pinned_commit
from remvalgate.baselines.observe import observe, symptom_query
from remvalgate.harness.cluster_state import changed, normalise
from remvalgate.harness.manifest import (
    DEFAULT_REGISTRY_PATH,
    HOTEL_RESERVATION,
    SOCIAL_NETWORK,
    Manifest,
    Slice,
    load_registry,
    start_run,
    write_manifest,
)
from remvalgate.overlay import new_orchestrator
from remvalgate.overlay.fixes import exec_shell

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "runs" / "queries"
CONTEXT = "kind-kind"
AGENT = "query-recorder"
NAMESPACES = (SOCIAL_NETWORK, HOTEL_RESERVATION)


def snapshot(namespace: str) -> dict:
    return normalise(json.loads(kubectl("-n", namespace, "get", "svc,deploy", "-o", "json").stdout))


def reset(prob) -> None:
    # AIOpsLab's recover step, then the README health check in the problem's namespace
    prob.recover_fault()
    kubectl(
        "-n", prob.namespace, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=300s"
    )


async def record(orch, prob, problem_id: str, clean: dict, results_dir: Path) -> dict:
    namespace = prob.namespace
    orch.session = Session(results_dir=results_dir)
    orch.session.set_problem(prob, pid=problem_id)
    orch.session.set_agent(AGENT)
    try:
        prob.inject_fault()
        injected = time.perf_counter()
        # the database faults change MongoDB users, which these objects do not record
        if "mongodb" not in problem_id:
            took = bool(changed(clean[namespace], snapshot(namespace)))
            check(took, f"{problem_id}: the injection left the cluster unchanged")
        await asyncio.to_thread(start_workload, prob)
        # the oracle run's check; for the database faults the only one, since their injector
        # deletes the service's pods whatever its script did
        wait_for_fault(prob, problem_id)
        orch.session.start()
        # the planner's first observation (scripts/run_planner_dev.py)
        query = await orch.ask_env(exec_shell(f"kubectl -n {namespace} get deployments,pods"))
        seconds = time.perf_counter() - injected
        listing = json.loads(
            kubectl("-n", namespace, "get", "deploy,svc,pods", "-o", "json").stdout
        )
        orch.session.end()
    finally:
        remove_workload()
        reset(prob)
    for ns in NAMESPACES:
        left = changed(clean[ns], snapshot(ns))
        check(not left, f"{problem_id}: the reset left {ns} changed: {left}")
    return {"query": query, "seconds": seconds, "listing": listing}


def main(requested: list[str]) -> int:
    current = subprocess.run(
        ["kubectl", "config", "current-context"], capture_output=True, text=True, check=True
    ).stdout.strip()
    check(current == CONTEXT, f"current kubectl context is {current!r}, expected {CONTEXT!r}")
    check(not config.get("qualitative_eval"), "set qualitative_eval: false in aiopslab/config.yml")
    registry = load_registry(DEFAULT_REGISTRY_PATH)
    pool = [i for i, s in registry.slices.items() if s is Slice.POOL]
    selected = list(dict.fromkeys(requested)) or pool
    others = [i for i in selected if i not in pool]
    check(not others, f"not pool problems: {', '.join(others)}")
    orch = new_orchestrator(results_dir=OUT_DIR)
    for ns in NAMESPACES:
        kubectl("-n", ns, "wait", "--for=condition=Ready", "pods", "--all", "--timeout=60s")
    clean = {ns: snapshot(ns) for ns in NAMESPACES}
    nodes = len(kubectl("get", "nodes", "-o", "name").stdout.split())
    print(f"cluster {CONTEXT}: {nodes} nodes, {len(selected)} pool problems")

    stamp = datetime.now(UTC).strftime("%Y%m%dt%H%M%Sz")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    queries = OUT_DIR / f"pool_queries-{stamp}.jsonl"
    for problem_id in selected:
        now = datetime.now(UTC)
        run_id = f"query-{problem_id}-{now.strftime('%Y%m%dt%H%M%Sz')}"
        prob = orch.probs.get_problem_instance(problem_id)
        # start_run refuses the run before its touch unless this is the problem's own namespace
        run = start_run(
            run_id,
            problem_id,
            registry,
            TOUCH_LOG,
            ROOT,
            pinned_commit(ROOT),
            now,
            namespace=prob.namespace,
        )
        result = asyncio.run(record(orch, prob, problem_id, clean, OUT_DIR / run_id))
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
            cluster={"context": CONTEXT, "namespace": run.namespace, "nodes": nodes},
            timestamp=run.timestamp,
        )
        path = write_manifest(manifest, run, OUT_DIR, registry, TOUCH_LOG)
        path.with_suffix(".listing.json").write_text(json.dumps(result["listing"], indent=2))
        line = {
            "problem_id": problem_id,
            "run_id": run_id,
            "query": result["query"],
            "symptom_query": symptom_query(observe(result["listing"])),
            "seconds_after_injection": round(result["seconds"], 1),
        }
        with queries.open("a", encoding="utf-8") as out:
            out.write(json.dumps(line, sort_keys=True) + "\n")
        print(f"{problem_id}: recorded in {line['seconds_after_injection']} s ({run_id})")
    print(f"queries {queries}")
    print("result: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
