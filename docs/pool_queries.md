# Pool queries

Story E3-S7 (#29), requirement R3.7. One fixed telemetry query per pool incident, recorded after
the injection, for the intrinsic retrieval metrics (#32), so they do not record their own. Oracle
labelling (#27) does not reuse these queries: its planner retrieves with its own first observation
of the live cluster, since a recorded one names pods that a new injection replaces (BRD version
1.10, confirmed by the group in version 1.14).

## What the query is

The query is the planner's first observation: the text of
`kubectl -n <namespace> get deployments,pods` in the problem's own namespace, taken through
AIOpsLab's interface after the injection and the problem's workload, as in
`scripts/run_planner_dev.py`. It is the string the planner retrieves with, so the intrinsic metrics
score the retrieval the system meets. It covers the whole namespace and states no fault type,
target or command. It does show the faulted workload wherever the fault leaves pod or Deployment
state, and its application tells the database problems apart (see Run); a `get_logs` call on one
service would name the target outright.

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
tree is clean. After the observation the fault is reset with AIOpsLab's recover step, all pods of
the problem's namespace must be Ready, and the Services and Deployments of both applications must
equal their state before the run. No model runs and nothing is tuned.

For the 4 database problems these checks do not read MongoDB: the injection is taken as done when
the service's pods were replaced, which AIOpsLab's injector does after its script whatever the
script's result, and the reset is checked on Kubernetes objects and pod readiness only. The query
records show the `geo` or `rate` pod failing after each of these injections, which the fault
explains, but the restored user and role state was not checked.

Since the run, `start_run` also refuses a pool, confirmation or singleton run that does not name the
problem's
own namespace (`logged_namespace` in `src/remvalgate/harness/manifest.py`), and the script passes
it. This catches a script that names the wrong namespace, not one that names the right one and then
acts in another. That change refactored the script, so the form in the repository has not run on
the cluster; running it on a pool problem again takes a new touch.

Since the run, the script also waits after the workload until the fault shows in pod status, with
the oracle run's check (`wait_for_fault` in `scripts/run_oracle_labels.py`). For the 4 database
problems that is a `geo` or `rate` client that runs and is not ready, so an injection whose script
failed now stops the run instead of being taken as done when the pods were replaced. The recorded
queries were taken without this wait.

The manifests, the JSON listings and `pool_queries-<stamp>.jsonl` (one line per problem: problem
id, run id, query, symptom query, seconds from the injection to the observation) go to
`runs/queries/`, which git ignores, and to the shared drive under `k8s-RemValGate/run-logs/`.

The submitted proposal records the queries during the oracle-labelling injections; R3.7 records
them before, after gate G1, so the intrinsic metrics do not wait for oracle labelling.

## Run

Recorded on 2026-10-31 (UTC) on the two node kind cluster at AIOpsLab
`576c3f080c7ae2459a972afd4a2f8d5f01828430`, with Social Network and Hotel Reservation deployed: one
query for each of the 16 pool problems, each observation taken 47 to 49 seconds after the
injection. After every reset the Services and Deployments matched their state before the run: in
Social Network for the first run, and in both applications for the second. No model ran.

It took two runs. The first, at commit `a0d84d8` (an earlier form of the script commit, later
folded into it and kept by the tag `pool-queries-run1`), recorded the three upstream TargetPortMisconfig
problems and stopped at `revoke_auth_mongodb-mitigation-1`: Hotel Reservation was not deployed, so
the injection found no pods to change and the workload could not find the frontend. Building the
problem had created the namespace `test-hotel-reservation` with 4 ConfigMaps (2 MongoDB init
scripts, 2 fault scripts), which were deleted before the application was deployed. That attempt has a touch and no query. The application was then deployed, the script made to
follow each problem's namespace, and the second run, at commit `7d55817`, recorded the other 13.
The touch log holds 17 query touches: 16 with a query and the failed attempt.

What the queries show, read from the records:

| Fault type | Problems | Planner observation | Symptom query |
|---|---|---|---|
| TargetPortMisconfig | 6 | nothing: every pod Running and every Deployment ready | names the faulted Service |
| ScalePod | 3 | the Deployment at `0/0` | names the Deployment |
| AssignNonExistentNode | 3 | a Pending pod and the Deployment at `0/1` | names the Deployment |
| RevokeAuth, UserUnregistered | 4 | the `geo` or `rate` pod in Error or CrashLoopBackOff and its Deployment at `0/1` | empty |

The observation shows Deployments and pods, not Services, so a wrong `targetPort` leaves no trace
in it: the 6 TargetPortMisconfig queries differ only in pod names and ages, and retrieval on them
cannot tell the faulted service. The symptom query also names `media-frontend` on every Social
Network problem, a healthy Service on a port its Deployment does not declare (the limit stated in
`src/remvalgate/baselines/observe.py`).

The namespace gives the database faults away. Only the 4 database problems run on Hotel
Reservation, so any query that lists `geo`, `rate` or `mongodb-*` objects is one of them, whatever
the symptoms, and fault-type Recall@3 is inflated for these 4. Within them, RevokeAuth and
UserUnregistered look the same (the pod in Error or CrashLoopBackOff, the Deployment at `0/1`), so
the query cannot tell the two types apart.

These findings are reported with the intrinsic metrics (#32), not changed here; the JSON listings keep the Services, so another query form can be built from them
without injecting again.

The two `pool_queries-<stamp>.jsonl` files, the manifests, the listings and the script logs are on
the shared drive under `k8s-RemValGate/run-logs/pool_queries_20261031/`.
