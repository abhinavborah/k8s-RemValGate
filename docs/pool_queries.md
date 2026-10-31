# Pool queries

Story E3-S7 (#29), requirement R3.7. One fixed telemetry query per pool incident, recorded after
the injection, for the intrinsic retrieval metrics (#32) and reused by oracle labelling (#27), so
neither records its own.

## What the query is

The query is the planner's first observation: the text of
`kubectl -n <namespace> get deployments,pods` in the problem's own namespace, taken through
AIOpsLab's interface after the injection and the problem's workload, as in
`scripts/run_planner_dev.py`. It is the string the planner retrieves with, so the intrinsic metrics
score the retrieval the system meets. It shows the whole namespace and names no service, so it does
not give the fault away; a `get_logs` call on one service would.

12 pool problems run on Social Network (`test-social-network`). The 4 RevokeAuth and
UserUnregistered problems run on Hotel Reservation (`test-hotel-reservation`,
`docs/problem_inventory.md`), which must be deployed too (README, Deploy and health check). Its
workload uses the same rate and duration, with the problem's own payload script choosing the paths,
as AIOpsLab's own workload does.

Each record also keeps the symptom query of the rule baselines, derived from a JSON listing taken
at the same time (`src/remvalgate/baselines/observe.py`). The rules read object-state faults only,
so the symptom query is empty for the 4 database problems, whose faults change MongoDB users and
delete the service's pods. What the observation shows for them depends on whether those pods have
come back by the time it is taken, so the retrieval metrics should report them apart.

## How it is recorded

    uv run python scripts/record_pool_queries.py

Each of the 16 pool problems is injected once in a pool run: `start_run` appends a touch to
`runs/touch_log.jsonl` and refuses the run unless the confirmation slice is sealed (gate G1) and the
tree is clean. After the observation the fault is reset with AIOpsLab's recover step, and the
Services and Deployments of both applications must equal their state before the run. No model runs
and nothing is tuned.

The manifests, the JSON listings and `pool_queries-<stamp>.jsonl` (one line per problem: problem
id, run id, query, symptom query, seconds from the injection to the observation) go to
`runs/queries/`, which git ignores, and to the shared drive under `k8s-RemValGate/run-logs/`.

The submitted proposal records the queries during the oracle-labelling injections; R3.7 records
them before, after gate G1, so the intrinsic metrics do not wait for oracle labelling.

## Run

Not run yet.
